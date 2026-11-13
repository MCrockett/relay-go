"""The snapshot builder's cross-build store (dashboard-scan D1, D1a). Everything kept here is keyed by a git object
id or by file stamps, so a reused answer is one that cannot have changed. The store is active only inside a build,
through a context variable, so owner actions always read live (D6)."""
import contextlib
import contextvars
import copy
import os
import re
import threading

from . import buildstats

_ACTIVE = contextvars.ContextVar("relay_scan_store", default=None)


class Store:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.lock = threading.RLock()
        self.repo_facts = {}       # common dir -> {"stamp", "url", "common", "worktrees", "refs"}
        self.checkout_facts = {}   # checkout -> {"stamp", "branch", "head"}
        self.trees = {}            # common dir -> {commit: {path under docs/relay: blob id}}
        self.blobs = {}            # common dir -> {blob id: text}
        self.parsed = {}           # common dir -> {(blob id, label): ("ok", state) or ("error", RelayError)}
        self.answers = {}          # answers keyed by commit ids, which cannot change (merge bases, merge logs)
        self.done = {}             # (repo, slug) -> (reuse key, row, detail) of a done feature (D3)
        self.generation = {}       # common dir -> how many times a build fetched it
        self.files = {}            # path -> (stamp, parsed value) of a file a build parses (D7a)
        self.build = {}            # per-build answers, reset by use()

    def begin(self):
        with self.lock:
            self.build = {"stamps": {}, "commons": {}, "used": {}, "errors": {}, "absent": set(),
                          "answers": set(), "done": set(), "files": set()}

    def prune(self, checkouts=None):
        """Keep only what this build used (R14): the trees of the commits it resolved, every blob and parsed state
        those trees reference, and facts for the checkouts listed. A repository whose reads failed this build
        keeps everything (F2)."""
        with self.lock:
            failed = set(self.build.get("errors", {}))
            used = self.build.get("used", {})
            for common in list(self.trees):
                if common in failed:
                    continue
                keep = used.get(common, set())
                self.trees[common] = {c: t for c, t in self.trees[common].items() if c in keep}
                blobs = {b for t in self.trees[common].values() for b in t.values()}
                self.blobs[common] = {b: v for b, v in self.blobs.get(common, {}).items() if b in blobs}
                self.parsed[common] = {k: v for k, v in self.parsed.get(common, {}).items() if k[0] in blobs}
                if not self.trees[common]:
                    for table in (self.trees, self.blobs, self.parsed):
                        table.pop(common, None)
            self.answers = {k: v for k, v in self.answers.items() if k in self.build.get("answers", ())}
            self.done = {k: v for k, v in self.done.items() if k in self.build.get("done", ())}
            self.files = {k: v for k, v in self.files.items() if k in self.build.get("files", ())}
            if checkouts is not None:
                real = {os.path.realpath(c) for c in checkouts}
                self.checkout_facts = {c: f for c, f in self.checkout_facts.items() if c in real}
                commons = {self.build["commons"].get(c) for c in real} | failed
                self.repo_facts = {c: f for c, f in self.repo_facts.items() if c in commons}


def per_build(key, make):
    """make() once per build for key, shared by every step of that build; make() each call outside a build."""
    store = active()
    if store is None:
        return make()
    with store.lock:
        if key in store.build:
            return store.build[key]
    value = make()
    with store.lock:
        return store.build.setdefault(key, value)


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
    paths = [os.path.join(common, "config")]
    wt = os.path.join(common, "worktrees")
    try:
        names = sorted(os.listdir(wt))
    except OSError:
        names = []
    for name in names:
        paths += [os.path.join(wt, name, "HEAD"), os.path.join(wt, name, "gitdir")]
    return (tuple(names),) + stamp(paths), stamp(_stamp_files(common, "refs/heads", "refs/remotes/origin"))


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
    return {"url": url.stdout.strip() if url.returncode == 0 else "",
            "common": os.path.realpath(common.stdout.strip()),
            "worktrees": [l[len("worktree "):] for l in trees.splitlines() if l.startswith("worktree ")]}


