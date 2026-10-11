# phone-artifact: plan

Spec: `docs/relay/phone-artifact/spec.md`. Gate after every task: `python3.11 -m unittest discover -s tests -t . -v`.

New module `relaylib/hubpage.py` holds the page: redaction, rendering, the page file and the link record. `relaylib/hub.py` keeps the digest and calls it. Tests go in `tests/test_hubpage.py` (the `HubHome` fixture from `tests/test_hub.py`: a temporary `RELAY_HOME`). No test calls real Claude, Codex or the Artifact tool.

## Task 1: redaction

Files: `relaylib/hubpage.py` (new), `tests/test_hubpage.py` (new).

`redact(text, ids=(), token=None)` returns text with the D5 rules applied, in this order: the dashboard token and secret patterns become `[hidden]`; UUIDs and each known id (longest first) become `[id]`; email addresses become `[email]`; absolute workstation paths become their final name. Non-string input returns `""`. `known_ids(found, records)` collects ids from the hub registry (`hub.registered()`), session records and the items (`session_id`, `session.session_id`). `dashboard_token()` reads `token` from `ui.json` (`server._discovery_path()`), or `None` on any error.

Test `RedactTest`: every row of the D5 table; a known non-UUID id inside text; the token inside text; `gho_`, `github_pat_`, `sk-ant-`, `xoxb-`, `AKIA`, `AIza` and a JWT; `api_key=...`, `auth: ...`; a 40-character hex run hidden and a 7-character SHA kept; `~/x/y.txt` and `/private/tmp/a/b` cut to `y.txt` and `b`; a path that is not a workstation path (`src/app.py`, `/api/snapshot`) kept; `None` and numbers give `""`.

Covers: R7, R8.

## Task 2: cards and page body

Files: `relaylib/hubpage.py`, `tests/test_hubpage.py`.

`render(digest_id, source, found, counts, failed=(), now=None, ids=(), token=None)` returns the HTML page as a string. Header: "Relay hub", digest id, made at (local time with zone, from `now`), data source and age (`hub._header` wording), and "numbers work until <time>" (now + `hub.KEEP_S`); for `digest_id=None`, the "actions need a new digest" line and unnumbered cards. Then "Could not check everything: ..." lines for `failed`, or "Nothing needs you right now." when there are no items and nothing failed. One card per item in digest order with the fields of D3, built from the same values `hub._item_lines` uses (error, session and feature items; wait from `wait_since`; `answer_here` marked with a class that stands out). Then `hub._closing(counts)` and the footer "Read-only. To act, tell the hub chat that sent this link, for example: merge 3." Every free-text value goes through `redact`, then `html.escape(quote=True)`. Fields that are missing or of the wrong type are left out of that card (F5). Structured private fields (`session_id`, `repo_path`, `seen`, `checkout`) are never read for output.

Test `RenderTest`: the three item kinds show their fields in digest order; the unsaved digest has no numbers and the header line; nothing waiting; a failed scan shows its lines and no all-clear (F4); `answer_here` card has the marker; a card with `wait_since="x"`, `asks=None`, `pr="7"` still renders with those fields left out (F5); item text `<script>alert(1)</script>` and `" onmouseover="x` appear only escaped; a session item with id `S-123` and `repo_path` `/Users/x/p` and `seen` `{"commit": "c0ffee..."}`: none of those strings appear in the page (R7); an excerpt quoting the token is hidden.

Covers: R4, R5, R7, R9.

## Task 3: page shell, limits and age script

Files: `relaylib/hubpage.py`, `tests/test_hubpage.py`.

The shell: `<!doctype html>`, charset and `viewport` metas, `<title>Relay hub</title>`, one inline `<style>` with color tokens on `:root`, the dark set under `@media (prefers-color-scheme: dark)` with `:root:not([data-theme="light"])` and again under `:root[data-theme="dark"]`, `body` background, 16px side gutter, `overflow-wrap: anywhere` on card text, no width above 328px. Limits: free text cut to 400 characters (excerpts to `hub.EXCERPT`), at most 5 asks and 5 flags per card then "+N more", at most 100 cards; then, if the encoded page exceeds 512 KB, cards are dropped from the end until it fits. When any card is left out, "N more items: ask the hub" follows the last card. The page embeds `data-made` and `data-expires` (epoch seconds) on `<body>` and one inline script that writes "made N minutes ago" into a placeholder and shows the expiry banner "These numbers have expired. Ask the hub for a new digest." once `Date.now()` passes the expiry; the header's fixed expiry time stays for readers without scripts.

Test `ShellTest`: the title, viewport, `:root` tokens, both dark selectors, body background; no `src=`, `href="http`, `@import` or `url(` in the page; one `<script>` with no `fetch`, `XMLHttpRequest`, `WebSocket` or `import`; a 150-item digest whose items carry 1,000-character values in every field and 9 asks: 100 cards, "50 more items", "+4 more", no value over 400 characters, under 512 KB; a digest of 100 items of 400 wide Unicode characters each (forcing the byte budget): fewer than 100 cards, the "more items" line with the right count, under 512 KB; the expiry banner text and the two data attributes present.

