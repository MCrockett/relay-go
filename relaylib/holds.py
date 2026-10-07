"""Who held which feature when (writer-usage D4, D9): windows from each feature's published state.md history."""
import contextlib
import datetime
import json
import math
import os

from . import config, gitops, merged, state
from .errors import RelayError

CACHE = "state-history.json"
KEEP = ("stage", "status", "branch", "pr", "owner")
BASES = ("origin/develop", "origin/main")


def _since(owner, fallback):
    try:
        at = datetime.datetime.fromisoformat(owner.get("since") or "")
        return at.timestamp() if at.tzinfo else fallback
    except (TypeError, ValueError, AttributeError):
        return fallback


def _load_cache():
    """{"states": {"<sha>:<path>": state reduced to KEEP}, "logs": {"<head sha>:<path>": [[sha, time, subject]]}}.
    Both are keyed by commit, so an entry is never stale; a malformed one is dropped and read again."""
    out = {"states": {}, "logs": {}}
    try:
        with open(os.path.join(config.relay_home(), CACHE)) as f:
            data = json.load(f)
        out["states"] = {k: v for k, v in data["states"].items() if isinstance(v, dict)}
        out["logs"] = {k: v for k, v in data["logs"].items()
                       if isinstance(v, list) and all(isinstance(r, list) and len(r) == 3
                                                      and all(isinstance(x, str) for x in r) for r in v)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    out["used"] = {}
    return out


def _features(repo):
    """{slug: (ref, path)} with the feature's own origin branch when it exists, else origin/develop, else
    origin/main. path is state.md spelled as that ref tracks it (a repo may use Docs/relay)."""
    found = {}
    for ref in gitops.remote_branches(repo):
        for path in gitops.ls_files(repo, ref, state.RELAY_DIR):
            parts = path.split("/")
            if len(parts) == 4 and parts[3].lower() == "state.md":
                found.setdefault(parts[2], {})[ref] = path
    out = {}
    for slug, refs in found.items():
        first = sorted(refs)[0]
        try:
            st = state.parse_state(gitops.show(repo, first, refs[first]) or "", slug)
        except RelayError:
            continue
        own = f"origin/{st.get('branch')}"
        ref = own if own in refs else next((r for r in BASES if r in refs), None)
        if ref:
            out[slug] = (ref, refs[ref])
    return out


def _history(repo, ref, path, cache):
    """[(commit time, sha, subject, state)] oldest first. A commit's history and state never change, so each is
    read from git once and kept in cache (D5)."""
    log_key = f"{gitops.head_sha(repo, ref)}:{path}"
    log = cache["logs"].get(log_key)
    if log is None:
        log = [line.split("\t", 2) for line in gitops.git(
            repo, "log", "--reverse", "--format=%H%x09%ct%x09%s", ref, "--", path).stdout.splitlines()]
    cache["used"][log_key] = log
    states = cache["states"]
    texts = gitops.cat_files(repo, [f"{sha}:{path}" for sha, _, _ in log if f"{sha}:{path}" not in states])
    for key, text in texts.items():
        try:
            st = state.parse_state(text or "", path)
        except RelayError:
            continue                                  # a commit that deleted or broke the state is skipped
        states[key] = {k: st.get(k) for k in KEEP} if isinstance(st, dict) else {}
    return [(float(at), sha, subject, states[f"{sha}:{path}"]) for sha, at, subject in log
            if f"{sha}:{path}" in states]


def _is_merged(repo, st, ref):
    """Merged per gh, or per origin alone: the branch is gone and its state is on a base branch (merging deletes
    the branch), or the branch is still there and its head is on a base branch."""
    if merged.is_done(repo, st):
        return True
    if ref in BASES:
        return True
    return any(gitops.git(repo, "merge-base", "--is-ancestor", ref, base, check=False).returncode == 0
               for base in BASES if base in gitops.remote_branches(repo))


def _feature_windows(repo, name, url, slug, ref, path, cache, handoffs, flags):
    rows = _history(repo, ref, path, cache)
    windows, cur = [], None
    for at, _sha, subject, st in rows:
        owner = st.get("owner") if isinstance(st.get("owner"), dict) else {}
        provider, session = owner.get("provider"), owner.get("session")
        if subject == f"relay: handoff {slug}" and session:
            handoffs.append((provider, session, at))
        if cur and (provider, session) != (cur["provider"], cur["session"]):
            cur["end"], cur = at, None
        if st.get("stage") == "done" or st.get("status") == "done":
            if cur:
                cur["end"], cur = at, None
            break
        if not cur and session:
            cur = {"repo": name, "url": url, "slug": slug, "branch": st.get("branch"), "provider": provider,
                   "session": session, "start": _since(owner, at), "end": None, "stages": [], "last_commit": None}
            windows.append(cur)
        if cur:
            cur["stages"].append([at, st.get("stage")])
    if not rows:
        return windows
    last_at, last_sha, _, last = rows[-1]
    for w in windows:
        w["last_commit"] = last_at
    if last.get("stage") != "done" and last.get("status") != "done" and _is_merged(repo, last, ref):
        head = gitops.head_sha(repo, ref) if ref not in BASES else last_sha
        at = merged.merged_at(repo, last, branch_head=head)
        if at is None:
            at = last_at
            flags.append(f"merge time unknown for {name} {slug}")
        for w in windows:
            w["end"] = min(w["end"] if w["end"] is not None else math.inf, at)
    return windows


def windows(checkouts):
    """Hold windows of every feature published on origin, as last fetched. Shares the caller's read memo (the
    dashboard snapshot) or uses its own."""
    with contextlib.nullcontext() if gitops.memo_active() else gitops.read_memo():
        return _windows(checkouts)


def _windows(checkouts):
    cache, out, flags, seen = _load_cache(), [], [], set()
    for repo in checkouts:
        try:
            url = gitops.origin_url(repo)
            if url in seen:
                continue
            seen.add(url)
            name, found, handoffs = gitops.repo_name(repo), [], []
            for slug, (ref, path) in sorted(_features(repo).items()):
                found += _feature_windows(repo, name, url, slug, ref, path, cache, handoffs, flags)
        except (RelayError, OSError):
            continue                                  # F5: one unreadable repo never hides the others
        for provider, session, at in handoffs:      # a handoff covers every feature the session holds here
            for w in found:
                if (w["provider"], w["session"]) == (provider, session) and w["start"] < at:
                    w["end"] = min(w["end"] if w["end"] is not None else math.inf, at)
        out += found
    try:
        saved = {"states": cache["states"], "logs": cache["used"]}   # logs of refs that moved are dropped
        config.atomic_write(os.path.join(config.relay_home(), CACHE), json.dumps(saved), 0o600)
    except OSError:
        flags.append("state history cache not saved")
    return {"windows": out, "flags": flags}
