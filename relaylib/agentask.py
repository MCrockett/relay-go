"""What a waiting agent last said, read live from the CLI's own log and never stored (waiting-visibility D3, D8)."""
import json
import os

from . import transcripts

WINDOW, EXCERPT, FULL = 1 << 20, 160, 4000


def _joined(parts, kind):
    texts = [p["text"] for p in parts if isinstance(p, dict) and p.get("type") == kind and isinstance(p.get("text"), str)]
    return "\n\n".join(t for t in texts if t.strip())


def _candidate(provider, record):
    """(source, text) when this log record is something the agent said to the owner, else None."""
    if not isinstance(record, dict):
        return None
    if provider == "claude":
        if record.get("type") == "system" and record.get("subtype") == "away_summary":
            text = record.get("content")
            return ("summary", text) if isinstance(text, str) and text.strip() else None
        message = record.get("message")
        if record.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("content"), list):
            text = _joined(message["content"], "text")
            return ("agent", text) if text else None
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if record.get("type") == "event_msg" and payload.get("type") == "task_complete":
        text = payload.get("last_agent_message")
        return ("agent", text) if isinstance(text, str) and text.strip() else None
    if (record.get("type") == "response_item" and payload.get("type") == "message"
            and payload.get("role") == "assistant" and isinstance(payload.get("content"), list)):
        text = _joined(payload["content"], "output_text")
        return ("agent", text) if text else None
    return None


def _tail_lines(path):
    """Complete lines in the last WINDOW bytes: a line cut by the window and a line still being written are left out."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(0, size - WINDOW)
        f.seek(start)
        data = f.read(WINDOW)
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]
    return lines[:-1]  # after the last newline: empty, or a partial line


def last_words(provider, session_id):
    """{source: "summary"|"agent", text} from the newest candidate in the session's parent log, or None."""
    try:
        files = [p for p in transcripts.files_for(provider, session_id)
                 if os.sep + "subagents" + os.sep not in p]
        if not files:
            return None
        newest = None
        for line in _tail_lines(max(files, key=os.path.getmtime)):
            try:
                found = _candidate(provider, json.loads(line))
            except ValueError:
                continue
            if found:
                newest = found
        return {"source": newest[0], "text": newest[1]} if newest else None
    except (OSError, ValueError):
        return None


def excerpt(text):
    flat = " ".join(text.split())
    return flat if len(flat) <= EXCERPT else flat[:EXCERPT] + "…"


def full(text):
    return text[:FULL]
