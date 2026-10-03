"""The review loop's progress rule (spec section 2): continue only while converging."""
from dataclasses import dataclass



@dataclass
class RoundRecord:
    round: int
    verdict: str
    blocking_ids: list
    prior: dict
    head: str | None = None


def decide(history, max_rounds, extra_rounds=0):
    """history holds this stage's NO-GO rounds, the one just finished last."""
    cur = history[-1]
    ceiling = max_rounds + extra_rounds
    if cur.round >= ceiling:
        return "stall", f"ceiling reached: round {cur.round} of {ceiling}"
    if len(history) < 2:
        return "continue", "first review round"
    prev = history[-2]
    before, after = len(prev.blocking_ids), len(cur.blocking_ids)
    if after > before:
        return "stall", f"blocking findings rose ({before} -> {after})"
    # "partial" is progress. No progress = the author tried to fix a finding and the reviewer
    # marks it unresolved; together with a count that did not drop, that is a stalled loop.
    stuck = sorted(i for i, s in cur.prior.items() if s == "unresolved" and i in prev.blocking_ids)
    if after == before and stuck:
        return "stall", (f"no progress ({before} -> {after} blocking), unresolved after a fix attempt: "
                         + ", ".join(stuck))
    return "continue", f"converging ({before} -> {after} blocking)"