Covers: R6, R10.

## Task 4: the page file

Files: `relaylib/hubpage.py`, `relaylib/hub.py`, `tests/test_hubpage.py`, `tests/test_hub.py`.

`write(digest_id, html)` writes `~/.relay/hub/pages/<digest id>.html` through a temp file in the same folder, mode 0600, `os.replace`, and returns the path; on any `OSError` it removes the temp file and raises. `hub._prune` also removes `pages/<id>.html` when it removes a digest, and removes page files whose digest is gone.

Test `PageFileTest`: path, content and mode 0600; two digests write two files, neither replacing the other; an unwritable `pages` folder raises and leaves no temp file (F1). In `tests/test_hub.py` `DigestStoreTest`: pruning a digest older than 24 hours beyond the newest 20 also removes its page, and an orphan page is removed.

Covers: R1, R2.

## Task 5: the link record

Files: `relaylib/hubpage.py`, `tests/test_hubpage.py`.

`ARTIFACT = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{1,128}")`. `record(session, digest_id, url, now)` refuses (RelayError, one line) a url that does not fully match, a session of `None`, or a digest id that `hub` did not save for this session within 24 hours (it reads the digest file as `hub.resolve` does); then under `hub._locked()` it writes `pages.json` (a dict keyed `"provider:session_id"`, values `{"url", "digest", "at"}`) with `hub._write_json`, keeping other sessions' entries. `published(session)` returns this session's `{"url", "digest", "at"}` with all values `None` when the file is missing, unreadable, malformed, or the url does not match (F2).

Test `LinkTest`: record then read back; a second session's record keeps the first's; a non-claude.ai url, `http://` url, url with a trailing path, another session's digest, an expired digest and an unknown digest are refused; a corrupt `pages.json` and a stored bad url read as all `None`; an unwritable folder makes `record` raise `OSError` (F3).

Covers: R3.

## Task 6: `relay hub --page` and `relay hub page-url`

Files: `relaylib/hub.py`, `relaylib/commands.py`, `tests/test_hub.py`, `tests/test_commands.py`.

`hub.digest(..., page=False)`: when `page` is true and the digest was saved, it renders the page from the same `found`, `source`, counts and failures, with `known_ids` and `dashboard_token()`, writes it, and adds the page info: text gets `page: <path>` and, when `published(session)` has a url, `publish to: <url>`; JSON gets `"page": {"path", "url", "published_digest", "published_at"}`. On a write failure, or when the digest could not be saved, text gets no page line and a warning ("could not write the page: ..."), and JSON gets `"page": null` with the warning in `warnings` (F1). Without `--page` the output is byte for byte what it is today. Parser: `--page` on `hub`; subcommand `page-url digest url`, which calls `hubpage.record` with `_hub_session(...)` and prints "relay: hub page link recorded"; errors exit non-zero with the message (F3).

Tests: `OutputTest.test_page_matches_the_digest` (the page's digest id equals the printed one and every item label appears, text and JSON); `test_no_page_without_the_flag` (output equal to a run before the change, from a fixed fake dashboard); `test_page_write_failure_keeps_the_digest` (text warning, `page: null`, references still resolve); `test_page_reports_the_link` (after `record`, both outputs show url and digest). `tests/test_commands.py`: `relay hub page-url` from a fake agent environment records and from the owner's terminal is refused (no session), and a bad url exits non-zero.

Covers: R1, R3.

## Task 7: skill and README

Files: `skills/relay-hub/SKILL.md`, `README.md`, `tests/test_hub.py`.

The skill gains a "The phone page" section with D7's rules: only when the owner asks; `relay hub --json --page`; publish the named file as an artifact, to the recorded url when there is one; after every successful publish `relay hub page-url <digest id> <url>`; a refused link means a new artifact; the not-refreshed wording, both forms; never share, never publish anything else, never on its own; when a number means different items on the page and in the newest chat digest, ask which, naming both. README: one paragraph under "The hub: relay from your phone".

Test `SkillTest.test_the_skill_has_the_page_rules`: the section contains `--page`, `page-url`, "not refreshed", "no page has been published from this hub yet", "never share", and "naming both"; no em-dash.

Covers: R11, R12.

## Task 8: live check and acceptance ledger

Files: `docs/relay/phone-artifact/build-notes.md`, `docs/relay/phone-artifact/acceptance.md`.

From this Claude hub session: `relay hub --json --page`; publish the page with the Artifact tool; `relay hub page-url`; a second `relay hub --json --page`; publish to the same url; confirm the link did not change, then view it at 360px width in a browser and check for no horizontal scroll and the dark set. Record the result, with no link or machine path, in build notes, with the requirement-to-test table. `acceptance.md` lists the owner opening the link on their phone, trigger: the owner's next "show me the page", status: open.

Covers: R13.
