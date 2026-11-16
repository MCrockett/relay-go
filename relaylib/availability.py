"""Is a provider able to review right now? Out of usage means: its weekly limit is used up (Codex logs it), its
5-hour window is full (Codex logs it; Claude's status line saves it), or a review just failed on a usage limit
(remembered in ~/.relay/unavailable.json with the limit it hit, until relay tries again)."""
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
FIVE_HOUR_MINUTES = 300
TAIL_BYTES = 256 * 1024
STALE_S = 3600  # a 5-hour sample older than this is shown as stale (five-hour-usage D2)


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0  # removed since it was listed


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
    return codex_window(WEEK_MINUTES)


def _newest_codex_logs():
    from . import transcripts  # a snapshot build lists from its Codex index (dashboard-scan R7b)
    found = transcripts.index("codex")
    files = found["files"] if found is not None else glob.glob(
        os.path.join(_codex_home(), "sessions", "*", "*", "*", "*.jsonl"))
    return sorted(files, key=_mtime, reverse=True)[:5]


def codex_window(minutes):
    """(used_percent, resets_at, source_time) for the Codex limit window of this length, or None. A snapshot
    build reads it once and shares it with every repository's review choices (dashboard-scan R7b)."""
    from . import scancache
    return scancache.per_build(("codex_window", minutes),
                               lambda: _codex_window(scancache.per_build("codex_logs", _newest_codex_logs), minutes))


def _codex_window(files, minutes):
    for path in files:
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
            for window in (limits.get("primary"), limits.get("secondary")):  # either slot may hold it
                if isinstance(window, dict) and window.get("window_minutes") == minutes \
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


def _claude_snapshot():
    try:
        with open(os.path.join(relay_home(), "claude-usage.json")) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _claude_window(name):
    """(used_pct, resets_at, sampled_at) from the saved Claude status-line snapshot, or None."""
    from .usage import timestamp
    data = _claude_snapshot()
    try:
        window, sampled = data[name], timestamp(data["sampled_at"])
        used, reset = window["used_pct"], timestamp(window["resets_at"])
    except (TypeError, KeyError, ValueError):
        return None
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100 or reset <= 0:
        return None
    return used, reset, sampled


def _window(provider):
    return codex_window(FIVE_HOUR_MINUTES) if provider == "codex" else _claude_window("five_hour")


def five_hour(provider, now=None):
    """The 5-hour window as {used_pct, resets_at, sampled_at, state}; state is current, stale, reset or unknown."""
    now = time.time() if now is None else now
    found = _window(provider)
    if not found:
        return {"used_pct": None, "resets_at": None, "sampled_at": None, "state": "unknown"}
    used, reset, sampled = found
    reset = reset or None  # Codex may log 0: no reset time
    if reset is not None and now >= reset:
        return {"used_pct": 0, "resets_at": reset, "sampled_at": sampled, "state": "reset"}
    state = "stale" if sampled is None or now - sampled > STALE_S else "current"
    return {"used_pct": used, "resets_at": reset, "sampled_at": sampled, "state": state}


def _store():
    return os.path.join(relay_home(), "unavailable.json")


def _load():
    try:
        with open(_store()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _when(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().strftime("%a %H:%M")


def is_usage_limit(error):
    return bool(USAGE_LIMIT.search(error or ""))


def _entry(value):
    """One unavailable.json value as {until, limit, resets_at, at, full}, or None. A bare number is the old format.
    at is when the failure was saved; entries from before relay saved it use the file's modified time. full says
    the limit's window was at 100% then; entries from before relay saved it count only a weekly limit as full,
    since relay names that one only when it is used up."""
    if isinstance(value, dict):
        until, limit, reset, at = value.get("until"), value.get("limit"), value.get("resets_at"), value.get("at")
        full = value.get("full")
    else:
        until, limit, reset, at, full = value, "unknown", None, None, None
    if isinstance(until, bool) or not isinstance(until, (int, float)):
        return None
    if limit not in ("5-hour", "weekly"):
        limit = "unknown"
    if isinstance(reset, bool) or not isinstance(reset, (int, float)) or limit == "unknown":
        reset = None
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        at = _mtime(_store())
    if not isinstance(full, bool):
        full = limit == "weekly"
    return {"until": until, "limit": limit, "resets_at": reset, "at": at, "full": full}


def _back(provider, entry):
    """True when a usage sample taken after the failure was saved shows the limit it hit under 100% again, as after
    the owner resets a limit by hand. Only a limit that was full at the failure counts: an unknown or guessed one
    has nothing to check and waits for its retry time."""
    if entry["limit"] == "unknown" or not entry["full"]:
        return False
    if provider == "codex":
        window = codex_window(WEEK_MINUTES if entry["limit"] == "weekly" else FIVE_HOUR_MINUTES)
    elif provider == "claude":
        window = _claude_window("seven_day" if entry["limit"] == "weekly" else "five_hour")
    else:
        return False
    return bool(window) and window[2] is not None and window[2] > entry["at"] and window[0] < 100


def _cause(provider, now):
    """(limit, resets_at, full) of the limit a usage failure most likely hit, from this machine's latest samples.
    full is False when the window was under 100%: Codex's 5-hour limit is then a guess from its reset time."""
    if provider == "codex":
        weekly, five = codex_weekly(), codex_window(FIVE_HOUR_MINUTES)
        if weekly and weekly[0] >= 100 and weekly[1] > now:
            return "weekly", weekly[1], True
        if five and five[1] > now:
            return "5-hour", five[1], five[0] >= 100
    elif provider == "claude":
        for limit, name in (("weekly", "seven_day"), ("5-hour", "five_hour")):
            window = _claude_window(name)
            if window and window[0] >= 100 and window[1] > now:
                return limit, window[1], True
    return "unknown", None, False


def record_out(provider, until=None):
    """Remember that a provider is out of usage, which limit it hit, and until when relay leaves it alone.
    Codex: until the limit's reset (weekly only when used up, else the 5-hour reset). Claude, or no known reset:
    an hour."""
    now = time.time()
    limit, reset, full = _cause(provider, now)
    if until is None:
        until = reset if provider == "codex" and reset else int(now) + 3600
    data = _load()
    data[provider] = {"until": until, "limit": limit, "resets_at": reset, "at": now, "full": full}
    os.makedirs(relay_home(), exist_ok=True)
    with open(_store(), "w") as f:
        json.dump(data, f)


def blocked(provider, limits):
    """(True, reason) when the provider should not be asked to review now."""
    now = time.time()
    entry = _entry(_load().get(provider))
    if entry and entry["until"] > now and not _back(provider, entry):
        until, limit, reset = entry["until"], entry["limit"], entry["resets_at"]
        if limit == "unknown":
            return True, f"a review failed on a usage limit; relay tries again {_when(until)}"
        if reset is None or reset == until:
            return True, f"{limit} limit used up until {_when(until)}"
        return True, f"{limit} limit used up, resets {_when(reset)}; relay tries again {_when(until)}"
    if provider == "codex":
        weekly = codex_weekly()
        stop = float(limits.get("weekly_stop_pct", 100))
        if weekly and weekly[0] >= stop and weekly[1] > now:
            return True, f"weekly limit {weekly[0]:.0f}% used until {_when(weekly[1])}"
    five = five_hour(provider, now)
    if five["state"] in ("current", "stale") and five["used_pct"] >= 100 and five["resets_at"]:
        return True, f"5-hour limit used up until {_when(five['resets_at'])}"
    return False, ""
