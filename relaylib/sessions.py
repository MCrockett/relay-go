"""Hook sink: agent sessions report their own state (spec session-health R1, R2). Never fails; prints only a
merge notice for the agent, once per session and PR (relaylib.notices)."""
import fcntl
import hashlib
import io
import json
import os
import re
import select
import subprocess
import tempfile
import time

from .config import relay_home

STATES = ("permission", "waiting", "working", "ended")
WAITING = {"claude": {"Stop"}, "codex": {"Stop", "Interrupt"}}
WORKING = {"claude": {"UserPromptSubmit"}, "codex": {"UserPromptSubmit"}}
NOTICE = {"claude": {"UserPromptSubmit", "SessionStart"}, "codex": {"UserPromptSubmit"}}  # merge notice events
TOOL_REFRESH_S = 30  # PostToolUse is frequent: one write per 30 seconds is enough
READ_DEADLINE_S = 0.7  # with the 0.1 s lock wait, the sink stays within 1 second
READ_CAP = 32_000_000  # far above any real hook input; only a runaway pipe reaches it
WALK_TIMEOUT_S, ALIVE_TIMEOUT_S, WALK_STEPS = 0.3, 1.0, 20  # running-now D1, D2
WALK_EVENTS = {"SessionStart", "UserPromptSubmit"}  # a resumed session may be a new process
LSTART = r"\w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4}"
AGENT_NODE = {"claude": "claude-code", "codex": "@openai/codex"}


def folder():
    return os.path.join(relay_home(), "sessions")


def record_path(provider, session_id):
    return os.path.join(folder(), hashlib.sha1(f"{provider}\n{session_id}".encode()).hexdigest() + ".json")


