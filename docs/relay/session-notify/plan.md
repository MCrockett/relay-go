# session-notify Implementation Plan

**Goal:** Let the owner send a note to a session from the dashboard: posted to a Claude session's inbox socket at once, or queued and handed to the session by relay's hook, with each note's status shown.

**Architecture:** The Claude hook records `inbox` (D2). A new module `relaylib/notes.py` owns the note log (D5): file naming, the lock, add, remove, take-for-hook, prune and the inbox post (D3, D4). `sessions.capture` takes queued notes at the D1 events and prints them with any merge notice. The snapshot carries each listed session's notes; `server.py` adds `POST /api/note` and `/api/note/remove`; the page adds the Send a note box and the notes list.

**Tech Stack:** Python 3.11 standard library (`socket`, `fcntl` through relay's existing lock helper), `unittest`, a fake Unix socket server in a test thread, Node for page functions (skipped without Node).

**Spec:** `docs/relay/session-notify/spec.md` (GO in round 2). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution line.
- Tests set `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME` to temporary folders; never real Claude or Codex; sockets are fake servers in the test's temporary folder (short paths: macOS limits socket paths to about 104 bytes).
- The hook never fails the agent and keeps its time budget (0.1 s lock wait for notes).
- relay never reads, stores or sends `CLAUDE_CODE_MESSAGING_TOKEN`.

## Spec clarifications

- **Lock:** `notes` reuses `sessions._lock(path, timeout)` on `~/.relay/notes/.lock`; the hook passes 0.1 s, the dashboard 2 s.
- **Ids:** a note id is `secrets.token_hex(8)`.
- **Send is one locked operation (D1, D4, D5, F3):** `notes.send` takes the notes lock (2 s) and keeps it for the whole send: read the log, write it with the new note `queued`, then, when the session may be posted to (Claude, an `inbox` recorded, not shown as `ended` or `stopped`), post, and on success write the log again with the note `posted`. The hook's `take` needs the same lock, so it can never take a note that is mid-send, and a note is either posted or left for the hook, never both. If the lock stays busy, or the log cannot be read or the first write fails, `send` raises before any socket is opened (F3: nothing sent or posted). The post holds the lock for at most its 2-second timeout; a hook arriving then finds the lock busy and leaves its notes for its next event (D5). If the second write fails after a successful post, the note stays `queued` and `send` reports "Posted to the session, but relay could not record it, so it may arrive twice."
- **Order inside the hook:** `capture` takes the notes after writing the session record and outside the sessions lock (two locks are never held together), so a busy notes lock never delays the record.
- **Listed sessions:** the API reuses `snapshot._listed` to decide whether a session is listed; the snapshot attaches `notes` to each session entry (other sessions, running, left off).
- **Socket checks:** `os.lstat` on the path: `stat.S_ISSOCK`, `st_uid == os.getuid()`, not a symlink; then `socket.AF_UNIX` connect and `sendall` with a 2-second timeout.
- **R9 manual check:** done after Task 5, before `relay submit`, with a throwaway interactive Claude session in a temporary folder; recorded in the PR body (no transcript content).

## Review Focus

1. No lost or double-printed note under concurrency (Task 2).
2. The hook stays within budget and never raises (Task 3).
3. Notes only for listed sessions; the file name never escapes the folder (Tasks 2, 4).
4. The token is never touched (Task 1).

## Tasks

### Task 1: Recording the inbox

Covers: R1, R8.

Files: `relaylib/sessions.py`, `tests/test_sessions.py`.

- [ ] Failing tests: a Claude hook with `CLAUDE_CODE_MESSAGING_SOCKET` set to an absolute path records `inbox`; unset drops it; a relative path is not recorded; a Codex record never has `inbox`; `_valid` rejects an `inbox` that is not a non-empty absolute string and accepts records without it; the token variable, when set, appears nowhere in the record.
- [ ] Implement in `apply` (path from the environment passed in by `capture`) and `_valid`.
- [ ] Run, commit `feat: record each Claude session's inbox socket`.

### Task 2: The note log and posting

Covers: R2, R3, R8.

Files: `relaylib/notes.py` (new), `tests/test_notes.py` (new).

- [ ] Failing tests: file name is the hash (an id with `/` and `..` stays in the folder); folder 0700 and file 0600; `add` returns the note `queued`; `post` to a fake socket server sends exactly the D3 line and marks it `posted`; a missing path, a regular file, a symlink to a socket, another owner (patched `os.lstat` uid), a refused connection and a timeout leave it `queued`; `remove` of a queued note, and `Conflict` for a delivered, posted or missing one; `take` marks queued notes `delivered` and returns them, a second `take` returns nothing; 20 threads adding at once all survive; an `add` while `take` holds the lock lands after it and stays `queued`; a busy lock makes `take` return `[]` within 0.1 s and `add` raise after 2 s; pruning after 7 days and an empty log removed; over 2,000 characters and whitespace-only refused.
- [ ] Failing tests for `send` (the locked sequence above): a fake socket receives the line and the note ends `posted`; with the lock held by another thread, `send` raises after 2 s and the fake socket receives nothing; with an unreadable log (a directory in its place) and with the first write failing (patched `os.replace`), `send` raises and the socket receives nothing; a `take` started while `send` is posting (the fake server holds the connection until the test releases it) returns `[]`, and after the post the note is `posted` and a later `take` returns nothing; the second write failing after a post leaves the note `queued` and returns the may-arrive-twice message.
- [ ] Implement `notes.add`, `post`, `send` (the locked sequence), `remove`, `take`, `list_for`, `prune`, `render` (the `additionalContext` text with the D3 prefix).
- [ ] Run, commit `feat: the note log and inbox posting`.

### Task 3: Delivery in the hook

Covers: R3, R8.

Files: `relaylib/sessions.py`, `relaylib/notices.py`, `tests/test_sessions.py`.

- [ ] Failing tests: Claude `UserPromptSubmit` and `PostToolUse`, Codex `UserPromptSubmit` print queued notes; other events print nothing and leave them queued; notes and a merge notice come out in one `hookSpecificOutput`; a busy notes lock writes the record and prints nothing; a real log failure (the log file replaced by a directory, and a notes folder without write permission) writes the record, prints nothing and leaves the notes `queued` once the failure is removed; `notes.take` raising writes the record and prints nothing; pruning runs with the session records' pruning.
- [ ] Implement: `NOTE` events per provider, `capture` calls `notes.take` after the record write, `notices.render` accepts extra lines.
- [ ] Run, commit `feat: hand queued notes to the session at its next event`.

### Task 4: Snapshot and API

Covers: R4, R8.

Files: `relaylib/ui/snapshot.py`, `relaylib/ui/server.py`, `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`.

- [ ] Failing tests: session entries carry `notes`; `POST /api/note` 200 for a listed session (queued, or posted with a fake socket), 404 unlisted, 400 for a missing or non-string field, empty, whitespace-only and over-long text; `/api/note/remove` 200, 409 with current notes for delivered, posted and missing notes, 404 for an unlisted session, 400 for a missing or non-string field; F3 end to end: with the notes lock held, and with an unwritable notes folder, `POST /api/note` answers an error, nothing reaches a fake inbox socket, and the page shows the error; a session shown as `ended` or `stopped` is queued even with a socket; token and Host checks reject as for other POST routes.
- [ ] Implement both routes and the snapshot field.
- [ ] Run, commit `feat: send and remove notes from the dashboard API`.

### Task 5: The page

Covers: R5.

Files: `relaylib/ui/page.html`, `tests/test_ui_server.py`.

- [ ] Failing tests: page strings from D6; Node tests: `noteWords(note, provider, now)` for `queued` (Claude and Codex), `delivered`, `posted`; Remove only on `queued`; the not-running line for `ended` and `stopped`; a 409 redraws the list from the response.
- [ ] Implement the box, the list and the calls in the session dialog.
- [ ] Browser check on a throwaway fixture server.
- [ ] Run, commit `feat: send a note from the session dialog`.

### Task 6: Docs, the manual check and the full run

Covers: R7, R9.

Files: `README.md`; and only if R9's fallback applies: `docs/relay/session-notify/spec.md`, `relaylib/notes.py`, `relaylib/ui/server.py`, `relaylib/ui/page.html`, `tests/test_notes.py`, `tests/test_ui_server.py`.

- [ ] README per R7.
- [ ] R9 manual check; if the line format is refused, apply R9's fallback (drop `posted`, page copy, README and tests) and update the spec first.
- [ ] Full suite green; push; PR; CI green; `relay submit`.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1 | 1 |
| R2 | 2 |
| R3 | 2, 3 |
| R4 | 4 |
| R5 | 5 |
| R7 | 6 |
| R8 | 1 to 4 |
| R9 | 6 |
