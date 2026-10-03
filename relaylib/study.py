"""Reviewer model studies: replay settled relay reviews against other models and score them.

Cases come from past relay reviews whose outcome is known. Every model gets exactly the prompt relay would
build, run against a frozen copy of the code at the reviewed commit. Which expected blockers a model found is
judged by a person (or an agent) from a scoring sheet; everything else is measured."""
import concurrent.futures as cf
import glob
import json
import os
import re
import subprocess

from . import gitops, identity, prompts, runner, state, verdict
from .config import parse_model_spec

NOTE = ("\n\nNote for this run: review the code exactly as of {head} against {base} (use those SHAs, not the "
        "current branch or PR state on GitHub, which may have moved on or been merged).\n")


def _review(repo, slug, stage, round_no):
    paths = [p for p in glob.glob(os.path.join(state.feature_dir(repo, slug), "reviews", f"{stage}-{round_no}.*.md"))
             if not p.endswith(".error.md")]
    if not paths:
        raise SystemExit(f"no {stage} round {round_no} review for {slug} in {repo}")
    text = open(sorted(paths)[0]).read()
    _, front, body = text.split("---", 2)
    return json.loads(front), body.strip()


def case_from_review(repo, slug, stage="build", round_no=1):
    """A study case from a settled review. Its blockers are the answer key candidates: keep only the ones that
    were confirmed (fixed with a failing test, or reproduced) and add any later-found real ones by hand."""
    repo = os.path.abspath(os.path.expanduser(repo))
    st = state.read_state(state.state_path(repo, slug))
    meta, body = _review(repo, slug, stage, round_no)
    v = verdict.parse(body, round_no)
    prior_ids = []
    if round_no > 1:  # a later round was asked to account for the previous round's blockers: replay that too
        _, prev_body = _review(repo, slug, stage, round_no - 1)
        prior_ids = [f.id for f in verdict.parse(prev_body, round_no - 1).blocking]
    return {"id": f"{gitops.repo_name(repo)}-pr{st.get('pr')}-{slug}", "repo": repo, "slug": slug, "stage": stage,
            "round": round_no, "prior_ids": prior_ids, "pr": st.get("pr"), "head": meta["head"],
            "base": meta.get("base_sha"),
            "base_ref": meta.get("base_ref") or "origin/develop", "small": bool(st.get("small")),
            "expected_verdict": v.verdict,
            "expected": [{"id": f.id, "summary": f.text} for f in v.blocking]}


def result_from_review(repo, slug, stage, round_no, case_id):
    """The original review as a result row (the baseline), without running anything."""
    repo = os.path.abspath(os.path.expanduser(repo))
    meta, body = _review(repo, slug, stage, round_no)
    v = verdict.parse(body, round_no)
    tokens = meta.get("tokens") or {}
    effort = f"@{meta['effort']}" if meta.get("effort") else ""
    return {"case": case_id, "label": f"{meta['reviewer']}:{meta['model']}{effort} (original)",
            "provider": meta["reviewer"], "model": meta["model"], "effort": meta.get("effort"), "ok": True,
            "error": "", "usage": {"input": tokens.get("input", 0), "cached": tokens.get("cached", 0),
                                   "output": tokens.get("output", 0)},
            "duration_s": meta.get("duration_s"), "verdict": v.verdict, "blocking": [f.text for f in v.blocking],
            "notes": v.notes, "format_ok": True, "text": body}


def label_of(spec):
    return f"{spec.provider}:{spec.model}" + (f"@{spec.effort}" if spec.effort else "")


def result_path(out_dir, case_id, label):
    safe = re.sub(r"[^A-Za-z0-9@._-]", "_", label)
    return os.path.join(out_dir, "results", f"{case_id}--{safe}.json")


def _one(case, spec, worktree, timeout_s):
    round_no = case.get("round", 1)
    ctx = {"feature_dir": os.path.relpath(state.feature_dir(worktree, case["slug"]), worktree), "round": round_no,
           "prior_ids": ", ".join(case.get("prior_ids") or []) or "none", "small": "yes" if case.get("small") else "no", "pr": case.get("pr") or "",
           "base_ref": case.get("base_ref", "origin/develop"), "base_sha": case.get("base") or "",
           "head_sha": case["head"]}
    prompt = prompts.render(case.get("stage", "build"), ctx) + NOTE.format(head=case["head"], base=case.get("base"))
    res = runner.run_review(spec, prompt, worktree, identity.child_env(dict(os.environ), spec.provider), timeout_s)
    out = {"case": case["id"], "label": label_of(spec), "provider": spec.provider, "model": spec.model,
           "effort": spec.effort, "ok": res.ok, "error": res.error, "usage": res.usage,
           "duration_s": round(res.duration_s, 1), "text": res.text}
    try:
        v = verdict.parse(res.text, round_no)
        out.update(verdict=v.verdict, blocking=[f.text for f in v.blocking], notes=v.notes, format_ok=True)
    except verdict.VerdictError as e:
        out.update(verdict=None, blocking=[], notes=[], format_ok=False, parse_error=str(e))
    return out


