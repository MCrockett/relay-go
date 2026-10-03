"""Local usage samples and daily carry-forward budgets. Percentages are weekly points."""
import contextlib
import datetime as dt
import fcntl
import json
import math
import os
import tempfile
import time
from zoneinfo import ZoneInfo

from . import availability
from .config import relay_home

WEEK = 7 * 86400


def timestamp(value):
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp needs a timezone")
        return parsed.timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("invalid timestamp")
    return float(value)


def atomic_write(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".relay-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            f.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextlib.contextmanager
def locked(timeout=None):
    os.makedirs(relay_home(), exist_ok=True)
    with open(os.path.join(relay_home(), "usage.lock"), "a") as f:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | (fcntl.LOCK_NB if deadline is not None else 0))
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("usage samples are busy")
                time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read():
    rows = []
    try:
        with open(os.path.join(relay_home(), "usage.jsonl")) as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if row["provider"] not in ("claude", "codex"):
                        continue
                    row["sampled_at"] = timestamp(row["sampled_at"])
                    row["resets_at"] = timestamp(row["resets_at"])
                    used = row["used_pct"]
                    if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or used < 0:
                        continue
                    rows.append(row)
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return rows


def append(sample, timeout=None, dedupe=False):
    with locked(timeout):
        if dedupe:
            previous = next((s for s in reversed(read()) if s["provider"] == sample["provider"]), None)
            if previous == sample:
                return
        path = os.path.join(relay_home(), "usage.jsonl")
        # Separate a truncated tail from the next complete record in the same write.
        prefix = ""
        if os.path.exists(path) and os.path.getsize(path):
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                prefix = "" if f.read(1) == b"\n" else "\n"
        with open(path, "a") as f:
            f.write(prefix + json.dumps(sample, sort_keys=True) + "\n")


def prune(now=None):
    cutoff = (time.time() if now is None else now) - 35 * 86400
    with locked():
        rows = [s for s in read() if s["sampled_at"] >= cutoff]
        atomic_write(os.path.join(relay_home(), "usage.jsonl"),
                     "".join(json.dumps(s, sort_keys=True) + "\n" for s in rows))


def record_codex_sample():
    weekly = availability.codex_weekly()
    if weekly and weekly[2] is not None:
        append({"provider": "codex", "used_pct": weekly[0], "resets_at": weekly[1],
                "sampled_at": weekly[2]}, dedupe=True)


def budget(provider, cfg, now=None):
    now = time.time() if now is None else now
    limits = cfg["limits"]
    zone = ZoneInfo(limits["timezone"])
    today = dt.datetime.fromtimestamp(now, zone)
    midnight = today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    samples = sorted((s for s in read() if s["provider"] == provider and s["sampled_at"] <= now),
                     key=lambda s: s["sampled_at"])
    result = {"state": "no-baseline", "meter": None, "used_pct": None}
    if not samples:
        return result
    latest = samples[-1]
    result.update(used_pct=latest["used_pct"], sampled_at=latest["sampled_at"], resets_at=latest["resets_at"])
    if now >= latest["resets_at"]:
        result["used_pct"] = 0
        return result
    start = latest["resets_at"] - WEEK
    day = max(1, min(7, (today.date() - dt.datetime.fromtimestamp(start, zone).date()).days + 1))
    result["day_index"] = day
    baseline = next((s for s in reversed(samples) if start <= s["sampled_at"] < midnight
                     and s["resets_at"] == latest["resets_at"]), None)
    if baseline:
        allowance = limits["daily_share_pct"] * day - baseline["used_pct"]
        used = max(0, latest["used_pct"] - baseline["used_pct"])
        meter = used / allowance * 100 if allowance > 0 else None
        result.update(allowance=allowance, used_today=used, meter=meter,
                      state="over" if meter is None else "red" if meter >= 125 else "amber" if meter >= 100 else "ok")
    if now - latest["sampled_at"] > limits["budget_stale_hours"] * 3600:
        result["state"] = "stale"
    return result


def capture_claude(stream):
    """Status-line sink: invalid input and storage failures must never disrupt its caller."""
    try:
        limits = json.load(stream)["rate_limits"]
        captured = {"sampled_at": time.time()}
        for name in ("seven_day", "five_hour"):
            window = limits[name]
            used = window["used_percentage"]
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
                return 0
            reset = timestamp(window["resets_at"])
            if reset <= 0:
                return 0
            captured[name] = {"used_pct": used, "resets_at": reset}
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    try:
        atomic_write(os.path.join(relay_home(), "claude-usage.json"), json.dumps(captured) + "\n")
    except OSError:
        pass
    try:
        append({"provider": "claude", "sampled_at": captured["sampled_at"], **captured["seven_day"]},
               timeout=0.100)
    except OSError:
        pass
    return 0
