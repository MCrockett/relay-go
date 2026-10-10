"""The hub (mobile-hub): a digest of what needs the owner, for a session the owner reaches from a phone, the
digests it printed (so actions refer to what the owner saw), and the hub sessions relay leaves out of the
sessions waiting on the owner. Local only, under ~/.relay/hub."""
import contextlib
import fcntl
import json
import os
import tempfile
import time

from .config import relay_home


def folder():
    return os.path.join(relay_home(), "hub")


@contextlib.contextmanager
def _locked():
    os.makedirs(folder(), exist_ok=True)
    with open(os.path.join(folder(), "lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _write_json(path, value):
    fd, temp = tempfile.mkstemp(prefix=".hub-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(value, f)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


# ---------------------------------------------------------------- hub sessions (D9)

def _sessions_path():
    return os.path.join(folder(), "sessions.json")


def _read_sessions():
    with open(_sessions_path()) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("hub sessions file is malformed")
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("provider"), str)
            and isinstance(e.get("session_id"), str)]


def registered():
    """{(provider, session_id)} of hub sessions; empty when the file is missing or unreadable (D9)."""
    try:
        return {(e["provider"], e["session_id"]) for e in _read_sessions()}
    except (OSError, ValueError):
        return set()


def register(provider, session_id, records, now=None):
    """Record this session as a hub, dropping entries whose session record is gone or ended. A warning, or None;
    never raises (D9)."""
    now = time.time() if now is None else now
    live = {(r.get("provider"), r.get("session_id")) for r in records if r.get("state") != "ended"}
    try:
        with _locked():
            try:
                entries = _read_sessions()
            except (OSError, ValueError):
                entries = []
            entries = [e for e in entries if (e["provider"], e["session_id"]) in live
                       and (e["provider"], e["session_id"]) != (provider, session_id)]
            entries.append({"provider": provider, "session_id": session_id, "at": now})
            _write_json(_sessions_path(), entries)
        return None
    except OSError as e:
        return f"could not record this hub session: {e}"


# ---------------------------------------------------------------- data (D3)

LOAD_TIMEOUT_S = 5


def _dashboard(timeout):
    """(data, age_seconds) from a running dashboard within `timeout` seconds for the whole request, else None.
    The token is used for this local request only."""
    import http.client
    import threading
    from .ui import server
    try:
        with open(server._discovery_path()) as f:
            info = json.load(f)
        if not isinstance(info.get("pid"), int) or info["pid"] <= 0:
            return None
        os.kill(info["pid"], 0)
        port, token = int(info["port"]), str(info["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    result = {}

    def fetch():
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/api/snapshot", headers={"X-Relay-Token": token})
            response = conn.getresponse()
            if response.status == 200:
                result["body"] = json.loads(response.read())
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            conn.close()

    worker = threading.Thread(target=fetch, name="relay-hub-dashboard", daemon=True)
    worker.start()
    worker.join(timeout)  # bounds connect, headers and body together
    body = None if worker.is_alive() else result.get("body")
    if not isinstance(body, dict) or body.get("loading") or not isinstance(body.get("data"), dict):
        return None
    age = body.get("age_seconds")
    return body["data"], (age if isinstance(age, (int, float)) else None)


def load(timeout=LOAD_TIMEOUT_S):
    """(data, source): a running dashboard's snapshot when it answers in time, else one built here (R6)."""
    found = _dashboard(timeout)
    if found:
        return found[0], {"from": "dashboard", "age_seconds": found[1]}
    from .ui import snapshot
    started = time.monotonic()
    data = snapshot.build()
    return data, {"from": "build", "seconds": time.monotonic() - started}
