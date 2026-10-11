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


# ---------------------------------------------------------------- the page (D3, D4, D6)

TEXT_MAX, LIST_MAX, ITEMS_MAX, FAILED_MAX, BYTES_MAX = 400, 5, 100, 5, 512 * 1024
FOOTER = "Read-only. To act, tell the hub chat that sent this link, for example: merge 3."
EXPIRED = "These numbers have expired. Ask the hub for a new digest."

STYLE = """
:root{--bg:#f6f5f2;--fg:#1d1d1b;--muted:#6b6a65;--card:#ffffff;--border:#e2e0da;--accent:#2f5d8a;
--here-bg:#fff4d6;--here-fg:#6b4a00;--warn-bg:#fde8e6;--warn-fg:#8a2318}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#171716;--fg:#ecebe7;--muted:#a3a29c;
--card:#232321;--border:#3a3936;--accent:#8db7e0;--here-bg:#3b3014;--here-fg:#f2d48a;--warn-bg:#3d1d1a;
--warn-fg:#f3b0a7;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#171716;--fg:#ecebe7;--muted:#a3a29c;--card:#232321;--border:#3a3936;
--accent:#8db7e0;--here-bg:#3b3014;--here-fg:#f2d48a;--warn-bg:#3d1d1a;--warn-fg:#f3b0a7;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;padding-block:16px;padding-inline:16px;background:var(--bg);color:var(--fg);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:640px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}
header p,.meta,.more,footer{color:var(--muted);font-size:13px;margin:2px 0}
.banner,.failed{background:var(--warn-bg);color:var(--warn-fg);border-radius:8px;padding:10px 12px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px;margin:12px 0}
.card h2{font-size:16px;margin:0 0 2px}
.card,.card *{overflow-wrap:anywhere}
.n{display:inline-block;min-width:24px;color:var(--accent)}
.card ul{margin:6px 0;padding-left:18px}
.card p{margin:4px 0}
.chip{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:0 8px;margin:2px 4px 0 0;
font-size:13px}
blockquote{margin:6px 0;padding-left:10px;border-left:3px solid var(--border);color:var(--muted)}
.here{background:var(--here-bg);color:var(--here-fg);border-radius:6px;padding:4px 8px;font-weight:600}
"""

SCRIPT = """(function(){var b=document.getElementById("hub");if(!b){return;}
var made=+b.dataset.made,expires=+b.dataset.expires;
function tick(){var now=Date.now()/1000,m=Math.max(0,Math.floor((now-made)/60));
var age=document.getElementById("age");
if(age){age.textContent=m<60?"made "+m+" min ago":"made "+Math.floor(m/60)+" h ago";}
var gone=document.getElementById("expired");if(gone){gone.hidden=now<expires;}}
tick();setInterval(tick,60000);})();"""


def _when(at):
    import time
    return time.strftime("%b %d, %H:%M %Z", time.localtime(at))


class _Text:
    """Redact, cut and escape free text, with the ids and token of this page."""

    def __init__(self, ids, token):
        self.ids, self.token = ids, token

    def __call__(self, value, limit=TEXT_MAX):
        import html
        text = redact(value, self.ids, self.token)
        if len(text) > limit:
            text = text[:limit - 3] + "..."
        return html.escape(text, quote=True)


def _capped(values, t):
    shown = [f"<li>{t(v)}</li>" for v in values[:LIST_MAX]]
    if len(values) > LIST_MAX:
        shown.append(f"<li>+{len(values) - LIST_MAX} more</li>")
    return shown


def _card(item, numbered, now, t):
    kind = item.get("kind")
    wait = hub._num(item.get("wait_since"))
    waited = f"waiting {hub._age(now - wait)}" if wait is not None else ""
    lead = f'<span class="n">{item["n"]}.</span> ' if numbered and isinstance(item.get("n"), int) else ""
    body = []
    if kind == "error":
        title = t(item.get("repo"))
        body.append(f'<p>could not be read: {t(item.get("error"))}</p>')
    elif kind == "session":
        title = f'{t(item.get("label"))} · {t(item.get("provider"))} session'
        state = "" if item.get("state") == "waiting" and waited else t(item.get("state"))  # "waiting 40m" says it
        body.append(f'<p class="meta">{" · ".join(x for x in (state, waited) if x)}</p>')
        tools = hub._strs(item.get("pending_tools"))
        if tools:
            body.append(f'<p>needs: {t(", ".join(tools))}</p>')
        if isinstance(item.get("excerpt"), str):
            body.append(f'<blockquote>{t(item["excerpt"], hub.EXCERPT)}</blockquote>')
    else:
        title = f'{t(item.get("repo"))}/{t(item.get("slug"))}'
        state = " ".join(x for x in (t(item.get("stage")), t(item.get("status"))) if x)
        body.append(f'<p class="meta">{" · ".join(x for x in (state, waited) if x)}</p>')
        asks = [a.get("text") for a in item.get("asks") if isinstance(a, dict)] \
            if isinstance(item.get("asks"), list) else []
        lines = _capped([a for a in asks if isinstance(a, str)], t) + _capped(hub._strs(item.get("flags")), t)
        if lines:
            body.append("<ul>" + "".join(lines) + "</ul>")
        pr = item.get("pr")
        if isinstance(pr, int) and not isinstance(pr, bool):
            body.append(f'<p>PR #{pr} · CI {t(item.get("ci")) or "unknown"}</p>')
        actions = hub._strs(item.get("actions"))
        if actions:
            body.append("<p>actions: " + "".join(f'<span class="chip">{t(a)}</span>' for a in actions) + "</p>")
        s = item.get("session")
        if isinstance(s, dict):
            body.append(f'<p>session: {t(s.get("label"))} ({t(s.get("provider"))}) · {t(s.get("state"))}</p>')
    if item.get("answer_here") is True:
        body.append(f'<p class="here">{hub.ANSWER_HERE}</p>')
    cls = "card answer" if item.get("answer_here") is True else "card"
    return f'<article class="{cls}"><h2>{lead}{title}</h2>{"".join(body)}</article>'


