"""The hub (mobile-hub): a digest of what needs the owner, for a session the owner reaches from a phone, the
digests it printed (so actions refer to what the owner saw), and the hub sessions relay leaves out of the
sessions waiting on the owner. Local only, under ~/.relay/hub."""
import contextlib
import fcntl
import json
import os
import re
import secrets
import string
import tempfile
import time

from .config import relay_home
from .errors import RelayError


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


# ---------------------------------------------------------------- items (D2)

QUESTION_TOOLS = {"AskUserQuestion"}  # a question tool waits through a permission request (D8)


def _feature_wanted(row):
    flags = row.get("flags") or []
    return bool(row.get("waiting_on_owner") or row.get("status") in ("waiting-owner", "ready-to-merge")
                or any(f.startswith(("stale", "fallback GO")) for f in flags))


def _feature_session(row):
    for ask in row.get("asks") or []:
        if ask.get("kind") in ("answer", "approve") and ask.get("session"):
            provider, _, sid = ask["session"].partition(":")
            return {"provider": provider, "session_id": sid, "label": row.get("repo") or row.get("feature"),
                    "state": "permission" if ask["kind"] == "approve" else "waiting"}
    return None


def _feature_item(row):
    if row.get("error"):
        return {"kind": "error", "repo": row.get("repo") or os.path.basename(row.get("checkout") or ""),
                "slug": row.get("feature"), "error": row["error"], "wait_since": None, "actions": []}
    info = row.get("pr_info") or {}
    session = _feature_session(row)
    return {"kind": "feature", "repo": row.get("repo"), "repo_path": row.get("repo_path"), "slug": row.get("feature"),
            "stage": row.get("stage"), "status": row.get("status"),
            "asks": [{"kind": a.get("kind"), "text": a.get("text")} for a in row.get("asks") or []],
            "flags": [f for f in row.get("flags") or [] if f.startswith(("stale", "fallback GO"))],
            "wait_since": row.get("wait_since"), "pr": row.get("pr"), "pr_state": info.get("state"),
            "ci": row.get("ci"), "actions": list(row.get("actions") or []), "session": session,
            "answer_here": bool(session and session["state"] == "permission"), "seen": row.get("seen")}


def _session_item(entry):
    tools = list(entry.get("pending_tools") or [])
    return {"kind": "session", "provider": entry.get("provider"), "session_id": entry.get("session_id"),
            "label": entry.get("label"), "state": entry.get("state"), "pending_tools": tools,
            "wait_since": entry.get("since"), "excerpt": (entry.get("excerpt") or {}).get("text"),
            "answer_here": entry.get("state") == "permission" or bool(QUESTION_TOOLS & set(tools))}


def _order(item):
    at = item.get("wait_since")
    name = (item.get("repo") or "", item.get("slug") or "") if item["kind"] != "session" else \
        (item.get("label") or "", item.get("session_id") or "")
    return (at is None, at or 0) + name


def items(data, hubs=None):
    """The digest's items, oldest wait first, numbered from 1 (D2, R1 to R3). Hub sessions are left out even
    when a snapshot cached before they registered lists them."""
    hubs = registered() if hubs is None else hubs
    out = [_feature_item(r) for r in data.get("rows") or [] if r.get("error") or _feature_wanted(r)]
    out += [_session_item(e) for e in data.get("other_sessions") or []
            if (e.get("provider"), e.get("session_id")) not in hubs]
    out.sort(key=_order)
    for n, item in enumerate(out, 1):
        item["n"] = n
    return out


def counts(data, found, hubs=None):
    """What needs nothing: features in progress and sessions working, beyond the items."""
    hubs = registered() if hubs is None else hubs
    listed = {(i.get("repo"), i.get("slug")) for i in found if i["kind"] != "session"}
    sessions = {(i.get("provider"), i.get("session_id")) for i in found if i["kind"] == "session"}
    sessions |= {(i["session"]["provider"], i["session"]["session_id"]) for i in found if i.get("session")}
    features = [r for r in data.get("rows") or [] if (r.get("repo"), r.get("feature")) not in listed
                and "done" not in (r.get("stage"), r.get("status"))]
    running = [e for e in data.get("running") or [] if (e.get("provider"), e.get("session_id")) not in sessions
               and (e.get("provider"), e.get("session_id")) not in hubs]
    return {"features": len(features), "sessions": len(running)}


# ---------------------------------------------------------------- saved digests (D4)

ALPHABET = string.digits + string.ascii_lowercase
KEEP_S, KEEP_MIN = 86400, 20
REF = re.compile(r"([0-9a-z]{5,20})\.([1-9][0-9]{0,3})")


def _digests():
    return os.path.join(folder(), "digests")


def _base36(n):
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = ALPHABET[r] + out
        if not n:
            return out


def new_id(now_ms):
    """Creation time in milliseconds plus 4 random characters: never repeats, even after ~/.relay/hub is wiped."""
    return _base36(int(now_ms)) + "".join(secrets.choice(ALPHABET) for _ in range(4))


def save(found, source, session, now=None):
    """(id, None) once the digest is on disk under a name no other digest has, else (None, warning)."""
    now = time.time() if now is None else now
    temp = None
    try:
        os.makedirs(_digests(), exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".digest-", dir=_digests())
        for _ in range(5):
            digest_id = new_id(now * 1000)
            with os.fdopen(os.dup(fd), "w") as f:
                f.seek(0)
                f.truncate()
                os.fchmod(f.fileno(), 0o600)
                json.dump({"id": digest_id, "created_at": now, "session": session, "source": source,
                           "items": found}, f)
            try:
                os.link(temp, os.path.join(_digests(), digest_id + ".json"))  # fails if the name exists
            except FileExistsError:
                continue
            os.close(fd)
            _prune(now)
            return digest_id, None
        os.close(fd)
        return None, "could not save the digest: no free id"
    except OSError as e:
        return None, f"could not save the digest: {e}"
    finally:
        if temp:
            try:
                os.unlink(temp)
            except OSError:
                pass


def _prune(now):
    saved = []
    for name in os.listdir(_digests()):
        if not name.endswith(".json"):
            continue
        path = os.path.join(_digests(), name)
        try:
            with open(path) as f:
                at = json.load(f)["created_at"]
        except (OSError, ValueError, KeyError, TypeError):
            at = 0
        saved.append((at, path))
    saved.sort(reverse=True)
    for at, path in saved[KEEP_MIN:]:
        if now - at > KEEP_S:
            try:
                os.unlink(path)
            except OSError:
                pass


def resolve(ref, session, now=None):
    """The saved item `<digest id>.<n>` refers to, for the session that printed that digest within 24 hours
    (D4, R7a). Anything else is an unknown reference."""
    now = time.time() if now is None else now
    unknown = RelayError(f"unknown reference {ref}: run relay hub for a fresh digest")
    m = REF.fullmatch(ref or "")
    if not m:
        raise unknown
    try:
        with open(os.path.join(_digests(), m.group(1) + ".json")) as f:
            digest = json.load(f)
        if digest["session"] != session or not 0 <= now - digest["created_at"] <= KEEP_S:
            raise unknown
        found = [i for i in digest["items"] if i.get("n") == int(m.group(2))]
    except (OSError, ValueError, KeyError, TypeError):
        raise unknown
    if len(found) != 1:
        raise unknown
    return found[0]
