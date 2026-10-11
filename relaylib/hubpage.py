"""The hub's phone page (phone-artifact): the digest the hub just printed, as one self-contained HTML file the hub
session publishes as a private claude.ai artifact. Read-only. The page carries less than the hub chat: no
session ids, paths, fingerprints or tokens, and free text from other sessions is redacted before it is escaped."""
import json
import os
import re

from . import hub

# ---------------------------------------------------------------- redaction (D5)

SECRETS = [re.compile(p) for p in (
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bsk-ant-[A-Za-z0-9_-]{10,}",
    r"\bsk-[A-Za-z0-9_-]{20,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bAIza[0-9A-Za-z_-]{35}",
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
)]
BEARER = re.compile(r"(\bBearer\s+)[^\s\"']+")
NAMED = re.compile(r"(\b[\w.-]*(?:token|secret|passwd|password|api_key|apikey|auth)[\w.-]*\s*[:=]\s*)"
                   r"(?!Bearer\b|\[hidden\])(\"[^\"]*\"|'[^']*'|[^\s,;]+)", re.IGNORECASE)
UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
LONG_RUN = re.compile(r"(?<![\w+=/-])(?=[\w+=-]*\d)(?=[\w+=-]*[A-Za-z])[A-Za-z0-9+=_-]{32,}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PATH = re.compile(r"(?:~|/(?:Users|home|private|var|tmp|opt|Volumes))((?:/[^\s/()\[\]\"'`<>,;]+)+)")
MIN_ID = 4  # shorter strings are too common to treat as ids


def redact(text, ids=(), token=None):
    """`text` with the D5 rules applied; anything that is not a string gives ""."""
    if not isinstance(text, str):
        return ""
    if token:
        text = text.replace(token, "[hidden]")
    for pattern in SECRETS:
        text = pattern.sub("[hidden]", text)
    text = BEARER.sub(r"\1[hidden]", text)
    text = NAMED.sub(r"\1[hidden]", text)
    text = UUID.sub("[id]", text)
    for known in sorted({i for i in ids if isinstance(i, str) and len(i) >= MIN_ID}, key=len, reverse=True):
        text = text.replace(known, "[id]")
    text = LONG_RUN.sub("[hidden]", text)
    text = EMAIL.sub("[email]", text)
    return PATH.sub(lambda m: m.group(1).rsplit("/", 1)[-1], text)


def known_ids(found, records):
    """Session ids relay knows: hub sessions, session records and the digest's items."""
    ids = {sid for _, sid in hub.registered()}
    ids |= {r.get("session_id") for r in records if isinstance(r, dict)}
    for item in found:
        ids.add(item.get("session_id"))
        if isinstance(item.get("session"), dict):
            ids.add(item["session"].get("session_id"))
    return [i for i in ids if isinstance(i, str) and i]


def dashboard_token():
    from .ui import server
    try:
        with open(server._discovery_path()) as f:
            token = json.load(f).get("token")
    except (OSError, ValueError, AttributeError):
        return None
    return token if isinstance(token, str) and token else None
