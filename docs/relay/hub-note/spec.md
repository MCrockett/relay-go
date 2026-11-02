# hub-note: spec

## Why

A local app in another repo, run by the owner, needs two things from relay: which hub sessions are open, and a way to send one of them a free-text request. Today `relay hub note` only takes a digest item ref and must be passed on by an agent with `--relayed`, and nothing on the command line lists hub sessions. The owner approved this in chat on 2026-11-01. See idea.md.

relay already has the parts. `hub.registered()` returns the registered hub sessions (mobile-hub D9). `notes.send` cleans the text, writes the notes log and posts to a Claude session's inbox socket. The dashboard decides whether a session can be posted to in `snapshot.send_note`: a session shown as ended or stopped is never posted to, and otherwise the inbox path from the session's record is used. This feature adds one small command on top of them and changes nothing else.

## Decisions

- D1. **One new command, `relay note`, with `--hub`.** `--hub` is required; `relay note` without it is refused with a one-line message, so the command can later grow other targets without changing what `--hub` means. It is for the owner's own tools and terminal, so it takes no `--relayed` and does not check who runs it. The two forms are exclusive: `--list` needs `--json` and takes neither `--session` nor text; a send takes text, may take `--session`, and takes neither `--list` nor `--json`. Any other mix is refused with a one-line message and nothing is read or posted.
- D2. **What "open" means.** A hub session is open when all of these hold, read live from disk and never from a dashboard cache:
  1. its provider is `claude` (only Claude sessions have an inbox socket);
  2. its session record under `~/.relay/sessions` exists and is well formed (`sessions._valid`);
  3. the dashboard would not call it ended or stopped: `leftoff._shown_state(record, alive)` is neither `ended` nor `stopped`, with `alive` from `sessions.alive(sessions.read_records())`;
  4. the record names an inbox path, and that path is a socket owned by the current user (the same test `notes.post` applies before it connects, written once as a small helper used by both).
  This is the rule `snapshot.send_note` follows for notes, plus the socket check, so the list and the send agree with each other and with the dashboard.
- D3. **`--list --json`.** Prints a JSON list, one object per registered hub session, in the order of the hub file, with exactly these keys: `provider`, `session_id`, `last_active` and `open`. The order comes from a new small function in `relaylib/hub.py` that returns the hub file's entries as a list; `registered()` and every existing caller stay as they are. `last_active` is the record's `at` as epoch seconds (a number) when the record is well formed by `sessions._valid`, and null for anything else (missing, unreadable, malformed, or without a numeric `at`). `open` is true or false by D2. A registered session whose record is gone is listed with `last_active` null and `open` false. An empty or missing registry prints `[]` and exits 0. There is no plain-text listing.
- D4. **Sending.** `relay note --hub [--session <session_id>] "<text>"`:
  1. With `--session`, the id must be a registered hub session (any provider; ids are matched exactly, and a bare id that matches two providers is refused as ambiguous). Without it, the command picks the only open hub session; with none open, or more than one, it refuses and names the open sessions (provider and id), or says none are open.
  2. The chosen session must be open by D2, else the command refuses and posts nothing, saying whether it is not registered or not open.
  3. The text goes through `notes.send(provider, session_id, text, inbox, prefix=..., queue=False)`, so `notes.clean` applies (empty or over 2,000 characters is refused) and the note is recorded in the notes log like any other.
  4. Exit 0 only when the note was posted to the socket. For this command, queued-but-not-posted is a failure, and a failed call must not deliver the note later through the hook at the session's next prompt. So `notes.send` gets one new keyword argument, `queue` (default true, which keeps today's behaviour for every existing caller). With `queue=False`, a failed post (or a session with no inbox) writes nothing to the log: the note is never published as queued, so no concurrent `notes.take` can deliver it and nothing is left to remove. In that case `send` returns `(None, "not posted")` and the command exits non-zero with a message that the note was not delivered and nothing is left waiting. The post and the log write still happen under the one notes lock, as today.
  5. When `notes.send` reports that it posted but could not record the note (`NOT_RECORDED`), the command exits 0 and prints that message as a warning on stderr, as `relay hub note` does.
- D5. **Prefix.** The note carries a prefix naming where it came from, "Note from the owner, sent with relay note:", so the receiving session does not take it for a dashboard note. It is passed through `notes.send`'s existing `prefix` argument.
- D6. **Output and exit codes.** Any `OSError` or `ValueError` from reading the hub file, the records or the notes log, or from `notes.send`, is turned into a `RelayError` with a plain message, so the exit status is 1 and no traceback prints. Success prints one line to stdout, "relay: posted to <provider> session <id>", or the JSON for `--list --json` and nothing else on stdout. Every refusal is a `RelayError` with a plain message on stderr and exit status 1, the way other relay commands report errors.
- D7. **Documentation.** The README gets a short section under the notes text describing the two forms and the failure rule.

## Non-goals

- No new delivery channel, no network, no dashboard or UI change.
- No change to `relay hub note`, `hub.deliverable`, the hub file, or any existing behaviour.
- No queueing: this command never leaves a note for a later prompt.
- Codex hub sessions are listed but are never open, because relay has no socket to post to.

## Requirements

- R1. `relay note` without `--hub` is refused; `--hub` needs either `--list --json` or text; the mixes D1 forbids (`--list` with text or `--session`, `--list` without `--json`, `--json` or `--list` with a send) are refused with nothing read or posted. (D1)
- R2. `relay note --hub --list --json` prints a JSON list of `{provider, session_id, last_active, open}` for every registered hub session, `[]` when none, exit 0. (D3)
- R3. `open` follows D2 exactly: false for a Codex session, a missing or malformed record, an ended or stopped session, a record without an inbox, and an inbox path that is not a socket owned by the user; true otherwise. `last_active` is null only when the record is missing or unreadable. (D2, D3)
- R4. `relay note --hub --session <id> "<text>"` posts the cleaned text to that registered, open session through `notes.send`, records it in the notes log with status `posted`, and exits 0. (D4.1 to D4.3, D5)
- R5. A `--session` id that is not a registered hub session is refused and nothing is posted or logged, even if a session record and inbox exist for that id. (D4.1)
- R6. A registered session that is not open is refused and nothing is posted or logged. (D4.2)
- R7. Without `--session`: exactly one open hub session receives the note; zero or several open sessions are refused with a message naming the open ones (or saying none), and nothing is posted or logged. (D4.1)
- R8. A failed post (the socket stops accepting after the open check) exits non-zero and writes nothing to the notes log, so a concurrent `notes.take` can never deliver it; the message says the note was not delivered. `notes.send` with the default `queue=True` behaves exactly as before. (D4.4)
- R9. Text that `notes.clean` refuses (empty, too long) is refused with its message and nothing is posted. (D4.3)
- R10. A posted note that could not be recorded exits 0 with the warning on stderr. (D4.5)
- R11. A storage or read failure gives a plain message and exit 1, no traceback. (D6)
- R12. No other command changes behaviour; the existing test suite passes unchanged. (Non-goals)
- R13. The README describes both forms. (D7)

## Failure paths

- No hub file, or an unreadable one: `hub.registered()` is empty, so the list is `[]` and every send is refused as "not a registered hub session".
- Notes storage busy or unwritable: `notes.send` raises its own `RelayError` and nothing is posted.
- The session ends between the open check and the post: the post fails, nothing is logged, the command fails (R8).

## Open questions

