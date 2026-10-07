"""Token usage from the CLIs' own session logs (writer-usage D1, D3, D6). Reads timestamps, models, usage
numbers, Claude message ids and git branches; never message text."""
import contextlib
import glob
import json
import os

from . import config
from .errors import RelayError
from .usage import timestamp

FORMAT_LINES = 200
CACHE, LOCK = "writer-usage.json", "writer-usage.lock"
VERSION, CHUNK = 1, 1 << 20


def claude_projects():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


def codex_sessions():
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")


def files_for(provider, session):
    """Every log file of one session. A Claude transcript sits under the folder of the cwd the session started
    in, not the repo, so every projects folder is searched."""
    if provider == "claude":
        root = claude_projects()
        found = glob.glob(os.path.join(root, "*", glob.escape(session) + ".jsonl"))
        found += glob.glob(os.path.join(root, "*", glob.escape(session), "subagents", "*.jsonl"))
    elif provider == "codex":
        found = glob.glob(os.path.join(codex_sessions(), "*", "*", "*", f"rollout-*-{glob.escape(session)}.jsonl"))
    else:
        found = []
    return sorted(found)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class FileState:
    """What one log file has given so far: its turns and the counts behind its D6 diagnostics."""

    def __init__(self, provider):
        self.provider, self.turns = provider, {}
        self.candidates = self.bad = self.malformed = self.lines = self.lines_since_record = self.events = 0
        self.had_valid, self.model, self.last_total = False, None, None

    def parse(self, lines):
        for line in lines:
            self.lines += 1
            self.lines_since_record += 1
            try:
                entry = json.loads(line)
            except ValueError:
                self.malformed += 1
                continue
            if isinstance(entry, dict):
                (self._claude if self.provider == "claude" else self._codex)(entry)

    def _ok(self):
        self.had_valid, self.lines_since_record = True, 0

    def _claude(self, e):
        """One turn per message id; a message's usage is repeated on each of its records, so the last one wins."""
        m = e.get("message")
        if e.get("type") != "assistant" or not isinstance(m, dict) or "usage" not in m:
            return
        if not m.get("id") or m.get("model") == "<synthetic>":
            return
        self.candidates += 1
        u = m["usage"]
        try:
            fields = [u[k] for k in ("input_tokens", "cache_read_input_tokens",
                                     "cache_creation_input_tokens", "output_tokens")]
            if not all(_count(v) for v in fields) or not isinstance(m.get("model"), str) or not m["model"]:
                raise ValueError("bad usage")
            at = timestamp(e["timestamp"])
        except (ValueError, KeyError, TypeError, AttributeError):
            self.bad += 1
            return
        inp, cread, cwrite, out = fields
        branch = e.get("gitBranch")
        self.turns[str(m["id"])] = {"at": at, "model": m["model"], "input": inp + cread + cwrite, "cached": cread,
                                    "output": out, "branch": branch if isinstance(branch, str) and branch else None}
        self._ok()

    def _codex(self, e):
        """Codex logs a running total; each increase is one turn. A drop (resume, restart) counts from zero."""
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if e.get("type") == "turn_context":
            self.candidates += 1
            if isinstance(p.get("model"), str) and p["model"]:
                self.model = p["model"]
                self._ok()
            else:
                self.bad += 1
            return
        if p.get("type") != "token_count" or p.get("info") is None:
            return
        self.candidates += 1
        try:
            t = p["info"]["total_token_usage"]
            total = [t["input_tokens"], t["cached_input_tokens"], t["output_tokens"]]
            if not all(_count(v) for v in total):
                raise ValueError("bad usage")
            at = timestamp(e["timestamp"])
        except (ValueError, KeyError, TypeError):
            self.bad += 1
            return
        self._ok()
        base = self.last_total if self.last_total and all(a >= b for a, b in zip(total, self.last_total)) \
            else [0, 0, 0]
        delta = [a - b for a, b in zip(total, base)]
        self.last_total = total
        if not any(delta):
            return
        self.events += 1
        self.turns[f"codex:{self.events}"] = {"at": at, "model": self.model or "unknown", "input": delta[0],
                                              "cached": delta[1], "output": delta[2], "branch": None}

    def problem(self):
        if self.lines and not self.had_valid:
            return "no usage records"
        found = []
        if self.bad:
            found.append(f"partial: {self.bad} of {self.candidates} usage records unreadable")
        if self.malformed:
            found.append(f"{self.malformed} lines are not JSON")
        if self.had_valid and self.lines_since_record >= FORMAT_LINES:
            found.append(f"format changed: {FORMAT_LINES} lines without usage records")
        return "; ".join(found) or None

    def to_json(self):
        return {k: getattr(self, k) for k in ("provider", "turns", "candidates", "bad", "malformed", "lines",
                                               "lines_since_record", "events", "had_valid", "model", "last_total")}

    @classmethod
    def from_json(cls, d):
        """Raises ValueError, KeyError or TypeError for any state this class could not have written (F3)."""
        if d["provider"] not in ("claude", "codex"):
            raise ValueError("bad provider")
        fs = cls(d["provider"])
        for k in fs.to_json():
            setattr(fs, k, d[k])
        counters = (fs.candidates, fs.bad, fs.malformed, fs.lines, fs.lines_since_record, fs.events)
        if not all(_count(v) for v in counters) or not isinstance(fs.had_valid, bool):
            raise ValueError("bad counters")
        if fs.model is not None and not isinstance(fs.model, str):
            raise ValueError("bad model")
        if fs.last_total is not None and not (isinstance(fs.last_total, list) and len(fs.last_total) == 3
                                             and all(_count(v) for v in fs.last_total)):
            raise ValueError("bad baseline")
        if not isinstance(fs.turns, dict):
            raise ValueError("bad turns")
        for key, turn in fs.turns.items():
            if not (isinstance(key, str) and isinstance(turn, dict)
                    and isinstance(turn.get("at"), (int, float)) and not isinstance(turn.get("at"), bool)
                    and isinstance(turn.get("model"), str)
                    and all(_count(turn.get(k)) for k in ("input", "cached", "output"))
                    and (turn.get("branch") is None or isinstance(turn["branch"], str))):
                raise ValueError("bad turn")
        return fs


