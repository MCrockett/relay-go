"""Owner actions bound to published revisions, isolated from session worktrees."""
import contextlib
import os
import re
import tempfile
import threading

from . import ciskip, config, freshness, gitops, state
from .errors import RelayError

ACTION_LOCK = threading.Lock()
EFFECTS = {
    "go": "Accept this stage and advance it.",
    "extra-round": "Allow exactly one more review round.",
    "reset-rounds": "Reset the review rounds so the author can resubmit.",
    "release": "Release the session's ownership by publishing a handoff.",
    "review": "Run a fresh build review with the reviewer you choose. A NO-GO sends it back to the author.",
    "merge": "Merge the PR and delete its branch on GitHub.",
}


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
            st = state.parse_state(gitops.show(repo, commit, path) or "", path)
            if ref == f"origin/{st.get('branch')}":
                return commit, st
    raise RelayError(f"no published branch for {slug}; it may have been deleted")


def fingerprint(repo, slug, fetch=True):
    if fetch:
        gitops.fetch(repo)
    commit, st = published(repo, slug)
    result = {"commit": commit, "branch": st["branch"], "stage": st["stage"], "status": st["status"],
              "owner": st.get("owner") or {}, "pr": st.get("pr"), "pr_head": None}
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
    return state.parse_state(gitops.show(repo, commit, path) or "", path)


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


@contextlib.contextmanager
def action_lock():
    if not ACTION_LOCK.acquire(blocking=False):
        raise Conflict("busy: another owner action is running")
    try:
        yield
    finally:
        ACTION_LOCK.release()


def _validate(repo, slug, action, seen):
    fresh = fingerprint(repo, slug)
    keys = ("commit", "stage", "status", "owner") + (("pr_head",) if action in ("merge", "review") else ())
    if not isinstance(seen, dict) or any(fresh[k] != seen.get(k) for k in keys):
        raise Conflict("changed since you looked; review the fresh details before trying again", fresh)
    st = state_at(repo, slug, fresh["commit"])
    if action not in applicable(st):
        raise RelayError(f"{action} is not available for {st['stage']} / {st['status']}")
    return fresh, st


def run_override(repo, slug, action, seen):
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
                message = apply_override(work, slug, st, action)
                state.write_state(state.state_path(work, slug), st)
                ciskip.commit(work, slug, st, config.load(work), message)  # the worktree goes if the push fails
                gitops.git(work, "push", "origin", f"HEAD:refs/heads/{fresh['branch']}")
                return {"message": f"{action} recorded for {slug}; now {st['stage']} / {st['status']}"}
            finally:
                if created:
                    gitops.git(repo, "worktree", "remove", "--force", work)


def github_repo(repo):
    url = gitops.origin_url(repo)
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?/?", url)
    if not match:
        raise RelayError("origin is not a GitHub repository")
    return match[1]


def merge_readiness(repo, st, seen):
    if st.get("confirm_with") or st.get("confirming"):
        raise RelayError("fallback confirmation is still pending")
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
    ci = gitops.ci_for_code(repo, seen["commit"], prefix)
    if ci == "not-started":
        raise RelayError("GitHub did not start CI for this code (a billing or spending-limit problem); fix it under "
                         "Settings, Billing and plans, rerun CI, then merge")
    if ci != "green":
        raise RelayError(f"CI is {ci}; wait for green")
    return info


def merge(repo, slug, seen):
    with action_lock():
        fresh, st = _validate(repo, slug, "merge", seen)
        info = merge_readiness(repo, st, fresh)
        remote = github_repo(repo)
        with tempfile.TemporaryDirectory(prefix="relay-merge-") as outside:
            result = gitops.run([os.environ.get("RELAY_GH_BIN", "gh"), "pr", "merge", str(info["number"]),
                                 "-R", remote, "--merge", "--match-head-commit", seen["pr_head"],
                                 "--delete-branch"], outside, timeout=gitops.GH_TIMEOUT_S)
        return {"message": (result.stdout or result.stderr).strip() or f"Merged PR #{info['number']}"}
