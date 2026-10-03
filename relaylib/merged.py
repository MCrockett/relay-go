"""A ready-to-merge feature whose PR is merged is done. Asked of gh; cached once true."""
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
