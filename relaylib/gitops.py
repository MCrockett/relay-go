"""Thin git and gh wrappers. Failures raise RelayError with git's own message."""
import contextlib
import contextvars
import json
import os
import signal
import subprocess
import threading

from .errors import RelayError

PR_FIELDS = "number,state,baseRefName,headRefOid,statusCheckRollup"
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
NOT_STARTED = "The job was not started"  # GitHub's annotation on a job it refused to run (billing)
WAITING = {"", "PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS"}

# Read-only git commands a snapshot may answer from its memo. Refs can move during a build only by
# an owner action, and the next build sees the move.
READS = {"show", "ls-tree", "rev-parse", "for-each-ref", "log", "rev-list", "diff", "status", "worktree", "remote"}
_MEMO = contextvars.ContextVar("relay_read_memo", default=None)
_MERGED_PRS, _MERGED_LOCK = {}, threading.Lock()  # (checkout, pr) -> info; a merged PR never changes


@contextlib.contextmanager
def read_memo():
    """Answer repeated git and gh reads from one shared memo for the rest of this context. Worker threads
    see it only when started with contextvars.copy_context(), so owner-action threads always read live."""
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def memo_active():
    return _MEMO.get() is not None


def _memo_key(kind, root, args):
    memo = _MEMO.get()
    if memo is None:
        return None, None
    if kind == "git":
        if (args[0] not in READS or (args[0] == "worktree" and args[1:2] != ("list",))
                or (args[0] == "remote" and args[1:2] not in ((), ("get-url",)))):
            return None, None
    elif not ((args[0] == "pr" and args[1:2] in (["view"], ["list"]))
              or (args[0] == "api" and not {"-X", "--method", "-f", "-F", "--input"} & set(args))):
        return None, None
    return memo, (kind, os.path.realpath(root), tuple(args))


FETCH_TIMEOUT_S = 120  # a fetch or push this slow is hung, usually on a dead network connection
GH_TIMEOUT_S = 60
SSH_OPTIONS = "ssh -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"


def run(args, cwd, check=True, env=None, timeout=None):
    """Run a command. With a timeout it runs in its own process group, and a hung run is stopped whole
    (git's ssh or https helper included) and raised as an error."""
    if timeout is None:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    else:
        proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    proc.wait(timeout=3)
                    break
                except subprocess.TimeoutExpired:
                    continue
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # descendants that outlived the leader
            except ProcessLookupError:
                pass
            proc.communicate()
            raise RelayError(f"{' '.join(args[:3])} timed out after {timeout:g}s")
        p = subprocess.CompletedProcess(args, proc.returncode, out, err)
    if check and p.returncode != 0:
        raise RelayError(f"{' '.join(args[:3])} failed: {(p.stderr or p.stdout).strip()}")
    return p


def git(root, *args, check=True):
    memo, key = _memo_key("git", root, args)
    if memo is None:
        return run(["git", *args], root, check)
    p = memo.get(key)
    if p is None:
        p = memo[key] = run(["git", *args], root, check=False)
    if check and p.returncode != 0:
        raise RelayError(f"{' '.join(['git', *args][:3])} failed: {(p.stderr or p.stdout).strip()}")
    return p


def repo_root(cwd):
    p = run(["git", "rev-parse", "--show-toplevel"], cwd, check=False)
    if p.returncode != 0:
        raise RelayError("not inside a git repository")
    return p.stdout.strip()


def repo_name(root):
    p = git(root, "remote", "get-url", "origin", check=False)
    url = p.stdout.strip()
    if p.returncode == 0 and url:
        return os.path.basename(url.rstrip("/")).removesuffix(".git")
    return os.path.basename(root)


def origin_url(root):
    """The origin URL, or the checkout's real path when there is none: a repo identity that,
    unlike the folder or repo name, cannot collide across owners."""
    p = git(root, "remote", "get-url", "origin", check=False)
    url = p.stdout.strip()
    return url if p.returncode == 0 and url else os.path.realpath(root)


