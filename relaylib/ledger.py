"""~/.relay/ledger.jsonl: one line per reviewer run relay launched (spec section 9)."""
import datetime
import json
import os

from .config import relay_home
from .errors import RelayError


def _path():
    return os.path.join(relay_home(), "ledger.jsonl")


def append(entry):
    os.makedirs(relay_home(), exist_ok=True)
    row = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), **entry}
    with open(_path(), "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def parse_since(text):
    try:
        n, unit = int(text[:-1]), text[-1]
    except (ValueError, IndexError):
        raise RelayError("--since takes Nd or Nh, for example 7d")
    if unit == "d":
        return datetime.timedelta(days=n)
    if unit == "h":
        return datetime.timedelta(hours=n)
    raise RelayError("--since takes Nd or Nh, for example 7d")


def read(since=None):
    if not os.path.exists(_path()):
        return []
    cutoff = datetime.datetime.now().astimezone() - since if since else None
    rows = []
    with open(_path()) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff and datetime.datetime.fromisoformat(row["at"]) < cutoff:
                continue
            rows.append(row)
    return rows


def summarize(rows):
    groups = {}
    for row in rows:
        key = (row.get("repo", "?"), row.get("role", "?"), f"{row.get('provider')}:{row.get('model')}")
        g = groups.setdefault(key, {"runs": 0, "input": 0, "cached": 0, "output": 0, "seconds": 0.0})
        g["runs"] += 1
        for k in ("input", "cached", "output"):
            g[k] += row.get(k, 0)
        g["seconds"] += row.get("duration_s", 0.0)
    return groups
