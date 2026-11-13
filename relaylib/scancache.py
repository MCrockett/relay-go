"""The snapshot builder's cross-build store (dashboard-scan D1, D1a). Everything kept here is keyed by a git object
id or by file stamps, so a reused answer is one that cannot have changed. The store is active only inside a build,
through a context variable, so owner actions always read live (D6)."""
import contextlib
import contextvars
import os
import threading

from . import buildstats

_ACTIVE = contextvars.ContextVar("relay_scan_store", default=None)


class Store:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.lock = threading.RLock()
        self.repo_facts = {}       # common dir -> {"stamp", "url", "common", "worktrees", "refs"}
        self.checkout_facts = {}   # checkout -> {"stamp", "branch", "head"}
        self.build = {}            # per-build answers, reset by use()

    def begin(self):
        with self.lock:
            self.build = {"stamps": {}, "commons": {}}


def active():
    store = _ACTIVE.get()
    return store if store is not None and store.enabled else None


@contextlib.contextmanager
def use(store):
    """Make store the active one for this context (one build). None leaves no store active."""
    if store is not None:
        store.begin()
    token = _ACTIVE.set(store)
    try:
        yield store
    finally:
        _ACTIVE.reset(token)


def stamp(paths):
    """(path, inode, size, mtime_ns) per path, None for one that cannot be stat'ed (F2a)."""
    out = []
    for path in paths:
        try:
            s = os.stat(path)
            out.append((path, s.st_ino, s.st_size, s.st_mtime_ns))
        except OSError:
            out.append((path, None))
    return tuple(out)


def git_dirs(checkout):
    """(git dir, common dir) found from files alone: a .git folder, or the folder a worktree's .git file names,
    and that folder's commondir file. None when the checkout has neither."""
    dot = os.path.join(checkout, ".git")
    if os.path.isdir(dot):
        gitdir = dot
    else:
        try:
            with open(dot) as f:
                line = f.read().strip()
        except OSError:
            return None
        if not line.startswith("gitdir:"):
            return None
        gitdir = os.path.join(checkout, line[len("gitdir:"):].strip())
    common = gitdir
    try:
        with open(os.path.join(gitdir, "commondir")) as f:
            common = os.path.join(gitdir, f.read().strip())
    except OSError:
        pass
    return os.path.realpath(gitdir), os.path.realpath(common)


def _walk(top):
    out = []
    try:
        entries = list(os.scandir(top))
    except OSError:
        return out
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                out += _walk(entry.path)
            else:
                out.append(entry.path)
        except OSError:
            out.append(entry.path)
    return out


def _stamp_files(common, *ref_dirs):
    paths = [os.path.join(common, "packed-refs")]
    for sub in ref_dirs:
        paths += sorted(_walk(os.path.join(common, *sub.split("/"))))
    return paths


def repo_stamp(common):
    """The stamp of everything a repository's shared facts come from: config, packed refs, the worktrees folder and
    the loose refs under refs/heads and refs/remotes/origin. FETCH_HEAD is left out (D1a)."""
    paths = [os.path.join(common, "config")] + _stamp_files(common, "refs/heads", "refs/remotes/origin")
    wt = os.path.join(common, "worktrees")
    try:
        names = sorted(os.listdir(wt))
    except OSError:
        names = []
    for name in names:
        paths += [os.path.join(wt, name, "HEAD"), os.path.join(wt, name, "gitdir")]
    return (tuple(names),) + stamp(paths)


def checkout_stamp(checkout):
    """(common dir or None, repo stamp, own stamp). The own stamp covers the checkout's HEAD file, packed refs and
    the local branch refs, so a fetch that moves only origin refs keeps the checkout's own facts."""
    dirs = git_dirs(checkout)
    if dirs is None:
        return None, None, (("missing", checkout),)
    gitdir, common = dirs
    own = stamp([os.path.join(gitdir, "HEAD")] + _stamp_files(common, "refs/heads"))
    return common, repo_stamp(common), own


def _stamps(store, checkout):
    """The stamps taken for checkout in this build, taken now on first use."""
    with store.lock:
        got = store.build["stamps"].get(checkout)
    if got is None:
        got = checkout_stamp(checkout)
        with store.lock:
            store.build["stamps"][checkout] = got
            if got[0]:
                store.build["commons"][checkout] = got[0]
    return got


def restamp(common):
    """Forget this build's stamps for one repository's checkouts, after a fetch wrote its refs."""
    store = active()
    if store is None:
        return
    common = os.path.realpath(common)
    with store.lock:
        for checkout, c in list(store.build["commons"].items()):
            if c == common:
                store.build["stamps"].pop(checkout, None)
                store.build["commons"].pop(checkout, None)


def _read_repo(checkout):
    from . import gitops
    url = gitops.run(["git", "remote", "get-url", "origin"], checkout, check=False)
    common = gitops.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], checkout)
    trees = gitops.run(["git", "worktree", "list", "--porcelain"], checkout).stdout
    refs = gitops.run(["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/remotes/origin"],
                      checkout).stdout
    return {"url": url.stdout.strip() if url.returncode == 0 else "",
            "common": os.path.realpath(common.stdout.strip()),
            "worktrees": [l[len("worktree "):] for l in trees.splitlines() if l.startswith("worktree ")],
            "refs": dict(line.rsplit(" ", 1) for line in refs.splitlines() if " " in line)}


def _read_checkout(checkout):
    from . import gitops
    return {"branch": gitops.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], checkout).stdout.strip(),
            "head": gitops.run(["git", "rev-parse", "HEAD"], checkout).stdout.strip()}


def repo_facts(checkout):
    """The repository facts of checkout (origin URL, common dir, worktrees, origin refs), or None when no store is
    active or git cannot read them, so the caller asks git itself and its error surfaces as today."""
    store = active()
    if store is None:
        return None
    common, rstamp, _ = _stamps(store, os.path.realpath(checkout))
    if common is None:
        return None
    with store.lock:
        cached = store.repo_facts.get(common)
    if cached is not None and cached["stamp"] == rstamp:
        buildstats.hit()
        return cached
    buildstats.miss()
    from .errors import RelayError
    try:
        facts = _read_repo(checkout)
    except (RelayError, OSError):
        return None
    facts["stamp"] = rstamp
    with store.lock:
        store.repo_facts[common] = facts
    return facts


def checkout_facts(checkout):
    """The checkout's current branch and HEAD commit, or None (see repo_facts)."""
    store = active()
    if store is None:
        return None
    real = os.path.realpath(checkout)
    common, _, own = _stamps(store, real)
    if common is None:
        return None
    with store.lock:
        cached = store.checkout_facts.get(real)
    if cached is not None and cached["stamp"] == own:
        buildstats.hit()
        return cached
    buildstats.miss()
    from .errors import RelayError
    try:
        facts = _read_checkout(checkout)
    except (RelayError, OSError):
        return None
    facts["stamp"] = own
    with store.lock:
        store.checkout_facts[real] = facts
    return facts
