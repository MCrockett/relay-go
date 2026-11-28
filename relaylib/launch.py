"""Start a Claude Code background session for the hub (hub-start-session D1 to D4, R5, R6, R9, R9a).

relay runs `claude --bg --remote-control <name> --session-id <uuid> "<prompt>"` as an argument list, with no shell,
in the folder the session should work in. It passes no permission flag: the session uses the owner's settings."""
import os
import subprocess

from . import gitops
from .config import relay_home
from .errors import RelayError

BYPASS = ("--permission-mode", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")
LAUNCH_WAIT_S = 60  # R9a: after this, the launch is unconfirmed and the process is left running


def argv(name, session_id, prompt):
    return ["claude", "--bg", "--remote-control", name, "--session-id", session_id, prompt]


def _first_line(path):
    try:
        with open(path, errors="replace") as f:
            return next((line.strip() for line in f if line.strip()), None)
    except OSError:
        return None


def run(args, cwd, session_id):
    """("ok", None), ("failed", reason) or ("unconfirmed", None). Output goes to files, never pipes, so a launcher
    left running cannot block on a full pipe."""
    folder = os.path.join(relay_home(), "launch")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    out, err = (os.path.join(folder, f"{session_id}.{ext}") for ext in ("out", "err"))
    try:
        with open(out, "w") as fo, open(err, "w") as fe:
            proc = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                                    start_new_session=True)
    except OSError as e:
        return "failed", f"cannot run claude: {e}"
    try:
        code = proc.wait(timeout=LAUNCH_WAIT_S)
    except subprocess.TimeoutExpired:
        return "unconfirmed", None
    if code == 0:
        return "ok", None
    return "failed", _first_line(err) or _first_line(out) or f"claude --bg exited with code {code}"


def start_prompt(request_id, slug):
    return (f"This session was started by relay for dispatch request {request_id}, on the owner's word through the "
            f"relay hub. Run relay dispatch --show {request_id}, then relay take --request {request_id} --slug "
            f"{slug}. Then continue with the relay skills through spec, plan and build. The owner merges.")


def clear_prompt(old_session_id, slugs):
    held = ", ".join(slugs) if slugs else "none"
    return (f"This session was started by relay to replace session {old_session_id}, which the owner retired "
            "through the relay hub. Run relay status and read any handoff, then relay take each feature that "
            f"session held: {held}. Then continue with the relay skills. The owner merges.")


def worktree_path(main, slug):
    return os.path.join(main, ".claude", "worktrees", slug)


def make_worktree(main, path, base):
    """A worktree detached at origin/<base> (D4); the commit it is at."""
    p = gitops.git(main, "worktree", "add", "--detach", path, f"origin/{base}", check=False)
    if p.returncode != 0:
        raise RelayError(f"could not make the worktree {path}: {(p.stderr or p.stdout).strip()}")
    return gitops.head_sha(path)


def remove_if_pristine(main, path, commit):
    """Remove a worktree relay made, only while it is clean, detached and still at `commit` (R9). True when removed."""
    if not os.path.isdir(path):
        return False
    status = gitops.git(path, "status", "--porcelain", check=False)
    detached = gitops.git(path, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0
    if status.returncode != 0 or status.stdout.strip() or not detached or gitops.head_sha(path) != commit:
        return False
    return gitops.git(main, "worktree", "remove", path, check=False).returncode == 0