def _sha1(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_process(process):
    return (isinstance(process, dict) and set(process) == {"pid", "started"} and isinstance(process["pid"], int)
            and not isinstance(process["pid"], bool) and process["pid"] > 0
            and isinstance(process["started"], str) and bool(process["started"]))


def _ps(columns, timeout):
    """`ps -A -o <columns>` lines, or None when ps is missing, fails, times out or prints nothing."""
    try:
        p = subprocess.run(["ps", "-A", "-o", columns], capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [l for l in p.stdout.splitlines() if l.strip()] if p.returncode == 0 else []
    return lines or None


def _is_agent(provider, args):
    words = args.split()
    if not words:
        return False
    name = os.path.basename(words[0])
    return name == provider or (name == "node" and any(AGENT_NODE[provider] in w for w in words[1:]))


def find_process(provider, lines, start_pid):
    """{pid, started} of the first ancestor of start_pid that is the agent, or None (running-now D1)."""
    table = {}
    for line in lines or []:
        m = re.match(rf"\s*(\d+)\s+(\d+)\s+({LSTART})\s+(.*)$", line)
        if m:
            table[int(m.group(1))] = (int(m.group(2)), m.group(3), m.group(4))
    pid = start_pid
    for _ in range(WALK_STEPS):
        if pid not in table or pid <= 1:
            return None
        ppid, started, args = table[pid]
        if pid != start_pid and _is_agent(provider, args):
            return {"pid": pid, "started": started}
        pid = ppid
    return None


def alive(records):
    """Session ids whose recorded process still runs, or None when that cannot be checked (running-now D2)."""
    lines = _ps("pid=,lstart=", ALIVE_TIMEOUT_S)
    if lines is None:
        return None
    running = {}
    for line in lines:
        m = re.match(rf"\s*(\d+)\s+({LSTART})\s*$", line)
        if m:
            running[int(m.group(1))] = m.group(2)
    if not running:
        return None  # nothing parseable: unknown, not "everything is dead"
    return {r["session_id"] for r in records
            if r.get("process") and running.get(r["process"]["pid"]) == r["process"]["started"]}


def _valid(record):
    """The full R1 record shape; anything else is treated as absent and replaced."""
    if not (isinstance(record, dict) and isinstance(record.get("provider"), str) and record["provider"] in WAITING
            and isinstance(record.get("session_id"), str) and record["session_id"]
            and record.get("state") in STATES and _number(record.get("since")) and _number(record.get("at"))
            and isinstance(record.get("event"), str) and isinstance(record.get("pending"), list)
            and (record.get("cwd") is None or isinstance(record.get("cwd"), str))):
        return False
    if "process" in record and not _valid_process(record["process"]):
        return False
    if "inbox" in record and not (isinstance(record["inbox"], str) and os.path.isabs(record["inbox"])):
        return False
    return all(isinstance(p, dict) and set(p) == {"tool_use_id", "tool_name", "input_sha1"}
               and (p["tool_use_id"] is None or isinstance(p["tool_use_id"], str))
               and isinstance(p["tool_name"], str) and isinstance(p["input_sha1"], str)
               for p in record["pending"])


def apply(record, provider, event, now, inbox=None):
    """The record after this event, or None when nothing should be written. `inbox` is the Claude session's inbox
    socket path from its hook environment (session-notify D2)."""
    if not isinstance(event, dict):
        return None
    sid, name, cwd = event.get("session_id"), event.get("hook_event_name"), event.get("cwd")
    if not (isinstance(sid, str) and sid and isinstance(name, str) and name):
        return None
    if "cwd" in event and not isinstance(cwd, str):  # present means a string; an empty one says nothing
        return None
    if name in ("PermissionRequest", "PostToolUse") and not isinstance(event.get("tool_name"), str):
        return None
    old = record if _valid(record) and record["provider"] == provider and record["session_id"] == sid else None
    state, pending = (old["state"], list(old["pending"])) if old else (None, [])
    if name == "PermissionRequest":
        tool_id = event.get("tool_use_id") if isinstance(event.get("tool_use_id"), str) else None
        pending.append({"tool_use_id": tool_id, "tool_name": event["tool_name"],
                        "input_sha1": _sha1(event.get("tool_input"))})
        new_state = "permission"
    elif name in WAITING[provider] or (provider == "claude" and name == "Notification"
                                       and event.get("notification_type") == "idle_prompt"):
        new_state, pending = "waiting", []
    elif name in WORKING[provider]:
        new_state, pending = "working", []
    elif provider == "claude" and name == "SessionStart":
        if event.get("source") == "compact":  # compaction happens mid-turn
            new_state, pending = "working", []
        elif state in ("waiting", "permission"):
            new_state = state  # reopening a session is not an answer (waiting-visibility D1)
        else:
            new_state, pending = "waiting", []  # a new or reopened session sits at the prompt
    elif name == "SessionEnd":
        new_state, pending = "ended", []
    elif name == "PostToolUse":
        tool_id = event.get("tool_use_id") if isinstance(event.get("tool_use_id"), str) else None
        digest = _sha1(event.get("tool_input"))
        match = None
        if tool_id is not None:  # any string is an id, even an empty one
            match = next((p for p in pending if p["tool_use_id"] == tool_id), None)
        if match is None:  # without ids on both sides, fall back to the same tool and input
            match = next((p for p in pending if p["tool_name"] == event["tool_name"] and p["input_sha1"] == digest
                          and not (tool_id is not None and p["tool_use_id"] is not None)), None)
        if match is not None:
            pending.remove(match)
        if state == "permission":
            new_state = "permission" if pending else "working"
        elif state in ("waiting", "ended"):
            new_state = state  # tool activity never ends a wait: a subagent can run after the parent's turn
        else:
            new_state = "working"
            if state == "working" and match is None and now - old["at"] < TOOL_REFRESH_S:
                return None
    else:
        return None
    resolved = os.path.realpath(cwd) if cwd else (old or {}).get("cwd")
    out = {"provider": provider, "session_id": sid, "cwd": resolved, "state": new_state,
           "since": old["since"] if old and state == new_state else now, "at": now, "event": name,
           "pending": pending}
    if old and "process" in old:
        out["process"] = old["process"]
    if provider == "claude" and isinstance(inbox, str) and os.path.isabs(inbox):
        out["inbox"] = inbox  # the path only: relay never reads or keeps the session's messaging token
    return out


def _lock(path, wait_s):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    until = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= until:
                os.close(fd)
                return None
            time.sleep(0.01)


def _write(path, record):
    fd, temp = tempfile.mkstemp(prefix=".relay-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(record, f)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _read(stream):
    """The hook's input: until EOF, READ_DEADLINE_S or READ_CAP bytes. None when EOF never came."""
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return stream.read(READ_CAP)  # an in-memory stream (tests) cannot block
    chunks, size, until = [], 0, time.monotonic() + READ_DEADLINE_S
    while size < READ_CAP:
        left = until - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            return None  # the pipe stayed open: treat it as no input rather than hold the agent
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks).decode("utf-8", "replace")


def _walk(provider, event):
    """The agent process when this event calls for a walk (running-now D1): a dict when found, None when walked and
    not found, False when no walk was needed."""
    name = event.get("hook_event_name")
    try:
        with open(record_path(provider, event["session_id"])) as f:
            has = isinstance(json.load(f).get("process"), dict)
    except (OSError, ValueError, AttributeError):
        has = False
    if name not in WALK_EVENTS and has:
        return False
    return find_process(provider, _ps("pid=,ppid=,lstart=,args=", WALK_TIMEOUT_S), os.getpid())


def capture(provider, stream, now=None):
    """`relay hook <provider>`: invalid input, a busy lock or a storage failure must never disturb the agent."""
    try:
        text = _read(stream)
        if text is None:
            return 0
        event = json.loads(text)
        if provider not in WAITING or not isinstance(event, dict) or not isinstance(event.get("session_id"), str):
            return 0
        name = event.get("hook_event_name")
        found = []
        if name in NOTICE[provider]:
            try:
                from . import notices
                found = notices.merged_features(event["session_id"], event.get("cwd"))
            except Exception:
                found = []  # a notice is a courtesy; the record still gets written
        os.makedirs(folder(), mode=0o700, exist_ok=True)
        walked = _walk(provider, event)  # before the lock: ps must not hold up other sessions' hooks
        lock = _lock(os.path.join(folder(), ".lock"), 0.1)
        if lock is None:
            return 0
        tell = []
        try:
            path = record_path(provider, event["session_id"])
            try:
                with open(path) as f:
                    old = json.load(f)
            except (OSError, ValueError):
                old = None
            new = apply(old, provider, event, time.time() if now is None else now,
                        os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") if provider == "claude" else None)
            if new is not None and walked is not False:
                new.pop("process", None)  # walked and found nothing: the old process may be gone (D1)
                if walked:
                    new["process"] = walked
            if new is not None:
                told = old.get("notified") if isinstance(old, dict) and old.get("session_id") == new["session_id"] else None
                told = [k for k in told if isinstance(k, str)] if isinstance(told, list) else []
                tell = [f for f in found if f[0] not in told]
                if told or tell:
                    new["notified"] = told + [f[0] for f in tell]
                _write(path, new)
        finally:
            os.close(lock)
        if tell:
            from . import notices
            print(notices.render(name, tell), flush=True)
    except Exception:  # the sink's whole contract: nothing an agent can see
        pass
    return 0


def read_records():
    """Every readable, well-formed session record."""
    out = []
    try:
        names = [n for n in os.listdir(folder()) if n.endswith(".json")]
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(folder(), name)) as f:
                record = json.load(f)
        except (OSError, ValueError):
            continue
        if _valid(record) and isinstance(record.get("session_id"), str):
            out.append(record)
    return out


LOCK_WAIT_PRUNE_S = 1.0


def prune(now=None, keep_days=7):
    """Delete records not written for keep_days, re-reading each under the folder lock (R3)."""
    now = time.time() if now is None else now
    try:
        os.makedirs(folder(), mode=0o700, exist_ok=True)
        lock = _lock(os.path.join(folder(), ".lock"), LOCK_WAIT_PRUNE_S)
    except OSError:
        return 0
    if lock is None:
        return 0
    deleted = 0
    try:
        for name in os.listdir(folder()):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder(), name)
            try:
                with open(path) as f:
                    at = json.load(f).get("at")
            except (OSError, ValueError, AttributeError):
                continue  # unreadable records are left alone; the next capture replaces them
            if _number(at) and now - at > keep_days * 86400:
                try:
                    os.unlink(path)
                    deleted += 1
                except OSError:
                    pass
    finally:
        os.close(lock)
    return deleted