def render(digest_id, source, found, counts, failed=(), now=None, ids=(), token=None):
    """The page for one digest: the same items, in the same order, as the text and JSON (R4 to R10)."""
    import html
    import time
    now = time.time() if now is None else now
    t = _Text(list(ids), token)
    found = [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []
    source = source if isinstance(source, dict) else {}
    where = (f"dashboard data {hub._age(source.get('age_seconds'))} old" if source.get("from") == "dashboard"
             else f"built in {hub._age(source.get('seconds'))}")
    expires = now + hub.KEEP_S
    head = [f"<h1>Relay hub</h1>",
            f'<p>{"digest " + t(digest_id) if digest_id else "Not saved: actions need a new digest."} · '
            f'made {html.escape(_when(now))} <span id="age"></span></p>',
            f"<p>{html.escape(where)}</p>"]
    if digest_id:
        head.append(f"<p>numbers work until {html.escape(_when(expires))}</p>")
    top = [f'<header>{"".join(head)}</header>', f'<div id="expired" class="banner" hidden>{EXPIRED}</div>']
    failed = [f for f in failed if isinstance(f, str)]
    for problem in failed[:FAILED_MAX]:
        top.append(f'<p class="failed">Could not check everything: {t(problem)}</p>')
    if len(failed) > FAILED_MAX:
        top.append(f'<p class="failed">+{len(failed) - FAILED_MAX} more</p>')
    if not found and not failed:
        top.append("<p>Nothing needs you right now.</p>")
    counts = counts if isinstance(counts, dict) else {}
    closing = html.escape(hub._closing({"features": counts.get("features") or 0,
                                        "sessions": counts.get("sessions") or 0}))
    cards = [_card(item, bool(digest_id), now, t) for item in found[:ITEMS_MAX]]

    def page(shown):
        more = len(found) - shown
        rest = [f'<p class="more">{more} more items: ask the hub</p>'] if more > 0 else []
        # The artifact publisher wraps the page in its own doctype, head (charset, viewport) and body (D6).
        return (f"<title>Relay hub</title><style>{STYLE}</style>"
                f'<main id="hub" data-made="{int(now)}" data-expires="{int(expires)}">'
                + "".join(top + cards[:shown] + rest)
                + f"<p>{closing}</p><footer>{FOOTER}</footer></main><script>{SCRIPT}</script>\n")

    shown = len(cards)
    out = page(shown)
    while len(out.encode()) > BYTES_MAX and shown:
        shown -= 1
        out = page(shown)
    return out


# ---------------------------------------------------------------- the page file (D2)

def pages():
    return os.path.join(hub.folder(), "pages")


def write(name, page):
    """Write pages/<name>.html atomically, mode 0600, and return its path; an OSError leaves no temp file."""
    import contextlib
    import tempfile
    os.makedirs(pages(), exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".page-", dir=pages())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(page)
        path = os.path.join(pages(), name + ".html")
        os.replace(temp, path)
        return path
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


# ---------------------------------------------------------------- this hub's link (D2)

ARTIFACT = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{1,128}")
NONE = {"url": None, "digest": None, "at": None}


def _links_path():
    return os.path.join(hub.folder(), "pages.json")


def _key(session):
    return f"{session['provider']}:{session['session_id']}"


def _read_links():
    with open(_links_path()) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("hub pages file is malformed")
    return data


def published(session):
    """This session's {url, digest, at}, all None when unknown or not a claude.ai artifact link (F2)."""
    if not session:
        return dict(NONE)
    try:
        entry = _read_links().get(_key(session))
    except (OSError, ValueError):
        return dict(NONE)
    if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not ARTIFACT.fullmatch(entry["url"]):
        return dict(NONE)
    return {"url": entry["url"], "digest": entry.get("digest") if isinstance(entry.get("digest"), str) else None,
            "at": hub._num(entry.get("at"))}


def _own_digest(digest_id, session, now):
    if not isinstance(digest_id, str) or not re.fullmatch(r"[0-9a-z]{5,20}", digest_id):
        return False
    try:
        with open(os.path.join(hub._digests(), digest_id + ".json")) as f:
            digest = json.load(f)
        return digest["session"] == session and 0 <= now - digest["created_at"] <= hub.KEEP_S
    except (OSError, ValueError, KeyError, TypeError):
        return False


def record(session, digest_id, url, now=None):
    """Remember this hub session's artifact link and the digest it now shows (D2, R3)."""
    import time
    from .errors import RelayError
    now = time.time() if now is None else now
    if not session:
        raise RelayError("relay hub page-url records a hub session's page: run it from the hub session")
    if not isinstance(url, str) or not ARTIFACT.fullmatch(url):
        raise RelayError("not a claude.ai artifact link: expected https://claude.ai/artifact/<id> "
                         "or https://claude.ai/code/artifact/<id>")
    if not _own_digest(digest_id, session, now):
        raise RelayError(f"unknown digest {digest_id}: use the digest id this session's relay hub --page printed")
    with hub._locked():
        try:
            links = _read_links()
        except (OSError, ValueError):
            links = {}
        links[_key(session)] = {"url": url, "digest": digest_id, "at": now}
        hub._write_json(_links_path(), links)
