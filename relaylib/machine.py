"""Stage/status transitions (spec section 2). Pure functions over the state dict."""
from dataclasses import asdict

from . import redact
from .errors import RelayError
from .progress import RoundRecord, decide

REVIEWED = ("spec", "plan", "build")
NEXT = {"spec": "plan", "plan": "build"}


def submit(st, author):
    stage, status = st["stage"], st["status"]
    if stage == "idea":
        if status != "drafting":
            raise RelayError(f"idea is {status}; nothing to submit")
        st["authors"]["idea"] = author
        st["stage"], st["status"] = "spec", "drafting"
        return "no-review"
    if stage == "done":
        raise RelayError("feature is done")
    if status not in ("drafting", "changes-requested"):
        raise RelayError(f"cannot submit {stage} while it is {status}")
    st["authors"][stage] = author
    st["rounds"][stage] = st["rounds"].get(stage, 0) + 1
    st["status"], st["refresh"], st["retried"] = "in-review", False, False
    return "review"


def history(st, stage):
    return [RoundRecord(**r) for r in st["history"].get(stage, [])]


def ceiling(st, stage, max_rounds):
    return max_rounds + st["extra_rounds"].get(stage, 0)


def start_cycle(st, stage, max_rounds):
    """An owner-requested independent review: its NO-GO goes back to the author for one more round at most,
    and the progress rules compare only rounds from here on (an earlier reviewer's findings do not count)."""
    base = st["rounds"].get(stage, 0)
    st.setdefault("round_base", {})[stage] = base
    short = base + 2 - ceiling(st, stage, max_rounds)  # the review is round base+1; the author's fix base+2
    if short > 0:
        st["extra_rounds"][stage] = st["extra_rounds"].get(stage, 0) + short


def known_ids(st, stage):
    ids = set()
    for r in st["history"].get(stage, []):
        ids.update(r["blocking_ids"])
        ids.update(r["prior"])
    return ids


def apply_go(st, snapshot):
    stage = st["stage"]
    st["verdicts"][stage] = "GO"
    st["reviewed"][stage] = snapshot
    st["refresh"] = False
    if stage in NEXT:
        st["stage"], st["status"] = NEXT[stage], "drafting"
    else:
        st["status"] = "ready-to-merge"


def apply_nogo(st, record, max_rounds):
    stage = st["stage"]
    st["rounds"][stage] = record.round
    st["history"].setdefault(stage, []).append(asdict(record))
    st["verdicts"][stage] = "NO-GO"
    st["refresh"] = False
    base = st.get("round_base", {}).get(stage, 0)
    cycle = [r for r in history(st, stage) if r.round > base]  # progress is judged within the current cycle
    action, reason = decide(cycle, ceiling(st, stage, max_rounds))
    st["status"] = "changes-requested" if action == "continue" else "waiting-owner"
    return action, reason


def apply_error(st, error=None):
    st["status"] = "review-error"
    reason = redact.public_line(error)  # state.md is published: no personal or machine-specific detail
    if reason:  # why it failed, for the owner's inbox (waiting-visibility D5); state.write_state drops it later
        st["review_error"] = reason


def mark_for_refresh(st, stage):
    """An approved input changed, or a build GO went stale: re-review without a new round."""
    for later in REVIEWED[REVIEWED.index(stage) + 1:]:
        st["verdicts"].pop(later, None)
        st["reviewed"][later] = None
    st["stage"], st["status"], st["refresh"], st["retried"] = stage, "in-review", True, False
