"""relay command line (spec section 4)."""
import argparse
import dataclasses
import json
import os
import re
import sys

from . import (availability, config, freshness, gitops, identity, ledger, machine, merged, notify, ownership,
               prompts, reviewtables, runner, state, verdict)
from .errors import RelayError
from .progress import RoundRecord

SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
IDEA_TEMPLATE = "# {slug}\n\n## Problem\n\n## Who it is for\n\n## What success looks like\n"
STUCK_OPTIONS = """## Your options

Run these in your own terminal, or tell the agent which one you choose: it runs the same command with
`--relayed`, which records that it passed on your decision.

- `relay override go`: accept the stage as it is.
- Narrow or split the stage: edit the stage file, then `relay override reset-rounds` and let the author resubmit.
- Change the reviewer: `relay roles set review.<author> "provider:model, ..."` (add `--until 23:00` for a
  temporary change), then `relay override extra-round`.
- `relay override extra-round`: allow exactly one more round.
"""


class Ctx:
    """The repo, config, and selected feature for one command."""

    def __init__(self, args):
        self.env = dict(os.environ)
        self.root = gitops.repo_root(os.getcwd())
        self.cfg = config.load(self.root)
        self.branch = gitops.current_branch(self.root)
        self.slug = state.select_feature(self.root, self.branch, getattr(args, "feature", None),
                                         is_done=lambda st: merged.is_done(self.root, st))
        self.path = state.state_path(self.root, self.slug)
        self.st = state.read_state(self.path)
        self.dir = state.feature_dir(self.root, self.slug)

    def reload(self):
        self.branch = gitops.current_branch(self.root)
        self.st = state.read_state(self.path)

    def sync(self):
        """Pick up published owner actions before making any session write."""
        if self.branch != self.st["branch"]:
            raise RelayError(f"switch to {self.st['branch']} first (you are on {self.branch})")
        fast_forward(self.root, self.branch)
        self.reload()

    def save(self, message, push="best"):
        if self.branch != self.st["branch"]:
            raise RelayError(f"switch to {self.st['branch']} first (you are on {self.branch})")
        state.write_state(self.path, self.st)
        gitops.commit_paths_under(self.root, f"{state.RELAY_DIR}/{self.slug}", message)
        try:
            gitops.push(self.root, self.branch)
        except RelayError as e:
            if push == "required":
                raise
            print(f"relay: warning: {e}", file=sys.stderr)


def fast_forward(root, branch):
    gitops.fetch(root)
    ref = f"origin/{branch}"
    if gitops.git(root, "rev-parse", "--verify", ref, check=False).returncode:
        raise RelayError(f"{ref} is missing; restore the published branch before continuing")
    if gitops.git(root, "merge-base", "--is-ancestor", ref, "HEAD", check=False).returncode == 0:
        return  # equal, or local commits that have not been pushed yet
    if gitops.git(root, "merge-base", "--is-ancestor", "HEAD", ref, check=False).returncode:
        raise RelayError(f"{branch} and {ref} have diverged; resolve the history yourself, then retry")
    result = gitops.git(root, "merge", "-q", "--ff-only", ref, check=False)
    if result.returncode:
        raise RelayError(f"cannot fast-forward {branch}; commit or stash local changes, then retry: "
                         + result.stderr.strip())


def owner_record(me, root):
    # The checkout's folder name only: state.md is committed, and a full path would publish the owner's home folder.
    return {"provider": me.provider, "session": me.session, "worktree": os.path.basename(os.path.normpath(root)),
            "since": state.now_iso()}


def record_owner_action(st, action, relayed_by=None, once=False):
    """once: participation ("owner joined spec") counts one action per stage, however many submits."""
    if once and any(a["action"] == action for a in st["owner_actions"]):
        return
    entry = {"at": state.now_iso(), "action": action}
    if relayed_by:
        entry["relayed_by"] = relayed_by
    st["owner_actions"].append(entry)


def unique_path(path):
    """Never overwrite review evidence: a taken name gets -2, -3 before .md."""
    stem, n, candidate = path[:-3], 2, path
    while os.path.exists(candidate):
        candidate, n = f"{stem}-{n}.md", n + 1
    return candidate


