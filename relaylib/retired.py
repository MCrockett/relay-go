"""Sessions the owner retired through `relay hub clear` (hub-start-session R11a, R15 to R19).

One file, `<relay home>/sessions/retired.json`, maps `<provider>:<session_id>` to an entry:
{provider, session_id, label, state: "clearing"|"retired", replaced_by, replaced_by_name, at, relayed_by}, plus
`handover`, `retired_at`, `launch`, `launch_error` and `retry` as the clear goes on. Only `state == "retired"`
changes what a session is told; a `clearing` entry is a reservation and changes nothing."""
import contextlib
import fcntl
import json
import os

from .config import atomic_write, relay_home

GRACE_S = 600  # a started session that has not shown up in 10 minutes is treated as not started (D9)
KEEP_S = 7 * 86400  # matches the session record retention in sessions.prune (R19)


def path():
    return os.path.join(relay_home(), "sessions", "retired.json")


@contextlib.contextmanager
def locked():
    folder = os.path.dirname(path())
    os.makedirs(folder, mode=0o700, exist_ok=True)
    with open(os.path.join(folder, "retired.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def key(provider, session_id):
    return f"{provider}:{session_id}"


def load():
    """Every entry; a missing or unreadable file is empty. Hold locked() when the result will be saved."""
    try:
        with open(path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save(entries):
    atomic_write(path(), json.dumps(entries, indent=1, sort_keys=True) + "\n")


def entry(provider, session_id):
    return load().get(key(provider, session_id))


def _live(provider, session_id):
    from . import events
    return bool(session_id) and events.session_live(provider, session_id) is not None


def is_stale(e, now, entries=None):
    """The replacement never showed up: no live record for it, and 10 minutes or more since `at` (D9). A
    replacement that was itself cleared counts only if its own replacement, and so on down the chain, is stale
    too: after A into B into C, C running keeps A's entry from going stale."""
    entries = load() if entries is None else entries
    seen = set()
    while True:
        at = e.get("at")
        if isinstance(at, (int, float)) and now - at < GRACE_S:
            return False
        if _live(e.get("provider"), e.get("replaced_by")):
            return False
        nxt = entries.get(key(e.get("provider"), e.get("replaced_by")))
        if not isinstance(nxt, dict) or nxt.get("session_id") in seen:
            return True
        seen.add(e.get("session_id"))
        e = nxt


def is_retired(provider, session_id):
    try:
        e = entry(provider, session_id)
        return isinstance(e, dict) and e.get("state") == "retired"
    except Exception:
        return False


def prune(now):
    """Drop retired entries 7 days old whose old session is not live, and stale clearing entries 7 days old (R19)."""
    try:
        with locked():
            entries = load()
            keep = {}
            for k, e in entries.items():
                if e.get("state") == "retired":
                    at = e.get("retired_at", e.get("at"))
                    old = isinstance(at, (int, float)) and now - at >= KEEP_S
                    drop = old and not _live(e.get("provider"), e.get("session_id"))
                else:
                    at = e.get("at")
                    drop = isinstance(at, (int, float)) and now - at >= KEEP_S and is_stale(e, now)
                if not drop:
                    keep[k] = e
            if len(keep) != len(entries):
                save(keep)
            return len(entries) - len(keep)
    except OSError:
        return 0
