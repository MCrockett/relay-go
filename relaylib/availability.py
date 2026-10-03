"""Is a provider able to review right now? Out of usage means: its weekly limit is used up (Codex logs it),
or a review just failed on a usage limit (remembered in ~/.relay/unavailable.json until the reset)."""
import datetime
import glob
import json
import os
import re
import time

from .config import relay_home

USAGE_LIMIT = re.compile(r"usage limit|rate limit|out of usage|quota|limit reached|insufficient credits|\b429\b",
                         re.I)
WEEK_MINUTES = 10080
TAIL_BYTES = 256 * 1024


def _codex_home():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key)
            if found is not None:
                return found
    return None


def codex_weekly():
    """(used_percent, resets_at, source_time) from Codex logs, or None. Time may be unknown."""
    files = sorted(glob.glob(os.path.join(_codex_home(), "sessions", "*", "*", "*", "*.jsonl")),
                   key=os.path.getmtime, reverse=True)
    for path in files[:5]:
        try:  # limits are logged on every turn: the end of the file is enough, however long the session
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                event = json.loads(line)
                limits = _find(event, "rate_limits") or {}
            except json.JSONDecodeError:
                continue
            for window in (limits.get("primary"), limits.get("secondary")):  # the weekly one may be either
                if isinstance(window, dict) and window.get("window_minutes") == WEEK_MINUTES \
                        and "used_percent" in window:
                    try:
                        from .usage import timestamp
                        source = timestamp(event["timestamp"]) if event.get("timestamp") else None
                        used, reset = float(window["used_percent"]), int(window.get("resets_at") or 0)
                        if not 0 <= used <= 100 or reset < 0:
                            continue
                        return used, reset, source
                    except (ValueError, TypeError, OverflowError):
                        continue
    return None


def _store():
    return os.path.join(relay_home(), "unavailable.json")


def _load():
    try:
        with open(_store()) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _when(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().strftime("%a %H:%M")


def is_usage_limit(error):
    return bool(USAGE_LIMIT.search(error or ""))


def record_out(provider, until=None):
    """Remember that a provider is out of usage: until its known reset, otherwise for an hour."""
    if until is None:
        weekly = codex_weekly() if provider == "codex" else None
        until = weekly[1] if weekly and weekly[1] > time.time() else int(time.time()) + 3600
    data = _load()
    data[provider] = until
    os.makedirs(relay_home(), exist_ok=True)
    with open(_store(), "w") as f:
        json.dump(data, f)


def blocked(provider, limits):
    """(True, reason) when the provider should not be asked to review now."""
    now = time.time()
    until = _load().get(provider, 0)
    if until > now:
        return True, f"out of usage until {_when(until)}"
    if provider == "codex":
        weekly = codex_weekly()
        stop = float(limits.get("weekly_stop_pct", 100))
        if weekly and weekly[0] >= stop and weekly[1] > now:
            return True, f"weekly limit {weekly[0]:.0f}% used until {_when(weekly[1])}"
    return False, ""
