"""Notes from the owner to a session (session-notify D1 to D5): one log per session under ~/.relay/notes, handed to
the session through its inbox socket (Claude) or by relay's hook at its next prompt or tool call. Local only."""
import contextlib
import hashlib
import json
import os
import secrets
import socket
import stat
import tempfile
import time

from . import sessions
from .config import relay_home
from .errors import RelayError

PREFIX = "Note from the owner, sent from the relay dashboard:"
MAX_CHARS = 2000
KEEP_S = 7 * 86400
SEND_WAIT_S, TAKE_WAIT_S, POST_TIMEOUT_S = 2.0, 0.1, 2.0
STATUSES = {"queued", "delivered", "posted"}
NOT_RECORDED = "Posted to the session, but relay could not record it."


class Conflict(RelayError):
    """The note cannot be removed any more; `notes` is the session's log as it is now."""

    def __init__(self, message, notes):
        super().__init__(message)
        self.notes = notes


def folder():
    return os.path.join(relay_home(), "notes")


def log_path(provider, session_id):
    """Named like session records, so no session id can reach outside the folder (D5)."""
    return os.path.join(folder(), hashlib.sha1(f"{provider}\n{session_id}".encode()).hexdigest() + ".json")


def clean(text):
    if not isinstance(text, str):
        raise RelayError("text must be a string")
    if not text.strip():
        raise RelayError("a note cannot be empty")
    if len(text) > MAX_CHARS:
        raise RelayError(f"a note can be at most {MAX_CHARS:,} characters")
    return text


def _note(n):
    return (isinstance(n, dict) and isinstance(n.get("id"), str) and isinstance(n.get("text"), str)
            and isinstance(n.get("at"), (int, float)) and n.get("status") in STATUSES
            and isinstance(n.get("status_at"), (int, float))
            and isinstance(n.get("prefix", ""), str))


def _load(path):
    """The log's notes; [] when there is none. A log that cannot be read raises."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        raise ValueError("the note log is malformed")
    return [n for n in data if _note(n)]


def _fresh(notes, now):
    return [n for n in notes if now - n["at"] <= KEEP_S]


def _prepare(notes):
    """A complete, synced temporary copy of a log, ready to rename into place."""
    fd, temp = tempfile.mkstemp(prefix=".note-", dir=folder())
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(notes, f)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _unlink(temp)
        raise
    return temp


def _unlink(path):
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


def _save(path, notes):
    """Replace the log; an empty log is removed (D5)."""
    if not notes:
        _unlink(path)
        return
    temp = _prepare(notes)
    try:
        os.replace(temp, path)
    finally:
        _unlink(temp)


def _acquire(wait_s):
    """The notes lock's descriptor, or None when it stayed busy for wait_s (D5: one lock for every change)."""
    os.makedirs(folder(), mode=0o700, exist_ok=True)
    return sessions._lock(os.path.join(folder(), ".lock"), wait_s)


@contextlib.contextmanager
def _locked(wait_s):
    fd = _acquire(wait_s)
    if fd is None:
        raise RelayError("notes are busy right now; try again")
    try:
        yield
    finally:
        os.close(fd)


def line(text, prefix=None):
    """The one line the inbox socket takes (D3). `prefix` names who passed the note on (mobile-hub D5)."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": (prefix or PREFIX) + "\n" + text}}) + "\n"


def post(inbox, text, prefix=None):
    """True when the session's inbox socket accepted the note (D3); False for anything else (D4)."""
    try:
        st = os.lstat(inbox)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            return False  # a symlink, a regular file or someone else's socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(POST_TIMEOUT_S)
            s.connect(inbox)
            s.sendall(line(text, prefix).encode())
        return True
    except (OSError, ValueError):
        return False


def send(provider, session_id, text, inbox=None, now=None, prefix=None):
    """(note, message): the whole send under the notes lock (plan: send is one locked operation). Every write that
    can fail is prepared before the post, so a storage failure stops the send with nothing posted (F3); only the
    final rename can fail after a post, and then the note is left out of the log (F5). `inbox` is None when the
    session must not be posted to (Codex, no recorded socket, or not running)."""
    text = clean(text)
    now = time.time() if now is None else now
    with _locked(SEND_WAIT_S):
        path = log_path(provider, session_id)
        notes = _fresh(_load(path), now)
        note = {"id": secrets.token_hex(8), "text": text, "at": now, "status": "queued", "status_at": now}
        if prefix:
            note["prefix"] = prefix
        posted = dict(note, status="posted")
        temps = [_prepare(notes + [note])]
        try:
            temps.append(_prepare(notes + [posted]))
            ok = provider == "claude" and bool(inbox) and post(inbox, text, prefix)
            try:
                os.replace(temps[1] if ok else temps[0], path)
            except OSError:
                if ok:
                    return posted, NOT_RECORDED
                raise
            return (posted if ok else note), None
        finally:
            for temp in temps:
                _unlink(temp)


def remove(provider, session_id, note_id, now=None):
    """The session's notes after removing one queued note; Conflict when it is not queued or not there."""
    now = time.time() if now is None else now
    with _locked(SEND_WAIT_S):
        path = log_path(provider, session_id)
        notes = _fresh(_load(path), now)
        match = next((n for n in notes if n["id"] == note_id), None)
        if match is None or match["status"] != "queued":
            raise Conflict("That note was already handed to the session or is gone.", newest_first(notes))
        notes.remove(match)
        _save(path, notes)
        return newest_first(notes)


def take(provider, session_id, now=None):
    """The queued notes, marked delivered, for the hook to print (D5). [] when the lock is busy: they stay queued."""
    now = time.time() if now is None else now
    if not os.path.isdir(folder()):
        return []
    fd = _acquire(TAKE_WAIT_S)
    if fd is None:
        return []
    try:
        path = log_path(provider, session_id)
        notes = _fresh(_load(path), now)
        queued = [n for n in notes if n["status"] == "queued"]
        if not queued:
            return []
        for n in queued:
            n.update(status="delivered", status_at=now)
        _save(path, notes)
        return queued
    finally:
        os.close(fd)


def newest_first(notes):
    return sorted(notes, key=lambda n: (-n["at"], n["id"]))


def list_for(provider, session_id, now=None):
    """The session's notes from the last 7 days, newest first; [] when the log cannot be read."""
    now = time.time() if now is None else now
    try:
        return newest_first(_fresh(_load(log_path(provider, session_id)), now))
    except (OSError, ValueError):
        return []


def prune(now=None):
    """Drop notes older than 7 days and remove empty logs. Never raises."""
    now = time.time() if now is None else now
    try:
        if not os.path.isdir(folder()):
            return
        with _locked(TAKE_WAIT_S):
            for name in os.listdir(folder()):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(folder(), name)
                try:
                    notes = _load(path)
                    kept = _fresh(notes, now)
                    if kept != notes or not kept:
                        _save(path, kept)
                except (OSError, ValueError):
                    continue
    except (OSError, RelayError):
        return


def render(notes):
    """The additionalContext lines for notes the hook took (D5)."""
    return [(n.get("prefix") or PREFIX) + "\n" + n["text"] for n in notes]
