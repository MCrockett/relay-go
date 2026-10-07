"""A ready-to-merge feature whose PR is merged is done. Asked of gh; cached once true."""
import datetime
import json
import os
import threading

from . import gitops
from .config import relay_home
from .errors import RelayError

_WRITE = threading.Lock()  # snapshot workers can settle several PRs at once


def _cache_path():
    return os.path.join(relay_home(), "merged.json")


def _load():
    try:
        with open(_cache_path()) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def is_done(repo_dir, st):
    if st.get("status") == "done":
        return True
    if st.get("status") != "ready-to-merge" or not st.get("pr"):
        return False
    key = f"{gitops.origin_url(repo_dir)}#{st['pr']}"
    cache = _load()
    if cache.get(key):
        return True
    try:
        info = gitops.gh_json(repo_dir, ["pr", "view", str(st["pr"]), "--json", "state"])
    except (RelayError, OSError):
        return False
    if info.get("state") != "MERGED":
        return False
    with _WRITE:
        cache = _load()
        cache[key] = True
        os.makedirs(relay_home(), exist_ok=True)
        with open(_cache_path(), "w") as f:
            json.dump(cache, f)
    return True


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
                with _WRITE:
                    cache = _load()
                    cache[key] = at
                    os.makedirs(relay_home(), exist_ok=True)
                    with open(_cache_path(), "w") as f:
                        json.dump(cache, f)
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
