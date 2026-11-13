"""Published feature data and a background cache shared by the UI endpoints."""
import concurrent.futures
import contextvars
import dataclasses
import datetime as dt
import inspect
import json
import os
import re
import sys
import threading
import time
import tomllib

from .. import (agentask, availability, buildstats, config, events, gitops, health, hookinstall, ledger, leftoff, merged,
               othersessions, notes as ownernotes, owneractions, reviewjobs, reviewtables, scancache, sessions, state, status,
               usage as samples, verdict, waiting, writerusage)
from ..errors import RelayError

WORKERS = 8  # parallel fetches and feature details; gh and git are the wait, not the CPU


def _allowed():
    root = status.projects_root()
    return scancache.per_build(("allowed", root), lambda: {os.path.realpath(p) for p in status.checkouts(root)})


def allowed_repo(repo):
    real = os.path.realpath(repo)
    if real not in _allowed():
        raise RelayError("repository is not in relay's projects or worktrees")
    return real


def read_file(repo, slug, ref, path):
    repo = allowed_repo(repo)
    prefix = f"{state.RELAY_DIR}/{slug}/"
    if (not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) or not re.fullmatch(r"[0-9a-f]{40}", ref)
            or not path.lower().startswith(prefix) or any(p in (".", "..", "") for p in path.split("/"))):
        raise RelayError("file must be under this feature's folder at a displayed commit")
    text = gitops.show(repo, ref, path)
    if text is None:
        raise RelayError("file is not present at this revision")
    return text


def _review(repo, ref, path):
    text = gitops.show(repo, ref, path) or ""
    result = {"path": path, "stage": os.path.basename(path).split("-")[0], "blocking": [], "notes": []}
    try:
        meta = state.parse_state(text, path)
        result.update(meta)
        try:
            result.update(dataclasses.asdict(verdict.parse(text, meta.get("round", 1))))
        except RelayError as e:
            result["parse_error"] = str(e)
        result["fallback"] = bool(meta.get("skipped_reviewers"))
        result["same_provider"] = bool(meta.get("same_provider"))
        result["confirmation"] = bool(meta.get("confirmation") or meta.get("confirming"))
    except RelayError as e:
        result["parse_error"] = str(e)
    return result


def _local(repo, slug, branch, commit, handoff):
    changed, local = False, None
    for checkout in gitops.worktrees(repo):
        if not os.path.isdir(checkout) or gitops.current_branch(checkout) != branch:
            continue
        changed = changed or bool(gitops.git(checkout, "status", "--porcelain").stdout.strip())
        changed = changed or gitops.git(checkout, "diff", "--quiet", commit, "HEAD", check=False).returncode != 0
        path = os.path.join(state.feature_dir(checkout, slug), "handoff.md")
        if os.path.isfile(path):
            with open(path) as f:
                text = f.read()
            if text != handoff:
                local = {"path": path, "text": text, "published": False}
    return changed, local


def published_for(repo, slug, ref):
    """(commit, state) of the feature on its own origin branch ref, from the build's scan results; None when ref is
    not that branch, so the caller walks the branches as owner actions do (dashboard-scan R4)."""
    if not ref or not gitops.remote_ref_exists(repo, ref):
        return None
    commit = gitops.head_sha(repo, ref)
    try:
        st = owneractions.state_at(repo, slug, commit)
    except RelayError:
        return None
    return (commit, st) if ref == f"origin/{st.get('branch')}" else None


def _scan_ref(row):
    """The origin branch the scan found the row's feature on: its own path for a remote row, else the branch the
    checked-out state names."""
    path = row.get("path") or ""
    if ":" in path:
        return path.split(":", 1)[0]
    try:
        branch = state.read_state(state.state_path(row["checkout"], row["feature"])).get("branch")
    except (RelayError, OSError):
        return None
    return f"origin/{branch}" if branch else None