def write_md(path, meta, body, unique=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    path = unique_path(path) if unique else path
    with open(path, "w") as f:
        f.write("---\n" + json.dumps(meta, indent=2, sort_keys=True) + "\n---\n\n" + body.rstrip() + "\n")
    return path


def require_pr_ready(c):
    """The build gate (spec section 2), one rule for submit and every build review: the PR is open,
    it has all of HEAD's code, and the newest completed CI result for that code is green
    (gitops.ci_for_code explains why that result can sit on a bookkeeping commit)."""
    info = gitops.pr_info(c.root, c.st.get("pr"))
    if info.get("state") != "OPEN":
        raise RelayError("open a PR for this branch first (gh pr create --base develop --fill)")
    prefix = freshness.bookkeeping_prefix(c.st)
    diff = gitops.git(c.root, "diff", "--quiet", info.get("headRefOid") or "", "HEAD", "--", ".",
                      f":(exclude,icase){prefix}", check=False)
    if diff.returncode != 0:
        raise RelayError("HEAD has code the PR does not have; push it first")
    head = info.get("headRefOid") or ""
    ci = gitops.ci_for_code(c.root, head, prefix)
    if ci == "none" and c.cfg["build"]["require_ci"]:
        raise RelayError("this PR has no CI checks; add CI, or set [build] require_ci = false in docs/relay/config.toml")
    if ci in ("pending", "failing"):
        raise RelayError(f"CI is {ci} for the code at {head[:8]}; wait for green")
    return info


def check_same_provider(env, args):
    """--same-provider is the owner's call. An agent may pass it only with --relayed, meaning the owner asked
    for it in this conversation. Sets args.same_relayed_by for the owner-action record."""
    same, relayed = getattr(args, "same_provider", False), getattr(args, "relayed", False)
    args.same_relayed_by = None
    if relayed and not same:
        raise RelayError("--relayed only goes with --same-provider here (the owner asked for a same-provider review)")
    in_agent = identity.in_agent_session(env)
    if relayed and not in_agent:
        raise RelayError("--relayed is for an agent passing on the owner's request; in your own terminal, "
                         "run it without --relayed")
    if same and in_agent:
        if not relayed:
            raise RelayError("--same-provider is the owner's call: pass it only when the owner asked for it in this "
                             "conversation, together with --relayed. Otherwise relay picks the reviewer from the "
                             "preference table, including a fallback when a provider is out of usage.")
        me = identity.detect(env, getattr(args, "by", None))
        if me.provider not in config.PROVIDERS:
            raise RelayError("--relayed records which agent passed the request on: --by must be claude or codex")
        args.same_relayed_by = f"{me.provider} session {me.session}"


def owner_or_relayed(args, what):
    """None for the owner in their own terminal; '<provider> session <id>' for an agent passing on the owner's
    words with --relayed. Anything else is refused."""
    env = dict(os.environ)
    if not args.relayed:
        identity.require_owner_terminal(env, what)
        return None
    if not identity.in_agent_session(env):
        raise RelayError("--relayed is for an agent passing on the owner's decision; in your own terminal, "
                         "run it without --relayed")
    me = identity.detect(env, args.by)
    if me.provider not in config.PROVIDERS:
        raise RelayError("--relayed records which agent passed the decision on: --by must be claude or codex")
    return f"{me.provider} session {me.session}"


def _review_author(key):
    author = key.split(".", 1)[1] if key.startswith("review.") else ""
    if author not in reviewtables.AUTHORS:
        raise RelayError(f"{key}: expected review.claude, review.codex or review.owner")
    return author


def refresh_upstream(c, me):
    """If an approved earlier stage changed, send the feature back to re-review it first. The session
    running this is the repo's writer, so it made the change: the reviewer must be the other provider."""
    stale = freshness.upstream_invalidated(c.root, c.st)
    if not stale:
        return False
    machine.mark_for_refresh(c.st, stale)
    if me.provider in config.PROVIDERS:
        c.st["authors"][stale] = me.provider
    c.save(f"relay: {stale} changed after its GO; re-review")
    print(f"relay: {stale}.md changed after its GO; re-reviewing {stale} first")
    return True


# ---------------------------------------------------------------- new

def cmd_new(args):
    env = dict(os.environ)
    root = gitops.repo_root(os.getcwd())
    me = identity.detect(env, args.by)
    slug = args.slug
    if not SLUG.fullmatch(slug):
        raise RelayError("slug must be lowercase letters, digits and dashes")
    if os.path.exists(state.state_path(root, slug)):
        raise RelayError(f"feature {slug} already exists")
    if args.small and not args.idea:
        raise RelayError('--small needs --idea "one line saying what changes"')
    idea = None
    if args.idea_file:  # read before switching branches: the notes may exist only on this one
        try:
            with open(args.idea_file) as f:
                idea = f.read()
        except OSError as e:
            raise RelayError(f"cannot read --idea-file: {e}")
    ownership.check_can_write(root, me.session)  # also fetches origin
    claims = [x for x in ownership.collect(root) if x.where.startswith("origin/")]
    branches = gitops.open_pr_branches(root)  # GitHub is the truth: a PR opened before submit counts,
    unverified = branches is None             # and a closed or merged one does not
    open_prs = [] if unverified else sorted({x.slug for x in claims if x.where[len("origin/"):] in branches})
    if unverified and not args.stack:
        raise RelayError("cannot verify open PRs (gh could not list them), so relay cannot enforce one open PR per "
                         "repo. Check GitHub; pass --stack only if the owner says to go ahead.")
    if open_prs and not args.stack:
        raise RelayError(f"{', '.join(open_prs)} has an open PR that is not merged yet. One open PR per repo "
                         "(an owner rule): finish or merge that first. Pass --stack only if the owner asked for another "
                         "feature to start before it merges.")
    base = f"origin/{args.base}"
    if gitops.git(root, "rev-parse", "--verify", "-q", base, check=False).returncode != 0:
        raise RelayError(f"no {base} to branch from; pass --base <branch>")
    if gitops.ls_files(root, base, f"{state.RELAY_DIR}/{slug}"):
        raise RelayError(f"feature {slug} already exists on {base}; pick another slug")
    branch = f"{args.type}/{slug}"
    gitops.git(root, "switch", "-q", "-c", branch, base)  # never from HEAD: unpublished local commits stay local
    d = state.feature_dir(root, slug)
    os.makedirs(d, exist_ok=True)
    if idea is None:
        idea = f"# {slug}\n\n{args.idea}\n" if args.idea else IDEA_TEMPLATE.format(slug=slug)
    with open(os.path.join(d, "idea.md"), "w") as f:
        f.write(idea)
    st = state.new_state(slug, gitops.repo_name(root), owner_record(me, root), branch, small=args.small)
    if me.provider == "owner":
        record_owner_action(st, "owner started")
    elif args.with_owner:
        record_owner_action(st, "owner joined idea", once=True)
    if open_prs or unverified:  # --stack: the owner asked for this; agents record who passed it on
        what = (f"while {', '.join(open_prs)} is open" if open_prs
                else "without a verified open-PR check")
        record_owner_action(st, f"owner approved starting {slug} {what}",
                            None if me.provider == "owner" else f"{me.provider} session {me.session}")
    state.write_state(state.state_path(root, slug), st)
    gitops.commit_paths_under(root, f"{state.RELAY_DIR}/{slug}", f"relay: new {slug}")
    gitops.push(root, branch)
    rivals = ownership.conflicts(ownership.collect(root), me.session)
    if rivals:  # another session published a claim between our check and our push
        raise RelayError("another session claimed this repo at the same time: "
                         + ", ".join(f"{x.slug} ({x.owner.get('session')})" for x in rivals)
                         + f". Stop here: {slug} is published on {branch}, and the other session will see your claim "
                           "at its next relay command. The owner decides who continues "
                           "(`relay override release` on one of the features).")
    nxt = "write the change, push, open a PR, then `relay submit`" if args.small else \
        f"fill {state.RELAY_DIR}/{slug}/idea.md, then `relay submit`"
    print(f"relay: {slug} started on {branch}; you ({me.provider}) own this repo. Next: {nxt}.")


# ---------------------------------------------------------------- adopt

def cmd_adopt(args):
    """Bring an open PR made before relay into relay at the build stage, on its own branch."""
    env = dict(os.environ)
    root = gitops.repo_root(os.getcwd())
    me = identity.detect(env, args.by)
    slug = args.slug
    if not SLUG.fullmatch(slug):
        raise RelayError("slug must be lowercase letters, digits and dashes")
    if os.path.exists(state.state_path(root, slug)):
        raise RelayError(f"feature {slug} already exists")
    branch = gitops.current_branch(root)
    try:
        info = gitops.gh_json(root, ["pr", "view", "--json", "number,state,title,body,headRefName,baseRefName"])
    except RelayError:
        info = {}
    if info.get("state") != "OPEN" or info.get("headRefName", branch) != branch:
        raise RelayError(f"no open PR for {branch}; relay adopt brings an existing open PR into relay "
                         "(start new work with relay new)")
    docs = {}
    for name, path in (("spec", args.spec), ("plan", args.plan)):
        if path:
            try:
                with open(path) as f:
                    docs[name] = f.read()
            except OSError as e:
                raise RelayError(f"cannot read --{name}: {e}")
    ownership.check_can_write(root, me.session)  # also fetches origin
    d = state.feature_dir(root, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "idea.md"), "w") as f:
        f.write(f"# {slug}\n\nAdopted from PR #{info['number']}: {info.get('title', '')}\n\n"
                f"{(info.get('body') or '').strip()}\n")
    for name, text in docs.items():
        with open(os.path.join(d, f"{name}.md"), "w") as f:
            f.write(text)
    st = state.new_state(slug, gitops.repo_name(root), owner_record(me, root), branch)
    st.update(stage="build", status="drafting", pr=info["number"], adopted=info["number"],
              skipped=[s for s in ("spec", "plan") if s not in docs])
    record_owner_action(st, f"owner asked to adopt PR #{info['number']}",
                        None if me.provider == "owner" else f"{me.provider} session {me.session}")
    state.write_state(state.state_path(root, slug), st)
    gitops.commit_paths_under(root, f"{state.RELAY_DIR}/{slug}", f"relay: adopt PR #{info['number']} as {slug}")
    gitops.push(root, branch)
    print(f"relay: adopted PR #{info['number']} as {slug} at the build stage; you ({me.provider}) own this repo. "
          "Next: wait for CI, then `relay submit` to get the whole PR reviewed.")


