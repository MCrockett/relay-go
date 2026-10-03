"""Merge notices for the relay hook: tell a session, once, that a PR it owns was merged.
Local files only (state.md, merged.json) plus one git call for the origin URL."""
import glob
import json
import os

from . import gitops, merged, state

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
    return [(f"{url}#{pr}", slug, pr) for slug, pr in owned if cache.get(f"{url}#{pr}")]


def render(event_name, features):
    lines = [f"relay: PR #{pr} ({slug}) was merged. Its feature is done: switch to the base branch and pull "
             "before new work." for _, slug, pr in features]
    return json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": "\n".join(lines)}})