def _read_refs(checkout):
    from . import gitops
    refs = gitops.run(["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/remotes/origin"],
                      checkout).stdout
    return dict(line.rsplit(" ", 1) for line in refs.splitlines() if " " in line)


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
    meta, refs = rstamp
    with store.lock:
        cached = store.repo_facts.get(common)
    if cached is not None and cached["stamp"] == rstamp:
        buildstats.hit()
        return cached
    buildstats.miss()
    from .errors import RelayError
    try:  # a ref move costs one for-each-ref; config or worktree changes read the rest
        facts = dict(cached) if cached is not None and cached["stamp"][0] == meta else _read_repo(checkout)
        facts["refs"] = (cached["refs"] if cached is not None and cached["stamp"][1] == refs and "refs" in cached
                         else _read_refs(checkout))
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


RELAY_PREFIX = "docs/relay/"
_SHA = re.compile(r"[0-9a-f]{40}")


def _common(store, repo):
    return _stamps(store, os.path.realpath(repo))[0]


def failed(repo):
    """The error this build's reads of repo's relay files hit, or None (F2)."""
    store = active()
    if store is None:
        return None
    common = _common(store, repo)
    with store.lock:
        return store.build["errors"].get(common)


def _fail(store, common, error):
    with store.lock:
        store.build["errors"].setdefault(common, str(error))


def tree(repo, commit):
    """{path: blob id} for every path under docs/relay at commit (matched case-insensitively), from one
    `git ls-tree -r` and cached by commit id. RelayError when git cannot list it; nothing is cached then."""
    from . import gitops
    from .errors import RelayError
    store = active()
    common = _common(store, repo) if store else None
    if common is None:
        raise RelayError("no scan store")
    with store.lock:
        got = store.trees.get(common, {}).get(commit)
        store.build["used"].setdefault(common, set()).add(commit)
    if got is not None:
        buildstats.hit()
        return got
    buildstats.miss()
    try:
        out = gitops.run(["git", "ls-tree", "-r", "-z", commit], repo).stdout
    except (RelayError, OSError) as e:
        _fail(store, common, e)
        raise RelayError(str(e))
    got = {}
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and path.lower().startswith(RELAY_PREFIX):
            got[path] = parts[2]
    with store.lock:
        store.trees.setdefault(common, {})[commit] = got
    return got


def prefetch(repo, commits):
    """Read every blob in these commits' relay trees that is not cached, with one `git cat-file --batch`. A blob
    the batch does not return is absent for this build and not cached (F3)."""
    from . import gitops
    from .errors import RelayError
    store = active()
    common = _common(store, repo) if store else None
    if common is None:
        return
    wanted = set()
    for commit in commits:
        try:
            wanted.update(tree(repo, commit).values())
        except RelayError:
            continue
    with store.lock:
        have = store.blobs.get(common, {})
        missing = sorted(b for b in wanted if b not in have and b not in store.build["absent"])
    if not missing:
        return
    try:
        got = gitops.cat_files(repo, missing)
    except RelayError as e:
        _fail(store, common, e)
        got = {}
    except ValueError:  # truncated output
        got = {}
    with store.lock:
        blobs = store.blobs.setdefault(common, {})
        for blob in missing:
            text = got.get(blob)
            if text is None:
                store.build["absent"].add(blob)
            else:
                blobs[blob] = text


def blob(repo, blob_id):
    """The text of one relay blob, read now when it was not prefetched; None when git has no such blob."""
    store = active()
    common = _common(store, repo)
    with store.lock:
        text = store.blobs.get(common, {}).get(blob_id)
        absent = blob_id in store.build["absent"]
    if text is not None:
        buildstats.hit()
        return text
    if absent:
        return None
    prefetch_blobs(repo, [blob_id])
    with store.lock:
        return store.blobs.get(common, {}).get(blob_id)


def prefetch_blobs(repo, blob_ids):
    from . import gitops
    from .errors import RelayError
    store = active()
    common = _common(store, repo)
    try:
        got = gitops.cat_files(repo, list(blob_ids))
    except RelayError as e:
        _fail(store, common, e)
        got = {}
    except ValueError:
        got = {}
    with store.lock:
        for b in blob_ids:
            if got.get(b) is None:
                store.build["absent"].add(b)
            else:
                store.blobs.setdefault(common, {})[b] = got[b]