def current_branch(root):
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def head_sha(root, ref="HEAD"):
    return git(root, "rev-parse", ref).stdout.strip()


def changed_paths_under(root, prefix):
    """Changed or untracked paths under prefix, matched case-insensitively and spelled
    the way git reports them, so a tree tracked as Docs/ cannot be silently missed."""
    tokens = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split("\0")
    want = prefix.replace(os.sep, "/").lower().rstrip("/") + "/"
    paths, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if len(tok) < 4:
            continue
        code, path = tok[:2], tok[3:]
        if code[0] in "RC":
            i += 1  # skip the rename source
        if path.lower().startswith(want):
            paths.append(path)
    return paths


SKIP_CI = "[skip ci]"


def commit_paths_under(root, prefix, message, skip_ci=False):
    """Commit what changed under prefix. skip_ci adds the body line GitHub reads to start no workflow
    (ci-skip-bookkeeping D1); the subject stays as given."""
    paths = changed_paths_under(root, prefix)
    if not paths:
        return False
    git(root, "add", "-A", "--", *paths)
    git(root, "commit", "-q", "-m", message, *(("-m", SKIP_CI) if skip_ci else ()), "--", *paths)
    return True


def marked(message):
    """True when a line of the commit message is relay's skip marker."""
    return any(line.strip() == SKIP_CI for line in message.splitlines())


def network_env(root):
    """Environment for git commands that talk to origin: never prompt in a terminal nobody watches, give up on
    a stalled https transfer, and add ssh connect and keepalive timeouts unless the owner chose their own ssh
    command (GIT_SSH_COMMAND, GIT_SSH or core.sshCommand)."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")
    env.setdefault("GIT_HTTP_LOW_SPEED_TIME", "30")
    configured = subprocess.run(["git", "config", "--get", "core.sshCommand"], cwd=root, capture_output=True,
                                text=True).stdout.strip()
    if "GIT_SSH_COMMAND" not in env and "GIT_SSH" not in env and not configured:
        env["GIT_SSH_COMMAND"] = SSH_OPTIONS
    return env


def network_git(root, *args):
    """A git command that talks to origin, bounded by FETCH_TIMEOUT_S; a timeout raises RelayError."""
    return run(["git", *args], root, check=False, env=network_env(root), timeout=FETCH_TIMEOUT_S)


def push(root, branch):
    p = network_git(root, "push", "-q", "-u", "origin", branch)
    if p.returncode != 0:
        raise RelayError(f"push failed, so this change is not published: {p.stderr.strip()}")


def fetch(root):
    if "origin" not in git(root, "remote").stdout.split():
        raise RelayError("repo has no 'origin' remote; relay publishes ownership there")
    try:
        p = network_git(root, "fetch", "-q", "--prune", "origin")
    except RelayError as e:
        raise RelayError(f"cannot fetch origin, so ownership cannot be confirmed: {e}")
    if p.returncode != 0:
        raise RelayError(f"cannot fetch origin, so ownership cannot be confirmed: {p.stderr.strip()}")


def common_dir(root):
    """The git directory a checkout shares with its linked worktrees (they share refs, so fetch once)."""
    return os.path.realpath(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())


def worktrees(root):
    out = git(root, "worktree", "list", "--porcelain").stdout
    return [line[len("worktree "):] for line in out.splitlines() if line.startswith("worktree ")]


def remote_branches(root):
    refs = git(root, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").stdout.split()
    return [r for r in refs if r not in ("origin", "origin/HEAD")]


def show(root, ref, path):
    p = git(root, "show", f"{ref}:{path}", check=False)
    return p.stdout if p.returncode == 0 else None


def cat_files(root, specs):
    """{"<rev>:<path>": text or None} for many blobs in one git process."""
    if not specs:
        return {}
    p = subprocess.run(["git", "cat-file", "--batch"], cwd=root, input="".join(f"{s}\n" for s in specs).encode(),
                       capture_output=True)
    if p.returncode != 0:
        raise RelayError(f"git cat-file failed: {p.stderr.decode(errors='replace').strip()}")
    out, data, pos = {}, p.stdout, 0
    for spec in specs:
        end = data.index(b"\n", pos)
        header = data[pos:end].split()
        pos = end + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            out[spec] = data[pos:pos + size].decode("utf-8", "replace")
            pos += size + 1
        else:
            out[spec] = None                     # missing, or not a blob
            if len(header) == 3:
                pos += int(header[2]) + 1
    return out


def ls_files(root, ref, prefix):
    want = prefix.lower().rstrip("/") + "/"
    out = git(root, "ls-tree", "-r", "--name-only", ref, check=False).stdout
    return [line for line in out.splitlines() if line.lower().startswith(want)]


def gh_json(root, args):
    memo, key = _memo_key("gh", root, args)
    if memo is None:
        return _gh_json(root, args)
    if key not in memo:
        try:
            memo[key] = (_gh_json(root, args), None)
        except RelayError as e:
            memo[key] = (None, e)
    result, error = memo[key]
    if error:
        raise error
    return json.loads(json.dumps(result))  # callers may edit what they get


def _gh_json(root, args):
    try:
        p = run([os.environ.get("RELAY_GH_BIN", "gh"), *args], root, check=False, timeout=GH_TIMEOUT_S)
    except OSError as e:
        raise RelayError(f"cannot run gh: {e}") from e
    if p.returncode != 0:
        raise RelayError(f"gh {' '.join(args[:2])} failed: {p.stderr.strip()}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RelayError(f"gh {' '.join(args[:2])} returned non-JSON output")


def open_pr_branches(root):
    """Head branches of the repo's open PRs, straight from GitHub; None if gh cannot tell."""
    try:
        prs = gh_json(root, ["pr", "list", "--state", "open", "--limit", "200", "--json", "headRefName,number"])
    except RelayError:
        return None
    return {p.get("headRefName") for p in prs}