def feature(repo, slug, records=None, published=None):
    """One feature's detail. published=(commit, state) comes from the snapshot's scan; without it the published
    branch is found as owner actions find it."""
    repo = allowed_repo(repo)
    archived = False
    try:
        if published is not None:
            seen = owneractions.seen_at(repo, published[0], published[1])
        else:
            seen = owneractions.fingerprint(repo, slug, fetch=False)
    except RelayError:
        # Merging deletes the feature branch. Keep its published history readable on the base.
        seen = None
        for ref in ("origin/develop", "origin/main"):
            try:
                commit = gitops.head_sha(repo, ref)
                st = owneractions.state_at(repo, slug, commit)
                if merged.is_done(repo, st):
                    seen = {"commit": commit, "branch": st["branch"], "stage": st["stage"],
                            "status": st["status"], "owner": st.get("owner") or {},
                            "pr": st.get("pr"), "pr_head": None}
                    archived = True
                    break
            except (RelayError, OSError):
                continue
        if seen is None:
            raise
    commit = seen["commit"]
    st = published[1] if published is not None and not archived else owneractions.state_at(repo, slug, commit)
    files = gitops.ls_files(repo, commit, f"{state.RELAY_DIR}/{slug}")
    handoff_path = next((p for p in files if p.lower().endswith("/handoff.md")), None)
    handoff = gitops.show(repo, commit, handoff_path) if handoff_path else None
    actions, reasons = [] if archived else owneractions.applicable(st), {}
    info, ci, tests = {"number": st.get("pr"), "state": "none", "url": None}, "none", None
    if st.get("pr") and (archived or merged.is_done(repo, st)):  # merged: nothing on GitHub can change
        info["state"], ci = "MERGED", "merged"
        try:
            info["url"] = f"https://github.com/{owneractions.github_repo(repo)}/pull/{st['pr']}"
        except RelayError:
            pass
    elif st.get("pr"):
        try:
            if seen.get("github_error"):
                raise RelayError(seen["github_error"])
            info.update(gitops.pr_info(repo, st["pr"]))
            try:
                info["url"] = f"https://github.com/{owneractions.github_repo(repo)}/pull/{st['pr']}"
            except RelayError:
                pass
            tests = status.tests_flag(repo, st, info)
            if tests and tests.startswith("tests: local,"):  # local mode: the gate is the test run, not GitHub CI
                ci = "local"
            else:
                tests = None
                ci = gitops.ci_for_code(repo, commit, f"{state.RELAY_DIR}/")
        except (RelayError, OSError) as e:
            info["state"], ci = "unknown", "unknown"
            reasons["merge"] = f"GitHub is unknown: {e}"
    if "merge" in actions and info["state"] != "MERGED":  # a merged PR has no actions: ask GitHub nothing more
        if "merge" not in reasons:
            try:
                owneractions.merge_readiness(repo, st, seen)
            except (RelayError, OSError) as e:
                reasons["merge"] = str(e)
        if "merge" in reasons:
            actions.remove("merge")
    if info["state"] == "MERGED":
        actions = []
    if "review" in actions and info["state"] != "OPEN":
        actions.remove("review")
    job = reviewjobs.status(repo, slug)
    if job and job.get("state") == "running":  # the running review owns the feature until it ends
        actions = []
    options, review_default, review_defaults = _review_choices(repo, st)
    reviews = [_review(repo, commit, p) for p in files if "/reviews/" in p.lower() and p.endswith(".md")]
    reviews.sort(key=lambda r: (r.get("at", ""), r["path"]), reverse=True)
    local_error = None
    try:
        changed, local = _local(repo, slug, st["branch"], commit, handoff)
    except (RelayError, OSError) as e:  # a failed local scan must not hide the session's health (R4)
        changed, local, local_error = False, None, str(e)
    timeline = [{"stage": stage, "author": st.get("authors", {}).get(stage),
                 "rounds": st.get("rounds", {}).get(stage, 0), "verdict": st.get("verdicts", {}).get(stage),
                 "skipped": stage in st.get("skipped", [])} for stage in ("idea", "spec", "plan", "build", "done")]
    record, agent_text = None, None
    if archived or info["state"] == "MERGED":  # a finished feature has no session to watch
        found, act = None, None
    else:
        found, act, record = health.session_health(repo, st, sessions.read_records() if records is None else records,
                                                   time.time())
    if (found or {}).get("kind") == "attention" and (record or {}).get("state") in ("waiting", "permission"):
        words = agentask.last_words(record["provider"], record["session_id"])  # read live, never stored (D3)
        agent_text = {"source": words["source"], "text": agentask.full(words["text"])} if words else None
    stuck = (waiting.stuck_reason(repo, commit, slug, st.get("stage"))
             if st.get("status") == "waiting-owner" else None)
    return {"repo_path": repo, "feature": slug, "seen": seen, "state": st, "actions": actions,
            "action_reasons": reasons, "pr_info": info, "ci": ci, "tests": tests, "timeline": timeline, "reviews": reviews,
            "owner_actions": st.get("owner_actions", []), "owner_session": st.get("owner"),
            "handoff": {"path": handoff_path, "text": handoff, "published": True} if handoff is not None else None,
            "local_handoff": local, "local_changes": changed,
            "review_job": job, "review_choices": options, "review_default": review_default,
            "review_defaults": review_defaults,
            "health": found, "unpushed": (act or {}).get("unpushed"), "origin_at": (act or {}).get("origin"),
            "local_error": local_error, "agent_text": agent_text, "stuck_reason": stuck,
            "pending_tools": [] if (found or {}).get("stopped") else  # its approval died with it
            [p.get("tool_name") for p in (record or {}).get("pending") or [] if p.get("tool_name")],
            "session_record": record}


