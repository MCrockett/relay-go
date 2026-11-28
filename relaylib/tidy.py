"""Tidy this machine's copy of a repo after its feature's PR merged (merge-and-tidy D5): remove the feature's
worktrees, bring the main checkout to the base branch, delete the local branch, prune. A step that is not safe is
skipped and reported with the command that would finish it; relay never forces one and never fails the merge."""
import os
import shutil

from . import gitops
from .errors import RelayError

SHOW = 5  # paths listed per skipped step
CACHE_DIRS = {"__pycache__", ".pytest_cache"}  # Python's own caches: safe to delete before removing a worktree


class Result:
    def __init__(self):
        self.lines = []  # one line per step, in order
        self.left = []   # short words for what was not tidied, for the merged event

    def done(self, text):
        self.lines.append(text)

    def skipped(self, text, short):
        self.lines.append(text)
        self.left.append(short)


def _git(root, *args):
    return gitops.git(root, *args, check=False)


def worktrees(repo):
    """[{path, branch}] from `git worktree list --porcelain`; the main checkout first."""
    out, found, current = _git(repo, "worktree", "list", "--porcelain").stdout, [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):], "branch": None}
            found.append(current)
        elif line.startswith("branch refs/heads/") and current is not None:
            current["branch"] = line[len("branch refs/heads/"):]
    return found


def _listed(paths):
    shown = ", ".join(paths[:SHOW])
    return shown + (f" and {len(paths) - SHOW} more" if len(paths) > SHOW else "")


def _entries(path, ignored):
    """[(code, path)] from `git status --porcelain -z`. A rename's second (source) path is its own entry."""
    args = ["status", "--porcelain", "-z"] + (["--ignored"] if ignored else [])
    p = _git(path, *args)
    if p.returncode:
        raise RelayError((p.stderr or p.stdout).strip() or "git status failed")
    return [(entry[:2], entry[3:]) for entry in p.stdout.split("\0") if entry]


def _changes(path, ignored):
    return [name for _, name in _entries(path, ignored)]


def _cache_root(rel):
    """The cache folder rel is in or is (up to and including its first cache part), or None."""
    parts = rel.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if part in CACHE_DIRS:
            return "/".join(parts[:i + 1])
    return None


def _caches_only(top, entries):
    """The cache folders to delete when every entry is an untracked or ignored Python cache, else None. A folder
    git lists as one entry is walked on disk: anything in it outside a cache folder, or any link, means None."""
    roots = set()
    for code, rel in entries:
        if code not in ("??", "!!"):
            return None
        root = _cache_root(rel)
        if root:
            roots.add(root)
            continue
        full = os.path.join(top, rel)
        if not rel.endswith("/") or os.path.islink(full.rstrip("/")):
            return None
        for here, dirs, files in os.walk(full):
            for d in list(dirs):
                if d in CACHE_DIRS and not os.path.islink(os.path.join(here, d)):
                    roots.add(os.path.relpath(os.path.join(here, d), top))
                    dirs.remove(d)
                elif os.path.islink(os.path.join(here, d)):
                    return None
            if files:
                return None
    for root in roots:
        if os.path.islink(os.path.join(top, root)):
            return None
    return sorted(roots)


