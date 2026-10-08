"""Where the owner left off: the newest sessions per project, in any state, and how to resume each
(where-i-left-off D1 to D8). Read-only and local: session records as the hooks wrote them, last messages read live
and never stored (D12)."""
import os
import shlex
import time

from . import agentask, health, othersessions, sessions, status

LIMIT = 3
QUIET_S, PROCESS_QUIET_S = 600, 7200  # running-now D3: without a process, and with a live one
COMMAND = {"claude": "claude --resume {}", "codex": "codex resume {}"}
EMPTY = "No sessions to show. relay sees sessions started after its hooks were installed, for 7 days."


def resume_line(provider, session_id, folder):
    """The command that resumes a session from its own folder (D7)."""
    command = COMMAND[provider].format(shlex.quote(session_id))
    if not folder or not os.path.isdir(folder):
        return command
    real, home = os.path.realpath(folder), os.path.realpath(os.path.expanduser("~"))
    if real == home:
        where = "~"
    elif real.startswith(home + os.sep):
        where = "~/" + shlex.quote(real[len(home) + 1:])
    else:
        where = shlex.quote(real)
    return f"cd {where} && {command}"


def _tools(record):
    out = []
    for p in record.get("pending") or []:
        if p["tool_name"] not in out:
            out.append(p["tool_name"])
    return out


def is_running(record, alive, now):
    """running-now D3: a working session whose process is alive (and not quiet for over 2 hours), or, without a
    process to check, one written in the last 10 minutes."""
    if record["state"] != "working":
        return False
    if record.get("process") and alive is not None:
        return record["session_id"] in alive and now - record["at"] <= PROCESS_QUIET_S
    return now - record["at"] <= QUIET_S


def _shown_state(record, alive):
    """running-now D4: a working session that is not running is stopped when its process is known dead."""
    if record["state"] == "working" and record.get("process") and alive is not None and record["session_id"] not in alive:
        return "stopped"
    return record["state"]


def _entry(record, checkout, marks, alive=None):
    words = agentask.last_words(record["provider"], record["session_id"])
    tools = _tools(record)
    if not words and not (record["state"] == "permission" and tools):
        return None  # never said anything and asks for nothing (D2)
    return {"provider": record["provider"], "session_id": record["session_id"], "folder": record.get("cwd"),
            "checkout": checkout, "state": record["state"], "shown_state": _shown_state(record, alive),
            "since": record["since"], "at": record["at"],
            "pending_tools": tools,
            "excerpt": {"source": words["source"], "text": agentask.excerpt(words["text"])} if words else None,
            "feature": marks.get(record["session_id"]),
            "resume": resume_line(record["provider"], record["session_id"], record.get("cwd"))}


def _placed(records, root):
    """[(record, project, checkout)] for the owner's sessions (D1, D3); one failing session is skipped (F4)."""
    try:
        checkouts = status.checkouts(root)
    except Exception:
        checkouts = []
    out = []
    for r in records:
        try:
            if not agentask.interactive(r["provider"], r["session_id"]):
                continue  # an automated run, such as a relay review (D1)
            out.append((r,) + othersessions.place(r.get("cwd"), checkouts))
        except Exception:  # F4: one session never hides the others
            continue
    return out


def running(records, marks, root, alive, now):
    """running-now D5: the owner's running sessions, newest activity first."""
    out = []
    for r, project, checkout in _placed([r for r in records if is_running(r, alive, now)], root):
        try:
            words = agentask.last_words(r["provider"], r["session_id"])
            out.append({"provider": r["provider"], "session_id": r["session_id"], "project": project,
                        "folder": r.get("cwd"), "checkout": checkout, "state": "working", "pending_tools": [],
                        "since": r["since"], "at": r["at"], "feature": marks.get(r["session_id"]),
                        "excerpt": ({"source": words["source"], "text": agentask.excerpt(words["text"])}
                                    if words else None),
                        "resume": resume_line(r["provider"], r["session_id"], r.get("cwd"))})
        except Exception:  # F2
            continue
    return sorted(out, key=lambda e: (-e["at"], e["session_id"]))


def projects(records, marks, root, limit=LIMIT, alive=None, now=None):
    """[{project, last_active, more, sessions}] newest first (D3, D5); limit None shows every session. Running
    sessions are left out: they are in Running now (running-now D4)."""
    now = time.time() if now is None else now
    groups = {}
    for r, project, checkout in _placed([r for r in records if not is_running(r, alive, now)], root):
        groups.setdefault(project, []).append((r, checkout))
    out = []
    for project, members in groups.items():
        members.sort(key=lambda m: (-m[0]["at"], m[0]["session_id"]))
        shown, walked = [], 0
        for r, checkout in members:
            if limit is not None and len(shown) >= limit:
                break
            walked += 1
            try:
                entry = _entry(r, checkout, marks, alive)
            except Exception:  # F4
                continue
            if entry:
                shown.append(entry)
        if shown:
            out.append({"project": project, "last_active": shown[0]["at"], "more": len(members) - walked,
                        "sessions": shown})
    return sorted(out, key=lambda p: (-p["last_active"], p["project"]))


def state_word(entry):
    state = entry.get("shown_state", entry["state"])
    if state == "permission":
        return "needs approval: " + (", ".join(entry["pending_tools"]) or "a tool")
    return {"waiting": "waiting on you", "working": "was working", "stopped": "stopped", "ended": "ended"}[state]


def render(found, now):
    if not found:
        return EMPTY
    blocks = []
    for p in found:
        lines = [f"{p['project']} · last active {health.age(now - p['last_active'])} ago"]
        for e in p["sessions"]:
            line = f"  {e['provider']} · {state_word(e)} · {health.age(now - e['at'])} ago"
            if e["checkout"]:
                line += f" · {e['checkout']}"
            if e["feature"]:
                line += f" · feature {e['feature']['slug']}" + (" (done)" if e["feature"]["done"] else "")
            lines.append(line)
            if e["excerpt"]:
                label = "Summary" if e["excerpt"]["source"] == "summary" else "Agent"
                lines.append(f"    {label}: {e['excerpt']['text']}")
            lines.append(f"    Resume: {e['resume']}")
        if p["more"]:
            lines.append(f"  Up to {p['more']} more: relay left --all")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def cmd_left(args):
    root = status.projects_root()
    try:
        records = sessions.read_records()
    except Exception:  # F4: no records
        records = []
    try:
        marks = status.local_marks(root)
    except Exception:  # F5: sessions without feature marks
        marks = {}
    print(render(projects(records, marks, root, None if args.all else LIMIT), time.time()))
