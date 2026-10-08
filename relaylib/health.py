"""Session health for the dashboard: local git activity plus hook records (spec session-health R4-R6, R9)."""
import datetime
import os

from . import config, gitops, state
from .errors import RelayError

UI_LIMITS = {"health_grace_minutes": (0, 60, 1), "quiet_minutes": (1, 1440, 30),
             "other_sessions_hours": (1, 168, 24)}
MAX_PATHS = 2000


def _ui(cfg, key):
    """(value, note): an invalid value falls back to its default with a note."""
    low, high, default = UI_LIMITS[key]
    value = (cfg.get("ui") or {}).get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        return default, f"ignored invalid [ui] {key}"
    return value, None


def ui_value(cfg, key):
    return _ui(cfg, key)[0]


def ui_settings(cfg):
    """(grace_min, quiet_min, notes); notes cover every [ui] setting."""
    found = {key: _ui(cfg, key) for key in UI_LIMITS}
    notes = [note for _, note in found.values() if note]
    return found["health_grace_minutes"][0], found["quiet_minutes"][0], notes


def branch_checkouts(repo, branch):
    """Real paths of the checkouts on branch; [] when they cannot be listed (activity must not hide health)."""
    try:
        trees = gitops.worktrees(repo)
    except Exception:
        return []
    out = []
    for path in trees:
        try:
            if os.path.isdir(path) and gitops.current_branch(path) == branch:
                out.append(os.path.realpath(path))
        except Exception:
            continue
    return out


def _git_int(checkout, *args):
    try:
        p = gitops.git(checkout, *args, check=False)
        return int(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None
    except (OSError, ValueError, Exception):
        return None


def _newest_file(checkout):
    try:
        p = gitops.git(checkout, "status", "--porcelain=v1", "-z", "--untracked-files=all", check=False)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    newest, parts, i, seen = None, p.stdout.split("\0"), 0, 0
    while i < len(parts) and seen < MAX_PATHS:
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":  # a rename or copy is followed by its old path, which no longer counts
            i += 1
        seen += 1
        try:
            mtime = os.lstat(os.path.join(checkout, entry[3:])).st_mtime
        except OSError:
            continue  # deleted or renamed away: no mtime
        newest = mtime if newest is None else max(newest, mtime)
    return newest


def activity(checkouts, branch):
    """{last_activity, unpushed, origin}: each input degrades to None on its own; never raises. `origin` is
    the newest commit on origin/<branch> (not the push time)."""
    times, unpushed, origin = [], None, None
    for checkout in checkouts:
        if not os.path.isdir(checkout):
            continue
        times.append(_newest_file(checkout))
        times.append(_git_int(checkout, "log", "-1", "--format=%ct", "HEAD"))
        if _git_int(checkout, "rev-list", "--count", f"origin/{branch}") is not None:
            at = _git_int(checkout, "log", "-1", "--format=%ct", f"origin/{branch}")
            times.append(at)
            if at is not None:
                origin = at if origin is None else max(origin, at)
            ahead = _git_int(checkout, "rev-list", "--count", f"origin/{branch}..HEAD")
            if ahead is not None:
                unpushed = ahead if unpushed is None else max(unpushed, ahead)
    known = [t for t in times if t is not None]
    return {"last_activity": max(known) if known else None, "unpushed": unpushed, "origin": origin}


def match(records, provider, session, since_ts, checkouts):
    """The feature's record: same provider; exact session id, else the newest record (ties: smaller id)
    written after since_ts whose cwd is a checkout on the branch or inside one."""
    mine = [r for r in records if r.get("provider") == provider]
    exact = [r for r in mine if r["session_id"] == session]
    if exact:
        return max(exact, key=lambda r: r["at"])
    roots = [os.path.realpath(c) for c in checkouts]
    def inside(cwd):
        real = os.path.realpath(cwd)  # both sides resolved at comparison (R6)
        return any(real == root or real.startswith(root + os.sep) for root in roots)
    near = [r for r in mine if r.get("cwd") and r["at"] > since_ts and inside(r["cwd"])]
    if not near:
        return None
    return sorted(near, key=lambda r: (-r["at"], r["session_id"]))[0]


def age(seconds):
    seconds = max(0, int(seconds))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def health(record, act, grace_min, quiet_min, now):
    """{kind, text, since} by the spec's first matching rule, or None with nothing to go on (R5)."""
    if record:
        held = now - record["since"]
        if record["state"] == "permission" and held > grace_min * 60:
            return {"kind": "attention", "text": f"needs permission {age(held)}", "since": record["since"]}
        if record["state"] == "waiting" and held > grace_min * 60:
            return {"kind": "attention", "text": f"waiting on you {age(held)}", "since": record["since"]}
        if record["state"] == "ended":
            return {"kind": "quiet", "text": f"session ended {age(held)} ago", "since": record["since"]}
    candidates = [t for t in (act.get("last_activity"), record and record["at"]) if t is not None]
    if not candidates:
        return None
    latest = max(candidates)
    if now - latest > quiet_min * 60:
        return {"kind": "attention", "text": f"no activity {age(now - latest)}", "since": latest}
    return {"kind": "ok", "text": f"active {age(now - latest)} ago", "since": latest}


def _since_ts(owner):
    try:
        return datetime.datetime.fromisoformat(owner.get("since") or "").timestamp()
    except (TypeError, ValueError):
        return 0.0


def session_health(repo, st, records, now):
    """(health, activity, record) for a feature a session holds, else (None, None, None) (session-health R4-R6;
    the record tells waiting-visibility which session to read)."""
    owner = st.get("owner") or {}
    if st.get("status") not in state.HOLDING_STATUSES or not owner.get("session") or st.get("stage") == "done":
        return None, None, None
    try:
        repo_cfg = config.load(repo)
    except (RelayError, OSError):  # a broken repository config must not hide the session's health
        repo_cfg = {}
    grace, quiet, _ = ui_settings(repo_cfg)
    try:
        checkouts = branch_checkouts(repo, st["branch"])
        act = activity(checkouts, st["branch"])
    except Exception:  # activity never hides what a hook record says (R4)
        checkouts, act = [], {"last_activity": None, "unpushed": None, "origin": None}
    record = match(records, owner.get("provider"), owner["session"], _since_ts(owner), checkouts)
    return health(record, act, grace, quiet, now), act, record