def pr_comment(root, pr, body):
    p = run([os.environ.get("RELAY_GH_BIN", "gh"), "pr", "comment", str(pr), "--body", body], root, check=False,
            timeout=GH_TIMEOUT_S)
    if p.returncode != 0:
        raise RelayError(f"gh pr comment failed: {p.stderr.strip()}")


def pr_info(root, pr=None):
    key = (os.path.realpath(root), str(pr)) if pr else None
    with _MERGED_LOCK:
        known = _MERGED_PRS.get(key)
    if known:
        return dict(known)
    info = gh_json(root, ["pr", "view"] + ([str(pr)] if pr else []) + ["--json", PR_FIELDS])
    if key and info.get("state") == "MERGED":
        with _MERGED_LOCK:
            _MERGED_PRS[key] = dict(info)
    return info


def ci_state(info, cancelled="failing"):
    """green, pending, failing or none. Any failing check wins over pending ones. With cancelled="skip", a
    cancelled check is neither green nor failing: the answer is "cancelled" unless a real failure or a
    pending check outranks it (ci-skip-bookkeeping D8)."""
    checks = info.get("statusCheckRollup") or []
    if not checks:
        return "none"
    seen = set()
    for check in checks:
        if check.get("status") and check["status"].upper() != "COMPLETED":
            seen.add("pending")
            continue
        result = (check.get("conclusion") or check.get("state") or "").upper()
        if result == "CANCELLED" and cancelled == "skip":
            seen.add("cancelled")
            continue
        seen.add("green" if result in GREEN else "pending" if result in WAITING else "failing")
    for worst in ("failing", "pending", "cancelled"):
        if worst in seen:
            return worst
    return "green"


def code_sha(root, prefix, ref="HEAD"):
    """The newest commit reachable from ref that changed anything outside prefix (relay's bookkeeping folder)."""
    return git(root, "log", "-1", "--format=%H", ref, "--", ".", f":(exclude,icase){prefix}").stdout.strip()