# ---------------------------------------------------------------- submit / review

def cmd_submit(args):
    check_same_provider(dict(os.environ), args)
    c = Ctx(args)
    c.sync()
    me = identity.detect(c.env, args.by)
    ownership.check_can_write(c.root, me.session)
    if refresh_upstream(c, me):
        return review_current(c, args)
    stage = c.st["stage"]
    if stage == "build" and c.st["status"] in ("drafting", "changes-requested"):
        c.st["pr"] = require_pr_ready(c)["number"]
    action = machine.submit(c.st, me.provider)
    if me.provider == "owner":
        record_owner_action(c.st, f"owner submitted {stage}")
    elif args.with_owner:
        record_owner_action(c.st, f"owner joined {stage}", once=True)
    c.save(f"relay: submit {stage}")
    if action == "no-review":
        print(f"relay: {stage} recorded; now at {c.st['stage']}")
        return 0
    return review_current(c, args)


def cmd_review(args):
    check_same_provider(dict(os.environ), args)
    c = Ctx(args)
    c.sync()
    me = identity.detect(c.env, args.by)
    ownership.check_can_write(c.root, me.session)
    if refresh_upstream(c, me):
        return review_current(c, args)
    st = c.st
    if st["status"] == "ready-to-merge":
        info = gitops.pr_info(c.root, st["pr"])
        fresh, why = freshness.check(c.root, st, "build", "origin/" + info["baseRefName"])
        if fresh and st.get("confirm_with"):  # a fresh fallback GO: the first choice confirms it when it can
            out, reason = availability.blocked(st["confirm_provider"], c.cfg["limits"])
            if out:
                print(f"relay: the fallback GO stands for now; {st['confirm_with']} is still out ({reason}). "
                      "Run relay review again when it is back, or the owner may merge on the fallback GO.")
                return 0
            print(f"relay: confirming the fallback GO with {st['confirm_with']} (an independent review)")
            machine.mark_for_refresh(st, "build")
            st["confirming"] = True
            return review_current(c, args)
        if fresh:
            print("relay: the build GO is still fresh; nothing to do")
            return 0
        print(f"relay: the build GO is stale ({why}); re-reviewing")
        machine.mark_for_refresh(st, "build")
        changed_by_writer = why == "code changed since the GO" or why.endswith(".md changed since the GO")
        if changed_by_writer and me.provider in config.PROVIDERS:  # not for a base-only change
            st["authors"]["build"] = me.provider
    elif st["status"] == "review-error":
        st["status"] = "in-review"
    return review_current(c, args)