def _review_choices(repo, st):
    """(choices, default, per-stage defaults) of the review dialog. They depend on the time through the timed
    tables and availability, so a build computes the choices once per repository and never reuses them."""
    def make():
        repo_cfg = config.load(repo)  # the repository's effective table, the one prepare checks against
        return repo_cfg, reviewjobs.choices(repo_cfg)
    repo_cfg, options = scancache.per_build(("review", os.path.realpath(repo)), make)
    options = json.loads(json.dumps(options))  # each row gets its own copy
    review_default = dict(zip(("id", "label", "note"), reviewjobs.default(repo_cfg, st, options)))
    review_defaults = {stage: dict(zip(("id", "label", "note"), reviewjobs.default(repo_cfg, st, options, stage)))
                       for stage in reviewjobs.STAGES}
    return options, review_default, review_defaults


def _listed(data, provider, session_id):
    """(label, entry) for a session the snapshot listed as waiting, running or left off, else (None, None)."""
    for e in (data or {}).get("other_sessions") or []:
        if e["provider"] == provider and e["session_id"] == session_id:
            return e["label"], e
    for e in (data or {}).get("running") or []:
        if e["provider"] == provider and e["session_id"] == session_id:
            return e["project"], e
    for p in (data or {}).get("left_off") or []:
        for e in p["sessions"]:
            if e["provider"] == provider and e["session_id"] == session_id:
                return p["project"], e
    return None, None


def _attach_notes(others, run, left):
    """Each listed session's notes from the last 7 days (session-notify D6); [] for one whose log cannot be read."""
    for e in list(others) + list(run) + [e for p in left for e in p["sessions"]]:
        e["notes"] = ownernotes.list_for(e["provider"], e["session_id"])


def _not_running(entry):
    return entry.get("shown_state", entry["state"]) in ("ended", "stopped")


def _inbox(provider, session_id):
    """The inbox socket the session's hook recorded (session-notify D2), or None."""
    try:
        with open(sessions.record_path(provider, session_id)) as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    return record.get("inbox") if sessions._valid(record) else None


def send_note(data, provider, session_id, text):
    """session-notify D6: post or queue a note for a listed session; None when the snapshot did not list it. A
    session shown as ended or stopped is never posted to: its note waits for the session's next prompt."""
    _, entry = _listed(data, provider, session_id)
    if entry is None:
        return None
    inbox = None if _not_running(entry) else _inbox(provider, session_id)
    note, message = ownernotes.send(provider, session_id, text, inbox)
    return {"note": note, "message": message, "notes": ownernotes.list_for(provider, session_id)}


def remove_note(data, provider, session_id, note_id):
    """The session's notes after removing a queued one; None when the snapshot did not list the session."""
    _, entry = _listed(data, provider, session_id)
    if entry is None:
        return None
    return {"notes": ownernotes.remove(provider, session_id, note_id)}


