import json
import os
import stat
import subprocess
import sys


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _identify(repo):
    sh(repo, "git", "config", "user.email", "test@example.com")
    sh(repo, "git", "config", "user.name", "Relay Test")


def make_repo(tmp):
    """A work repo on `develop` with one commit, pushed to a bare `origin`."""
    origin, work = os.path.join(tmp, "origin.git"), os.path.join(tmp, "work")
    sh(tmp, "git", "init", "-q", "--bare", "-b", "develop", origin)
    sh(tmp, "git", "init", "-q", "-b", "develop", work)
    _identify(work)
    write(os.path.join(work, "README.md"), "hello\n")
    sh(work, "git", "add", "README.md")
    sh(work, "git", "commit", "-q", "-m", "init")
    sh(work, "git", "remote", "add", "origin", origin)
    sh(work, "git", "push", "-q", "-u", "origin", "develop")
    return origin, work


def clone(origin, dest):
    sh(os.path.dirname(dest), "git", "clone", "-q", origin, dest)
    _identify(dest)
    return dest


def fake_bin(directory, name, body):
    path = os.path.join(directory, name)
    write(path, body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


FAKE_REVIEWER = '''#!/usr/bin/env python3
import json, os, sys, time
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "provider": os.environ.get("RELAY_PROVIDER"),
                            "claudecode": os.environ.get("CLAUDECODE")}) + "\\n")
if os.environ.get("FAKE_GRANDCHILD"):
    import subprocess
    subprocess.Popen(["sleep", "30"])  # inherits our stdout pipe, like a stray reviewer subprocess
if os.environ.get("FAKE_STUBBORN"):
    import subprocess
    subprocess.Popen(["sh", "-c", "trap '' TERM; echo $$ > $0; sleep 30", os.environ["FAKE_STUBBORN"]])
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
queue = os.environ["FAKE_OUT"]
first = sorted(os.listdir(queue))[0]
with open(os.path.join(queue, first)) as f:
    out = f.read()
os.remove(os.path.join(queue, first))
if out.startswith("#sleep "):
    head, _, out = out.partition("\\n")
    time.sleep(float(head.split()[1]))
sys.stdout.write(out)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''

FAKE_GH = '''#!/usr/bin/env python3
import os, sys
import json, re
args = " ".join(sys.argv[1:])
if os.environ.get("FAKE_GH_LOG"):
    with open(os.environ["FAKE_GH_LOG"], "a") as log:
        log.write(args + "\\n")
sha = re.search(r"commits/([0-9a-f]+)/", args)
by_sha = os.environ.get("FAKE_GH_RUNS_BY_SHA")
api = os.environ.get("FAKE_GH_API")  # {path substring: response, or {"__rc": 1}}; the longest match wins
if api:
    table = json.load(open(api))
    hit = next((k for k in sorted(table, key=len, reverse=True) if k in args), None)
    if hit is not None:
        if isinstance(table[hit], dict) and table[hit].get("__rc"):
            sys.stderr.write("gh: HTTP 500\\n")
            sys.exit(table[hit]["__rc"])
        sys.stdout.write(json.dumps(table[hit]))
        sys.exit(0)
if args.startswith("pr ready") and os.path.exists(os.environ.get("FAKE_GH_JSON") or ""):
    info = json.load(open(os.environ["FAKE_GH_JSON"]))  # GitHub flips the draft flag
    if isinstance(info, dict):
        info["isDraft"] = "--undo" in args
        open(os.environ["FAKE_GH_JSON"], "w").write(json.dumps(info))
    sys.exit(0)
if args.startswith("pr list"):
    if os.environ.get("FAKE_GH_PR_LIST_FAILS"):
        sys.stderr.write("gh: could not reach GitHub\\n")
        sys.exit(1)
    prs = os.environ.get("FAKE_GH_PRS")
    sys.stdout.write(open(prs).read() if prs and os.path.exists(prs) else "[]")
    sys.exit(0)
if "check-runs" in args and by_sha and sha:
    table = json.load(open(by_sha))
    sys.stdout.write(json.dumps(table.get(sha.group(1), {"total_count": 0, "check_runs": []})))
    sys.exit(0)
if "check-runs" in args:
    path = os.environ.get("FAKE_GH_RUNS")
elif args.startswith("api ") and args.endswith("/status"):
    path = os.environ.get("FAKE_GH_STATUS")
else:
    path = os.environ.get("FAKE_GH_JSON")
sys.stdout.write(open(path).read())
'''


def codex_output(text):
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Reading the files."}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "echo 'verdict: GO'"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50}},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def claude_output(text, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": text,
                       "usage": {"input_tokens": 10, "cache_read_input_tokens": 900,
                                 "cache_creation_input_tokens": 0, "output_tokens": 40}})


def verdict_block(v, blocking=(), prior=()):
    lines = ["```", f"verdict: {v}", "prior:"]
    lines += [f"  - id: {i} status: {s}" for i, s in prior]
    lines += ["blocking:"] + [f"  - {b}" for b in blocking] + ["notes:", "```"]
    return "Review done.\n\n" + "\n".join(lines) + "\n"


HOLD_LOCK = '''import sys
sys.path.insert(0, sys.argv[1])
from relaylib import owneractions
with owneractions.action_lock():
    print("held", flush=True)
    sys.stdin.read()
'''


def hold_action_lock():
    """A separate process holding relay's owner action lock until `.stdin.close()` (mobile-hub D7)."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, root], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, env=dict(os.environ))
    assert child.stdout.readline().strip() == "held"
    return child


def release(child):
    child.stdin.close()
    child.wait(10)
    child.stdout.close()