def review_current(c, args, candidates=None, discard_errors=False, announce=True):
    st, stage = c.st, c.st["stage"]
    if st["status"] != "in-review":
        raise RelayError(f"{stage} is {st['status']}; run `relay submit` first")
    author = st["authors"].get(stage)
    same = bool(getattr(args, "same_provider", False))
    explicit = candidates is not None  # the owner picked the reviewer: used alone, never a fallback
    candidates = list(candidates) if explicit else config.review_preferences(c.cfg, author)
    if same and not explicit:
        if author not in config.PROVIDERS:
            raise RelayError("--same-provider needs a stage written by claude or codex")
        candidates = [x for x in candidates if x.provider == author] or [config.reviewer_model(c.cfg, author)]
        record_owner_action(st, f"owner asked for a same-provider {stage} review",
                            getattr(args, "same_relayed_by", None), once=True)
    refresh = bool(st.get("refresh"))
    record_round = st["rounds"].get(stage, 0) + (1 if refresh else 0)
    label = f"{stage}-{st['rounds'].get(stage, 0)}r" if refresh else f"{stage}-{record_round}"
    known = machine.known_ids(st, stage)
    past = machine.history(st, stage)
    required = [] if refresh or not past else list(past[-1].blocking_ids)
    # A confirmation of a fallback GO is an independent fresh review: the first choice may block on anything
    # the fallback missed, so none of the later-round rules (prior accounting, tagged new findings) apply.
    independent = bool(st.get("confirming"))  # kept until a valid verdict, so a retry after an error stays independent
    if independent:
        required, known = [], None
    head = gitops.head_sha(c.root)
    ctx = {"feature_dir": os.path.relpath(c.dir, c.root), "round": 1 if independent else record_round,
           "prior_ids": ", ".join(required) or "none", "small": "yes" if st.get("small") else "no",
           "pr": st.get("pr") or "", "base_ref": "", "base_sha": "", "head_sha": head}
    name, base_ref = stage, None
    if stage == "build":
        info = require_pr_ready(c)
        base_ref = "origin/" + info["baseRefName"]
        ctx.update(base_ref=base_ref, base_sha=gitops.head_sha(c.root, base_ref))
        if info["baseRefName"] == "main":
            name = "release"
    snap = freshness.snapshot(c.root, st, base_ref)
    prompt = prompts.render(name, ctx, root=c.root)
    timeout = float(c.cfg["limits"]["review_timeout_min"]) * 60
    # Walk the preference table: the first reviewer that is not out of usage reviews. A usage limit hit
    # mid-review is remembered until the reset and the next entry takes over in the same submit.
    ceiling = machine.ceiling(st, stage, int(c.cfg["limits"]["max_rounds"]))
    effort = config.review_effort(c.cfg, record_round >= ceiling, name == "release")
    skipped, res, v, reviewer = [], None, None, "none"
    for cand in candidates:
        if not cand.effort:  # the entry's own @effort wins; otherwise the [review] effort for this round
            cand = dataclasses.replace(cand, effort=effort)
        out, why = availability.blocked(cand.provider, c.cfg["limits"])
        if out:
            skipped.append(f"{cand.provider}:{cand.model} {why}")
            continue
        spec, reviewer = cand, cand.provider
        env = identity.child_env(c.env, reviewer)
        meta = {"reviewer": reviewer, "model": spec.model, "effort": spec.effort, "round": record_round,
                "refresh": refresh, "same_provider": same or reviewer == author, "head": head, "base_ref": base_ref,
                "base_sha": snap.get("base_sha"), "inputs": snap["inputs"], "skipped_reviewers": list(skipped),
                "confirmation": independent}
        print(f"relay: {reviewer} ({spec.model}) is reviewing {stage}, round {record_round}. This can take minutes.")
        res, v = attempt(c, stage, spec, prompt, env, timeout, record_round, known, required, label, meta)
        if v is None and res.timed_out:  # each reviewer gets one retry of its own
            st["retried"] = True
            print("relay: reviewer timed out; retrying once", file=sys.stderr)
            res, v = attempt(c, stage, spec, prompt, env, timeout, record_round, known, required, label, meta)
            if v is None and res.timed_out:
                skipped.append(f"{reviewer}:{spec.model} timed out twice")
                print(f"relay: {reviewer} timed out twice; trying the next reviewer", file=sys.stderr)
                res, v = None, None
                continue
        if v is None and availability.is_usage_limit(res.error):
            availability.record_out(reviewer)
            skipped.append(f"{reviewer}:{spec.model} hit its usage limit during the review")
            print(f"relay: {reviewer} is out of usage; trying the next reviewer in the preference table",
                  file=sys.stderr)
            res, v = None, None
            continue
        break
    if v is None:
        who = author if author in config.PROVIDERS else "owner"
        error = (res.error if res
                 else "no reviewer available: " + "; ".join(skipped) if skipped
                 else f"no reviewers configured for work by {who}: run relay roles set review.{who} \"provider:model, ...\"")
        if discard_errors:  # an owner-requested review never costs the feature its state
            raise RelayError(f"review failed: {error}")
        machine.apply_error(st, error)
        c.save(f"relay: {stage} review error ({reviewer})")
        notify.send(c.cfg, f"relay: {st['repo']} review failed", f"{c.slug} {stage}: {error[:120]}")
        print(f"relay: review failed: {error}. Nothing advanced. Re-run `relay review` or tell the owner.",
              file=sys.stderr)
        return 2
    notes = []
    if skipped:
        notes.append(f"fallback: {reviewer}:{spec.model} because " + "; ".join(skipped))
    if same or reviewer == author:
        notes.append("same provider as the author")
    st.setdefault("review_notes", {})[stage] = ", ".join(notes)
    if not notes:
        st["review_notes"].pop(stage)
    provider = reviewer  # review files are named by provider
    reviewer = (f"{reviewer}, fallback" if skipped
                else f"{reviewer}, same provider" if same or reviewer == author else reviewer)
    st.pop("confirming", None)  # a valid verdict ends the confirmation
    if v.verdict == "GO":
        machine.apply_go(st, snap)
        if stage == "build" and skipped:  # the first choice must still confirm this GO before it counts
            first = candidates[0]
            st["confirm_with"], st["confirm_provider"] = f"{first.provider}:{first.model}", first.provider
        elif stage == "build":
            st.pop("confirm_with", None)
            st.pop("confirm_provider", None)
        c.save(f"relay: {stage} GO ({reviewer})")
        if not announce:  # the caller announces once its result is published
            pass
        elif st["status"] == "ready-to-merge" and st.get("confirm_with"):
            notify.send(c.cfg, f"relay: {st['repo']} reviewed by fallback",
                        f"{c.slug}: PR #{st.get('pr')} got a GO from {provider}; {st['confirm_with']} confirms it "
                        "when available")
        elif st["status"] == "ready-to-merge":
            notify.send(c.cfg, f"relay: {st['repo']} ready to merge",
                        f"{c.slug}: PR #{st.get('pr')} passed {reviewer}'s review")
        nxt = "the owner merges the PR" if st["status"] == "ready-to-merge" else f"write {st['stage']}"
        print(f"relay: {stage} GO from {reviewer}. Next: {nxt}.")
        return 0
    if stage == "build":  # a NO-GO supersedes any fallback GO that was waiting for confirmation
        st.pop("confirm_with", None)
        st.pop("confirm_provider", None)
    record = RoundRecord(record_round, "NO-GO", [f.id for f in v.blocking], v.prior, head)
    action, reason = machine.apply_nogo(st, record, int(c.cfg["limits"]["max_rounds"]))
    if action == "stall":
        write_stuck(c, stage, reason)
        c.save(f"relay: {stage} NO-GO ({reviewer}); stopped: {reason}")
        if announce:
            notify.send(c.cfg, f"relay: {st['repo']} needs your decision", f"{c.slug} {stage} review stopped: {reason}")
        print(f"relay: {stage} NO-GO and the loop stopped ({reason}). Waiting on the owner: "
              f"{state.RELAY_DIR}/{c.slug}/reviews/{stage}-stuck.md")
        return 0
    c.save(f"relay: {stage} NO-GO ({reviewer})")
    print(f"relay: {stage} NO-GO ({len(v.blocking)} blocking; {reason}). Fix the findings in the newest "
          f"{state.RELAY_DIR}/{c.slug}/reviews/{label}.{provider}*.md, then `relay submit`.")
    return 0