def ignored_collisions(checkout, target):
    """Ignored files in checkout that moving to target would overwrite: a path target tracks, a path that is a
    folder in target, or a file under a path target tracks as a file."""
    ignored = [p for p in _git(checkout, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
               .stdout.split("\0") if p]
    if not ignored:
        return []
    listing = _git(checkout, "ls-tree", "-r", "--name-only", "-z", target)
    if listing.returncode:
        raise RelayError(f"cannot read {target}: {listing.stderr.strip()}")
    tracked = {p for p in listing.stdout.split("\0") if p}
    folders = {os.path.dirname(p) for p in tracked}
    for path in list(folders):
        while path:
            path = os.path.dirname(path)
            folders.add(path)
    hits = []
    for path in ignored:
        parents, up = [], os.path.dirname(path)
        while up:
            parents.append(up)
            up = os.path.dirname(up)
        if path in tracked or path in folders or any(p in tracked for p in parents):
            hits.append(path)
    return hits


def _exists(repo, ref):
    return _git(repo, "rev-parse", "--verify", "-q", ref).returncode == 0


def _inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def run(repo, branch, base, pr_head, cwd=None, keep=None):
    """Tidy after the PR for `branch` merged into `base` at `pr_head`. Never raises for a step. `keep` (a delegated
    merge, R13) is {path, provider, session, main}: the folder a live session is working in, left alone."""
    r = Result()
    try:
        _run(r, repo, branch, base, pr_head, cwd, keep)
    except Exception as e:  # a step's own failure is reported by the step; this is anything unexpected
        r.skipped(f"tidy stopped: {e}", "tidy stopped early")
    return r


def _run(r, repo, branch, base, pr_head, cwd, keep=None):
    p = gitops.network_git(repo, "fetch", "--prune", "origin")
    if p.returncode:
        r.skipped(f"fetch failed ({p.stderr.strip()}); run: git fetch --prune origin", "fetch")
    trees = worktrees(repo)
    main, removed = trees[0]["path"], []  # every step after this runs from main, which is never removed
    for wt in trees[1:]:
        if wt["branch"] != branch:
            continue
        if keep and os.path.realpath(wt["path"]) == os.path.realpath(keep["path"]):
            r.skipped(f"kept worktree {wt['path']}: {keep['provider']} session {keep['session']} is working in it; "
                      "it removes it when it next runs", f"worktree {wt['path']}")
            continue
        try:
            entries = _entries(wt["path"], ignored=True)
        except RelayError as e:
            r.skipped(f"kept worktree {wt['path']}: {e}", f"worktree {wt['path']}")
            continue
        caches = _caches_only(wt["path"], entries) if entries else None
        if caches:
            try:
                for root in caches:
                    shutil.rmtree(os.path.join(wt["path"], root))
            except OSError as e:
                r.skipped(f"kept worktree {wt['path']}: cannot delete its Python caches ({e})", f"worktree {wt['path']}")
                continue
            r.done(f"deleted Python caches in {wt['path']} ({_listed(caches)})")
            try:
                entries = _entries(wt["path"], ignored=True)
            except RelayError as e:
                r.skipped(f"kept worktree {wt['path']}: {e}", f"worktree {wt['path']}")
                continue
        files = [name for _, name in entries]
        if files:
            r.skipped(f"kept worktree {wt['path']}: it has files git does not hold ({_listed(files)}). Check "
                      f"them, then run: git worktree remove --force {wt['path']}", f"worktree {wt['path']}")
            continue
        p = _git(main, "worktree", "remove", wt["path"])
        if p.returncode:
            r.skipped(f"kept worktree {wt['path']}: {p.stderr.strip()}", f"worktree {wt['path']}")
        else:
            removed.append(wt["path"])
            r.done(f"removed worktree {wt['path']}")
    main_branch = trees[0]["branch"]
    keep_main = bool(keep and keep.get("main") and main_branch == branch)
    if keep_main:
        r.skipped(f"left the main checkout on {branch} and kept local branch {branch}: {keep['provider']} session "
                  f"{keep['session']} is working in it; it switches when it next runs", "main checkout")
    elif main_branch == branch:
        main_branch = _switch(r, main, base, branch)
    if main_branch == base:
        _pull(r, main, base)
    elif not any(wt["branch"] == base for wt in worktrees(main)):
        p = gitops.network_git(main, "fetch", "origin", f"{base}:{base}")
        if p.returncode:
            r.skipped(f"left local {base} as it is ({p.stderr.strip()})", f"local {base}")
        else:
            r.done(f"fast-forwarded local {base} (the main checkout stays on {main_branch})")
    else:
        r.done(f"the main checkout stays on {main_branch}")
    if not keep_main:
        _delete_branch(r, main, branch, pr_head)  # from main: the folder relay ran in may be gone now
    _git(main, "worktree", "prune")
    r.done("pruned worktrees")
    if cwd and any(_inside(cwd, path) for path in removed):
        r.done(f"the folder you ran this from was removed; continue in {main}")


def _switch(r, main, base, branch):
    """Move the main checkout off the merged branch; the branch it ends on."""
    try:
        changes = _changes(main, ignored=False)
    except RelayError as e:
        r.skipped(f"left the main checkout on {branch}: {e}", "main checkout")
        return branch
    if changes:
        r.skipped(f"left the main checkout on {branch}: it has uncommitted changes ({_listed(changes)}); commit "
                  f"or move them, then run: git switch {base}", "main checkout")
        return branch
    target = base if _exists(main, f"refs/heads/{base}") else f"origin/{base}"
    try:
        hits = ignored_collisions(main, target)
    except RelayError as e:
        r.skipped(f"left the main checkout on {branch}: {e}", "main checkout")
        return branch
    if hits:
        r.skipped(f"left the main checkout on {branch}: switching to {base} would overwrite ignored files "
                  f"({_listed(hits)}). Move them, then run: git switch {base}", "main checkout")
        return branch
    p = _git(main, "switch", base)
    if p.returncode:
        r.skipped(f"left the main checkout on {branch}: {p.stderr.strip()}", "main checkout")
        return branch
    r.done(f"switched the main checkout to {base}")
    return base


def _pull(r, main, base):
    """Fast-forward base to the origin commit whose tree was checked: no second fetch, no configured upstream."""
    try:
        changes = _changes(main, ignored=False)
        target = _git(main, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{base}^{{commit}}").stdout.strip()
        if not target:
            raise RelayError(f"origin/{base} is not known here")
        hits = [] if changes else ignored_collisions(main, target)
    except RelayError as e:
        r.skipped(f"did not pull {base}: {e}", f"pull {base}")
        return
    if changes:
        r.skipped(f"did not pull {base}: the main checkout has uncommitted changes ({_listed(changes)})",
                  f"pull {base}")
    elif hits:
        r.skipped(f"did not pull {base}: it would overwrite ignored files ({_listed(hits)}). Move them, then run: "
                  "git pull --ff-only", f"pull {base}")
    else:
        p = _git(main, "merge", "--ff-only", "-q", target)
        if p.returncode:
            r.skipped(f"did not pull {base}: {(p.stderr or p.stdout).strip()}", f"pull {base}")
        else:
            r.done(f"pulled {base}")


def _delete_branch(r, repo, branch, pr_head):
    if not _exists(repo, f"refs/heads/{branch}"):
        return
    if any(wt["branch"] == branch for wt in worktrees(repo)):
        r.skipped(f"kept local branch {branch}: a worktree still has it checked out", f"branch {branch}")
        return
    tip = _git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip()
    if not pr_head or _git(repo, "merge-base", "--is-ancestor", tip, pr_head).returncode:
        r.skipped(f"kept local branch {branch}: it has commits the merged PR does not ({tip[:7]}). Check them, "
                  f"then run: git branch -D {branch}", f"branch {branch}")
        return
    p = _git(repo, "branch", "-D", branch)
    if p.returncode:
        r.skipped(f"kept local branch {branch}: {p.stderr.strip()}", f"branch {branch}")
    else:
        r.done(f"deleted local branch {branch}")


def leftovers(repo, branch):
    """Short words for what a merge that did not tidy leaves: the remote branch, each worktree on the branch (the
    main checkout counts) and the local branch."""
    left = []
    if gitops.network_git(repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}").stdout.strip():
        left.append(f"remote branch {branch}")
    left += [f"worktree {wt['path']}" for wt in worktrees(repo) if wt["branch"] == branch]
    if _exists(repo, f"refs/heads/{branch}"):
        left.append(f"local branch {branch}")
    return left


def find_keep(repo, owner, branch):
    """The `keep` for tidy: the folder on `branch` whose name is the owner's recorded worktree while the owner's
    session is live; None otherwise. owner.worktree is a folder name, so the branch narrows it to one folder."""
    from . import events
    if not isinstance(owner, dict) or not all(isinstance(owner.get(k), str) and owner[k]
                                              for k in ("provider", "session", "worktree")):
        return None
    if not events.session_live(owner["provider"], owner["session"]):
        return None
    trees = worktrees(repo)
    for index, wt in enumerate(trees):
        if wt["branch"] == branch and os.path.basename(wt["path"].rstrip("/")) == owner["worktree"]:
            return {"path": wt["path"], "provider": owner["provider"], "session": owner["session"],
                    "main": index == 0}
    return None