def other_session(data, provider, session_id):
    """One session the snapshot listed (other_sessions or left_off) with its full last message, read live
    (other-sessions D6, D7; where-i-left-off D10), or None when the snapshot did not list it."""
    label, entry = _listed(data, provider, session_id)
    if entry is None:
        return None
    words = agentask.last_words(provider, session_id)
    return {"provider": provider, "session_id": session_id, "label": label, "state": entry["state"],
            "pending_tools": entry["pending_tools"], "text": agentask.full(words["text"]) if words else None,
            "source": words["source"] if words else None,
            "resume": leftoff.resume_line(provider, session_id, entry.get("folder")),
            "not_running": _not_running(entry), "notes": ownernotes.list_for(provider, session_id)}


def providers(cfg, now):
    """Per provider: latest weekly sample, whether it is out of usage and why, today's budget and the live 5-hour
    window (five-hour-usage R5)."""
    rows = samples.read()
    out = {}
    for provider in config.PROVIDERS:
        weekly = max((s for s in rows if s["provider"] == provider), key=lambda s: s["sampled_at"], default=None)
        blocked, why = availability.blocked(provider, cfg["limits"])
        out[provider] = {"weekly": weekly, "blocked": blocked, "reason": why,
                         "budget": samples.budget(provider, cfg, now),
                         "five_hour": availability.five_hour(provider, now)}
    return out


def usage():
    cfg, now = config.load(), time.time()
    providers_now = providers(cfg, now)
    history = ledger.read()
    totals = {}
    for days in (7, 30):
        groups = {"repos": {}, "models": {}}
        for row in history:
            try:
                if samples.timestamp(row["at"]) < now - days * 86400:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            for kind, name in (("repos", row.get("repo", "?")),
                               ("models", f"{row.get('provider', '?')}:{row.get('model', '?')}")):
                total = groups[kind].setdefault(name, {"name": name, "runs": 0, "input": 0, "cached": 0,
                                                       "output": 0, "minutes": 0})
                total["runs"] += 1
                for key in ("input", "cached", "output"):
                    total[key] += row.get(key, 0)
                total["minutes"] += row.get("duration_s", 0) / 60
        totals[str(days)] = {}
        for kind, group in groups.items():
            for total in group.values():
                total["cached_share"] = total["cached"] / total["input"] * 100 if total["input"] else 0
            totals[str(days)][kind] = sorted(group.values(), key=lambda r: r["name"])
    return {"providers": providers_now, "ledger": totals}


def _overriding(root, repos=None):
    """Projects whose own docs/relay/config.toml sets [review.prefer] (models-tab D6)."""
    out, top = [], os.path.realpath(root)
    for repo in status.checkouts(root) if repos is None else repos:
        if os.path.dirname(os.path.realpath(repo)) != top:
            continue  # worktrees repeat their repository
        path = os.path.join(state.relay_dir(repo), "config.toml")
        try:
            with open(path, "rb") as f:
                prefer = (tomllib.load(f).get("review") or {}).get("prefer")
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(prefer, dict) and prefer:
            out.append({"repo": os.path.basename(repo), "path": path, "authors": sorted(prefer)})
    return out


