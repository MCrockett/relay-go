"""One-line events for the hub (merge-and-tidy D9, D10): a build GO, a feature waiting on the owner, a merge, a
release. Queued by the operation that decides them and delivered after it reports its result: a line in the hub
log, and a note to each registered hub session except the one that caused it. Local only; never fails a command."""
import contextlib
import contextvars
import json

KINDS = ("go", "waiting", "merged", "released")
PREFIX = "relay event:"


class Batch:
    def __init__(self, cause=None):
        self.events = []
        self.cause = cause  # the (provider, session_id) running this operation: it is not told its own events

    def flush(self):
        """Deliver this operation's events; a list of warnings."""
        events, self.events = self.events, []
        warnings = []
        for event in events:
            warnings += deliver(event)
        return warnings


_BATCH = contextvars.ContextVar("relay_events", default=None)


@contextlib.contextmanager
def collect(cause=None):
    """Events queued inside belong to this operation (one per request, job or command), so one operation's flush
    never delivers another's before that one has reported."""
    batch = Batch(cause)
    token = _BATCH.set(batch)
    try:
        yield batch
    finally:
        _BATCH.reset(token)


def set_cause(cause):
    """Name the session running the current operation, so it is not told its own events (D10)."""
    batch = _BATCH.get()
    if batch is not None and cause:
        batch.cause = cause


def owner_of(st):
    """(provider, session) of the feature's owner session, or None (delegated-merge R14)."""
    owner = st.get("owner") if isinstance(st, dict) else None
    if (isinstance(owner, dict) and isinstance(owner.get("session"), str) and owner["session"]
            and owner.get("provider") in ("claude", "codex")):
        return owner["provider"], owner["session"]
    return None


def requesters_of(st):
    """[(provider, session_id)] who asked for a feature through relay dispatch (R19): the hub that dispatched it
    and the asker. Empty for a feature with no request or one the owner dispatched from their terminal."""
    from . import dispatch
    request = st.get("request") if isinstance(st, dict) else None
    return dispatch.requesters(request) if isinstance(request, dict) else []


def request_id_of(st):
    request = st.get("request") if isinstance(st, dict) else None
    rid = request.get("id") if isinstance(request, dict) else None
    return rid if isinstance(rid, str) and rid else None


def request_fields(st, kind):
    """Keyword arguments for queue()/event() when this feature came from a dispatch request, and the dispatch
    record moves to `kind` (merged or released) now, whether or not a note is then sent (R21). {} otherwise."""
    rid = request_id_of(st)
    if not rid:
        return {}
    from . import dispatch
    dispatch.mark_landed(rid, kind)
    return {"requesters": requesters_of(st), "request_id": rid}


def event(kind, repo, slug, stage, pr, text, cause=None, *, owner=None, note_extra="", fields=None,
          log_only=False, owner_only=False, requesters=(), request_id=None):
    """`owner`: the feature's session, told of a merge (R14) with `note_extra` added to its note. `fields` join the
    hub log line. `log_only`: the log line and no note at all; `owner_only`: the log line and the owner's note,
    not the hub sessions' (they already had the event from the resolver that saw the merge first).
    `request_id` and `requesters`: the feature came from a dispatch request (R20); a merged or released note to a
    requester ends with "This was your request <id>." (to every hub when `requesters` is empty)."""
    if kind not in KINDS:
        raise ValueError(f"unknown event {kind}")
    return {"kind": kind, "repo": repo, "slug": slug, "stage": stage, "pr": pr, "text": text, "cause": cause,
            "owner": owner, "note_extra": note_extra, "fields": fields, "log_only": log_only,
            "owner_only": owner_only, "requesters": [tuple(r) for r in requesters], "request_id": request_id}


def queue(kind, repo, slug, stage, pr, text, cause=None, **extra):
    """Hold an event until the current operation flushes; delivered at once outside any collect()."""
    batch = _BATCH.get()
    ev = event(kind, repo, slug, stage, pr, text, cause or (batch.cause if batch else None), **extra)
    if batch is None:
        return deliver(ev)
    batch.events.append(ev)
    return []


