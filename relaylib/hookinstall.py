"""Add relay's session hooks to the owner's Claude Code and Codex config (spec session-health R10, R11)."""
import datetime
import json
import os
import shutil
import tempfile

from . import sessions
from .errors import RelayError

EVENTS = {
    "claude": [("PermissionRequest", None), ("Stop", None), ("Notification", "idle_prompt"),
               ("UserPromptSubmit", None), ("SessionStart", None), ("PostToolUse", None), ("SessionEnd", None)],
    "codex": [("PermissionRequest", None), ("Stop", None), ("Interrupt", None), ("UserPromptSubmit", None),
              ("PostToolUse", None), ("SessionEnd", None)],
}
SHORT = {("codex", "Interrupt"), ("codex", "SessionEnd"), ("claude", "SessionEnd")}  # at most 3 seconds
TRUST = "Codex runs new hooks only after you trust them: open Codex and review them with /hooks."


def path_for(provider):
    return os.path.expanduser("~/.claude/settings.json" if provider == "claude" else "~/.codex/hooks.json")


def command(provider):
    return f"~/.local/bin/relay hook {provider}"


def _load(path):
    """(text or None, data): None text when the file does not exist."""
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        return None, {}
    except OSError as e:
        raise RelayError(f"cannot read {path}: {e}")
    try:
        data = json.loads(text)
    except ValueError:
        raise RelayError(f"{path} is not valid JSON")
    if not isinstance(data, dict):
        raise RelayError(f"{path} is not a JSON object")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(v, list) for v in hooks.values()):
        raise RelayError(f"{path} has a hooks value relay does not understand")
    return text, data


def _present(groups, matcher, cmd):
    for group in groups:
        if isinstance(group, dict) and group.get("matcher") == matcher:
            if any(isinstance(h, dict) and h.get("command") == cmd for h in group.get("hooks") or []):
                return True
    return False


def configured(provider, path=None):
    try:
        _, data = _load(path or path_for(provider))
    except RelayError:
        return []
    hooks, cmd = data.get("hooks", {}), command(provider)
    return [event for event, matcher in EVENTS[provider] if _present(hooks.get(event, []), matcher, cmd)]


def install_file(provider, path, stamp):
    """Add the missing hook entries to one file; returns the events added. Raises RelayError, file untouched."""
    text, data = _load(path)
    hooks, cmd, added = data.setdefault("hooks", {}), command(provider), []
    for event, matcher in EVENTS[provider]:
        groups = hooks.setdefault(event, [])
        if _present(groups, matcher, cmd):
            continue
        group = {"hooks": [{"type": "command", "command": cmd, "timeout": 3 if (provider, event) in SHORT else 5}]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        groups.append(group)
        added.append(event)
    if not added:
        return []
    folder, temp = os.path.dirname(path), None
    try:
        os.makedirs(folder, exist_ok=True)
        if text is not None:
            shutil.copy2(path, f"{path}.relay-backup-{stamp}")
    except OSError as e:
        raise RelayError(f"cannot back up {path}: {e}")
    try:
        fd, temp = tempfile.mkstemp(prefix=".relay-", dir=folder)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        if text is not None:
            shutil.copymode(path, temp)
        if _current(path) != text:  # checked last, right before the replace, so no edit is overwritten
            raise RelayError(f"{path} changed while installing; run it again")
        os.replace(temp, path)
    except OSError as e:
        raise RelayError(f"cannot write {path}: {e}")
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
    return added


def _current(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return None


def install(stamp=None):
    stamp = stamp or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = []
    for provider in EVENTS:
        path = path_for(provider)
        try:
            added = install_file(provider, path, stamp)
            lines.append(f"{provider}: added {', '.join(added)} to {path}" if added
                         else f"{provider}: already installed in {path}")
        except (RelayError, OSError) as e:
            lines.append(f"{provider}: refused {path}: {e}")
    lines.append(TRUST)
    return lines


def status_lines():
    newest = {}
    for record in sessions.read_records():
        newest[record["provider"]] = max(newest.get(record["provider"], 0), record["at"])
    lines = []
    for provider, events in EVENTS.items():
        have = configured(provider)
        missing = [event for event, _ in events if event not in have]
        seen = (f"last event {datetime.datetime.fromtimestamp(newest[provider]).strftime('%b %d %H:%M')}"
                if provider in newest else "no events seen yet")
        lines.append(f"{provider}: configured {', '.join(have) or 'none'}"
                     + (f"; missing {', '.join(missing)}" if missing else "") + f"; {seen}")
    return lines