def reviewers(now=None, repos=None):
    """The owner's review tables with the timed file applied, for the Models tab (models-tab R9)."""
    now = time.time() if now is None else now
    revision = reviewtables.revision()  # first: a save that lands while this builds is caught as stale
    cfg, base = config.load(), config.load(timed=False)
    tz, limits = base["limits"]["timezone"], cfg["limits"]
    timed, problems = reviewtables.read(now)
    changes = reviewtables.latest()
    authors = {}
    for author in reviewtables.AUTHORS:
        t, change = timed.get(author), changes.get(author)
        authors[author] = {
            "entries": [reviewjobs.option(s, limits) for s in config.review_preferences(cfg, author)],
            "permanent": [reviewjobs.option(s, limits) for s in config.review_preferences(base, author)],
            "until": t["until"] if t else None,
            "until_label": reviewtables.when(t["until"], tz) if t else None,
            "until_iso": reviewtables.iso(t["until"], tz) if t else None,
            "changed": {"label": reviewtables.when(change["at"], tz), "by": change["by"]} if change else None}
    known = {f"{p['provider']}:{p['model']}" for a in authors.values() for p in a["entries"] + a["permanent"]}
    for provider, model in base["roles"]["reviewer_models"].items():
        known.add(f"{provider}:{config.parse_model_spec(model, provider).model}")
    for row in ledger.read():
        if row.get("provider") in config.PROVIDERS and row.get("model"):
            known.add(f"{row['provider']}:{row['model']}")
    known_list = []
    for item in sorted(known):
        option = reviewjobs.option(config.parse_model_spec(item), limits)
        known_list.append({"id": item, "provider": option["provider"], "available": option["available"],
                           "reason": option["reason"]})
    review = base.get("review") or {}
    return {"authors": authors, "writers": {r: base["roles"][r] for r in config.ROLE_KEYS},
            "effort": {k: review.get(k) for k in ("effort", "final_effort", "release_effort")},
            "known": known_list, "overriding": _overriding(status.projects_root(), repos), "problems": problems,
            "timezone": tz, "revision": revision}


def _reviewers_or_error(repos=None):
    try:
        return reviewers(repos=repos)
    except (RelayError, OSError, ValueError, KeyError, TypeError) as e:
        return {"error": str(e)}


def _writing_or_error(repos, errors):
    """Writing-session totals (writer-usage R9). A repo whose fetch failed is read from its last fetched refs."""
    try:
        writing = writerusage.summary(repos)
    except (RelayError, OSError, ValueError, KeyError, TypeError) as e:
        return {"error": str(e)}
    failed = sorted({os.path.basename(repo) for repo in repos if os.path.realpath(repo) in errors})
    writing["notes"] = writing["notes"] + [
        f"fetch failed for {name}: writing sessions use the last fetched history" for name in failed]
    return writing


def _parallel(fn, items):
    """fn over items in order, on worker threads that share the caller's read memo."""
    contexts = [contextvars.copy_context() for _ in items]  # copied here: a worker's own context has no memo
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(lambda pair: pair[0].run(fn, pair[1]), zip(contexts, items)))


def _prefetch(members):
    """Read every relay blob of the repository's origin heads in one batch (dashboard-scan R3)."""
    repo = members[0]
    try:
        heads = {gitops.head_sha(repo, ref) for ref in gitops.remote_branches(repo)}
    except (RelayError, OSError):
        return
    scancache.prefetch(repo, sorted(heads))


def _now():
    return time.time()


def _note_live(groups, rows):
    """Record which repositories have a not-done feature, for the next build's fetch schedule."""
    live = {os.path.realpath(r["checkout"]) for r in rows if r.get("checkout") and r.get("stage") != "done"}
    for common, members in groups.items():
        scancache.fetch_live(common, any(os.path.realpath(m) in live for m in members))


def _fetch(repo):
    try:
        gitops.fetch(repo)
        return None
    except (RelayError, OSError) as e:
        return str(e)


REVIEW_KEYS = ("review_choices", "review_default", "review_defaults")


BASE_REFS = ("origin/develop", "origin/main")


def _done_key(repo, row, fetched):
    """The reuse key of a done row (dashboard-scan R6), or None when it must be built: the origin refs the row is
    read from, its checkouts' own stamps, the fetch error, the config files and, for a closed or gone PR, how often
    the repository was fetched (D5).

    The refs are the commit of the origin branch the scan found the feature on and of the R6a fallbacks, plus the
    names of all origin branches, so a branch that appears or goes is seen. A fetch that moves only other branches
    keeps the row: that is most fetches in a repository with live work."""
    if not scancache.active() or row.get("stage") != "done" or row.get("scan_error") or scancache.failed(repo):
        return None
    try:
        trees = sorted(c for c in gitops.worktrees(repo) if os.path.isdir(c))
        common, own = scancache.checkouts_key(repo, trees)
        facts = scancache.repo_facts(repo)
    except (RelayError, OSError):
        return None
    if common is None or facts is None:
        return None
    refs = facts["refs"]
    ref = _scan_ref(row)
    commits = tuple((r, refs.get(r)) for r in (ref,) + BASE_REFS)
    configs = scancache.stamp([config.config_path(), os.path.join(state.relay_dir(repo), "config.toml")])
    return (facts["url"], tuple(trees), commits, tuple(sorted(refs)), own, fetched.get(row.get("checkout")), configs,
            scancache.generation(repo) if row.get("hidden") else 0, tuple(row.get("flags") or ()))


