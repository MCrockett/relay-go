"""Review tables beyond config.toml: temporary tables in ~/.relay/review-until.json, their end times, the change
log in ~/.relay/roles-log.jsonl, and the writes the CLI and the dashboard share (models-tab)."""
import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

from . import config
from .errors import RelayError

AUTHORS = config.PROVIDERS + ("owner",)
MAX_S = 7 * 86400


def when(epoch, tz_name):
    return dt.datetime.fromtimestamp(epoch, ZoneInfo(tz_name)).strftime("%a %H:%M")


def iso(epoch, tz_name):
    return dt.datetime.fromtimestamp(epoch, ZoneInfo(tz_name)).isoformat(timespec="minutes")


def parse_until(text, tz_name, now=None):
    """End time as epoch seconds: HH:MM (next occurrence in tz_name) or ISO 8601 with a zone (R1, D12)."""
    now = time.time() if now is None else now
    zone, text = ZoneInfo(tz_name), (text or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if match:
        hour, minute = int(match[1]), int(match[2])
        if hour > 23 or minute > 59:
            raise RelayError(f"{text} is not a time of day")
        today = dt.datetime.fromtimestamp(now, zone).date()
        for day in (today, today + dt.timedelta(days=1)):
            wall = dt.datetime.combine(day, dt.time(hour, minute))
            local = wall.replace(tzinfo=zone, fold=0)  # fold=0: a time that occurs twice means the first
            if local.timestamp() > now:
                break
        if local.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
            raise RelayError(f"{text} does not occur on {day} in {tz_name}: a daylight-saving change skips it")
        end = local.timestamp()
    else:
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            raise RelayError(f"until must be HH:MM or an ISO date-time with a time zone, not {text!r}")
        if parsed.tzinfo is None:
            raise RelayError(f"{text} needs a time zone, e.g. 2026-10-07T23:00-04:00")
        end = parsed.timestamp()
    if end <= now:
        raise RelayError(f"until {text} is in the past")
    if end - now > MAX_S:
        raise RelayError(f"until {text} is more than 7 days away; a temporary table lasts at most 7 days")
    return int(end)