def commit_ci_state(root, sha, cancelled="failing"):
    """CI for one commit: GitHub check runs plus legacy commit statuses. With cancelled="skip" (ci_for_code
    only) two more answers are possible (ci-on-submit D5): "not-started" when a run GitHub refused to start
    (billing) is all that stands between the commit and green, and "skipped" when every check run was skipped,
    as on a draft PR whose workflow skips its jobs."""
    data = gh_json(root, ["api", "--paginate", "--slurp",
                          f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs?per_page=100"])
    pages = data if isinstance(data, list) else [data]
    runs = [r for page in pages for r in page.get("check_runs", [])]
    total = max((page.get("total_count", 0) for page in pages), default=0)
    status = gh_json(root, ["api", f"repos/{{owner}}/{{repo}}/commits/{sha}/status"])
    refused = [r for r in runs if cancelled == "skip" and _not_started(root, r)]
    checks = [{"status": r.get("status") or "", "conclusion": r.get("conclusion") or ""}
              for r in runs if r not in refused]
    if status.get("total_count"):
        checks.append({"state": status.get("state") or ""})
    state = ci_state({"statusCheckRollup": checks}, cancelled)
    if len(runs) < total and state != "failing":
        return "pending"  # GitHub reported more checks than we could read: never call that green
    if cancelled == "skip" and state in ("green", "cancelled", "none"):
        if refused:
            return "not-started"  # outranks cancelled and green, never a real failure or a pending check
        if runs and not status.get("total_count") and all((r.get("conclusion") or "").upper() == "SKIPPED"
                                                          for r in runs):
            return "skipped"
    return state


def _not_started(root, run):
    """D5.1: a failing GitHub Actions run whose annotations say GitHub never started the job. Annotations are
    read only for such runs; a failed read means a real failure."""
    if (run.get("status") or "").lower() != "completed" or (run.get("conclusion") or "").lower() != "failure":
        return False
    if (run.get("app") or {}).get("slug") != "github-actions" or not (run.get("output") or {}).get("annotations_count"):
        return False
    try:
        notes = gh_json(root, ["api", f"repos/{{owner}}/{{repo}}/check-runs/{run.get('id')}/annotations"])
    except (RelayError, ValueError):
        return False
    return any(isinstance(n, dict) and n.get("annotation_level") == "failure"
               and (n.get("message") or "").startswith(NOT_STARTED) for n in notes or [])


def ci_for_code(root, head, prefix, limit=20):
    """CI evidence for the code at head: the newest completed result (green or failing) on any commit
    whose code is identical to head's outside prefix. GitHub runs push CI only on the pushed tip, so the
    code commit itself may have no run, and relay's bookkeeping commits re-trigger CI on the head.
    Each candidate is compared with head, so a side branch a merge discarded never counts. A commit carrying
    the skip marker never has a run of its own, so it is passed over without asking GitHub and does not count
    toward limit (D6); a cancelled run is no result, unless a real failure sits beside it (D8). Nor is a run
    GitHub refused to start, or a commit whose jobs were all skipped (ci-on-submit D5); when refused runs are all
    it found, the answer is "not-started" (D6)."""
    base = code_sha(root, prefix, head)
    log = git(root, "log", "--format=%H%x00%B%x01", "--ancestry-path", f"{base}..{head}").stdout
    messages = dict(entry.strip("\n").split("\0", 1) for entry in log.split("\x01") if "\0" in entry)
    newer = git(root, "rev-list", "--ancestry-path", f"{base}..{head}").stdout.split()
    pending, refused, asked = False, False, 0
    for sha in newer + [base]:
        if asked >= limit:
            break
        if marked(messages.get(sha, "")):
            continue
        same_code = sha == head or git(root, "diff", "--quiet", sha, head, "--", ".",
                                        f":(exclude,icase){prefix}", check=False).returncode == 0
        if not same_code:
            continue
        asked += 1
        state = commit_ci_state(root, sha, cancelled="skip")
        if state in ("green", "failing"):
            return state
        pending = pending or state == "pending"
        refused = refused or state == "not-started"
    return "pending" if pending else "not-started" if refused else "none"