def _reuse(repo, row, key):
    """The done row and detail of an earlier build under the same key, with this build's review choices."""
    got = scancache.done_get(os.path.realpath(repo), row["feature"], key)
    if got is None:
        return None
    old_row, detail = got
    branch = detail["state"].get("branch")
    try:  # a done branch someone has checked out can gain local changes: build it every time
        if any(gitops.current_branch(c) == branch for c in gitops.worktrees(repo) if os.path.isdir(c)):
            return None
    except (RelayError, OSError):
        return None
    row.clear()
    row.update(old_row)
    options, default, defaults = _review_choices(repo, detail["state"])
    for target in (row, detail):
        target.update(review_choices=options, review_default=default, review_defaults=defaults)
    return detail


def _enrich(row, errors, fetched, records):
    repo = row.get("checkout")
    row.update(actions=[], action_reasons={}, health=None, health_inbox=False)
    if not repo or row["feature"] == "?":
        return None
    row["repo_path"] = os.path.realpath(repo)
    key = _done_key(repo, row, fetched)
    reused = _reuse(repo, row, key) if key is not None else None
    if reused is not None:
        return reused
    detail = _enrich_one(row, errors, fetched, records)
    if (key is not None and detail is not None and not row.get("error") and detail.get("health") is None
            and detail.get("session_record") is None and detail.get("agent_text") is None):
        scancache.done_put(row["repo_path"], row["feature"], key, row, detail)
    return detail


def _enrich_one(row, errors, fetched, records):
    repo = row["checkout"]
    try:
        detail = feature(repo, row["feature"], records, published_for(repo, row["feature"], _scan_ref(row)))
        st, seen = detail["state"], detail["seen"]
        published = status._remote_row(repo, seen["commit"], row["feature"], st, bool(detail["handoff"]), fetched)
        published["flags"] = [f.replace(seen["commit"], "origin/" + seen["branch"]) for f in published["flags"]]
        if gitops.current_branch(repo) == seen["branch"]:
            published["flags"] = [f for f in published["flags"] if "not checked out" not in f]
        row.update(published)
        for key in ("seen", "actions", "action_reasons", "pr_info", "ci", "tests", "review_job", "health", "review_choices",
                    "review_default", "review_defaults"):  # the card's Request review opens the same dialog as the details
            row[key] = detail[key]
        asks = waiting.asks(row, st, detail["health"], detail["session_record"], detail["stuck_reason"])
        words = detail["agent_text"]
        row.update(asks=asks, wait_since=waiting.since(asks), waiting_on_owner=bool(asks),
                   excerpt={"source": words["source"], "text": agentask.excerpt(words["text"])} if words else None,
                   health_inbox=bool(asks) and all(a["kind"] in ("answer", "approve", "check") for a in asks))
        if detail["local_changes"]:
            row["flags"].append("local changes not published")
        if detail["local_handoff"]:
            row["flags"].append("local handoff drafted, not pushed yet")
        if detail["local_error"]:
            row["flags"].append(f"could not read the local checkout: {detail['local_error']}")
        if row.get("scan_error"):
            row["flags"].append(row["scan_error"])
        if row["repo_path"] in errors:
            row["flags"].append(errors[row["repo_path"]])
            row["actions"] = detail["actions"] = []
        return detail
    except (RelayError, OSError, ValueError, KeyError) as e:
        row["flags"].append(str(e))
        row["actions"] = []
        row["error"] = str(e)
        return None


