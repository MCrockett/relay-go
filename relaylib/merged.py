"""Whether a feature's PR is finished, asked of gh (merge-and-tidy D11). Merged PRs are cached in merged.json (only
the value true means merged); PRs GitHub says this repo does not have are cached in prgone.json, a separate file so
older relay code, which treats any merged.json value as merged, never reads them. Closed PRs are asked again."""
import contextlib
import datetime
import fcntl
import json
import os
import threading

from . import gitops
from .config import relay_home
from .errors import RelayError

_WRITE = threading.Lock()  # snapshot workers can settle several PRs at once
NOT_FOUND = "Could not resolve to a PullRequest"


def _cache_path():
    return os.path.join(relay_home(), "merged.json")


def _gone_path():
    return os.path.join(relay_home(), "prgone.json")


def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load():
    return _read(_cache_path())


@contextlib.contextmanager
def _locked():
    """One writer at a time, across threads and processes; yields nothing, callers re-read inside."""
    with _WRITE:
        os.makedirs(relay_home(), exist_ok=True)
        with open(os.path.join(relay_home(), "merged.lock"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def _store(path, key, value):
    cache = _read(path)
    cache[key] = value
    temp = path + f".{os.getpid()}.tmp"
    with open(temp, "w") as f:
        json.dump(cache, f)
    os.replace(temp, path)


def _key(repo_dir, pr):
    return f"{gitops.origin_url(repo_dir)}#{pr}"


def mark_merged(repo_dir, pr):
    """The one claim on a merge's event: (claimed, warning). Claimed only for the call that changed the PR to
    merged in the cache, so a PR gets one event whichever path saw the merge first."""
    key = _key(repo_dir, pr)
    try:
        with _locked():
            if _load().get(key) is True:
                return False, None
            _store(_cache_path(), key, True)
            return True, None
    except OSError as e:
        return False, f"PR #{pr} is merged, but relay could not record it locally: {e}; status will find it on GitHub"


def _github_name(repo_dir):
    from .owneractions import github_repo
    try:
        return github_repo(repo_dir)
    except RelayError:
        return gitops.origin_url(repo_dir)


def _queue_merge_event(repo_dir, st, base):
    from . import events
    events.queue("released" if base == "main" else "merged", st.get("repo") or gitops.repo_name(repo_dir),
                 st.get("feature"), "build", st["pr"],
                 events.merged_text(st.get("repo") or gitops.repo_name(repo_dir), st.get("feature"), st["pr"], base))


def resolve(repo_dir, st):
    """(outcome, reason) for the feature's PR: open, merged, closed, gone or unknown (D11)."""
    pr = st.get("pr")
    if not pr:
        return "open", None
    key = _key(repo_dir, pr)
    if _load().get(key) is True:
        return "merged", None
    gone = _read(_gone_path()).get(key)
    if isinstance(gone, str):
        return "gone", gone
    try:
        info = gitops.gh_json(repo_dir, ["pr", "view", str(pr), "--json", "state,baseRefName"])
    except (RelayError, OSError) as e:
        if NOT_FOUND not in str(e):
            return "unknown", str(e)
        reason = f"PR #{pr} is not in {_github_name(repo_dir)}"
        if st.get("repo") and st["repo"] != gitops.repo_name(repo_dir):
            reason += f"; this feature was copied from {st['repo']}"
        try:
            with _locked():
                _store(_gone_path(), key, reason)
        except OSError:
            pass  # asked again next time
        return "gone", reason
    state_ = info.get("state") if isinstance(info, dict) else None
    if state_ == "MERGED":
        claimed, _ = mark_merged(repo_dir, pr)
        if claimed:
            _queue_merge_event(repo_dir, st, info.get("baseRefName"))
        return "merged", None
    if state_ == "CLOSED":
        return "closed", f"PR #{pr} was closed without merging"
    if state_ == "OPEN":
        return "open", None
    return "unknown", f"gh gave PR #{pr} the state {state_!r}"


def is_done(repo_dir, st):
    if st.get("status") == "done":
        return True
    if not st.get("pr"):
        return False
    return resolve(repo_dir, st)[0] == "merged"


def merged_at(repo_dir, st, branch_head=None):
    """When the feature's PR merged (writer-usage D9): gh mergedAt, cached; else the first merge commit on
    origin/develop or origin/main that contains branch_head; else None."""
    if st.get("pr"):
        key = f"{gitops.origin_url(repo_dir)}#{st['pr']}@at"
        cached = _load().get(key, True)
        if isinstance(cached, (int, float)) and not isinstance(cached, bool):
            return float(cached)
        try:
            # False in the cache: gh said merged but gave no time, so it is not asked again
            info = gitops.gh_json(repo_dir, ["pr", "view", str(st["pr"]), "--json", "state,mergedAt"]) if cached else {}
            if info.get("state") == "MERGED":
                at = (datetime.datetime.fromisoformat(info["mergedAt"].replace("Z", "+00:00")).timestamp()
                      if info.get("mergedAt") else False)
                with _locked():
                    _store(_cache_path(), key, at)
                if at is not False:
                    return at
        except (RelayError, OSError, ValueError, AttributeError):
            pass
    if branch_head:
        for base in ("origin/develop", "origin/main"):
            out = gitops.git(repo_dir, "log", "--merges", "--ancestry-path", "--reverse", "--format=%ct",
                             f"{branch_head}..{base}", check=False).stdout.split()
            if out:
                return float(out[0])
    return None
