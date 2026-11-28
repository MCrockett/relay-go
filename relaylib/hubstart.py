"""The hub starts a Claude session for a waiting request, or replaces a closed one (hub-start-session).

`start` turns a waiting dispatch request into a background Claude session in a new worktree (R1 to R10). `clear`
retires a closed session and starts a fresh one in its folder (R11 to R14). Both run only on the owner's words,
which the caller checks; nothing here stops, signals or types into a session."""
import os
import shutil
import time
import uuid

from . import basebranch, dispatch, gitops, launch
from .errors import RelayError


def _when(at):
    return time.strftime("%H:%M", time.localtime(at))


def _ours(record, sid):
    started = dispatch.launch_record(record)
    return started is not None and started.get("session_id") == sid


def _set_launch(request_id, sid, value):
    def change(record):
        if _ours(record, sid):
            record["started"]["launch"] = value
    dispatch.mutate(request_id, change)


def _clear_started(request_id, sid):
    def change(record):
        if _ours(record, sid):
            record.pop("started", None)
    dispatch.mutate(request_id, change)


def start(request_id, slug, relayed_by, now=None):
    """Start a session for a waiting request. {session_id, worktree, name, launch, reused}; RelayError otherwise."""
    now = time.time() if now is None else now
    record = dispatch.read(request_id)  # R1
    if record is None:
        raise RelayError(f"no request {request_id} on this machine")
    if record["status"] != "waiting":
        raise dispatch._not_waiting(record)
    old = dispatch.launch_record(record)
    if old and not dispatch.launch_stale(old, now):  # checked again under the lock below (R2)
        raise RelayError(f"request {request_id} already has a session starting: {old.get('name')} "
                         f"({old.get('session_id')}); try again after {_when(old.get('at', now) + 600)}")
    if old and old.get("worktree") and os.path.isdir(old["worktree"]):
        if slug and slug != old.get("slug"):  # a re-start works on the recorded worktree and slug (R4a)
            raise RelayError(f"request {request_id} was started as {old.get('slug')} in {old['worktree']}; run it "
                             "again with that slug")
        slug = old.get("slug")
    slug = slug or record.get("slug")
    if not slug:
        raise RelayError(f"request {request_id} has no slug; run relay dispatch --start {request_id} --slug <slug> "
                         "--relayed")
    dispatch.check_slug(slug)
    if shutil.which("claude") is None:  # R3
        raise RelayError("claude is not on PATH; start the session at the Mac")
    root = record["repo"]["path"]
    gitops.fetch(root)  # R4
    base = basebranch.choose(root)[0]
    main = dispatch.main_checkout(root)
    reused = bool(old and old.get("worktree") and os.path.isdir(old["worktree"]))
    if reused:  # R4a
        path = old["worktree"]
        if os.path.exists(os.path.join(path, "docs", "relay", slug, "state.md")):
            raise RelayError(f"{slug} was already started in {path} by session {old.get('session_id')}; its work is "
                             "kept there. Finish it at the Mac, or push its branch and run relay override release")
    else:
        path = launch.worktree_path(main, slug)
        branch = f"feat/{slug}"
        if os.path.exists(path):
            raise RelayError(f"{path} already exists")
        if branch in gitops.local_branches(main) or gitops.remote_ref_exists(main, f"origin/{branch}"):
            raise RelayError(f"the branch {branch} already exists")
    sid = str(uuid.uuid4())
    name = f"{record['repo']['name']} {slug}"

    def reserve(rec):  # R2 and R7 in one locked mutate
        if rec["status"] != "waiting":
            raise dispatch._not_waiting(rec)
        claim = rec.get("claim")
        if dispatch.claim_blocks(claim, now):
            raise RelayError(f"request {request_id} is being started by {claim.get('provider')} session "
                             f"{claim.get('session_id')}")
        current = dispatch.launch_record(rec)
        if current and not dispatch.launch_stale(current, now):
            raise RelayError(f"request {request_id} already has a session starting: {current.get('name')} "
                             f"({current.get('session_id')}); try again after "
                             f"{_when(current.get('at', now) + 600)}")
        if current != old:
            raise RelayError(f"request {request_id} changed; run it again")
        rec["started"] = {"provider": "claude", "session_id": sid, "slug": slug, "worktree": path, "name": name,
                          "at": now, "relayed_by": relayed_by, "launch": "pending"}
    dispatch.mutate(request_id, reserve)
    commit = None
    if not reused:
        try:
            commit = launch.make_worktree(main, path, base)
        except RelayError:
            _clear_started(request_id, sid)
            raise
    outcome, reason = launch.run(launch.argv(name, sid, launch.start_prompt(request_id, slug)), path, sid)
    if outcome == "failed":  # R9
        if not reused:
            launch.remove_if_pristine(main, path, commit)
        _clear_started(request_id, sid)
        raise RelayError(reason)
    _set_launch(request_id, sid, outcome)
    return {"session_id": sid, "worktree": path, "name": name, "launch": outcome, "reused": reused}
