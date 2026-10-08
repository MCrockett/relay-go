# other-sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** List the sessions waiting on the owner that no open feature accounts for, in `relay status` and the dashboard inbox, read-only, so the owner sees everything waiting on them in one place and answers in the terminal.

**Architecture:** A new `relaylib/othersessions.py` selects and labels session records (D1 to D4). `status.scan_with_claims` and the snapshot gather which sessions features already account for (D2) and pass them in. `relay status` prints a section; the snapshot carries `other_sessions`; a new `GET /api/session` returns one listed session's full last message; the page shows cards and a dialog.

**Tech Stack:** Python 3.11 standard library, `unittest`, fake session records and fake transcripts (as `tests/test_agentask.py` writes them), temp git repos from `tests/helpers.py`, Node for page functions (skipped without Node).

**Spec:** `docs/relay/other-sessions/spec.md` (GO in round 2, `reviews/spec-2.codex.md`). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution line.
- Tests: `python3.11 -m unittest discover -s tests -t . -v`; every test sets `RELAY_HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` to temporary folders; never real Claude or Codex.
- Read-only and local (D8): nothing written to repositories, nothing published, last messages never stored.
- Feature rows, their asks and `relay status --json` stay unchanged.
- No action buttons and no terminal opening on session cards (owner decision 2026-10-08).

## Spec clarifications

- **How the claimed ids reach selection (spec review note 1):** `othersessions.listed(records, claimed, root, cfg, now)`, where `claimed` is a set of session ids. D4's `rows` argument becomes `claimed`; feature rows are never read by `listed`.
- **Dashboard disconnected (spec review note 2):** a failed `/api/session` request (network error, server stopped) uses the page's existing request-error display (`notice(message, true)`), as every other request does; the dialog is not opened.
- **Settings:** `health.UI_LIMITS` gains `"other_sessions_hours": (1, 168, 24)`. `health.ui_settings` keeps returning `(grace, quiet, notes)` (its notes now cover the new key too); a new `health.ui_value(cfg, key)` returns one validated value. `listed` reads grace and window from the global configuration it is given (`config.load()`); the snapshot adds the global configuration's notes to its notes.
- **Repository name for D3:** the main checkout of the checkout containing the folder is `os.path.dirname(gitops.common_dir(checkout))`; its folder name is the label, followed by the checkout's own folder name in parentheses when the two differ. Longest matching checkout wins when checkouts nest.
- **Excerpt:** `agentask.excerpt(words["text"])` with its source, as feature rows use. `pending_tools` are the record's `pending` tool names, in order, duplicates kept once.

## Review Focus

1. A session holding a not-done feature is never listed; one whose feature is done is (Task 2).
2. Status output and `--json` for feature rows are unchanged (Task 3).
3. `/api/session` cannot read a transcript the snapshot did not list (Task 4).
4. Session cards carry no action buttons (Task 5).

## Tasks

### Task 1: Selecting and labeling sessions

Covers: R1, R2, R3, D1, D3, D4, F2, F3, F5.

Files: `relaylib/othersessions.py` (new), `relaylib/health.py`, `tests/test_othersessions.py` (new), `tests/test_health.py`.

- [ ] Failing tests (fake records written as `sessions` writes them; fake transcripts as in `test_agentask`):
  - Listed: a waiting Claude session with a last message, held past the grace, inside the window; a permission Codex session without a transcript, with its tools.
  - Not listed: waiting within the grace; waiting longer than the window; ended; working; waiting without a last message (including an unreadable transcript); a session id in `claimed`.
  - Order: oldest `since` first, ties by session id.
  - Labels: a folder in a repository under the root (`repo`), a subfolder of it, a linked worktree (`repo (repo-wt)`), a folder under the home directory (`~/x/y`, with `HOME` set to a temp folder), a folder elsewhere (as is), a folder that no longer exists (its path), a record with `cwd` null ("Unknown folder").
  - Entries carry `provider, session_id, label, folder, state, since, pending_tools, excerpt`.
  - One record whose transcript read raises an unexpected error is skipped and the others are listed (patch `agentask.last_words`).
  - `[ui] other_sessions_hours`: default 24; 2 narrows the window; 0, 200 and a string fall back to 24 with the note "ignored invalid [ui] other_sessions_hours"; `ui_settings` still returns three values.
- [ ] Implement `health.ui_value`, the new limit, and `othersessions.listed(records, claimed, root, cfg, now)` with a `label(cwd, checkouts)` helper (checkouts from `status.checkouts(root)`, computed once per call; avoid an import cycle by importing `status` inside the function or by passing checkouts in).
- [ ] Run, commit `feat: select the sessions waiting on the owner outside any feature`.

### Task 2: Which sessions the features account for

