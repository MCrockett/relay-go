"""Parse the reviewer's final verdict block. Only the final message is ever passed in."""
import re
from dataclasses import dataclass, field

from .errors import RelayError

TAG = "[introduced-by-revision]"
OPEN = ("partial", "unresolved")
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
HEAD = re.compile(r"^(verdict|prior|blocking|notes):\s*(.*)$", re.I)
PRIOR = re.compile(r"^id:\s*(\S+)\s+status:\s*(resolved|partial|unresolved)\b", re.I)
BLOCK_ID = re.compile(r"^id:\s*(\S+)\s+(.*)$")
EMPTY = ("none", "n/a", "[]")
BULLET = re.compile(r"^[-*]\s+(.*)$")


class VerdictError(RelayError):
    pass


@dataclass
class Finding:
    id: str
    text: str
    introduced: bool = False


@dataclass
class Verdict:
    verdict: str
    prior: dict = field(default_factory=dict)
    blocking: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def parse(text, round_no):
    text = text or ""
    blocks = [m for m in FENCE.finditer(text) if re.search(r"^\s*verdict:", m.group(1), re.M | re.I)]
    if not blocks:
        raise VerdictError("no verdict block in the reviewer's final message")
    if text[blocks[-1].end():].strip():
        raise VerdictError("the verdict block must end the reviewer's final message")
    v = Verdict(verdict="")
    section = None
    for raw in blocks[-1].group(1).splitlines():
        line = raw.strip()
        if not line:
            continue
        head = HEAD.match(line)
        if head:
            section, rest = head.group(1).lower(), head.group(2).strip()
            if section == "verdict":
                v.verdict = rest.strip("*` ").upper().replace(" ", "")
            elif section in ("prior", "blocking") and rest and rest.lower() not in EMPTY:
                raise VerdictError(f"{section} items must be bullets on their own lines")
            continue
        bullet = BULLET.match(line)
        if not bullet:
            if section in ("prior", "blocking"):
                raise VerdictError(f"unreadable {section} line: {line!r}")
            continue
        item = bullet.group(1).strip()
        if item.lower() in EMPTY:
            continue
        if section == "prior":
            m = PRIOR.match(item)
            if not m:
                raise VerdictError(f"unreadable prior line: {item!r}")
            v.prior[m.group(1)] = m.group(2).lower()
        elif section == "blocking":
            m = BLOCK_ID.match(item)
            fid, body = (m.group(1), m.group(2)) if m else (f"R{round_no}-{len(v.blocking) + 1}", item)
            v.blocking.append(Finding(fid, body.replace(TAG, "").strip(), TAG in body))
        elif section == "notes":
            v.notes.append(item)
    if v.verdict not in ("GO", "NO-GO"):
        raise VerdictError(f"verdict must be GO or NO-GO, got {v.verdict!r}")
    if v.verdict == "GO" and v.blocking:
        v.verdict = "NO-GO"
    return v


def check_new_findings_tagged(v, round_no, known_ids):
    if round_no < 2:
        return
    bad = [f.id for f in v.blocking if f.id not in known_ids and not f.introduced]
    if bad:
        raise VerdictError(f"new blocking findings in round {round_no} must be tagged {TAG}: {', '.join(bad)}")


def check_prior(v, required_ids):
    """Round 2+: every blocking id of the previous NO-GO must be accounted for, and a GO cannot leave one open."""
    missing = [i for i in required_ids if i not in v.prior]
    if missing:
        raise VerdictError("prior must account for every earlier blocking finding; missing: " + ", ".join(missing))
    if v.verdict == "GO":
        still_open = sorted(i for i, status in v.prior.items() if status in OPEN)
        if still_open:
            raise VerdictError("GO while earlier findings are still open: " + ", ".join(still_open))
    blocking_ids = {f.id for f in v.blocking}
    dropped = sorted(i for i, status in v.prior.items() if status in OPEN and i not in blocking_ids)
    if dropped:
        raise VerdictError("findings marked open must stay under blocking with their id: " + ", ".join(dropped))
