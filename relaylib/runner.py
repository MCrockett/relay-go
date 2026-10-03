"""Launch the reviewer CLI headless and read its final message (spec section 4)."""
import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

CLAUDE_READONLY_TOOLS = ",".join([
    "Read", "Grep", "Glob",
    "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)", "Bash(git status:*)",
    "Bash(gh pr view:*)", "Bash(gh pr diff:*)",
])


@dataclass
class RunResult:
    ok: bool
    text: str = ""
    usage: dict = field(default_factory=lambda: {"input": 0, "cached": 0, "output": 0})
    duration_s: float = 0.0
    error: str = ""
    timed_out: bool = False
    stderr_tail: str = ""


def build_command(spec, prompt, cwd):
    if spec.provider == "codex":
        cmd = [os.environ.get("RELAY_CODEX_BIN", "codex"), "exec", "--sandbox", "read-only", "--json",
               "-C", cwd, "-m", spec.model]
        if spec.effort:
            cmd += ["-c", f"model_reasoning_effort={spec.effort}"]
        return cmd + [prompt]
    cmd = [os.environ.get("RELAY_CLAUDE_BIN", "claude"), "-p", prompt, "--model", spec.model,
           "--output-format", "json", "--permission-mode", "dontAsk",
           "--allowedTools", CLAUDE_READONLY_TOOLS]
    if spec.effort:
        cmd += ["--effort", spec.effort]
    return cmd


def extract_codex(stdout):
    messages, usage, failure = [], None, ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, item = event.get("type"), event.get("item") or {}
        if kind == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
            messages.append(item["text"])
        elif kind == "turn.completed":
            u = event.get("usage") or {}
            usage = {"input": u.get("input_tokens", 0), "cached": u.get("cached_input_tokens", 0),
                     "output": u.get("output_tokens", 0)}
        elif kind in ("turn.failed", "error"):
            failure = json.dumps(event.get("error") or event.get("message") or event)[:500]
    if failure:
        return None, usage, f"codex reported: {failure}"
    if usage is None:
        return None, None, "codex run did not complete a turn"
    if not messages:
        return None, usage, "codex produced no final message"
    return messages[-1], usage, ""


def extract_claude(stdout):
    try:
        obj = json.loads(stdout)
    except json.JSONDecodeError:
        return None, None, "claude output is not JSON"
    u = obj.get("usage") or {}
    cached = u.get("cache_read_input_tokens", 0)
    usage = {"input": u.get("input_tokens", 0) + cached + u.get("cache_creation_input_tokens", 0),
             "cached": cached, "output": u.get("output_tokens", 0)}
    if obj.get("is_error"):
        return None, usage, f"claude reported an error: {str(obj.get('result'))[:300]}"
    if not obj.get("result"):
        return None, usage, "claude produced no final message"
    return obj["result"], usage, ""


def _kill_group(proc):
    """TERM the whole group, give the reviewer 5 seconds, then KILL whatever is left of the group,
    including descendants that ignore TERM after the parent has exited."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _stop(proc):
    """Kill the reviewer's process group, then collect output without waiting forever
    on a descendant that escaped the group and still holds a pipe."""
    _kill_group(proc)
    try:
        return proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        return "", ""


_ACTIVE, _ACTIVE_LOCK = set(), threading.Lock()  # reviewer processes this process started and still waits on
_STOPPED = threading.Event()  # set by stop_all: no reviewer starts after it


def stop_all():
    """Stop every running reviewer this process started, and refuse new ones (the dashboard is stopping)."""
    with _ACTIVE_LOCK:
        _STOPPED.set()
        procs = list(_ACTIVE)
    for proc in procs:
        _kill_group(proc)


def run_review(spec, prompt, cwd, env, timeout_s):
    if _STOPPED.is_set():
        return RunResult(False, error="reviews are stopping")
    cmd = build_command(spec, prompt, cwd)
    start = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except FileNotFoundError:
        return RunResult(False, error=f"{cmd[0]} not found")
    with _ACTIVE_LOCK:
        late = _STOPPED.is_set()
        if not late:
            _ACTIVE.add(proc)
    if late:  # stop_all ran between the check above and the spawn
        _stop(proc)
        return RunResult(False, duration_s=time.monotonic() - start, error="reviews are stopping")
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        out, err = _stop(proc)
        return RunResult(False, duration_s=time.monotonic() - start, timed_out=True,
                         error=f"timed out after {timeout_s:.0f}s", stderr_tail=(err or "")[-2000:])
    except KeyboardInterrupt:
        _stop(proc)
        return RunResult(False, duration_s=time.monotonic() - start, error="interrupted by the user")
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(proc)
    extract = extract_codex if spec.provider == "codex" else extract_claude
    text, usage, why = extract(out)
    if proc.returncode != 0:
        why = f"exit code {proc.returncode}" + (f"; {why}" if why else "")
    result = RunResult(ok=not why, text=text or "", duration_s=time.monotonic() - start,
                       error=why, stderr_tail=err[-2000:])
    if usage:
        result.usage = usage
    return result
