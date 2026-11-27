"""Dispatched work (dispatch D1): a request the hub records for another repo, kept on this machine in
~/.relay/dispatch until a session in that repo turns it into a feature. One JSON file per request. This module is
the only code that reads or writes that folder. Local only."""
import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
import unicodedata

from . import config, gitops, state
from .config import relay_home
from .errors import RelayError

ID_RE = re.compile(r"q[0-9a-z]+(-[0-9]+)?")
FINISHED = ("merged", "released", "cancelled")
KEEP_S = 30 * 86400
MAX_CHARS = 4000
CONTROL = ("Cc", "Cf", "Cs")


class DispatchError(RelayError):
    pass


def folder():
    path = os.path.join(relay_home(), "dispatch")
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


@contextlib.contextmanager
def _locked():
    with open(os.path.join(folder(), ".lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _base36(n):
    from .hub import _base36 as base36
    return base36(n)


def new_id(now_ms, taken):
    """`q` + base 36 of the creation time in milliseconds; `-2`, `-3` ... when that id is taken."""
    base = "q" + _base36(int(now_ms))
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _check_id(request_id):
    if not isinstance(request_id, str) or not ID_RE.fullmatch(request_id):
        raise DispatchError(f"{request_id!r} is not a request id")
    return request_id


def _path(request_id):
    name = _check_id(request_id) + ".json"
    return os.path.join(folder(), name)


def _load(path):
    try:
        with open(path) as f:
            record = json.load(f)
    except (OSError, ValueError) as e:
        raise DispatchError(f"could not read {path}: {e}")
    if not (isinstance(record, dict) and isinstance(record.get("id"), str) and isinstance(record.get("repo"), dict)
            and isinstance(record.get("request"), str) and isinstance(record.get("status"), str)
            and isinstance(record.get("created_at"), (int, float))):
        raise DispatchError(f"could not read {path}: not a dispatch record")
    return record


def read(request_id):
    """The record, None when there is none, DispatchError naming the file when it cannot be read."""
    path = _path(request_id)
    if not os.path.exists(path):
        return None
    return _load(path)


def all_records():
    """([records], [unreadable file paths]), oldest first; an unreadable file never blocks the others (R23)."""
    records, unreadable = [], []
    for name in sorted(os.listdir(folder())):
        if not name.endswith(".json") or name.startswith("."):
            continue
        path = os.path.join(folder(), name)
        try:
            records.append(_load(path))
        except DispatchError:
            unreadable.append(path)
    records.sort(key=lambda r: (r["created_at"], r["id"]))
    return records, unreadable


def prune(now=None):
    """Remove finished records whose last change is over 30 days old (R22). Waiting and started ones stay."""
    now = time.time() if now is None else now
    for name in os.listdir(folder()):
        if not name.endswith(".json") or name.startswith("."):
            continue
        path = os.path.join(folder(), name)
        try:
            record = _load(path)
        except DispatchError:
            continue
        updated = record.get("updated_at")
        if (record["status"] in FINISHED and isinstance(updated, (int, float)) and now - updated > KEEP_S):
            with contextlib.suppress(OSError):
                os.unlink(path)


def _write(record, now=None):
    """Temporary file in the folder (0600), renamed onto <id>.json; sets updated_at. Callers hold the lock."""
    now = time.time() if now is None else now
    record["updated_at"] = now
    path = _path(record["id"])
    fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=folder())
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(record, f, indent=1)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise
    prune(now)


def create(fields, now=None):
    """Write a new waiting record built from `fields` under the lock; the record, with its id."""
    now = time.time() if now is None else now
    try:
        with _locked():
            taken = {n[:-5] for n in os.listdir(folder()) if n.endswith(".json")}
            record = {"id": new_id(now * 1000, taken), **fields, "status": "waiting", "created_at": now}
            _write(record, now)
            return record
    except OSError as e:
        raise DispatchError(f"could not record the request in {os.path.join(relay_home(), 'dispatch')}: {e}")


def mutate(request_id, change, now=None):
    """Read the record under the lock, let `change(record)` edit it (it may raise), write it. The record."""
    try:
        with _locked():
            record = read(request_id)
            if record is None:
                raise DispatchError(f"no request {request_id} on this machine")
            change(record)
            _write(record, now)
            return record
    except OSError as e:
        raise DispatchError(f"could not update request {request_id}: {e}")


# ---------------------------------------------------------------- checking a dispatch's inputs

def main_checkout(path):
    """The real path of the checkout that owns `path`: for a linked worktree, the main one."""
    p = gitops.run(["git", "rev-parse", "--git-common-dir"], path, check=False)
    if p.returncode != 0:
        raise DispatchError(f"{path} is not a git checkout")
    common = os.path.realpath(os.path.join(path, p.stdout.strip()))
    return os.path.dirname(common) if os.path.basename(common) == ".git" else common


def target_repo(name):
    """The real path of <projects root>/<name> when it is a direct child that is a git checkout (R1)."""
    from . import status
    root = status.projects_root()
    refusal = DispatchError(f"{name} is not a repository under {root}")
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise refusal
    path = os.path.realpath(os.path.join(root, name))
    if not os.path.isdir(path):
        raise refusal
    try:
        top = gitops.repo_root(path)
    except RelayError:
        raise refusal
    if os.path.realpath(top) != path:
        raise refusal
    return path


def clean_text(text, what):
    """Control characters removed (a new line and a tab stay), then refuse empty text and text over 4,000 characters."""
    if not isinstance(text, str):
        raise DispatchError(f"{what} must be text")
    text = "".join(c for c in text.replace("\r\n", "\n") if c in "\n\t" or unicodedata.category(c) not in CONTROL)
    if not text.strip():
        raise DispatchError(f"{what} cannot be empty")
    if len(text) > MAX_CHARS:
        raise DispatchError(f"{what} can be at most {MAX_CHARS:,} characters")
    return text


def check_slug(slug):
    from .commands import SLUG
    if not SLUG.fullmatch(slug):
        raise DispatchError("slug must be lowercase letters, digits and dashes")
    return slug


def _inside(path, roots):
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def _repo_roots(repo_path):
    roots = {os.path.realpath(repo_path)}
    try:
        roots.update(os.path.realpath(w) for w in gitops.worktrees(repo_path))
    except RelayError:
        pass
    return roots


def _repo_name(cwd):
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        return os.path.basename(main_checkout(cwd))
    except RelayError:
        return None


def resolve_asker(value, hub, target_path):
    """{provider, session_id, repo} of the session that first asked for the work (R1a). `value` is
    `<provider>:<session_id>` or a repo folder name; `hub` is the dispatching hub session as a dict, or None."""
    from . import events, sessions
    hub_pair = (hub["provider"], hub["session_id"]) if hub else None
    target_roots = _repo_roots(target_path)
    provider, _, sid = value.partition(":")
    if sid and provider in config.PROVIDERS:
        try:
            with open(sessions.record_path(provider, sid)) as f:
                record = json.load(f)
        except (OSError, ValueError):
            record = None
        if not (sessions._valid(record) and (record["provider"], record["session_id"]) == (provider, sid)):
            raise DispatchError(f"unknown session {value}: it has no record on this machine")
    else:
        repo_path = target_repo(value)
        roots = _repo_roots(repo_path)
        found = [r for r in sessions.read_records()
                 if isinstance(r.get("cwd"), str) and _inside(r["cwd"], roots)
                 and events.session_live(r["provider"], r["session_id"])]
        named = ", ".join(f"{r['provider']}:{r['session_id']}" for r in found)
        if not found:
            raise DispatchError(f"no live session in {value}; name the asker as <provider>:<session_id>")
        if len(found) > 1:
            raise DispatchError(f"more than one live session in {value} ({named}); name the asker as "
                                "<provider>:<session_id>")
        record = found[0]
    pair = (record["provider"], record["session_id"])
    if pair == hub_pair:
        raise DispatchError("--for cannot name the hub itself")
    if isinstance(record.get("cwd"), str) and _inside(record["cwd"], target_roots):
        raise DispatchError(f"--for cannot name a session inside {os.path.basename(target_path)}, "
                            "the repo the work is for")
    return {"provider": pair[0], "session_id": pair[1], "repo": _repo_name(record.get("cwd"))}


# ---------------------------------------------------------------- finding a request's feature (R18 step 1)

def _request_id(st):
    request = st.get("request") if isinstance(st, dict) else None
    return request.get("id") if isinstance(request, dict) else None


def _candidates(root, request_id):
    """Every copy of a feature whose state.md carries this request, on any origin branch, any local branch and
    the working copy of every worktree. Unlike ownership.collect, a copy whose `branch` names another branch is
    kept: a feature merged into develop after its branch was deleted may be the only evidence left."""
    try:
        gitops.fetch(root)
    except RelayError as e:
        raise DispatchError(str(e))
    out = []

    def add(slug, ref, where, st):
        branch = st.get("branch")
        short = ref[len("origin/"):] if where == "origin" else ref
        owner = st.get("owner") if isinstance(st.get("owner"), dict) else {}
        out.append({"slug": slug, "ref": ref, "where": where, "own": bool(branch) and branch == short,
                    "branch": branch or short, "provider": owner.get("provider"),
                    "session_id": owner.get("session")})

    refs = [(r, "origin") for r in gitops.remote_branches(root)]
    refs += [(f"refs/heads/{b}", "local") for b in gitops.local_branches(root)]
    for ref, where in refs:
        files = gitops.ls_files(root, ref, state.RELAY_DIR)
        for path in files:
            parts = path.split("/")
            if len(parts) != 4 or parts[3].lower() != "state.md":
                continue
            try:
                st = state.parse_state(gitops.show(root, ref, path) or "", f"{ref}:{path}")
            except RelayError:
                continue
            if _request_id(st) == request_id:
                add(parts[2], ref[len("refs/heads/"):] if where == "local" else ref, where, st)
    for wt in gitops.worktrees(root):
        try:
            wt_branch = gitops.current_branch(wt)
            features = state.list_features(wt)
        except (RelayError, OSError):
            continue
        for slug, st in features:
            if _request_id(st) == request_id:
                add(slug, wt_branch, "local", st)
    best = {}
    for c in out:
        key = (c["where"] != "origin", not c["own"])
        if c["slug"] not in best or key < best[c["slug"]][0]:
            best[c["slug"]] = (key, c)
    return [c for _, c in sorted(best.values(), key=lambda kc: kc[1]["slug"])]


def features_with(root, request_id):
    """[(slug, ref, where)] for every feature that carries this request, `where` being origin or local. Fetches
    first; a fetch failure raises DispatchError with git's message."""
    return [(c["slug"], c["ref"], c["where"]) for c in _candidates(root, request_id)]


def find_features(root, request_id):
    """As features_with, but each as a dict with slug, ref, where, branch and the owner session, ready for
    mark_started."""
    found = _candidates(root, request_id)
    now = time.time()
    return [{"slug": c["slug"], "ref": c["ref"], "where": c["where"], "branch": c["branch"],
             "provider": c["provider"], "session_id": c["session_id"], "at": now} for c in found]