def session_live(provider, session_id):
    """The session's record while it is running (valid, shown state neither ended nor stopped), else None."""
    from . import leftoff, sessions
    try:
        with open(sessions.record_path(provider, session_id)) as f:
            record = json.load(f)
        if not sessions._valid(record):
            return None
        alive = sessions.alive(sessions.read_records())
        if leftoff._shown_state(record, alive) in ("ended", "stopped"):
            return None
        return record
    except Exception:
        return None


def _inbox(provider, session_id):
    """The session's inbox socket while it is running, else None (the note is queued for its next hook event)."""
    return (session_live(provider, session_id) or {}).get("inbox")


def _send(provider, sid, text, warnings):
    from . import notes
    try:
        _, message = notes.send(provider, sid, text, _inbox(provider, sid), prefix=PREFIX)
        if message:
            warnings.append(f"event note to {provider} session {sid}: {message}")
    except Exception as e:
        warnings.append(f"could not send the event to {provider} session {sid}: {e}")


def deliver(ev):
    """The hub log line, then a note to every hub session but the cause, then one to the feature's own session
    (R14); warnings, never an exception."""
    from . import hub
    warnings = []
    try:
        warning = hub.log({"command": "event", "event": ev["kind"], "repo": ev["repo"], "slug": ev["slug"],
                           "pr": ev["pr"], "text": ev["text"], **(ev.get("fields") or {})})
        if warning:
            warnings.append(warning)
        if ev.get("log_only"):
            return warnings
        registered = hub.registered()
        landed = ev["kind"] in ("merged", "released")
        asked = ev.get("requesters") or []
        add = f" This was your request {ev['request_id']}." if landed and ev.get("request_id") else ""
        cause = ev.get("cause")
        if not ev.get("owner_only"):
            for provider, sid in sorted(registered):
                if cause == (provider, sid):
                    continue
                _send(provider, sid, ev["text"] + (add if not asked or (provider, sid) in asked else ""), warnings)
        owner = tuple(ev["owner"]) if ev.get("owner") else None
        if owner and owner != cause and owner not in registered and landed:
            extra = ev.get("note_extra")
            _send(owner[0], owner[1], ev["text"] + (" " + extra if extra else "")
                  + (add if owner in asked else ""), warnings)
        if add:
            for provider, sid in asked:  # a requester no note above reached: not a hub, not the cause, not the owner
                if (provider, sid) in registered or (provider, sid) == cause or (provider, sid) == owner:
                    continue
                _send(provider, sid, ev["text"] + add, warnings)
    except Exception as e:
        warnings.append(f"could not deliver the event: {e}")
    return warnings


def pr_label(repo, slug, pr):
    return f"{repo} PR #{pr} ({slug})" if pr else f"{repo} {slug}"


def go_text(repo, slug, pr, reviewer):
    return f"relay: {pr_label(repo, slug, pr)} has a build GO from {reviewer}; waiting for the owner's merge."


def waiting_text(repo, slug, stage, pr, reason, error=False):
    what = "review failed" if error else "review stopped"
    return f"relay: {pr_label(repo, slug, pr)} {stage} {what}: {reason}; waiting on the owner."


def merged_text(repo, slug, pr, base, left=(), tidied=False, delegated=False):
    """`tidied`: relay merged it here and tidied this machine; otherwise the merge was found on GitHub."""
    tail = ", delegated under D1." if delegated else "."
    head = (f"relay: {pr_label(repo, slug, pr)} is released to main{tail}" if base == "main"
            else f"relay: {pr_label(repo, slug, pr)} is merged into {base or 'its base'}{tail}")
    if left:
        return head + " Left to tidy: " + "; ".join(left) + "."
    return head + (" Worktree removed, repo free." if tidied else "")


def delegated_text(repo, slug, pr, message, left=()):
    """A delegated merge that did not end as a plain merge (R10): what happened, and what is left to tidy."""
    return (f"relay: {pr_label(repo, slug, pr)}: {message}; delegated under D1."
            + (" Not tidied: " + "; ".join(left) + "." if left else ""))
