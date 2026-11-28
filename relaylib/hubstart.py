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


# ---------------------------------------------------------------- releasing a retired session's features (D8)

def held_features(root, session_id, claims=None):
    """Slugs the session still holds in this repo. A release publishes a handoff and keeps the owner field, and one
    published handoff releases every feature the session holds (ownership.released_sessions), so a released
    session holds nothing."""
    from . import ownership, state
    claims = ownership.collect(root) if claims is None else claims
    if session_id in ownership.released_sessions(claims):
        return []
    return sorted({c.slug for c in claims if c.owner.get("session") == session_id
                   and c.status in state.HOLDING_STATUSES and not c.done})


def release_all(root, session_id, relayed_by):
    """Release every feature the session holds, after checking each is published (D8, R12). The released slugs."""
    from . import owneractions
    held = held_features(root, session_id)
    for slug in held:  # check them all before releasing any
        try:
            owneractions.published(root, slug)
        except RelayError:
            raise RelayError(f"{slug} is not published on origin; push its branch first")
    done = []
    for slug in held:
        try:
            owneractions.run_override(root, slug, "release", owneractions.fingerprint(root, slug),
                                      relayed_by=relayed_by)
        except RelayError as e:
            raise RelayError(f"could not release {slug}: {e}; released so far: {', '.join(done) or 'none'}")
        done.append(slug)
    return done


# ---------------------------------------------------------------- clear: retire a closed session (R11 to R14)

class _Lost(RelayError):
    pass


def _update(k, check, change):
    """Under the retired lock: change the entry when `check(entry)` holds, else raise _Lost."""
    from . import retired
    with retired.locked():
        entries = retired.load()
        e = entries.get(k)
        if not e or not check(e):
            raise _Lost("already being cleared")
        change(e)
        retired.save(entries)
        return e


def _session_record(provider, session_id):
    import json
    from . import sessions
    try:
        with open(sessions.record_path(provider, session_id)) as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    ok = sessions._valid(record) and (record["provider"], record["session_id"]) == (provider, session_id)
    return record if ok else None


def clear(item, relayed_by, now=None):
    """Retire the closed session behind a digest item and start a fresh one in its folder. {session_id, name, old,
    launch, released}; RelayError otherwise."""
    from . import events, hub, retired
    now = time.time() if now is None else now
    if item.get("kind") != "session":  # R11
        raise RelayError(f"item {item.get('n')} is not a session")
    provider, old_id = item.get("provider"), item.get("session_id")
    label = item.get("label") or old_id
    if provider != "claude":
        raise RelayError("clear starts Claude sessions only")
    record = _session_record(provider, old_id)
    cwd = record.get("cwd") if record else None
    if not cwd or not os.path.isdir(cwd):
        raise RelayError(f"{label}'s folder is gone")
    if (provider, old_id) in hub.registered():
        raise RelayError(f"{label} is a hub session")
    if events.session_live(provider, old_id) is not None:
        raise RelayError(f"close {label} in the Remote Control list first, then clear again")
    k = retired.key(provider, old_id)
    existing = retired.entry(provider, old_id)
    if existing and existing.get("state") == "retired" and not retired.is_stale(existing, now):  # R16
        raise RelayError(f"{label} was retired; replaced by {existing.get('replaced_by_name')}, which is still "
                         "starting or running")
    if shutil.which("claude") is None:
        raise RelayError("claude is not on PATH; start the session at the Mac")
    root = dispatch.main_checkout(cwd)
    gitops.fetch(root)
    new_id = str(uuid.uuid4())
    name = f"{os.path.basename(root)} {label} (fresh)"
    busy = RelayError(f"{label} is already being cleared")
    try:
        if existing and existing.get("state") == "retired":
            _retry(root, k, label, new_id, name, relayed_by, now)
        else:
            _first(root, k, provider, old_id, label, new_id, name, relayed_by, now)
    except _Lost:
        raise busy
    e = retired.entry(provider, old_id) or {}
    handover = list(e.get("handover") or [])
    outcome, reason = launch.run(launch.argv(name, new_id, launch.clear_prompt(old_id, handover)), cwd, new_id)

    def record_launch(entry):  # R13: the entry stays retired whatever the launch did
        entry["launch"] = outcome
        if reason:
            entry["launch_error"] = reason
        else:
            entry.pop("launch_error", None)
    try:
        _update(k, lambda entry: entry.get("replaced_by") == new_id, record_launch)
    except _Lost:
        pass  # a later clear took over; its own launch is recorded there
    if outcome == "failed":
        raise RelayError(reason)
    return {"session_id": new_id, "name": name, "old": old_id, "label": label, "launch": outcome,
            "released": handover}