def _left_marks(details):
    """{session id: {slug, done}} from the feature details: the owner session and the record health matched
    (where-i-left-off D6, dashboard rule); done when the stage is done or the PR merged."""
    found = {}
    for d in details:
        done = d["state"].get("stage") == "done" or (d.get("pr_info") or {}).get("state") == "MERGED"
        new = {"slug": d["feature"], "done": done, "updated": str(d["state"].get("updated") or "")}
        for sid in {(d.get("owner_session") or {}).get("session"), (d.get("session_record") or {}).get("session_id")}:
            if sid and status.mark_wins(new, found.get(sid)):
                found[sid] = new
    return {sid: {"slug": m["slug"], "done": m["done"]} for sid, m in found.items()}


def build(full=False, store=None):
    """One snapshot. store is the cross-build cache (dashboard-scan D1); a fresh one when not given, as for the
    hub's one-shot build (D9)."""
    store = scancache.Store() if store is None else store
    with gitops.read_memo(), scancache.use(store), buildstats.collect() as stats:
        data = _build(root=status.projects_root(), full=full)
        store.prune(status.checkouts(status.projects_root()))
        data["build_stats"] = stats.finish()
    return data


def build_line(stats):
    """One line for a build's cost (dashboard-scan R12), the same words as the page's."""
    p = stats.get("processes") or {}
    return (f"Last build {stats.get('seconds', 0):.1f} s, {stats.get('cpu_seconds', 0):.1f} s CPU, "
            f"{sum(p.values())} processes (git {p.get('git', 0)}, gh {p.get('gh', 0)}, other {p.get('other', 0)}), "
            f"fetched {len(stats.get('fetched') or ())}, cache {(stats.get('cache') or {}).get('hits', 0)} hits")


def _slow(stats):
    """A build worth a line on stderr (R13)."""
    return stats.get("seconds", 0) > 20 or sum((stats.get("processes") or {}).values()) > 300


def _build(root, full):
    samples.record_codex_sample()
    samples.prune()
    sessions.prune()
    ownernotes.prune()  # with the session records (session-notify D5)
    try:
        records = sessions.read_records()
    except Exception:  # where-i-left-off F4: no records
        records = []
    repos, groups = status.checkouts(root), {}
    for repo in repos:  # linked worktrees share refs: concurrent fetches of one repository collide
        try:
            groups.setdefault(gitops.common_dir(repo), []).append(repo)
        except RelayError:
            groups.setdefault(repo, []).append(repo)
    fetched, now, due = {}, _now(), {}
    for common, members in groups.items():  # the fetch schedule (dashboard-scan D4): due, or the last error
        due[common] = scancache.fetch_due(common, members, now, full)
    todo = [(common, members) for common, members in groups.items() if due[common][0]]
    for (common, members), error in zip(todo, _parallel(lambda pair: _fetch(pair[1][0]), todo)):
        scancache.restamp(common)  # the fetch wrote its refs
        scancache.fetched(members[0])
        buildstats.note_fetch(os.path.basename(members[0]))
        scancache.fetch_done(common, members, now, error)
        due[common] = (True, error)
    for common, members in groups.items():
        fetched.update(dict.fromkeys(members, due[common][1]))
    _parallel(_prefetch, list(groups.values()))
    errors = {os.path.realpath(repo): error for repo, error in fetched.items() if error}
    try:
        rows, claimed = status.scan_with_claims(root, records, fetched)  # kept for rows whose details fail to load (D2)
        details = [d for d in _parallel(lambda row: _enrich(row, errors, fetched, records), rows) if d]
        waiting.one_ask_per_session(rows)
        _note_live(groups, rows)
        scan_note = None
    except Exception as e:  # where-i-left-off F5: the rest of the dashboard is still built
        rows, claimed, details, scan_note = [], set(), [], f"Features could not be read: {e}"
    writing = _writing_or_error(repos, errors)
    try:
        marks = _left_marks(details)
    except Exception:  # where-i-left-off F5: sessions without feature marks
        marks = {}
    try:
        alive = sessions.alive(records)
    except Exception:  # running-now D2: unknown liveness
        alive = None
    now = time.time()
    try:
        turns = leftoff.codex_turns(records, alive, now)  # one read per run, for both lists (codex-liveness D4)
    except Exception:  # D5: unknown, running-now D3 decides
        turns = None
    try:
        left = leftoff.projects(records, marks, root, alive=alive, now=now, turns=turns)
    except Exception:  # F4
        left = []
    try:
        run = leftoff.running(records, marks, root, alive, now, turns)
    except Exception:  # running-now F4
        run = []
    seen = {r["provider"] for r in records}
    hints = {}
    for provider in hookinstall.EVENTS:
        if provider in seen:
            hints[provider] = None
        elif hookinstall.configured(provider):
            hints[provider] = ("Hooks are configured but no events have arrived yet."
                               + (" For Codex, trust them with /hooks." if provider == "codex" else ""))
        else:
            hints[provider] = "Session health needs relay's hooks: run relay hooks install in your terminal."
    notes = set()
    for repo in {r.get("repo_path") for r in rows if r.get("repo_path")}:
        try:
            notes.update(health.ui_settings(config.load(repo))[2])
        except (RelayError, OSError):
            continue  # that repository's row already reports its broken config
    for d in details:  # the sessions the listed, not-done features account for (other-sessions D2)
        if d["state"].get("stage") != "done" and d["pr_info"].get("state") != "MERGED":
            claimed.update(x for x in ((d.get("owner_session") or {}).get("session"),
                                       (d.get("session_record") or {}).get("session_id")) if x)
    try:
        global_cfg = config.load()
        notes.update(health.ui_settings(global_cfg)[2])
        others = [] if scan_note else othersessions.listed(records, claimed, root, global_cfg, time.time())
    except Exception:  # F1, F5: the features are shown whatever happens here
        others = []
    if scan_note:
        notes.add(scan_note)
    notes = sorted(notes)
    _attach_notes(others, run, left)
    hidden = [{"repo": os.path.basename(r["checkout"]), "slug": r["feature"], "reason": r["hidden"]}
              for r in rows if r.get("hidden")]  # closed or gone PRs: why they left the inbox (merge-and-tidy D12)
    return {"rows": rows, "features": details, "other_sessions": others, "left_off": left, "running": run, "usage": usage(), "session_hints": hints, "notes": notes,
            "reviewers": _reviewers_or_error(repos), "writing": writing, "hidden": hidden}