def attempt(c, stage, spec, prompt, env, timeout, record_round, known, required, label, meta):
    """One reviewer run: parse and validate the verdict, keep the artifact, log the ledger line."""
    res = runner.run_review(spec, prompt, c.root, env, timeout)
    v = None
    if res.ok:
        try:
            v = verdict.parse(res.text, record_round)
            if known is not None:  # None: an independent confirmation review
                verdict.check_new_findings_tagged(v, record_round, known)
            verdict.check_prior(v, required)
        except verdict.VerdictError as e:
            res.ok, res.error, v = False, str(e), None
    meta = dict(meta, tokens=res.usage, duration_s=round(res.duration_s, 1), at=state.now_iso(),
                verdict=v.verdict if v else "error")
    reviews = os.path.join(c.dir, "reviews")
    if v is None:
        write_md(os.path.join(reviews, f"{label}.{spec.provider}.error.md"), meta,
                 f"Reason: {res.error}\n\nstderr tail:\n\n~~~\n{res.stderr_tail}\n~~~\n\nFinal message:\n\n{res.text}")
    else:
        write_md(os.path.join(reviews, f"{label}.{spec.provider}.md"), meta, res.text)
    ledger.append({"repo": c.st["repo"], "feature": c.slug, "stage": stage, "role": "review",
                   "provider": spec.provider, "model": spec.model, "effort": spec.effort,
                   "input": res.usage["input"], "cached": res.usage["cached"], "output": res.usage["output"],
                   "duration_s": round(res.duration_s, 1),
                   "result": v.verdict if v else f"error: {res.error}"})
    return res, v


def write_stuck(c, stage, reason):
    records = machine.history(c.st, stage)
    ids = []
    for r in records:
        for i in list(r.prior) + r.blocking_ids:
            if i not in ids:
                ids.append(i)
    rows = []
    for i in ids:
        marks = [f"R{r.round}: {r.prior[i]}" if i in r.prior else f"R{r.round}: raised"
                 for r in records if i in r.prior or i in r.blocking_ids]
        rows.append(f"| {i} | {', '.join(marks)} |")
    changed = "(not enough rounds to compare)"
    if len(records) >= 2 and records[-2].head and records[-1].head:
        folder = f"{state.RELAY_DIR}/{c.slug}"
        changed = gitops.git(c.root, "diff", "--stat", records[-2].head, records[-1].head, "--", ".",
                             f":(exclude,icase){folder}/reviews/", f":(exclude,icase){folder}/state.md",
                             check=False).stdout.strip() or "(no changes)"
    body = (f"# {stage} review stopped\n\nReason: {reason}\n\n## Findings by round\n\n| id | history |\n|---|---|\n"
            + "\n".join(rows) + f"\n\n## What changed between the last two rounds\n\n~~~\n{changed}\n~~~\n\n"
            + STUCK_OPTIONS)
    write_md(os.path.join(c.dir, "reviews", f"{stage}-stuck.md"), {"stage": stage, "reason": reason}, body)


# ---------------------------------------------------------------- owner decisions

def apply_override(root, slug, st, action, relayed_by=None):
    """Shared state change for the terminal command and isolated UI owner actions."""
    stage = st["stage"]
    if action == "release":
        prev = st["owner"] or {}
        write_md(os.path.join(state.feature_dir(root, slug), "handoff.md"), {"released_by": "owner", "at": state.now_iso()},
                 f"# Handoff: {slug}\n\nReleased by the owner. The previous owner was "
                 f"{prev.get('provider')} session {prev.get('session')}. The next agent may run `relay take`.",
                 unique=False)
    elif st["status"] not in ("waiting-owner", "review-error"):
        raise RelayError(f"{stage} is {st['status']}; overrides apply when it is waiting on you")
    elif action == "go":
        base_ref = None
        if stage == "build":
            gitops.fetch(root)
            base_ref = "origin/" + gitops.pr_info(root, st["pr"])["baseRefName"]
        machine.apply_go(st, freshness.snapshot(root, st, base_ref))
    elif action == "extra-round":
        st["extra_rounds"][stage] = st["extra_rounds"].get(stage, 0) + 1
        st["status"] = "changes-requested"
    elif action == "reset-rounds":
        st["rounds"][stage], st["history"][stage], st["extra_rounds"][stage] = 0, [], 0
        st.get("round_base", {}).pop(stage, None)
        st["status"] = "changes-requested"
    record_owner_action(st, f"override {action} ({stage})", relayed_by)
    note = f" (relayed by {relayed_by})" if relayed_by else ""
    return f"relay: owner override {action} on {stage}{note}"


def cmd_override(args):
    env = dict(os.environ)
    relayed_by = None
    stage = getattr(args, "stage", None)
    if stage and args.action != "review":
        raise RelayError("--stage is only for relay override review")
    if args.relayed:  # an agent passing on a decision the owner made in its conversation; recorded as such
        if not identity.in_agent_session(env):
            raise RelayError("--relayed is for an agent passing on the owner's decision; in your own terminal, "
                             "run it without --relayed")
        me = identity.detect(env, args.by)
        if me.provider not in config.PROVIDERS:
            raise RelayError("--relayed records which agent passed the decision on: --by must be claude or codex")
        relayed_by = f"{me.provider} session {me.session}"
    else:
        identity.require_owner_terminal(env, f"relay override {args.action}")
    c = Ctx(args)
    gitops.fetch(c.root)  # an owner decision nobody else can see is not a decision; refuse offline
    if args.action != "review":  # decide on the published state: a review job or the dashboard may have moved it
        c.sync()
    if args.action == "review":
        from . import owneractions, reviewjobs
        stage = stage or "build"
        job = reviewjobs.prepare(c.root, c.slug, owneractions.fingerprint(c.root, c.slug), args.reviewer, relayed_by,
                                 stage=stage)
        print(f"relay: {reviewjobs.spec_id(job.spec)} is reviewing {c.slug}"
              + (f"'s {stage}" if stage != "build" else "") + ". This can take minutes.")
        result = job.run()
        if not result["ok"]:
            raise RelayError(result["message"])
        if relayed_by and c.st.get("pr"):
            try:
                gitops.pr_comment(c.root, c.st["pr"], f"relay: owner decision `override review` on {stage}, "
                                                      f"relayed by {relayed_by}.")
            except RelayError as e:
                print(f"relay: warning: could not comment on PR #{c.st['pr']}: {e}", file=sys.stderr)
        print(f"relay: {result['message']}")
        return 0
    st, stage = c.st, c.st["stage"]
    message = apply_override(c.root, c.slug, st, args.action, relayed_by)
    note = f" (relayed by {relayed_by})" if relayed_by else ""
    c.save(message, push="required")
    if relayed_by and st.get("pr"):  # make a relayed decision visible where the owner merges
        try:
            gitops.pr_comment(c.root, st["pr"], f"relay: owner decision `override {args.action}` on {stage}, "
                                               f"relayed by {relayed_by}.")
        except RelayError as e:
            print(f"relay: warning: could not comment on PR #{st['pr']}: {e}", file=sys.stderr)
    print(f"relay: override {args.action} recorded on {stage}{note}; now {st['stage']} / {st['status']}")


