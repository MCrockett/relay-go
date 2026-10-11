# phone-artifact: spec

## Why

The relay hub (mobile-hub, PR #19) answers "what needs me?" in a remote-control chat. On a phone that reply is a long block of text: items scroll away, there is no way to glance at the list again without asking, and long excerpts wrap badly. A claude.ai artifact is a web page the hub session can publish and the owner can open on the phone. This feature lets the hub publish the digest it just printed as such a page. See idea.md.

What exists (checked 2026-10-11):
- `hub.digest(session, records, as_json, now)` loads the dashboard data, builds the numbered items, saves the digest under an id and renders text or JSON. Saved items keep `seen` and `repo_path` for actions; `render_json` leaves them out (`PRIVATE`).
- Items carry free text from other sessions: a session's last words (`excerpt`) and ask texts. Excerpts can quote absolute paths on the workstation.
- A Claude session can publish a local HTML file as a private claude.ai artifact and republish later to the same link. Codex sessions have no such tool.

## Owner answers (2026-10-11)

- One link, updated in place: the hub republishes the same artifact on each refresh, so the owner can keep it open or bookmark it.
- Read-only: the page shows the digest; the owner still types actions in the hub chat.
- From mobile-hub, still in force: the hub acts only when asked, with no polling and no push alerts.

## Decisions

- D1. **relay renders the page; the hub session publishes it.** `relay hub --page` (with or without `--json`) saves and prints the digest exactly as today and also writes the same digest, same id and same numbers, as one self-contained HTML file. The page is built with the standard library from the same items the text and JSON come from, so it is tested and looks the same every time. The session never writes the page itself. Reason: a page the agent writes by hand from JSON would differ run to run and could not be tested.
- D2. **One file, one link.** The page is always written to `~/.relay/hub/page.html` (mode 0600, written to a temp file and renamed). relay keeps the artifact's link in `~/.relay/hub/page.json` once the hub records it with `relay hub page-url <url>`. `relay hub --page` reports both: in text, a last line `page: <path>` and, when known, `publish to: <url>`; in JSON, a `page` object `{"path": ..., "url": ... or null}`. `page-url` accepts only a claude.ai artifact link (`https://claude.ai/artifact/<id>` or `https://claude.ai/code/artifact/<id>`) and refuses anything else. It is not an owner action and needs no `--relayed`: it records where the hub's own page lives.
- D3. **What the page shows.** A header with the title "Relay hub", the digest id, the time it was made (workstation local time with zone), where the data came from and its age, and when its item numbers stop working (24 hours after it was made, from mobile-hub D4). Then the "Could not check everything" lines if the scan failed, or "Nothing needs you right now." when nothing waits. Then one card per item in digest order, showing what the text digest shows for that item: number, repo and feature or session label and provider, stage and status or session state, wait time, ask texts and flags, PR number and CI, the actions the item allows, the excerpt (cut to the text digest's length), and "open this session to answer" where it applies, marked so it stands out. Then the closing count line, and a footer: "Read-only. To act, tell the hub, for example: merge 3." When the digest could not be saved, items have no numbers and the header says actions need a new digest, as the text does.
- D4. **The page states its own age.** A short inline script, with no network access, shows how long ago the page was made, and once the 24 hours have passed shows a banner: "These numbers have expired. Ask the hub for a new digest." Without scripts the page still reads correctly with its fixed time.
- D5. **Nothing private goes on the page.** The page lives on claude.ai and can be shared, so it carries less than the hub chat:
  - no session ids, repo paths, checkout paths, `seen` fingerprints, dashboard token or anything from `~/.relay/ui.json`;
  - every free-text value (excerpts, ask texts, flags, errors, labels) has absolute workstation paths cut to their last part: a path that starts with `~/` or with `/Users/`, `/home/`, `/private/`, `/var/`, `/tmp/`, `/opt/` or `/Volumes/` becomes its final name (`/Users/x/app/google-services.json` becomes `google-services.json`);
  - every value is HTML-escaped; no item text is ever inserted as markup, attributes or script.
  The text and JSON digests are unchanged.
- D6. **The page follows the artifact rules.** One HTML file with a `<title>` of "Relay hub", inline CSS only, no external scripts, fonts or images, colors as tokens on `:root` with a dark set under `prefers-color-scheme: dark` (guarded by `:root:not([data-theme="light"])`) and again under `:root[data-theme="dark"]`, an explicit `body` background, a layout that works at phone width with a 16px side gutter and no horizontal scroll, and long words and links that wrap. The file stays well under 1 MB for any digest.
- D7. **The hub skill publishes only when asked.** The `relay-hub` skill gains a section: when the owner asks for the page ("show me the page", "send me the link", "refresh the page"), run `relay hub --json --page`, then publish the file it names as an artifact, to the recorded link when there is one, and give the owner the link with a one-line summary (how many items, oldest wait). After the first publish, record the link with `relay hub page-url <url>`. If a publish to the recorded link is refused because the artifact is gone or not editable, publish a new one and record its link. If the session cannot publish artifacts (Codex, or no artifact tool), say so and show the digest in the chat. The hub never shares the page, never publishes anything else, and never publishes on its own between the owner's requests. Acting stays as before: only on what the owner types in the chat, on references from a digest they were shown. Numbers on the page belong to the digest it shows, which is the latest one the hub printed when the page was made.

## Requirements

- R1. `relay hub --page` writes `~/.relay/hub/page.html` for the same digest id and items the text or JSON output shows, in addition to that output, which is unchanged (D1).
- R2. The page file is written atomically with mode 0600; the folder keeps its existing mode (D2).
- R3. `relay hub page-url <url>` stores a claude.ai artifact link in `~/.relay/hub/page.json` and refuses any other value with a one-line error; `relay hub --page` reports the path and the stored link in text and JSON (D2).
- R4. The page shows the header, warnings or "Nothing needs you right now.", one card per item in digest order with the fields in D3, the closing counts and the read-only footer (D3).
- R5. An unsaved digest renders unnumbered cards and the "actions need a new digest" header line (D3).
- R6. The page embeds its creation and expiry times and an inline script that shows its age and an expiry banner after 24 hours; the page reads correctly without the script (D4).
- R7. The page never contains session ids, repo or checkout paths, `seen` fingerprints or the dashboard token (D5).
- R8. Absolute workstation paths in free text are cut to their final part on the page, by the rule in D5.
- R9. Every item value is HTML-escaped; item text containing markup, quotes or script renders as plain text (D5).
- R10. The page meets the artifact rules in D6: title, inline CSS only, no external resources, light and dark tokens, explicit body background, phone-width layout without horizontal scroll.
- R11. The `relay-hub` skill carries the publishing rules in D7, and its tests check that they are present.
- R12. The README's hub section gains a short paragraph on the phone page.

## Failure paths

- F1. The page cannot be written (folder unwritable, disk full): the digest still prints and its references still work; text gets a warning line on stderr, JSON reports `page: null` with the reason in `warnings`. No partial `page.html` is left behind.
- F2. `page.json` is missing, unreadable or holds something that is not a claude.ai artifact link: the link is reported as unknown (`url: null`), and the hub publishes a new artifact.
- F3. `page-url` cannot write `page.json`: it exits non-zero with the reason; the page link is simply not remembered.
- F4. The dashboard data is stale or the scan failed: the page shows the same age and "Could not check everything" lines the text shows; it never shows an all-clear the text would not.
- F5. An item has values missing or of the wrong type (an old snapshot): the card leaves that field out rather than failing the whole page.
- F6. The owner opens a page older than 24 hours: the expiry banner shows (with scripts), and the fixed expiry time in the header says the same (without).

## Non-goals

- Buttons or forms on the page, or any way for the page to act or message the hub.
- Publishing from relay itself, or any network call from relay for the page.
- Polling, scheduled refreshes or push alerts.
- A page for Codex hubs.
- Changes to the dashboard, the text digest or the JSON digest beyond the `page` field and line.

## Open questions