def run(cases, models, out_dir, workers=8, timeout_s=1200):
    """Every model reviews every case, in parallel, each case from a detached worktree at its reviewed head."""
    specs = [parse_model_spec(m) for m in models]
    os.makedirs(os.path.join(out_dir, "results"), exist_ok=True)
    trees = {}
    try:
        for case in cases:
            repo = os.path.abspath(os.path.expanduser(case["repo"]))  # cases.json may use ~/ paths
            wt = os.path.abspath(os.path.join(out_dir, f"wt-{case['id']}"))
            subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "--detach", wt, case["head"]],
                           check=True, capture_output=True)
            trees[case["id"]] = (repo, wt)
        jobs = [(case, spec) for case in cases for spec in specs]
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda j: _one(j[0], j[1], trees[j[0]["id"]][1], timeout_s), jobs))
        for r in results:
            with open(result_path(out_dir, r["case"], r["label"]), "w") as f:
                json.dump(r, f, indent=2)
        return results
    finally:
        for repo, wt in trees.values():
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], capture_output=True)
            subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True)


def load_results(out_dir):
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(out_dir, "results", "*.json")))]


def sheet(cases, results):
    """A scoring sheet: each case's answer key next to every model's blockers, for a person to judge."""
    lines = ["# Scoring sheet", "", "For each case and model, list the expected ids the model found in scores.json:",
             '`{"<case>": {"<label>": {"found": ["H1"], "comment": "..."}}}`. A blocker that is not in the key is',
             "a false blocker on a clean case, or unverified on the others, until someone confirms it.", ""]
    for case in cases:
        lines += [f"## {case['id']} (expected {case['expected_verdict']})", ""]
        lines += [f"- expected {e['id']}: {e.get('summary', '')}" for e in case["expected"]] or ["- (clean: no blockers)"]
        for r in [r for r in results if r["case"] == case["id"]]:
            lines += ["", f"### {r['label']}: {r['verdict']}"]
            lines += [f"- {b}" for b in r["blocking"]] or ["- (no blockers)"]
        lines.append("")
    return "\n".join(lines)


def report(cases, results, scores):
    """Markdown tables: bugs found, wrong verdicts, time, tokens per model; and time per review."""
    by_case = {c["id"]: c for c in cases}
    labels = list(dict.fromkeys(r["label"] for r in results))
    rows = ["| Model | Real bugs found | Wrong verdicts | Total time | Input tokens (cached) | Output tokens | Format ok |",
            "|---|---|---|---|---|---|---|"]
    for label in labels:
        mine = [r for r in results if r["label"] == label and r["case"] in by_case]
        total = sum(len(by_case[r["case"]]["expected"]) for r in mine)
        found = sum(len(((scores.get(r["case"]) or {}).get(label) or {}).get("found", [])) for r in mine)
        wrong = sum(1 for r in mine if r.get("verdict") != by_case[r["case"]]["expected_verdict"])
        secs = sum(r.get("duration_s") or 0 for r in mine)
        tin = sum(r["usage"]["input"] for r in mine)
        cached = sum(r["usage"]["cached"] for r in mine)
        tout = sum(r["usage"]["output"] for r in mine)
        fmt = sum(1 for r in mine if r.get("format_ok"))
        pct = f"{100 * cached / tin:.0f}%" if tin else "-"
        rows.append(f"| {label} | {found}/{total} | {wrong} | {secs:.0f}s | {tin / 1000:.0f}k ({pct}) | "
                    f"{tout / 1000:.1f}k | {fmt}/{len(mine)} |")
    ids = [c["id"] for c in cases]
    times = ["| Model | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
    for label in labels:
        cells = []
        for cid in ids:
            r = next((r for r in results if r["label"] == label and r["case"] == cid), None)
            cells.append(f"{r['duration_s']:.0f}" if r and r.get("duration_s") is not None else "-")
        times.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(["## Results", ""] + rows + ["", "## Time per review (seconds)", ""] + times) + "\n"
