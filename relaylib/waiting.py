"""What the owner needs to do for a feature, in plain words, and since when (waiting-visibility D4, D6).
Shared by `relay status` and the dashboard so both agree on what is waiting."""
import datetime
import re

from . import gitops, state
from .errors import RelayError


def _updated(st):
    try:
        at = datetime.datetime.fromisoformat((st or {}).get("updated") or "")
        return at.timestamp() if at.tzinfo else None
    except (TypeError, ValueError):
        return None


def _ask(kind, text, since, session=None):
    out = {"kind": kind, "text": text, "since": since}
    if session:
        out["session"] = session  # the session this ask waits on, so one wait shows once (one_ask_per_session)
    return out


def _session(record):
    return f"{record.get('provider')}:{record.get('session_id')}" if record and record.get("session_id") else None


def _stale(flags):
    return any(f.startswith("stale") or (f.startswith("fallback GO") and f.endswith("(agent: relay review)"))
               for f in flags)


def asks(row, st, health, record, stuck_reason):
    """Asks in D4's order. `health` may come from local activity alone, with no hook record (F2)."""
    flags = row.get("flags") or []
    if row.get("feature") == "?":
        return [_ask("error", "Fix: " + ("; ".join(flags) or "the repository could not be read"), None)]
    if row.get("stage") == "done" or row.get("status") == "done":
        return []
    status, stage, at, out = row.get("status"), (st or {}).get("stage") or row.get("stage"), _updated(st), []
    if status == "waiting-owner":
        out.append(_ask("decide", f"Decide: {stage} review stopped" + (f". {stuck_reason}" if stuck_reason else ""), at))
    if status == "review-error":
        why = (st or {}).get("review_error")
        out.append(_ask("review-failed", f"Review failed: {why}" if why else "Review failed", at))
    if status == "ready-to-merge" and not _stale(flags):
        call = [f for f in flags if f.startswith("fallback GO") and f.endswith("your call)")]
        out.append(_ask("merge", " · ".join([f"Merge PR #{row.get('pr')}" if row.get("pr") else "Merge the PR"] + call), at))
    if any(f.startswith("handoff") and "relay take" in f for f in flags):
        out.append(_ask("take", "Take the handoff: open a session and run relay take", at))
    if (health or {}).get("kind") == "attention":
        provider = (record or {}).get("provider") or ((st or {}).get("owner") or {}).get("provider") or "agent"
        text = health.get("text") or ""
        if text.startswith("needs permission") and record:
            tools = list(dict.fromkeys(p.get("tool_name") for p in record.get("pending") or [] if p.get("tool_name")))
            what = ", ".join(tools) or "a tool"
            out.append(_ask("approve", f"Approve {what} in the {provider} session", record.get("since"),
                            _session(record)))
        elif text.startswith("waiting on you") and record:
            out.append(_ask("answer", f"Answer the {provider} session", record.get("since"), _session(record)))
        else:
            out.append(_ask("check", f"Check the {provider} session: {text}", health.get("since")))
    return out


def since(found):
    times = [a["since"] for a in found if a.get("since") is not None]
    return min(times) if times else None


def one_ask_per_session(rows):
    """One session that owns several features waits on the owner once: its ask stays on the feature updated last,
    naming the others, and leaves the others' rows (which stop waiting when it was their only ask)."""
    groups = {}
    for r in rows:
        for key in {a["session"] for a in r.get("asks") or [] if a.get("session")}:
            groups.setdefault(key, []).append(r)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        keep = max(members, key=lambda r: (r.get("updated") or "", r.get("feature") or ""))
        also = ", ".join(sorted(r.get("feature") or "" for r in members if r is not keep))
        for r in members:
            if r is keep:
                for a in r["asks"]:
                    if a.get("session") == key:
                        a["text"] += f" · also for {also}"
                continue
            r["asks"] = [a for a in r["asks"] if a.get("session") != key]
            r["wait_since"], r["waiting_on_owner"] = since(r["asks"]), bool(r["asks"])
            if "health_inbox" in r:
                r["health_inbox"] = bool(r["asks"]) and all(a["kind"] in ("answer", "approve", "check") for a in r["asks"])
    return rows


def sort_key(row):
    """Waiting first, newest wait first (running-now D8), waits without a time after, then repo and feature."""
    at = row.get("wait_since")
    return (not row.get("waiting_on_owner"), at is None, -(at or 0), row.get("repo") or "", row.get("feature") or "")


def oldest_first_key(row):
    """The order `relay status --json` has always used: waiting first, oldest wait first (running-now D8)."""
    at = row.get("wait_since")
    return (not row.get("waiting_on_owner"), at is None, at or 0, row.get("repo") or "", row.get("feature") or "")


def stuck_reason(repo, ref, slug, stage):
    """The `reason` of the newest `<stage>-stuck*.md` at ref (write_stuck numbers repeats -2, -3, ...), or None."""
    pattern = re.compile(rf"{re.escape(stage)}-stuck(?:-(\d+))?\.md", re.I)
    try:
        found = []
        for path in gitops.ls_files(repo, ref, f"{state.RELAY_DIR}/{slug}/reviews"):
            m = pattern.fullmatch(path.rsplit("/", 1)[-1])
            if m:
                found.append((int(m.group(1) or 1), path))
        if not found:
            return None
        meta = state.parse_state(gitops.show(repo, ref, max(found)[1]) or "", "stuck file")
        reason = meta.get("reason") if isinstance(meta, dict) else None
        return reason if isinstance(reason, str) and reason.strip() else None
    except (RelayError, OSError):
        return None
