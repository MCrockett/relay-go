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


def event(kind, repo, slug, stage, pr, text, cause=None):
    if kind not in KINDS:
        raise ValueError(f"unknown event {kind}")
    return {"kind": kind, "repo": repo, "slug": slug, "stage": stage, "pr": pr, "text": text, "cause": cause}


def queue(kind, repo, slug, stage, pr, text, cause=None):
    """Hold an event until the current operation flushes; delivered at once outside any collect()."""
    batch = _BATCH.get()
    ev = event(kind, repo, slug, stage, pr, text, cause or (batch.cause if batch else None))
    if batch is None:
        return deliver(ev)
    batch.events.append(ev)
    return []


def _inbox(provider, session_id):
    """The session's inbox socket while it is running, else None (the note is queued for its next hook event)."""
    from . import leftoff, sessions
    try:
        with open(sessions.record_path(provider, session_id)) as f:
            record = json.load(f)
        if not sessions._valid(record):
            return None
        alive = sessions.alive(sessions.read_records())
        if leftoff._shown_state(record, alive) in ("ended", "stopped"):
            return None
        return record.get("inbox")
    except Exception:
        return None


def deliver(ev):
    """The hub log line, then a note to every hub session but the cause; warnings, never an exception."""
    from . import hub, notes
    warnings = []
    try:
        warning = hub.log({"command": "event", "event": ev["kind"], "repo": ev["repo"], "slug": ev["slug"],
                           "pr": ev["pr"], "text": ev["text"]})
        if warning:
            warnings.append(warning)
        for provider, sid in sorted(hub.registered()):
            if ev.get("cause") == (provider, sid):
                continue
            try:
                _, message = notes.send(provider, sid, ev["text"], _inbox(provider, sid), prefix=PREFIX)
                if message:
                    warnings.append(f"event note to {provider} session {sid}: {message}")
            except Exception as e:
                warnings.append(f"could not send the event to {provider} session {sid}: {e}")
    except Exception as e:
        warnings.append(f"could not deliver the event: {e}")
    return warnings


def pr_label(repo, slug, pr):
    return f"{repo} PR #{pr} ({slug})" if pr else f"{repo} {slug}"


def go_text(repo, slug, pr, reviewer, confirm_with=None):
    if confirm_with:
        return (f"relay: {pr_label(repo, slug, pr)} has a build GO from {reviewer}, a fallback reviewer; "
                f"{confirm_with} still confirms it before it counts.")
    return f"relay: {pr_label(repo, slug, pr)} has a build GO from {reviewer}; waiting for the owner's merge."


def waiting_text(repo, slug, stage, pr, reason, error=False):
    what = "review failed" if error else "review stopped"
    return f"relay: {pr_label(repo, slug, pr)} {stage} {what}: {reason}; waiting on the owner."


def merged_text(repo, slug, pr, base, left=(), tidied=False):
    """`tidied`: relay merged it here and tidied this machine; otherwise the merge was found on GitHub."""
    head = (f"relay: {pr_label(repo, slug, pr)} is released to main." if base == "main"
            else f"relay: {pr_label(repo, slug, pr)} is merged into {base or 'its base'}.")
    if left:
        return head + " Left to tidy: " + "; ".join(left) + "."
    return head + (" Worktree removed, repo free." if tidied else "")