def _first(root, k, provider, old_id, label, new_id, name, relayed_by, now):
    """Reserve (R11a), note what the fresh session must take, release it all (D8), then mark retired (R12)."""
    from . import retired
    with retired.locked():
        entries = retired.load()
        e = entries.get(k)
        if e and (e.get("state") == "retired" or not retired.is_stale(e, now)):
            raise _Lost("already being cleared")
        entries[k] = {"provider": provider, "session_id": old_id, "label": label, "state": "clearing",
                      "replaced_by": new_id, "replaced_by_name": name, "at": now, "relayed_by": relayed_by,
                      "handover": list((e or {}).get("handover") or [])}  # a stale reservation's list is kept
        retired.save(entries)
    ours = lambda entry: entry.get("replaced_by") == new_id  # noqa: E731
    held = held_features(root, old_id)
    _update(k, ours, lambda entry: entry.update(handover=sorted(set(entry["handover"]) | set(held))))
    release_all(root, old_id, relayed_by)  # a refusal leaves the entry clearing; it goes stale on its own
    _update(k, ours, lambda entry: entry.update(state="retired", retired_at=now))


def _retry(root, k, label, new_id, name, relayed_by, now):
    """R13: the replacement never showed up. Release what it took, retire it too, and point at a new one. Until
    the releases succeed, `replaced_by` keeps naming the stale replacement, so a failed retry loses nothing."""
    from . import retired
    with retired.locked():
        entries = retired.load()
        e = entries.get(k)
        retry = (e or {}).get("retry")
        fresh_retry = isinstance(retry, dict) and now - retry.get("at", 0) < retired.GRACE_S
        if not e or e.get("state") != "retired" or not retired.is_stale(e, now) or fresh_retry:
            raise _Lost("already being cleared")
        e["retry"] = {"token": new_id, "at": now}
        retired.save(entries)
        stale_id = e.get("replaced_by")
        stale_name = e.get("replaced_by_name")
    ours = lambda entry: (entry.get("retry") or {}).get("token") == new_id  # noqa: E731
    try:
        held = held_features(root, stale_id) if stale_id else []
        _update(k, ours, lambda entry: entry.update(handover=sorted(set(entry.get("handover") or []) | set(held))))
        if stale_id:
            release_all(root, stale_id, relayed_by)
    except RelayError as err:
        if not isinstance(err, _Lost):
            try:
                _update(k, ours, lambda entry: entry.pop("retry", None))
            except _Lost:
                pass
        raise
    with retired.locked():
        entries = retired.load()
        e = entries.get(k)
        if not e or not ours(e):
            raise _Lost("already being cleared")
        if held:
            entries[retired.key("claude", stale_id)] = {
                "provider": "claude", "session_id": stale_id, "label": stale_name or stale_id, "state": "retired",
                "replaced_by": new_id, "replaced_by_name": name, "at": now, "retired_at": now,
                "relayed_by": relayed_by, "handover": []}
        e.update(replaced_by=new_id, replaced_by_name=name, at=now)
        for field in ("retry", "launch", "launch_error"):
            e.pop(field, None)
        retired.save(entries)
