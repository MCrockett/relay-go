"""Make one line of tool output safe to publish in state.md (waiting-visibility D5, owner 2026-10-07): no personal
data and no machine-specific detail such as home folders, usernames, emails, URL credentials or tokens."""
import getpass
import os
import re

URL = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^\s/@]*@)?([^\s/?#'\"]+)[^\s'\"]*", re.I)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PATH = re.compile(r"(?<![\w.:/~-])~?/[^\s'\"()\[\]{},;]+")
TOKEN = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b")


def _base(match):
    name = os.path.basename(match.group(0).rstrip("/"))
    return name or "<path>"


def public_line(text, limit=200):
    """The first non-empty line, cleaned, cut to limit characters; "" when there is nothing."""
    line = next((l.strip() for l in (text or "").splitlines() if l.strip()), "")
    line = URL.sub(lambda m: m.group(0).split("://", 1)[0] + "://" + m.group(1), line)
    line = EMAIL.sub("<email>", line)
    line = PATH.sub(_base, line)
    line = TOKEN.sub("<redacted>", line)
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if len(user) >= 3:
        line = re.sub(rf"\b{re.escape(user)}\b", "<user>", line, flags=re.I)
    return line[:limit]
