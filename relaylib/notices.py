"""Merge notices for the relay hook: tell a session, once, that a PR it owns was merged.
Local files only (state.md, merged.json) plus one git call for the origin URL."""
import glob
import json
import os

from . import gitops, merged, state
from .config import relay_home

MAX_DEPTH = 40  # how far up from the session's folder to look for the project


def project_root(cwd):
    """The nearest folder at or above cwd that holds docs/relay (any case), or None."""
    path = os.path.realpath(cwd)
    for _ in range(MAX_DEPTH):
        if os.path.isdir(state.relay_dir(path)):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return None


def merged_features(session_id, cwd):
    """[(key, slug, pr)] for the ready-to-merge features this session owns here whose PR is cached as merged."""
    root = project_root(cwd) if cwd else None
    if root is None:
        return []
    cache = merged._load()
    if not cache:
        return []
    owned = []
    for path in sorted(glob.glob(os.path.join(state.relay_dir(root), "*", "state.md"))):
        slug = os.path.basename(os.path.dirname(path))
        try:
            st = state.read_state(path)
        except Exception:
            continue  # one unreadable feature must not hide the others
        owner = st.get("owner") if isinstance(st.get("owner"), dict) else {}
        if owner.get("session") == session_id and st.get("status") == "ready-to-merge" and st.get("pr"):
            owned.append((slug, st["pr"]))
    if not owned:
        return []
    url = gitops.origin_url(root)
    return [(f"{url}#{pr}", slug, pr) for slug, pr in owned if cache.get(f"{url}#{pr}") is True]


def waiting_requests(session_id, cwd):
    """[(key, line)] for each dispatch request waiting for this session's repo (dispatch R12), key
    `dispatch:<id>`. Any failure gives [] so the hook is never disturbed."""
    try:
        if not cwd or not os.path.isdir(os.path.join(relay_home(), "dispatch")):
            return []
        from . import dispatch
        records, _ = dispatch.all_records()
        waiting = [r for r in records if r["status"] == "waiting"]
        if not waiting:
            return []
        here = dispatch.main_checkout(cwd)
        out = []
        for r in waiting:
            if r["repo"].get("path") == here:
                first = (r["request"].strip().splitlines() or [""])[0]
                out.append((f"dispatch:{r['id']}",
                            f"relay: request {r['id']} from the hub is waiting in {r['repo']['name']}: {first}. "
                            f"Read it with relay dispatch --show {r['id']}, then start it with relay take "
                            f"--request {r['id']} or relay new <slug> --request {r['id']}."))
        return out
    except Exception:
        return []


def render(event_name, features, extra=()):
    """One hookSpecificOutput with the merge and request notices, then any other lines (owner notes, session-notify
    D5). A merge notice is (key, slug, pr); a request notice is (key, line)."""
    lines = [f[1] if len(f) == 2 else
             f"relay: PR #{f[2]} ({f[1]}) was merged. Its feature is done: switch to the base branch and pull "
             "before new work." for f in features] + list(extra)
    return json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": "\n".join(lines)}})
