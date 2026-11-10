"""Local test runs for ci = "local" (ci-modes D4 to D11): keys, records, liveness locks, the detached runner,
cleanup and the status flag text. A run uses a clean detached worktree of the commit under test. A record is
local to the machine and lives in <git common dir>/relay/tests/."""
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import config, gitops
from .errors import RelayError
from .state import RELAY_DIR

KEEP_LOGS = 20
ENV_COMMAND = "RELAY_TEST_COMMAND"
ENV_TIMEOUT = "RELAY_TEST_TIMEOUT_MIN"
START_WAIT_S = 10.0


def key_id(code, command):
    return hashlib.sha256((code + "\0" + command).encode()).hexdigest()[:16]


def _tests_dir(repo):
    return os.path.join(gitops.common_dir(repo), "relay", "tests")


def _runs_dir(repo):
    return os.path.join(gitops.common_dir(repo), "relay", "test-runs")


def paths(repo, kid):
    base = os.path.join(_tests_dir(repo), kid)
    return {"record": base + ".json", "lock": base + ".lock", "log": base + ".log"}


def _now():
    return time.time()


def live(repo, kid):
    """True when another process holds the run's lock (D11). The pid is never consulted."""
    lock = paths(repo, kid)["lock"]
    if not os.path.exists(lock):
        return False
    with open(lock, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _with_liveness(repo, rec):
    if rec and rec.get("status") == "running" and not live(repo, key_id(rec["code"], rec["command"])):
        rec = dict(rec, status="stopped")
    return rec


def read(repo, code, command):
    kid = key_id(code, command)
    return _with_liveness(repo, _load(paths(repo, kid)["record"]))


def write(repo, record):
    """Write to a temporary file in the same folder, then rename it into place."""
    p = paths(repo, key_id(record["code"], record["command"]))
    os.makedirs(os.path.dirname(p["record"]), exist_ok=True)
    temp = f"{p['record']}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(temp, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
    os.replace(temp, p["record"])


def records(repo):
    """Every record on this machine for the repo, liveness applied."""
    folder = _tests_dir(repo)
    out = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith(".json"):
            rec = _load(os.path.join(folder, name))
            if rec:
                out.append(_with_liveness(repo, rec))
    return out


def _repo_name(repo):
    return os.path.basename(os.path.dirname(gitops.common_dir(repo)))


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _remove_worktree(repo, path):
    if os.path.isdir(path):
        subprocess.run(["git", "worktree", "remove", "--force", path], cwd=repo, capture_output=True)
        shutil.rmtree(path, ignore_errors=True)


def run(repo, commit, code, command, timeout_min, slug, branch):
    """The foreground body of a run. Returns the final record, or the existing one when a run holds the key."""
    kid = key_id(code, command)
    p = paths(repo, kid)
    os.makedirs(os.path.dirname(p["lock"]), exist_ok=True)
    lock = open(p["lock"], "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return read(repo, code, command)
    rec = {"code": code, "command": command, "commit": commit, "status": "running", "exit": None,
           "started": _now(), "finished": None, "slug": slug, "branch": branch, "repo_name": _repo_name(repo),
           "pid": os.getpid(), "log": p["log"], "lock": p["lock"], "reason": ""}
    stop = []
    previous = signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    proc = None
    work = os.path.join(_runs_dir(repo), f"{kid}-{os.getpid()}")
    try:
        write(repo, rec)
        with open(p["log"], "wb") as log:
            os.makedirs(_runs_dir(repo), exist_ok=True)
            add = subprocess.run(["git", "worktree", "add", "--detach", work, commit], cwd=repo,
                                 stdout=log, stderr=subprocess.STDOUT)
            if add.returncode != 0:
                rec.update(status="fail", exit=add.returncode, reason="could not create the test worktree")
            else:
                proc = subprocess.Popen(["/bin/sh", "-c", command], cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
                deadline = time.monotonic() + float(timeout_min) * 60
                while proc.poll() is None:
                    if stop:
                        _kill_group(proc)
                        proc.wait()
                        rec.update(status="stopped", exit=proc.returncode, reason="stopped by relay")
                        break
                    if time.monotonic() >= deadline:
                        _kill_group(proc)
                        proc.wait()
                        rec.update(status="fail", exit=proc.returncode,
                                   reason=f"timed out after {timeout_min:g} min")
                        break
                    time.sleep(0.05)
                if rec["status"] == "running":
                    rec.update(status="pass" if proc.returncode == 0 else "fail", exit=proc.returncode)
                _kill_group(proc)  # stragglers of the command's group
    except BaseException as e:  # the gate must never pass on a run that did not finish
        if proc is not None and proc.poll() is None:
            _kill_group(proc)
        rec.update(status="fail", reason=f"runner error: {e}")
    finally:
        _remove_worktree(repo, work)
        rec["finished"] = _now()
        if rec["status"] == "running":
            rec["status"] = "fail"
        write(repo, rec)
        signal.signal(signal.SIGTERM, previous)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return rec


def _wait_started(repo, kid, since):
    deadline = time.monotonic() + START_WAIT_S
    while time.monotonic() < deadline:
        rec = _load(paths(repo, kid)["record"])
        if live(repo, kid) and rec and rec.get("status") == "running":
            return True
        if rec and rec.get("status") != "running" and (rec.get("started") or 0) >= since:
            return True  # this run already finished
        time.sleep(0.02)
    return False


def start(repo, commit, code, command, timeout_min, slug, branch):
    """Start a background run: "started", or "running" when a run already holds this key (D7, D10)."""
    kid = key_id(code, command)
    if live(repo, kid):
        return "running"
    for rec in records(repo):  # D10: one run per feature at a time
        if rec.get("slug") == slug and rec["status"] == "running" and key_id(rec["code"], rec["command"]) != kid:
            try:
                os.kill(rec["pid"], signal.SIGTERM)
            except (ProcessLookupError, PermissionError, TypeError):
                pass
    since = _now() - 1  # a record older than this belongs to an earlier run
    env = dict(os.environ, **{ENV_COMMAND: command, ENV_TIMEOUT: str(timeout_min)})
    subprocess.Popen([sys.executable, "-m", "relaylib.localtests", "run", repo, commit, code, slug, branch],
                     cwd=config.RELAY_HOME_DIR, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    _wait_started(repo, kid, since)
    return "started"


def ensure(repo, commit, code, command, timeout_min, slug, branch):
    """D8: "pass" when a pass exists, "running" when live, else start a run. A fail is rerun on purpose."""
    rec = read(repo, code, command)
    if rec and rec["status"] == "pass":
        return "pass"
    if rec and rec["status"] == "running":
        return "running"
    return start(repo, commit, code, command, timeout_min, slug, branch)


def _feature_done(repo, rec):
    from . import state
    text = gitops.show(repo, f"origin/{rec.get('branch')}", f"{RELAY_DIR}/{rec.get('slug')}/state.md")
    if text is None:
        return True  # a branch that is gone was merged or abandoned
    try:
        st = state.parse_state(text)
    except RelayError:
        return False
    return "done" in (st.get("stage"), st.get("status"))


def cleanup(repo):
    """R13. Remove run worktrees no live run holds, prune, and keep the newest KEEP_LOGS logs."""
    runs = _runs_dir(repo)
    for name in sorted(os.listdir(runs)) if os.path.isdir(runs) else []:
        if not live(repo, name.split("-", 1)[0]):
            _remove_worktree(repo, os.path.join(runs, name))
    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
    recs = sorted(records(repo), key=lambda r: r.get("started") or 0, reverse=True)
    protected, seen = set(), set()
    for rec in recs:  # newest first: the first record of each slug is that slug's newest
        kid = key_id(rec["code"], rec["command"])
        slug = rec.get("slug")
        if rec["status"] == "running":
            protected.add(kid)
        if slug not in seen:
            seen.add(slug)
            if not _feature_done(repo, rec):
                protected.add(kid)
    kept = 0
    for rec in recs:
        kid = key_id(rec["code"], rec["command"])
        if kept < KEEP_LOGS:
            kept += 1
            continue
        if kid in protected:
            continue
        for path in paths(repo, kid).values():
            try:
                os.unlink(path)
            except OSError:
                pass


def _hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts or 0))


def flag(rec, code):
    sha8 = code[:8]
    status = rec["status"] if rec else None
    if status == "pass":
        return f"tests: local, green at {sha8}"
    if status == "running":
        return f"tests: local, running since {_hhmm(rec.get('started'))}"
    if status == "fail":
        return f"tests: local, failed at {sha8}"
    if status == "stopped":
        return f"tests: local, stopped at {sha8}"
    return f"tests: local, not run for {sha8}"


def _main(argv):
    if len(argv) == 6 and argv[0] == "run":
        repo, commit, code, slug, branch = argv[1:]
        command = os.environ[ENV_COMMAND]
        timeout_min = float(os.environ[ENV_TIMEOUT])
        try:
            cleanup(repo)  # before the run, so a leftover from a dead run goes first (N3)
        except Exception:
            pass
        rec = run(repo, commit, code, command, timeout_min, slug, branch)
        notify_finished(repo, rec)
        return 0
    print("usage: python -m relaylib.localtests run <repo> <commit> <code> <slug> <branch>", file=sys.stderr)
    return 2


def notify_finished(repo, rec):
    """Hook for the Task 7 notifications (R17): a fail sends "needs you"; a pass after a fresh GO sends
    "ready to merge". Nothing is sent yet."""


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