def _valid_entry(entry, provider):
    if not isinstance(entry, dict) or not all(
            isinstance(entry.get(k), int) and not isinstance(entry.get(k), bool)
            for k in ("inode", "size", "mtime_ns", "offset")):
        raise ValueError("bad cache entry")
    if FileState.from_json(entry["state"]).provider != provider:
        raise ValueError("bad cache entry")
    return entry


def _usable(entry, provider):
    try:
        return _valid_entry(entry, provider) if entry else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None                                   # F3: a broken entry is read again from the start


def _load_cache():
    try:
        with open(os.path.join(config.relay_home(), CACHE)) as f:
            data = json.load(f)
        if (data.get("version") == VERSION and isinstance(data.get("files"), dict)
                and isinstance(data.get("sessions"), dict)):
            # a malformed session path list is dropped; its files are found again by files_for
            data["sessions"] = {k: v for k, v in data["sessions"].items()
                                if isinstance(v, list) and all(isinstance(x, str) for x in v)}
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": VERSION, "files": {}, "sessions": {}}


def _advance(path, entry, provider):
    """Parse the bytes after entry's offset in CHUNK-sized reads (F6); a shrunk or replaced file starts over."""
    st = os.stat(path)
    entry = _usable(entry, provider)
    if not entry or entry["inode"] != st.st_ino or st.st_size < entry["offset"]:
        entry = {"inode": st.st_ino, "size": 0, "mtime_ns": 0, "offset": 0, "state": FileState(provider).to_json()}
    fs, offset = FileState.from_json(entry["state"]), entry["offset"]
    if st.st_size > offset:
        with open(path, "rb") as f:
            f.seek(offset)
            carry, left = b"", st.st_size - offset    # read only up to the size seen by stat
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                data = carry + chunk
                end = data.rfind(b"\n") + 1          # a partial last line waits for the next chunk or refresh
                if end:
                    fs.parse(data[:end].decode("utf-8", "replace").splitlines())
                    offset += end
                carry = data[end:]
    return {"inode": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "offset": offset,
            "state": fs.to_json()}


def read_sessions(sessions):
    """Turns of each (provider, session), every file of a session merged and sorted by time. The cache in relay
    home makes a refresh read only what was appended. When its lock is busy or relay home is not writable, the
    work is done in memory and notes says so (D5)."""
    out = {"turns": {}, "unreadable": {}, "notes": []}
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(config.write_lock(name=LOCK))
            saving = True
        except (RelayError, OSError):
            saving = False
            out["notes"].append("usage cache not saved")
        cache = _load_cache()
        for provider, session in sorted(sessions):
            key = f"{provider}:{session}"
            known = sorted(set(cache["sessions"].get(key) or []) | set(files_for(provider, session)))
            cache["sessions"][key] = known
            if not known:
                out["unreadable"][(provider, session)] = "log not found"
                continue
            turns, problems = [], []
            for path in known:
                try:
                    cache["files"][path] = _advance(path, cache["files"].get(path), provider)
                except OSError:
                    problems.append("log not found")          # gone now: its cached turns stay counted
                entry = _usable(cache["files"].get(path), provider)
                if entry:
                    fs = FileState.from_json(entry["state"])
                    turns += fs.turns.values()
                    if fs.problem():
                        problems.append(fs.problem())
            out["turns"][(provider, session)] = sorted(turns, key=lambda t: t["at"])
            if problems:
                out["unreadable"][(provider, session)] = "; ".join(sorted(set(problems)))
        if saving:
            try:
                config.atomic_write(os.path.join(config.relay_home(), CACHE), json.dumps(cache), 0o600)
            except OSError:
                out["notes"].append("usage cache not saved")
    return out