def commit_of(repo, ref):
    """The commit id ref names, from the build's facts where it can; None when git cannot resolve it."""
    from . import gitops
    if _SHA.fullmatch(ref):
        return ref
    if ref == "HEAD":
        facts = checkout_facts(repo)
        if facts is not None:
            return facts["head"]
    elif ref.startswith("origin/"):
        facts = repo_facts(repo)
        if facts is not None and ref in facts["refs"]:
            return facts["refs"][ref]
    p = gitops.git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def lookup(repo, ref, path):
    """(found, blob id) for path at ref in a build: found is False when the store cannot answer, so the caller
    asks git; a None blob id means the path is not there."""
    from .errors import RelayError
    store = active()
    if store is None or not path.lower().startswith(RELAY_PREFIX) or _common(store, repo) is None:
        return False, None
    commit = commit_of(repo, ref)
    if commit is None:
        return True, None
    try:
        return True, tree(repo, commit).get(path)
    except RelayError:
        return True, None


def parsed(repo, blob_id, label, text):
    """state.parse_state of one blob, cached by blob id (F4: an error is cached and raised again). Callers get
    their own copy."""
    from . import state
    from .errors import RelayError
    store = active()
    common = _common(store, repo)
    key = (blob_id, label)
    with store.lock:
        got = store.parsed.get(common, {}).get(key)
    if got is None:
        try:
            got = ("ok", state.parse_state(text or "", label))
        except RelayError as e:
            got = ("error", e)
        if text is not None:
            with store.lock:
                store.parsed.setdefault(common, {})[key] = got
    else:
        buildstats.hit()
    if got[0] == "error":
        raise RelayError(str(got[1]))
    return copy.deepcopy(got[1])


def answer(key, make):
    """make() once for key across builds: only for answers keyed by commit ids, which cannot change."""
    store = active()
    if store is None:
        return make()
    with store.lock:
        store.build["answers"].add(key)
        if key in store.answers:
            buildstats.hit()
            return store.answers[key]
    value = make()
    with store.lock:
        store.answers[key] = value
    return value


def fetched(repo):
    """Count one fetch of repo's repository (a closed PR is asked again after one, D5)."""
    store = active()
    if store is None:
        return
    common = _common(store, repo) or os.path.realpath(repo)
    with store.lock:
        store.generation[common] = store.generation.get(common, 0) + 1


def generation(repo):
    store = active()
    if store is None:
        return None
    common = _common(store, repo) or os.path.realpath(repo)
    with store.lock:
        return store.generation.get(common, 0)


def repo_key(repo, checkouts):
    """The stamps that every reused done row of repo depends on: the repository's shared stamp and each of its
    checkouts' own stamps (D1a)."""
    store = active()
    common, rstamp, _ = _stamps(store, os.path.realpath(repo))
    return common, rstamp, tuple(_stamps(store, os.path.realpath(c))[2] for c in sorted(checkouts))


def done_get(repo, slug, key):
    store = active()
    if store is None or key is None:
        return None
    with store.lock:
        store.build["done"].add((repo, slug))
        got = store.done.get((repo, slug))
    if got is None or got[0] != key:
        return None
    buildstats.hit()
    return copy.deepcopy(got[1]), copy.deepcopy(got[2])


def done_put(repo, slug, key, row, detail):
    store = active()
    if store is None or key is None:
        return
    with store.lock:
        store.build["done"].add((repo, slug))
        store.done[(repo, slug)] = (key, copy.deepcopy(row), copy.deepcopy(detail))


def _copy(value):
    if isinstance(value, list):
        return [dict(v) if isinstance(v, dict) else copy.deepcopy(v) for v in value]
    return copy.deepcopy(value)


def parsed_file(path, parse):
    """parse(path), kept across builds while the file's stamp is unchanged (dashboard-scan D7a, R7b). Each read
    stats the file, so a write earlier in the same build is seen. Outside a build it simply parses. Callers get
    their own copy."""
    store = active()
    if store is None:
        return parse(path)
    now = stamp([path])
    with store.lock:
        store.build["files"].add(path)
        old = store.files.get(path)
    if old is not None and old[0] == now:
        buildstats.hit()
        return _copy(old[1])
    got = parse(path)
    with store.lock:
        store.files[path] = (now, got)
    return _copy(got)
