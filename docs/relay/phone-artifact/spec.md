# phone-artifact: spec

## Why

The relay hub (mobile-hub, PR #19) answers "what needs me?" in a remote-control chat. On a phone that reply is a long block of text: items scroll away, there is no way to glance at the list again without asking, and long excerpts wrap badly. A claude.ai artifact is a web page the hub session can publish and the owner can open on the phone. This feature lets the hub publish the digest it just printed as such a page. See idea.md.

What exists (checked 2026-10-11):
- `hub.digest(session, records, as_json, now)` loads the dashboard data, builds the numbered items, saves the digest under an id and renders text or JSON. Saved items keep `seen` and `repo_path` for actions; `render_json` leaves them out (`PRIVATE`).
- Items carry free text from other sessions: a session's last words (`excerpt`) and ask texts. Excerpts can quote absolute paths on the workstation.
- Claude Code's Artifact tool publishes a local HTML file as a claude.ai artifact that is private by default, and republishes a later version of a file to the same link when given that link (`url`). This session used it on 2026-10-11 (listing, publishing). Codex sessions have no such tool. The build ends with a live check (R13) that the whole path works.

## Owner answers (2026-10-11)

- One link, updated in place: the hub republishes the same artifact on each refresh, so the owner can keep it open or bookmark it.
- Read-only: the page shows the digest; the owner still types actions in the hub chat.
- From mobile-hub, still in force: the hub acts only when asked, with no polling and no push alerts.

## Decisions

- D1. **relay renders the page; the hub session publishes it.** `relay hub --page` (with or without `--json`) saves and prints the digest exactly as today and also writes the same digest, same id and same numbers, as one self-contained HTML file. The page is built with the standard library from the same items the text and JSON come from, so it is tested and looks the same every time. The session never writes the page itself. Reason: a page the agent writes by hand from JSON would differ run to run and could not be tested.
- D2. **One file per digest, one link per hub session.**
  - The page for a digest is written to `~/.relay/hub/pages/<digest id>.html` (mode 0600, written to a temp file and renamed into place), so the path names exactly the digest the session just printed and no other run can replace it. Page files are pruned with their digests (mobile-hub D4: older than 24 hours, keeping the newest 20).
  - relay keeps each hub session's artifact link in `~/.relay/hub/pages.json`, keyed by the session (provider and id), with the digest id last published there. `relay hub page-url <digest id> <url>` records them; it accepts only a digest this same session printed (the rule references already follow) and only a claude.ai artifact link (`https://claude.ai/artifact/<id>` or `https://claude.ai/code/artifact/<id>`), and refuses anything else with a one-line error. It is not an owner action and needs no `--relayed`: it records where this hub's own page lives. The file is written under the hub folder's lock, like `sessions.json`.
  - `relay hub --page` reports the page: in text, a last line `page: <path>` and, when this session has one, `publish to: <url>`; in JSON, a `page` object `{"path": ..., "url": ... or null, "published_digest": ... or null}`.
  - Two hub sessions therefore never share a link or a file, so one hub can never publish another's digest. One hub session works one request at a time, so within a session the file it publishes is the one its own last command named.
- D3. **What the page shows.** A header with the title "Relay hub", the digest id, the time it was made (workstation local time with zone), where the data came from and its age, and when its item numbers stop working (24 hours after it was made, from mobile-hub D4). Then the "Could not check everything" lines if the scan failed, or "Nothing needs you right now." when nothing waits. Then one card per item in digest order, showing what the text digest shows for that item: number, repo and feature or session label and provider, stage and status or session state, wait time, ask texts and flags, PR number and CI, the actions the item allows, the excerpt (cut to the text digest's length), and "open this session to answer" where it applies, marked so it stands out. Then the closing count line, and a footer: "Read-only. To act, tell the hub chat that sent this link, for example: merge 3." When the digest could not be saved, items have no numbers and the header says actions need a new digest, as the text does.
- D4. **The page states its own age.** A short inline script, with no network access, shows how long ago the page was made, and once the 24 hours have passed shows a banner: "These numbers have expired. Ask the hub for a new digest." Without scripts the page still reads correctly with its fixed time.
- D5. **Nothing private goes on the page.** The page lives on claude.ai and could be shared, so it carries less than the hub chat. Structured fields are left out, and every free-text value (excerpts, ask texts, flags, errors, labels, the session label) passes through one redaction function before it is escaped:
  - Left out: session ids, repo and checkout paths, `seen` fingerprints, and anything from `~/.relay/ui.json`.
  - Paths: an absolute workstation path, one starting with `~/` or with `/Users/`, `/home/`, `/private/`, `/var/`, `/tmp/`, `/opt/` or `/Volumes/`, becomes its final name.
  - Ids: any UUID (8-4-4-4-12 hex) becomes `[id]`, and so does any session id relay knows (the hub registry, the session records and the digest's own items), wherever it appears.
  - Secrets: these become `[hidden]`:
    - the dashboard token, if it appears;
    - `gh[pousr]_` and `github_pat_` tokens; `sk-` and `sk-ant-` keys; `xox[abprs]-` tokens; `AKIA` and `ASIA` key ids; `AIza` keys;
    - JWT-shaped values (`eyJ...` with two dots);
    - the value after `Bearer `;
    - the value in `name=value` or `name: value` when the name contains token, secret, password, passwd, api_key, apikey or auth;
    - any run of 32 or more base64 or hex characters.
  - Contact details: email addresses become `[email]`.
  - Escaping: every value is HTML-escaped after redaction; no item text is ever inserted as markup, attributes or script.

  Representative cases the tests check:

  | Input | On the page |
  |---|---|
  | `claude --resume 958dee3a-031e-4b49-be5f-09efa15292b1` | `claude --resume [id]` |
  | `[app/google-services.json](/Users/x/p/app/google-services.json)` | `[app/google-services.json](google-services.json)` |
  | `export GH_TOKEN=ghp_0123456789abcdefghijABCDEFGHIJ012345` | `export GH_TOKEN=[hidden]` |
  | `Authorization: Bearer abc.def.ghi` | `Authorization: Bearer [hidden]` |
  | `password: hunter22` | `password: [hidden]` |
  | `mail me at someone@example.com` | `mail me at [email]` |
  | `<script>alert(1)</script>` | shown as that text, not run |

  Redaction is best effort for free text that other sessions wrote; it is a second guard, not a promise that any secret an agent prints is caught. The page carries nothing the hub chat does not already carry. The text and JSON digests are unchanged.
- D6. **The page follows the artifact rules.** One HTML file with a `<title>` of "Relay hub", inline CSS only, no external scripts, fonts or images, colors as tokens on `:root` with a dark set under `prefers-color-scheme: dark` (guarded by `:root:not([data-theme="light"])`) and again under `:root[data-theme="dark"]`, an explicit `body` background, a layout that works at 360 CSS pixels wide with a 16px side gutter and no horizontal scroll (`overflow-wrap: anywhere` on card text, no fixed widths above 328px), and a `viewport` meta tag. Size is bounded: each free-text value is cut to 400 characters (excerpts keep the text digest's shorter cut), the page shows at most the first 100 items and then one line "N more items: ask the hub", and the file stays under 512 KB.
- D7. **The hub skill publishes only when asked.** The `relay-hub` skill gains a section: when the owner asks for the page ("show me the page", "send me the link", "refresh the page"), run `relay hub --json --page`, then publish the file it names as an artifact, to the recorded link when there is one, and give the owner the link with a one-line summary (how many items, oldest wait). After the first publish, record the link with `relay hub page-url <url>`. If a publish to the recorded link is refused because the artifact is gone or not editable, publish a new one and record its link. After a successful publish, record the digest it holds with `page-url`. If the session cannot publish artifacts (Codex, or no artifact tool), or `--page` reports `page: null`, or the publish fails for any other reason (offline, timeout, sign-in), say in one line that the page was not refreshed, that the link still shows the digest made at <time>, and show the digest in the chat. The hub never shares the page, never publishes anything else, and never publishes on its own between the owner's requests.
  Acting stays as before: only on what the owner types in the chat, on references from a digest they were shown. The page's numbers belong to the digest it shows (`published_digest`), which can differ from the newest digest in the chat, for example after the digest the hub prints following an action. When the owner names an item by number and the page's digest and the newest chat digest give that number different targets, the hub asks which one, naming both, and never guesses (the existing rule for words that could match more than one item).

## Requirements

- R1. `relay hub --page` writes `~/.relay/hub/pages/<digest id>.html` for the same digest id and items the text or JSON output shows, in addition to that output, which is unchanged (D1).
- R2. The page file is written atomically with mode 0600 and pruned with its digest; one session's run never replaces another digest's page (D2).
- R3. `relay hub page-url <digest id> <url>` stores this session's artifact link and published digest in `~/.relay/hub/pages.json`, refuses a digest another session printed or a value that is not a claude.ai artifact link, and keeps each session's link separate; `relay hub --page` reports the path, this session's link and its published digest in text and JSON (D2).
- R4. The page shows the header, warnings or "Nothing needs you right now.", one card per item in digest order with the fields in D3, the closing counts and the read-only footer (D3).
- R5. An unsaved digest renders unnumbered cards and the "actions need a new digest" header line (D3).
- R6. The page embeds its creation and expiry times and an inline script that shows its age and an expiry banner after 24 hours; the page reads correctly without the script (D4).
- R7. The page never contains session ids, repo or checkout paths, `seen` fingerprints or the dashboard token, including when free text quotes them (D5).
- R8. Free text on the page passes the D5 redaction: every case in D5's table gives the output shown.
- R9. Every item value is HTML-escaped; item text containing markup, quotes or script renders as plain text (D5).
- R10. The page meets the artifact rules in D6: title, viewport tag, inline CSS only, no external resources, light and dark tokens, explicit body background, the 360px layout rules, the 400-character and 100-item limits and the 512 KB ceiling (a test builds a digest of 150 items with maximal text).
- R11. The `relay-hub` skill carries the publishing rules in D7, and its tests check that they are present.
- R12. The README's hub section gains a short paragraph on the phone page.
- R13. Live check at build: from a Claude hub session, `relay hub --json --page`, publish the page, record the link, republish after a second digest to the same link, and open it at phone width. Recorded in build notes.

## Failure paths

- F1. The page cannot be written (folder unwritable, disk full): the digest still prints and its references still work; text gets a warning line on stderr, JSON reports `page: null` with the reason in `warnings`. No partial `page.html` is left behind.
- F2. `pages.json` is missing, unreadable, or holds something for this session that is not a claude.ai artifact link: the link is reported as unknown (`url: null`), and the hub publishes a new artifact.
- F3. `page-url` cannot write `pages.json`: it exits non-zero with the reason; the page link is simply not remembered, and the next refresh publishes a new artifact.
- F4. The dashboard data is stale or the scan failed: the page shows the same age and "Could not check everything" lines the text shows; it never shows an all-clear the text would not.
- F5. An item has values missing or of the wrong type (an old snapshot): the card leaves that field out rather than failing the whole page.
- F6. A publish fails (no tool, offline, timeout, sign-in, refused link): the hub says the page was not refreshed and which digest the link still shows, and shows the digest in the chat (D7). A refused link leads to a new artifact.
- F7. The owner opens a page older than 24 hours: the expiry banner shows (with scripts), and the fixed expiry time in the header says the same (without).

## Non-goals

- Buttons or forms on the page, or any way for the page to act or message the hub.
- Publishing from relay itself, or any network call from relay for the page.
- Polling, scheduled refreshes or push alerts.
- A page for Codex hubs.
- Changes to the dashboard, the text digest or the JSON digest beyond the `page` field and line.

## Open questions
