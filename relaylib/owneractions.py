"""Owner actions bound to published revisions, isolated from session worktrees."""
import contextlib
import fcntl
import os
import re
import tempfile
import threading
import time

from . import ciskip, cimode, config, freshness, gitops, localtests, merged, scancache, state
from .errors import RelayError

ACTION_LOCK = threading.Lock()
EFFECTS = {
    "go": "Accept this stage and advance it.",
    "extra-round": "Allow exactly one more review round.",
    "reset-rounds": "Reset the review rounds so the author can resubmit.",
    "release": "Release the session's ownership by publishing a handoff.",
    "review": "Run a fresh build review with the reviewer you choose. A NO-GO sends it back to the author.",
    "merge": "Merge the PR, delete its branch on GitHub, then tidy this machine: its worktree, local branch and "
             "base branch.",
}


class Replay(RelayError):
    pass


class NotApplicable(RelayError):
    """The action does not apply to the published state."""


class Conflict(RelayError):
    def __init__(self, message, fresh=None):
        super().__init__(message)
        self.fresh = fresh


def published(repo, slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise RelayError("invalid feature name")
    for ref in gitops.remote_branches(repo):
        files = gitops.ls_files(repo, ref, f"{state.RELAY_DIR}/{slug}")
        path = next((p for p in files if p.lower() == f"{state.RELAY_DIR}/{slug}/state.md"), None)
        if path:
            commit = gitops.head_sha(repo, ref)
            st = state.parse_at(repo, commit, path)
            if ref == f"origin/{st.get('branch')}":
                return commit, st
    raise RelayError(f"no published branch for {slug}; it may have been deleted")


def find_by_pr(repo, number):
    """The slug of the published feature whose PR is `number` (merge-and-tidy D1)."""
    for ref in gitops.remote_branches(repo):
        for path in gitops.ls_files(repo, ref, state.RELAY_DIR):
            if not path.lower().endswith("/state.md"):
                continue
            st = state.parse_at(repo, ref, path)
            if st.get("pr") == number and ref == f"origin/{st.get('branch')}":
                return st["feature"]
    raise RelayError(f"PR #{number} is not a relay feature; merge it on GitHub")


def fingerprint(repo, slug, fetch=True):
    if fetch:
        gitops.fetch(repo)
    commit, st = published(repo, slug)
    return seen_at(repo, commit, st)


def seen_at(repo, commit, st):
    """The fingerprint of a feature published at commit with state st: the PR head is read from GitHub."""
    result = {"commit": commit, "branch": st["branch"], "stage": st["stage"], "status": st["status"],
              "owner": st.get("owner") or {}, "pr": st.get("pr"), "pr_head": None}
    if st.get("pr") and scancache.active() and (st.get("status") == "done" or merged.recorded(repo, st)):
        return result  # a snapshot build asks GitHub nothing about a merged PR (dashboard-scan R6)
    if st.get("pr"):
        try:
            result["pr_head"] = gitops.pr_info(repo, st["pr"])["headRefOid"]
        except (RelayError, OSError, KeyError) as e:
            result["github_error"] = str(e)
    return result


def state_at(repo, slug, commit):
    paths = gitops.ls_files(repo, commit, f"{state.RELAY_DIR}/{slug}")
    path = next((p for p in paths if p.lower() == f"{state.RELAY_DIR}/{slug}/state.md"), None)
    if not path:
        raise RelayError("feature state is missing at the displayed commit")
    return state.parse_at(repo, commit, path)


def applicable(st):
    actions = []
    if st.get("status") in ("waiting-owner", "review-error"):
        actions.append("go")
    if st.get("status") == "waiting-owner":
        actions += ["extra-round", "reset-rounds"]
    if st.get("status") in state.HOLDING_STATUSES and (st.get("owner") or {}).get("session"):
        actions.append("release")
    order = ("idea", "spec", "plan", "build", "done")
    if (st.get("status") in state.HOLDING_STATUSES and st.get("status") != "in-review"
            and st.get("stage") in order[2:4]):  # the owner may ask for an approved earlier stage again (stage-rereview)
        for earlier in ("spec", "plan"):
            if (order.index(earlier) < order.index(st["stage"]) and (st.get("verdicts") or {}).get(earlier) == "GO"
                    and earlier not in (st.get("skipped") or [])):
                actions.append(f"review-{earlier}")
    if st.get("stage") == "build" and st.get("status") in ("ready-to-merge", "review-error") and st.get("pr"):
        actions.append("review")
    if st.get("status") == "ready-to-merge":
        actions.append("merge")
    return actions


BUSY = "busy: another owner action is running"


def _lock_path():
    return os.path.join(config.relay_home(), "owner-action.lock")


@contextlib.contextmanager
def action_lock(wait=0, busy=BUSY):
    """One owner action at a time across processes (mobile-hub D7): the thread lock covers the dashboard's own
    threads, the flock every relay process. `wait` seconds of retrying before refusing with `busy`."""
    deadline = time.monotonic() + wait
    if not (ACTION_LOCK.acquire(timeout=wait) if wait > 0 else ACTION_LOCK.acquire(blocking=False)):
        raise Conflict(busy)
    try:
        os.makedirs(config.relay_home(), exist_ok=True)
        fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Conflict(busy)
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    finally:
        ACTION_LOCK.release()


def _validate(repo, slug, action, seen):
    fresh = fingerprint(repo, slug)
    keys = ("commit", "stage", "status", "owner") + (("pr_head",) if action in ("merge", "review") else ())
    if not isinstance(seen, dict) or any(fresh[k] != seen.get(k) for k in keys):
        raise Conflict("changed since you looked; review the fresh details before trying again", fresh)
    st = state_at(repo, slug, fresh["commit"])
    if action not in applicable(st):
        raise NotApplicable(f"{action} is not available for {st['stage']} / {st['status']}")
    return fresh, st


def run_override(repo, slug, action, seen, relayed_by=None, via=None):
    if action not in ("go", "extra-round", "reset-rounds", "release"):
        raise RelayError("unknown override")
    with action_lock():
        fresh, st = _validate(repo, slug, action, seen)
        with tempfile.TemporaryDirectory(prefix="relay-owner-") as temp:
            work = os.path.join(temp, "work")
            created = False
            try:
                gitops.git(repo, "worktree", "add", "--detach", work, fresh["commit"])
                created = True
                from .commands import apply_override
                message = apply_override(work, slug, st, action, relayed_by, via=via)  # recorded, no PR comment (D11)
                state.write_state(state.state_path(work, slug), st)
                ciskip.commit(work, slug, st, config.load(work), message)  # the worktree goes if the push fails
                gitops.git(work, "push", "origin", f"HEAD:refs/heads/{fresh['branch']}")
                return {"message": f"{action} recorded for {slug}; now {st['stage']} / {st['status']}"}
            finally:
                if created:
                    gitops.git(repo, "worktree", "remove", "--force", work)


def fingerprint_full(repo, slug):
    fp = fingerprint(repo, slug)
    st = state_at(repo, slug, fp["commit"])
    pr_url = None
    if st.get("pr"):
        try:
            pr_url = f"https://github.com/{github_repo(repo)}/pull/{st['pr']}"
        except RelayError:
            pass
    return {"commit": fp["commit"], "branch": fp["branch"], "stage": st["stage"], "status": st["status"],
            "round": (st.get("rounds") or {}).get(st["stage"], 0), "pr": st.get("pr"), "pr_url": pr_url,
            "owner": fp["owner"]}


def act(repo, slug, action, expect_commit, via):
    """An owner decision collected outside relay, bound to the revision the owner saw."""
    if action not in ("go", "extra-round", "reset-rounds"):
        raise RelayError("unknown owner action")
    fresh = fingerprint(repo, slug)
    st_now = state_at(repo, slug, fresh["commit"])
    if any((a.get("via") or {}).get("event") == via["event"] and (a.get("via") or {}).get("channel") == via["channel"]
           for a in st_now.get("owner_actions", [])):
        raise Replay(f"event {via['event']} is already recorded for {slug}")
    try:
        seen_state = state_at(repo, slug, expect_commit)
    except RelayError:
        raise Conflict("the revision you decided on is not published", fresh)
    seen = {"commit": expect_commit, "stage": seen_state["stage"], "status": seen_state["status"],
            "owner": seen_state.get("owner") or {}}
    result = run_override(repo, slug, action, seen, via=via)
    if seen_state["stage"] == "build" and st_now.get("pr"):
        from .commands import via_note
        try:
            gitops.pr_comment(repo, st_now["pr"], f"relay: owner decision `{action}` on build{via_note(via)}.")
        except (RelayError, OSError) as e:  # the decision is already published; report, never fail
            result["pr_comment"] = f"failed: {e}"
    return result


def github_repo(repo):
    url = gitops.origin_url(repo)
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?/?", url)
    if not match:
        raise RelayError("origin is not a GitHub repository")
    return match[1]


def _require_local_pass(repo, mode, code):
    cimode.require_command(mode)
    rec = localtests.read(repo, code, mode.test_command)
    sha8 = code[:8]
    status = rec["status"] if rec else None
    if status == "pass":
        return
    if status == "running":
        raise RelayError(f"tests are running for {sha8} (started {localtests._hhmm(rec.get('started'))})")
    if status == "fail":
        raise RelayError(f"tests failed for {sha8}; see {rec['log']}")
    if status == "stopped":
        raise RelayError(f"tests stopped without a result for {sha8}; run `relay test`")
    raise RelayError(f"no local test result for {sha8} on this machine; run `relay test`")


def merge_readiness(repo, st, seen):
    if st.get("confirming"):  # a fresh review is still running; a fallback GO alone is a full GO
        raise RelayError("a review is still running")
    if seen.get("github_error"):
        raise RelayError(f"GitHub is unknown: {seen['github_error']}")
    info = gitops.pr_info(repo, st.get("pr"))
    if info.get("headRefOid") != seen.get("pr_head"):
        raise Conflict("PR head changed since you looked", {**seen, "pr_head": info.get("headRefOid")})
    if info.get("state") != "OPEN":
        raise RelayError("PR is not open")
    base = "origin/" + info["baseRefName"]
    fresh, why = freshness.check(repo, st, "build", base, ref=seen["commit"])
    if not fresh:
        raise RelayError(f"stale GO: {why}")
    prefix = freshness.bookkeeping_prefix(st)
    if gitops.git(repo, "diff", "--quiet", seen["commit"], seen["pr_head"], "--", ".",
                  f":(exclude,icase){prefix}", check=False).returncode:
        raise RelayError("PR code differs from the published revision")
    mode = cimode.settings(repo, info["baseRefName"])
    if mode.mode == "local":  # ci-modes R10: GitHub CI is not read; a local pass for this code and command is
        _require_local_pass(repo, mode, gitops.code_sha(repo, prefix, seen["commit"]))
        return info
    ci = gitops.ci_for_code(repo, seen["commit"], prefix)
    if ci == "not-started":
        raise RelayError("GitHub did not start CI for this code (a billing or spending-limit problem); fix it under "
                         "Settings, Billing and plans, rerun CI, then merge")
    if ci == "none" and mode.mode == "none":
        return info  # a repo without PR CI (ci = none) merges on the GO and its local gates
    if ci != "green":
        raise RelayError(f"CI is {ci}; wait for green")
    return info


RELAYED_FROM = {"hub": " from the relay hub", "session": " in its conversation with the owner", "dashboard": ""}


def merge(repo, slug, seen, relayed_by=None, source="hub", cause=None, cwd=None):
    """Merge a ready PR, then tidy this machine (merge-and-tidy D4 to D7). With relayed_by, a PR comment records
    who passed the decision on: the merge deletes the branch, so the feature's state cannot (mobile-hub D6). The
    merged event is queued for the caller to deliver after it reports (D10)."""
    from . import events, merged, tidy
    with action_lock():
        fresh, st = _validate(repo, slug, "merge", seen)
        info = merge_readiness(repo, st, fresh)
        remote = github_repo(repo)
        with tempfile.TemporaryDirectory(prefix="relay-merge-") as outside:
            result = gitops.run([os.environ.get("RELAY_GH_BIN", "gh"), "pr", "merge", str(info["number"]),
                                 "-R", remote, "--merge", "--match-head-commit", seen["pr_head"],
                                 "--delete-branch"], outside, timeout=gitops.GH_TIMEOUT_S)
            done = {"message": (result.stdout or result.stderr).strip() or f"Merged PR #{info['number']}"}
            warnings = []
            if relayed_by:
                try:
                    note = gitops.run([os.environ.get("RELAY_GH_BIN", "gh"), "pr", "comment", str(info["number"]),
                                       "-R", remote, "--body",
                                       f"Merged by the owner, relayed by {relayed_by}{RELAYED_FROM.get(source, '')}"],
                                      outside, check=False, timeout=gitops.GH_TIMEOUT_S)
                    why = ((note.stderr or "").strip() or f"exit {note.returncode}") if note.returncode else None
                except (RelayError, OSError) as e:
                    why = str(e)
                if why:
                    warnings.append(f"PR #{info['number']} is merged, but the comment recording who relayed "
                                    f"it could not be posted: {why}")
        claimed, warning = merged.mark_merged(repo, info["number"])
        if warning:
            warnings.append(warning)
        tidied = tidy.run(repo, fresh["branch"], info["baseRefName"], seen["pr_head"], cwd)
        done["tidy"] = tidied.lines
        if claimed or warning:  # one event per PR: a resolver that saw the merge first already announced it
            name = st.get("repo") or gitops.repo_name(repo)
            events.queue("released" if info["baseRefName"] == "main" else "merged", name, slug, "build",
                         info["number"], events.merged_text(name, slug, info["number"], info["baseRefName"],
                                                            tidied.left, tidied=True), cause)
        if warnings:
            done["warning"] = "; ".join(warnings)
        return done