class Cache:
    def __init__(self, builder=build, interval=60):
        self.builder, self.interval = builder, interval
        self._lock, self._wake, self._stop = threading.Lock(), threading.Event(), threading.Event()
        self._data, self._error, self._at, self._attempted = None, None, None, False
        self._full, self._thread = False, None
        self.store = scancache.Store()  # one store for every build: the cross-build reuse lives here (D1)

    def get(self):
        with self._lock:
            return {"loading": not self._attempted, "data": self._data, "error": self._error,
                    "age_seconds": time.time() - self._at if self._at is not None else None,
                    "built_at": self._at}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="relay-snapshot", daemon=True)
            self._thread.start()

    def refresh(self, full=False):
        """Build again now. full (the page's Refresh) also fetches every repository and asks GitHub again about
        closed PRs (dashboard-scan D5). One that arrives during a build is kept for the next."""
        with self._lock:
            self._full = self._full or full
            self._wake.set()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _call(self, full):
        try:
            params = inspect.signature(self.builder).parameters
        except (TypeError, ValueError):
            params = {}
        kwargs = {k: v for k, v in (("full", full), ("store", self.store)) if k in params}
        return self.builder(**kwargs)

    def _run(self):
        while not self._stop.is_set():
            with self._lock:  # cleared here, not before the wait: a refresh during the build starts the next one
                full, self._full = self._full, False
                self._wake.clear()
            with events.collect() as batch:  # merges a build discovers are announced after it is stored (D10)
                try:
                    data = self._call(full)
                    with self._lock:
                        self._data, self._error, self._at, self._attempted = data, None, time.time(), True
                    stats = data.get("build_stats") if isinstance(data, dict) else None
                    if isinstance(stats, dict) and _slow(stats):
                        print("relay: build " + build_line(stats).removeprefix("Last build "), file=sys.stderr)
                except Exception as e:
                    with self._lock:
                        self._error, self._attempted = str(e), True
                for warning in batch.flush():
                    print(f"relay: warning: {warning}", file=sys.stderr)
            self._wake.wait(self.interval)
