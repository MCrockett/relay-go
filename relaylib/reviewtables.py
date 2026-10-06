"""Review tables beyond config.toml: temporary tables in ~/.relay/review-until.json, their end times, the change
log in ~/.relay/roles-log.jsonl, and the writes the CLI and the dashboard share (models-tab)."""
import datetime as dt
import hashlib
import json
import os
import re
import time
from zoneinfo import ZoneInfo

from . import config
from .errors import RelayError

AUTHORS = config.PROVIDERS + ("owner",)
MAX_S = 7 * 86400
FILE, LOG = "review-until.json", "roles-log.jsonl"
LOG_TAIL = 64 * 1024


class Stale(RelayError):
    """The owner config or the timed file changed since the caller looked (D8)."""


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


def path():
    return os.path.join(config.relay_home(), FILE)


def read(now=None):
    """(active timed tables by author, problems). Never raises: a bad file or entry is reported and ignored (R3)."""
    now = time.time() if now is None else now
    try:
        with open(path()) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as e:
        return {}, [f"{FILE} ignored: {e}"]
    tables, problems = {}, []
    for author, table in data.items():
        try:
            if author not in AUTHORS:
                raise ValueError("unknown author")
            until, entries = table["until"], table["entries"]
            if isinstance(until, bool) or not isinstance(until, (int, float)):
                raise ValueError("bad end time")
            if not isinstance(entries, list) or not entries:
                raise ValueError("no reviewers")
            for entry in entries:
                config.parse_model_spec(entry)
        except (ValueError, KeyError, TypeError, AttributeError, RelayError) as e:
            problems.append(f"{FILE}: the temporary table for {author} was ignored ({e})")
            continue
        if until > now:
            tables[author] = table
    return tables, problems


def _bytes(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def revision():
    """What the owner saw: the owner config and the timed file; a missing file counts as empty (R9)."""
    digest = hashlib.sha256(_bytes(config.config_path()))
    digest.update(b"\0" + _bytes(path()))
    return digest.hexdigest()[:16]


def _append_log(rows):
    os.makedirs(config.relay_home(), exist_ok=True)
    fd = os.open(os.path.join(config.relay_home(), LOG), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _log(rows):
    """Append after the write succeeded; a failure leaves the write standing with a warning (R2a)."""
    try:
        _append_log(rows)
        return ""
    except OSError as e:
        return f"\nrelay: warning: the change was saved but not logged ({e})"


def latest():
    """The newest log row per author, from the tail of the log. Missing or bad lines are skipped."""
    try:
        with open(os.path.join(config.relay_home(), LOG), "rb") as f:
            f.seek(max(0, os.path.getsize(f.name) - LOG_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        try:
            row = json.loads(line)
            if row["author"] in AUTHORS and isinstance(row["at"], (int, float)):
                out[row["author"]] = row
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _check(seen):
    if seen is not None and seen != revision():
        raise Stale("changed since you looked; review the fresh table before saving again")


def _write_timed(tables):
    config.atomic_write(path(), json.dumps(tables, indent=2, sort_keys=True) + "\n", 0o600)


def _permanent_text(author):
    prefs = config.review_preferences(config.load(timed=False), author)
    return " -> ".join(f"{p.provider}:{p.model}" + (f"@{p.effort}" if p.effort else "") for p in prefs)


def save(author, table, entries, until, by, seen=None, now=None):
    """Write a permanent (config.toml) or timed (review-until.json) review table. CLI and dashboard (R1, R4, R10)."""
    if author not in AUTHORS:
        raise RelayError(f"review.{author}: the author must be claude, codex or owner")
    if table not in ("permanent", "timed"):
        raise RelayError("table must be permanent or timed")
    if table == "timed" and not until:
        raise RelayError("a temporary table needs an end time (until)")
    if table == "permanent" and until:
        raise RelayError("a permanent table has no end time; use a temporary table for that")
    if not isinstance(entries, list):
        raise RelayError("entries must be a list of provider:model[@effort]")
    entries = config.validate_reviewers(entries)
    now = time.time() if now is None else now
    with config.write_lock():
        _check(seen)
        tz = config.load(timed=False)["limits"]["timezone"]
        if table == "timed":
            end = parse_until(until, tz, now)
            tables = read(now)[0]
            tables[author] = {"entries": entries, "until": end, "set_at": int(now), "by": by}
            _write_timed(tables)
            message = (f"review.{author} = {', '.join(entries)} until {when(end, tz)} (temporary; "
                       f"then {_permanent_text(author)})")
        else:
            end = None
            config._set_role(config.config_path(), f"review.{author}", ", ".join(entries))
            message = f"review.{author} = {', '.join(entries)} in {config.config_path()} (applies to every repo)"
            active = read(now)[0].get(author)
            if active:
                message += f"; the temporary table still applies until {when(active['until'], tz)}"
        return message + _log([{"at": int(now), "author": author, "table": table, "entries": entries,
                                "until": end, "by": by}])


def end(author, by, seen=None, now=None):
    """End timed tables now: one author or all (R5, R11). Nothing to end is not an error."""
    if author != "all" and author not in AUTHORS:
        raise RelayError(f"review.{author}: the author must be claude, codex, owner or all")
    now = time.time() if now is None else now
    with config.write_lock():
        _check(seen)
        tables = read(now)[0]
        ended = [a for a in (AUTHORS if author == "all" else (author,)) if a in tables]
        if not ended:
            return "no temporary tables" if author == "all" else f"no temporary table for {author}"
        for a in ended:
            del tables[a]
        _write_timed(tables)
        message = "; ".join(f"review.{a} is back to {_permanent_text(a)}" for a in ended)
        return message + _log([{"at": int(now), "author": a, "table": "ended", "entries": None, "until": None,
                                "by": by} for a in ended])