Covers: D2, R1.

Files: `relaylib/status.py`, `tests/test_status.py`.

- [ ] Failing tests: `status.scan_with_claims(root)` returns `(rows, claimed)`: `claimed` holds the owner session of a not-done feature (even when its status is ready-to-merge, which health does not watch) and the session id of the record health matched for a row; it does not hold the owner session of a done feature. `status.scan(root)` returns the same rows as before.
- [ ] Implement: `with_asks` reports the matched record to its caller (a return value or an out-parameter set, keeping the row unchanged); `scan_with_claims` gathers the ids during the existing scan; `scan` returns `scan_with_claims(root)[0]`.
- [ ] Run, commit `feat: know which sessions the listed features account for`.

### Task 3: The terminal section

Covers: R4, D5, F1.

Files: `relaylib/status.py`, `tests/test_status.py`, `tests/test_commands.py` if the CLI tests live there.

- [ ] Failing tests:
  - With a feature and two listed sessions: the table as before, then "Other sessions waiting on you", the lines `* proteindiary · claude · waiting 2h` and `    Agent: <excerpt>`, a permission line `* ~/x · codex · needs approval: Bash · 5m`, and the count line "3 waiting on you (*)" when the feature waits too.
  - With no features and one session: "No relay features…" followed by the section and "1 waiting on you (*)".
  - No sessions: output identical to today's.
  - `--all` lists the same sessions; `--json` output equals today's (same keys, no sessions).
- [ ] Implement: `cmd_status` uses `scan_with_claims`, calls `othersessions.listed` with `sessions.read_records()` (an exception there lists none, F1/F5) and `config.load()`, and `render(rows, others=())` prints the section.
- [ ] Run, commit `feat: other sessions waiting on you in relay status`.

### Task 4: Snapshot and API

Covers: R5, R6, D6, D7.

Files: `relaylib/ui/snapshot.py`, `relaylib/ui/server.py`, `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`.

- [ ] Failing tests:
  - `build()` returns `other_sessions` (patched records and transcripts): a waiting session in an unrelated folder is listed; a feature's owner session is not; feature rows are unchanged; an invalid global `other_sessions_hours` adds its note.
  - `GET /api/session?provider=claude&session=S1` for a listed session: 200 with `provider, session_id, label, state, pending_tools, text, source, resume` (`resume` is `claude --resume S1`, or `codex resume C1`); `text` is `agentask.full` of the last message.
  - The transcript removed after the snapshot: 200 with `text` and `source` null.
  - A session not in the cached snapshot's `other_sessions` (including a real record that is claimed): 404. Missing provider or session: 400. No token: 403.
- [ ] Implement: `build()` gathers claimed ids from each feature detail (`state.owner.session` for not-done features, `session_record.session_id`) and calls `othersessions.listed`; the server handler reads the cache's current data, finds the entry, and reads `agentask.last_words` live.
- [ ] Run, commit `feat: other sessions in the dashboard snapshot and API`.

### Task 5: The page

Covers: R7, D6.

Files: `relaylib/ui/page.html`, `tests/test_ui_server.py`.

- [ ] Failing tests: page strings `Claude session`, `Codex session`, `Waiting for approval: `, `Resume with: `, `No last message to show.`, `This session is no longer waiting.`, and a `session-dialog`; Node tests: `sessionLine(entry, now)` gives "Waiting on you · 2h" and "Waiting for approval: Bash · 5m"; `inboxOrder` sorts session entries and feature rows together by wait start (sessions use `since`); a session card has no `.actions` buttons; `inbox-count` counts features and sessions.
- [ ] Implement: `renderRows` appends session cards (class `card decide session`) into the inbox order; the card click fetches `/api/session` and opens `session-dialog` (label and provider title, tools for a permission session, the message in a `pre` built through `node()`, the resume line); 404 shows "This session is no longer waiting." and reloads; other failures use `notice(message, true)`.
- [ ] Browser check on a throwaway fixture server (`relay ui` is owner-only: use the server module directly): a session card shows beside feature cards, has no buttons, and opens the dialog with the full message.
- [ ] Run, commit `feat: other session cards in the dashboard inbox`.

### Task 6: Docs and the full check

Covers: R8, R9.

Files: `README.md`.

- [ ] README: in the status and dashboard sections, what "Other sessions waiting on you" lists (D1, D2), that it is read-only and you answer in the terminal, the resume line, and `[ui] other_sessions_hours`.
- [ ] Full suite green; push; PR; CI green; `relay submit`.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1 | 1, 2 |
| R2, R3 | 1 |
| R4 | 3 |
| R5, R6 | 4 |
| R7 | 5 |
| R8 | 6 |
| R9 | 1 to 5 (tests in each), 6 |
