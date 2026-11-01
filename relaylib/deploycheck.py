"""Is it safe to update the live relay now? (merge-and-tidy, deployment.) Lists what is in flight: reviews running,
a held owner-action lock and relay processes, each with what to wait for. The dashboard is not in flight; it only
needs a restart after the pull."""
import os
import subprocess

from . import owneractions, reviewjobs, status
from .errors import RelayError


def _processes(lines=None):
    """(blocking, dashboards): relay commands running now, and running dashboards, from `ps` lines."""
    if lines is None:
        try:
            p = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            return [f"could not list running processes ({e}); check by hand that no relay command runs"], []
        if p.returncode:
            return [f"could not list running processes (ps exit {p.returncode}); check by hand that no relay "
                    "command runs"], []
        lines = p.stdout.splitlines()
    blocking, dashboards = [], []
    for line in lines:
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        words = command.split()
        at = next((i for i, w in enumerate(words) if w.endswith("/relay") or w.endswith("/bin/relay")), None)
        if at is None:
            continue
        args = words[at + 1:]
        if args[:1] == ["ui"]:
            dashboards.append(f"dashboard (pid {pid})")
        elif args[:1] not in (["hook"], ["usage-snapshot"]):  # hook calls take milliseconds
            blocking.append(f"relay {' '.join(args)} (pid {pid}): wait for it to finish")
    return blocking, dashboards


def check(root=None, ps_lines=None):
    """(blocking, notes): blocking lists what must finish before the pull; notes what to do after it."""
    blocking = []
    try:
        rows = status.scan(root or status.projects_root())
    except Exception as e:  # cannot tell: say so rather than report safe
        rows, blocking = [], [f"could not read your features: {e}"]
    for row in rows:
        name = f"{os.path.basename(row.get('checkout') or '')}/{row['feature']}"
        if row.get("status") == "in-review":
            blocking.append(f"{name}: a review is running; wait for its verdict")
        try:
            job = reviewjobs.status(row["checkout"], row["feature"]) if row.get("checkout") else None
        except (RelayError, OSError, KeyError):
            job = None
        if job and job.get("state") == "running":
            blocking.append(f"{name}: a dashboard or hub review is running; wait for it to end")
    try:
        with owneractions.action_lock():
            pass
    except owneractions.Conflict:
        blocking.append("an owner action (merge, override or review publish) is running; wait for it")
    procs, dashboards = _processes(ps_lines)
    blocking += procs
    notes = [f"after the pull, restart the {d}: stop it, then run relay ui --background" for d in dashboards]
    return blocking, notes


def main():
    blocking, notes = check()
    for line in blocking:
        print(f"wait: {line}")
    for line in notes:
        print(f"then: {line}")
    if blocking:
        print("Not yet: something above is in flight.")
        return 1
    print("Safe to pull the live relay now. Sessions need nothing; hooks pick up the new code at their next event.")
    return 0