# ---------------------------------------------------------------- ownership handoff

def cmd_take(args):
    c = Ctx(args)
    c.sync()
    me = identity.detect(c.env, args.by)
    claims = ownership.collect(c.root)
    blockers = ownership.conflicts(claims, me.session)
    if blockers:
        raise RelayError("another session owns work in this repo and has not pushed a handoff.md: "
                        + ", ".join(f"{x.slug} in {x.where} ({x.owner.get('session')})" for x in blockers)
                        + ". Ask it to run `relay handoff` and `relay handoff --commit`, "
                          "or the owner can run `relay override release`.")
    # Decide from origin, not the local copy: after a failed push the local state can already say "mine".
    published = next((x for x in claims if x.slug == c.slug and x.where == f"origin/{c.st['branch']}"), None)
    prev = (published.owner if published else (c.st["owner"] or {})).get("session")
    if published and published.handed_over_from and prev == me.session:
        prev = published.handed_over_from  # a re-run: this feature moved, the old session may still hold others
    siblings = [(slug, branch) for slug, branch in ownership.held_by(claims, prev)
                if slug != c.slug] if prev and prev != me.session else []
    if siblings and gitops.git(c.root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise RelayError("commit or stash your changes first: taking over also switches to "
                         + ", ".join(branch for _, branch in siblings))
    start, taken = c.branch, []

    def on_branch(branch, slug, change, message):
        gitops.git(c.root, "switch", "-q", branch)
        fast_forward(c.root, branch)
        path = state.state_path(c.root, slug)
        st = state.read_state(path)
        change(st, state.feature_dir(c.root, slug))
        state.write_state(path, st)
        gitops.commit_paths_under(c.root, f"{state.RELAY_DIR}/{slug}", message)
        gitops.push(c.root, branch)

    def set_owner(st, _dir):
        st["owner"] = owner_record(me, c.root)
        st["handed_over_from"] = prev  # a handoff still on this feature keeps releasing prev until cleared

    def drop_handoff(st, d):
        st.pop("handed_over_from", None)
        if os.path.exists(os.path.join(d, "handoff.md")):
            os.remove(os.path.join(d, "handoff.md"))

    # 1. Move ownership of every feature, touching no handoff: until all of them are moved, the published
    #    handoff (wherever it lives) still releases the previous session, so a failed take can be re-run.
    try:
        for slug, branch in siblings:
            on_branch(branch, slug, set_owner, f"relay: {me.provider} takes {slug}")
            taken.append(slug)
        if not published or published.owner.get("session") != me.session:
            on_branch(c.st["branch"], c.slug, set_owner, f"relay: {me.provider} takes {c.slug}")
        leftover = ownership.held_by(ownership.collect(c.root), prev) if prev and prev != me.session else []
        if leftover:
            raise RelayError("origin still shows " + ", ".join(slug for slug, _ in leftover) + f" owned by {prev}")
    except RelayError as e:
        gitops.git(c.root, "switch", "-q", start, check=False)
        raise RelayError(f"took {', '.join(taken) or 'nothing'} before failing: {e}. Fix that and run "
                         "`relay take` again; the handoff is still in place.")
    # 2. Every ownership change is now confirmed on origin. Only now clear the handoffs and the transfer
    #    markers on every feature this session holds; if this fails, `relay take` again finishes it.
    try:
        after = ownership.collect(c.root)
        pending = {x.slug for x in after if x.where.startswith("origin/") and x.owner.get("session") == me.session
                   and (x.has_handoff or x.handed_over_from)}
        for slug, branch in ownership.held_by(after, me.session):
            if slug in pending:
                on_branch(branch, slug, drop_handoff, f"relay: {me.provider} clears the handoff on {slug}")
    except RelayError as e:
        raise RelayError(f"ownership moved, but clearing a handoff failed: {e}. Run `relay take` again to finish.")
    finally:
        gitops.git(c.root, "switch", "-q", start, check=False)
    c.reload()
    others = f" (also: {', '.join(taken)})" if taken else ""
    print(f"relay: you ({me.provider}) now own {c.slug}{others}. Read its files and continue at {c.st['stage']}.")


def cmd_handoff(args):
    c = Ctx(args)
    c.sync()
    me = identity.detect(c.env, args.by)
    ownership.check_can_write(c.root, me.session)
    if (c.st["owner"] or {}).get("session") != me.session:
        raise RelayError("only the session that owns this feature can hand it off")
    path = os.path.join(c.dir, "handoff.md")
    if args.commit:
        if not os.path.exists(path):
            raise RelayError("run `relay handoff` first and fill the file in")
        c.save(f"relay: handoff {c.slug}", push="required")
        held = [slug for slug, _ in ownership.held_by(ownership.collect(c.root), me.session)]
        covered = sorted(set(held) | {c.slug})
        print(f"relay: handoff committed and pushed. It hands off everything this session holds here: "
              f"{', '.join(covered)}. Another session can now `relay take`.")
        notify.send(c.cfg, f"relay: {c.st['repo']} handed off",
                    f"{', '.join(covered)}: open a session and run relay take")
        return 0
    base = gitops.git(c.root, "merge-base", "HEAD", "origin/develop", check=False).stdout.strip() or "unknown"
    dirty = gitops.git(c.root, "status", "--short").stdout.strip() or "clean"
    with open(path, "w") as f:
        f.write(f"# Handoff: {c.slug}\n\n- From: {me.provider} session {me.session}\n- At: {state.now_iso()}\n"
                f"- Branch: {c.branch}\n- Base: {base}\n- Head: {gitops.head_sha(c.root)}\n"
                f"- Stage/status: {c.st['stage']} / {c.st['status']}\n\n## Done\n\n## Next\n\n## Open decisions\n\n"
                f"## Tests run (exact commands and results)\n\n## Uncommitted work and where it is\n\n"
                f"~~~\n{dirty}\n~~~\n")
    print(f"relay: wrote {path}. Fill in every section, then run `relay handoff --commit` and stop.")


# ---------------------------------------------------------------- roles / rule / cost

def cmd_roles(args):
    if args.action in ("set", "end"):
        by = owner_or_relayed(args, f"relay roles {args.action}") or "owner"
        if args.action == "end":
            if not args.key:
                raise RelayError("usage: relay roles end review.<author> | all")
            print("relay: " + reviewtables.end("all" if args.key == "all" else _review_author(args.key), by))
            return 0
        if not (args.key and args.value):
            raise RelayError("usage: relay roles set <role> <provider:model[@effort]> [--until HH:MM]")
        if args.key.startswith("review."):
            print("relay: " + reviewtables.save(_review_author(args.key), "timed" if args.until else "permanent",
                                                args.value.split(","), args.until, by))
            return 0
        if args.until:
            raise RelayError("only review tables can be temporary: review.claude, review.codex, review.owner")
        config.set_role(config.config_path(), args.key, args.value)
        print(f"relay: {args.key} = {args.value} in {config.config_path()} (applies to every repo)")
        return 0
    cfg = config.load()
    for role in ("spec", "plan", "build", "audit"):
        print(f"{role:8} {cfg['roles'][role]}   (who writes; informational until relay launches writers)")
    print("\nReviewers, first available wins (relay roles set review.<author> \"provider:model, ...\"):")
    base, (timed, problems), changes = config.load(timed=False), reviewtables.read(), reviewtables.latest()
    tz = cfg["limits"]["timezone"]

    def chain(prefs):
        return " -> ".join(f"{p.provider}:{p.model}" + (f"@{p.effort}" if p.effort else "") for p in prefs)

    for author in reviewtables.AUTHORS:
        line = f"  review.{author:6} " + chain(config.review_preferences(cfg, author))
        if author in timed:
            line += f"   temporary until {reviewtables.when(timed[author]['until'], tz)}"
            line += f"\n  {'':13} then {chain(config.review_preferences(base, author))}"
        if author in changes:
            line += (f"\n  {'':13} last changed {reviewtables.when(changes[author]['at'], tz)} "
                     f"by {changes[author]['by']}")
        print(line)
    for problem in problems:
        print(f"  note: {problem}")
    review = cfg.get("review") or {}
    print(f"  effort {review.get('effort', 'medium')}; last round before you: {review.get('final_effort', 'high')}; "
          f"release PRs: {review.get('release_effort', 'high')} (an entry's own @effort wins)")
    print("\nAvailability now:")
    for provider in config.PROVIDERS:
        out, why = availability.blocked(provider, cfg["limits"])
        print(f"  {provider}: {why if out else 'available'}")
    print()
    print(f"max_rounds {cfg['limits']['max_rounds']}, review timeout {cfg['limits']['review_timeout_min']} min, "
          f"require_ci {cfg['build']['require_ci']}")


def cmd_rule(args):
    """An owner rule: a project's (in its docs/relay/RULES.md) or, with --global, every project's."""
    env, relayed_by = dict(os.environ), None
    if args.relayed:  # an agent passing on a rule the owner gave in its conversation; recorded as such
        if not identity.in_agent_session(env):
            raise RelayError("--relayed is for an agent passing on the owner's rule; in your own terminal, "
                             "run it without --relayed")
        me = identity.detect(env, args.by)
        if me.provider not in config.PROVIDERS:
            raise RelayError("--relayed records which agent passed the rule on: --by must be claude or codex")
        relayed_by = f"{me.provider} session {me.session}"
    else:
        identity.require_owner_terminal(env, "relay rule")
    if args.globally:
        path, header = prompts.global_rules_path(), prompts.GLOBAL_HEADER
    else:
        try:
            root = gitops.repo_root(os.getcwd())
        except RelayError:
            raise RelayError("run relay rule inside a project for that project's rule, or add --global for every "
                             "project")
        path, header = prompts.project_rules_path(root), prompts.PROJECT_HEADER
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write(header)
        f.write(f"- {state.now_iso()[:10]}: {args.text}" + (f" (relayed by {relayed_by})" if relayed_by else "") + "\n")
    print(f"relay: {'global' if args.globally else 'project'} rule added to {path}"
          + ("" if args.globally else "; commit it with your next change"))


def cmd_rules(args):
    try:
        root = gitops.repo_root(os.getcwd())
    except RelayError:
        root = None
    text = prompts.rules_text(root)
    print(text or 'relay: no rules yet. The owner adds them with relay rule "..." (or --global).')


def cmd_cost(args):
    since = ledger.parse_since(args.since)
    groups = ledger.summarize(ledger.read(since))
    if not groups:
        print(f"relay: no reviewer runs in the last {args.since}")
    else:
        print(f"{'REPO':16} {'ROLE':8} {'MODEL':28} {'RUNS':>4} {'INPUT':>10} {'CACHED':>10} {'OUTPUT':>8} {'MIN':>6}")
        for (repo, role, model), g in sorted(groups.items()):
            print(f"{repo:16} {role:8} {model:28} {g['runs']:>4} {g['input']:>10} {g['cached']:>10} "
                  f"{g['output']:>8} {g['seconds'] / 60:>6.1f}")
    _print_writing(since.total_seconds() / 86400)


def _print_writing(days):
    """Writing sessions for the same period (writer-usage R11), from origin refs as last fetched (no fetch)."""
    from . import status, writerusage
    writing = writerusage.summary(status.checkouts(status.projects_root()), periods=(days,))
    rows = writing[str(days)]
    width = max([len(r["feature"]) for r in rows["feature_models"]] + [len("FEATURE")])
    print(f"\nWRITING\n{'FEATURE':{width}} {'MODEL':28} {'SESS':>4} {'TURNS':>5} {'INPUT':>10} {'CACHED':>10} "
          f"{'OUTPUT':>8} {'MIN':>6}")
    for r in rows["feature_models"]:                   # feature, stage and model in their own columns
        print(f"{r['feature']:{width}} {r['model']:28} {r['sessions']:>4} {r['turns']:>5} {r['input']:>10} "
              f"{r['cached']:>10} {r['output']:>8} {r['minutes']:>6.1f}")
    if not rows["feature_models"]:
        print("  no writing sessions in this period")
    for provider, u in sorted(rows["unattributed"].items()):
        print(f"  unattributed {provider}: {u['turns']} turns, {u['input']} input, {u['minutes']:.1f} min")
    for u in writing["unreadable"]:
        print(f"  unreadable {u['provider']} session {u['session'][:8]}: {u['reason']}")
    for note in writing["notes"]:
        print(f"  note: {note}")


# ---------------------------------------------------------------- parser

def cmd_usage_snapshot(args):
    from .usage import capture_claude
    return capture_claude(sys.stdin)


def cmd_hook(args):
    from .sessions import capture
    return capture(args.provider, sys.stdin)


def cmd_hooks(args):
    from . import hookinstall
    if args.action == "install":
        identity.require_owner_terminal(dict(os.environ), "relay hooks install")
        lines = hookinstall.install()
    else:
        lines = hookinstall.status_lines()
    print("\n".join(lines))
    return 1 if any(": refused " in line for line in lines) else 0


def cmd_ui(args):
    from .ui.server import launch
    return launch(args.port, args.background, args.serve_child, args.host)


def build_parser():
    p = argparse.ArgumentParser(prog="relay", description="idea -> spec -> plan -> build, reviewed by the other provider")
    sub = p.add_subparsers(dest="cmd")

    def add(name, func, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(func=func)
        return sp

    n = add("new", cmd_new, "start a feature: branch, folder, state; you become the repo's writer")
    n.add_argument("slug")
    n.add_argument("--small", action="store_true", help="skip idea/spec/plan; starts at build")
    n.add_argument("--type", default="feat", choices=["feat", "fix", "chore", "docs", "refactor", "perf", "test"])
    n.add_argument("--idea", help="one-line idea (required with --small)")
    n.add_argument("--idea-file", help="start idea.md from existing notes")
    n.add_argument("--by", help="claude, codex or owner, when detection is ambiguous")
    n.add_argument("--with-owner", action="store_true", help="the owner took part in shaping the idea")
    n.add_argument("--base", default="develop", help="branch on origin to start from (default develop)")
    n.add_argument("--stack", action="store_true",
                   help="start while another feature's PR is open; only when the owner asked for it")
    a = add("adopt", cmd_adopt, "bring an existing open PR (made before relay) into relay at the build stage")
    a.add_argument("slug")
    a.add_argument("--plan", help="the PR's existing plan document, copied in as plan.md")
    a.add_argument("--spec", help="the PR's existing spec document, copied in as spec.md")
    a.add_argument("--by", help="claude, codex or owner, when detection is ambiguous")
    for name, func, text in (("submit", cmd_submit, "hand in the current stage; runs the review when one is due"),
                             ("review", cmd_review, "re-run the review (after an error, or a stale GO)")):
        sp = add(name, func, text)
        sp.add_argument("--feature")
        sp.add_argument("--by")
        sp.add_argument("--same-provider", action="store_true",
                        help="the owner's call: let the author's own provider review (recorded as an owner action)")
        sp.add_argument("--relayed", action="store_true",
                        help="agents only, with --same-provider: the owner asked for it in this conversation")
        if name == "submit":
            sp.add_argument("--with-owner", action="store_true", help="the owner took part in this stage")
    o = add("override", cmd_override, "owner-only decisions, run in your own terminal")
    o.add_argument("action", choices=["go", "extra-round", "reset-rounds", "release", "review"])
    o.add_argument("--reviewer", help="review only: provider:model[@effort] from the review preference table")
    o.add_argument("--stage", choices=["spec", "plan", "build"],
                   help="review only: re-review an approved spec or plan (default: the build)")
    o.add_argument("--feature")
    o.add_argument("--relayed", action="store_true",
                   help="agents only: pass on a decision the owner made in this conversation (recorded as relayed)")
    o.add_argument("--by", help="claude or codex, when detection is ambiguous (with --relayed)")
    t = add("take", cmd_take, "take over a feature another session handed off")
    t.add_argument("--feature")
    t.add_argument("--by")
    h = add("handoff", cmd_handoff, "write handoff.md; --commit publishes it")
    h.add_argument("--feature")
    h.add_argument("--by")
    h.add_argument("--commit", action="store_true")
    ro = add("roles", cmd_roles, "show roles, or `roles set <role> <provider:model[@effort]> [--until HH:MM]`, "
                                 "or `roles end review.<author>|all` (set and end are owner only)")
    ro.add_argument("action", nargs="?", choices=["set", "end"])
    ro.add_argument("key", nargs="?")
    ro.add_argument("value", nargs="?")
    ro.add_argument("--until", help="review tables only: HH:MM or an ISO date-time with a zone, at most 7 days "
                                    "away; the normal table returns by itself after it")
    ro.add_argument("--relayed", action="store_true",
                    help="agents only: pass on a change the owner asked for in this conversation (logged as relayed)")
    ro.add_argument("--by", help="claude or codex, when detection is ambiguous (with --relayed)")
    ru = add("rule", cmd_rule, "add an owner rule for this project, or every project with --global (owner only)")
    ru.add_argument("text")
    ru.add_argument("--global", dest="globally", action="store_true", help="a rule for every project")
    ru.add_argument("--relayed", action="store_true",
                    help="agents only: pass on a rule the owner gave in this conversation (recorded as relayed)")
    ru.add_argument("--by", help="claude or codex, when detection is ambiguous (with --relayed)")
    add("rules", cmd_rules, "print the rules in effect here: the owner's global rules, then this project's")
    co = add("cost", cmd_cost, "token use of relay-launched runs")
    co.add_argument("--since", default="7d")
    us = add("usage-snapshot", cmd_usage_snapshot, "quietly capture Claude status-line usage from stdin")
    us.add_argument("provider", choices=["claude"])
    hk = add("hook", cmd_hook, "quietly record an agent hook event from stdin (installed by relay hooks install)")
    hk.add_argument("provider", choices=["claude", "codex"])
    hs = add("hooks", cmd_hooks, "install relay's session hooks (owner only), or show their status")
    hs.add_argument("action", choices=["install", "status"])
    ui = add("ui", cmd_ui, "open the local owner dashboard")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"),
                    help="listen address: 127.0.0.1 (default), or 0.0.0.0 inside a container")
    ui.add_argument("--background", action="store_true")
    ui.add_argument("--serve-child", action="store_true", help=argparse.SUPPRESS)
    try:
        from . import status
    except ImportError:
        status = None
    if status:
        s = add("status", status.cmd_status, "every feature across your projects, and what waits on you")
        s.add_argument("--json", action="store_true")
        s.add_argument("--all", action="store_true", help="include done features")
    return p


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except RelayError as e:
        print(f"relay: {e}", file=sys.stderr)
        return 1
