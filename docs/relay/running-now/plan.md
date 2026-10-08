# running-now Implementation Plan

**Goal:** Show what is running now (checked against the session's process), list the inbox newest wait first, and make "Where you left off" sortable, in `relay left`, `relay status` and the dashboard.

**Architecture:** The hook records the agent process (`sessions.find_process`, D1); `sessions.alive` checks recorded processes with one `ps` call (D2). `leftoff.running` and `leftoff.projects` take `alive` and apply D3 and D4. `relay left` gains the Running now block and `--recent`; `waiting.sort_key` and the page's `inboxOrder` turn newest first; the snapshot carries `running`; the page gains the section and the sort control.

**Tech Stack:** Python 3.11 standard library, `unittest`, `ps` output patched through one function per call site (`sessions._ps`), fake records and transcripts as in `tests/test_leftoff.py`, Node for page functions (skipped without Node).

**Spec:** `docs/relay/running-now/spec.md` (GO in round 2). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution line.
- Tests set `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` (and `HOME` where paths matter) to temporary folders; never real Claude or Codex; `ps` output is always patched with representative lines.
- The hook never fails the agent and keeps its time budget.
- `relay status --json` unchanged.

## Spec clarifications

- **`ps` access:** `sessions._ps(columns, timeout)` runs `ps -A -o <columns>` and returns its lines, or None on any failure or timeout. Both D1 (`pid=,ppid=,lstart=,args=`, 0.3 s) and D2 (`pid=,lstart=`, 1 s) use it. `lstart` is a fixed-width 24-character field (`Thu Oct  8 15:13:49 2026`); lines are parsed as pid, ppid, the next 24 characters, then args.
- **Agent match (D1):** on the first token of `args` (its base name), plus for `node` any later token containing `claude-code` (Claude) or `@openai/codex` (Codex).
- **`apply` stays pure:** `capture` decides whether to walk (D1 events, or no `process` on the old record), and passes the result into the new record after `apply`; `apply` copies the old `process` by default.
- **`--json` order (D8):** `cmd_status` sorts rows with `waiting.sort_key` before printing either form, so today's `--json` order is oldest wait first. D8's "keeps its order" means that order: the old key stays as `waiting.oldest_first_key` and is used for `--json`; the new newest-first `waiting.sort_key` is used for the text output only. A test asserts the `--json` order equals the oldest-first order.
- **Running entries and the dialog:** running entries also carry `state: "working"` and `pending_tools: []` (alongside the D5 fields), so `snapshot.other_session` handles them unchanged; Task 4 tests the endpoint with an entry produced by `leftoff.running`.
- **Stopped word:** `leftoff.state_word` gains `stopped`; entries carry `state: "stopped"` only in the presentation field `shown_state`, so `state` stays the record's state. Renderers use `shown_state`.

## Review Focus

1. The hook stays within its budget and never raises (Task 1).
2. `alive` None never produces `stopped`; edges at exactly 600 and 7200 seconds (Task 2).
3. `/api/session` still only answers for listed sessions (Task 4).
4. `relay status --json` unchanged (Task 3).

## Tasks

### Task 1: Recording and checking the process

Covers: R1, R2, D1, D2, F1, F3.

Files: `relaylib/sessions.py`, `tests/test_sessions.py`.

- [ ] Failing tests (patched `_ps`): the walk from the hook pid through `sh` to `claude` records `{pid, started}`; an npm `node .../claude-code/cli.js` agent is found; a `codex` record ignores a Claude ancestor; no agent found gives no `process`; `_ps` None gives no `process`; on `PostToolUse` with an existing `process` no walk happens and it is kept; on `UserPromptSubmit` a failed walk drops the old `process`, and the next `PostToolUse` walks and records the new one; a malformed `process` makes `_valid` false, records without it stay valid; `alive` returns the live session ids, leaves out a reused pid (different `lstart`) and returns None when `_ps` is None; `_ps` returns None on timeout (patched `subprocess.run` raising `TimeoutExpired`), on a missing `ps` (`FileNotFoundError`) and on a non-zero exit; `alive` returns None for empty output and for wholly unparseable output; the walk stops at pid 1 and after 20 steps (a fake 25-deep chain with the agent at the top is not found).
- [ ] Implement `_ps`, `find_process(provider, ps_lines, start_pid)`, the capture wiring, `_valid`, `alive`.
- [ ] Run, commit `feat: record the agent process behind each session`.

### Task 2: Running and stopped

Covers: R3, D3, D4, D5, F2.

Files: `relaylib/leftoff.py`, `tests/test_leftoff.py`.

- [ ] Failing tests: `running(records, marks, root, alive, now)` lists an owner's `working` session with a live process (at 7200 s yes, 7201 s no), one without a process (600 s yes, 601 s no), one with `alive` None (fallback), not a dead one, not an automated one, not `waiting` or `permission`; a running session without a last message is listed with `excerpt` None; one session whose read raises is skipped and another still listed (F2); order newest `at` first, ties by id; entry fields per D5. `projects(..., alive=...)` leaves running sessions out (and out of `more`), shows a dead `working` one as `stopped`, a quiet one without a process as `was working`, and never `stopped` with `alive` None.
- [ ] Implement `running`, the `alive` parameter (default None) on `projects`, `shown_state`.
- [ ] Run, commit `feat: tell running sessions from stopped ones`.

### Task 3: Terminal

Covers: R4, D6, D8.

Files: `relaylib/leftoff.py`, `relaylib/commands.py`, `relaylib/waiting.py`, `relaylib/status.py`, `relaylib/othersessions.py`, `tests/test_leftoff.py`, `tests/test_status.py`, `tests/test_waiting.py` (if sort tests live there), `tests/test_othersessions.py`.

- [ ] Failing tests: `relay left` prints the Running now block first (with and without excerpt, checkout and feature) and nothing extra when none run; `relay left --recent` one list newest first led by project, with the "Showing the newest 3 per project" line when any project has `more`, and `--recent --all` without it; `relay status` prints `*` rows newest wait first and rows without a wait after them; other sessions newest wait first; `--json` order still oldest wait first (`waiting.oldest_first_key`); `relay left` makes no `git fetch` and no `gh` call (existing test still passes).
- [ ] Implement: `cmd_left` reads `sessions.alive(records)` and prints `render_running` then the projects or the recent list; `--recent` flag; `waiting.sort_key` newest first (waits without a time after); `othersessions.listed` sorted newest first; update the existing tests that pinned oldest-first order.
- [ ] Run, commit `feat: running now and recent-first lists in the terminal`.

### Task 4: Snapshot and API

Covers: R5, D7, F4.

Files: `relaylib/ui/snapshot.py`, `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`.

- [ ] Failing tests: `build()` returns `running` (patched `sessions.alive`) with marks; running sessions are not in `left_off`; `leftoff.running` raising gives `running` `[]`; `/api/session` answers for a running session taken from `build()`'s own `running` list (an entry produced by `leftoff.running`: 200 with label its project, state `working`, the full message and the resume line) and still 404 for unlisted ones.
- [ ] Implement: one `sessions.alive(records)` call in `build()`, passed to both; `_listed` also searches `running`.
- [ ] Run, commit `feat: running now in the dashboard snapshot and API`.

### Task 5: The page

Covers: R6, D7, D8, D9.

Files: `relaylib/ui/page.html`, `tests/test_ui_server.py`.

- [ ] Failing tests: page strings `Running now`, `Nothing running.`, `By project`, `Most recent`, `Showing the newest 3 per project: run relay left --all for every session.`; section order running, inbox, left off, features; Node tests: `inboxOrder` newest first with undated rows last; `runLine(e, now)` gives "Running 25m" plus checkout and feature; running cards have no buttons and their 404 shows "This session is no longer listed." and reloads; `leftSort` read and write survive `localStorage` throwing (default "By project"); the "Most recent" render lists sessions newest first across projects with the project as eyebrow and the note when any `more`.
- [ ] Implement the section, `runLine`, `runCard`, `renderRunning`, the sort control and `renderLeftOff` modes; `stopped` in `leftLine`'s words.
- [ ] Browser check on a throwaway fixture server: Running now, the inbox order, both sort modes, the dialog.
- [ ] Run, commit `feat: running now and sorting in the dashboard`.

### Task 6: Docs and the full check

Covers: R7, R8.

Files: `README.md`.

- [ ] README: Running now (what counts, the process check and the 10-minute and 2-hour rules), stopped vs was working, the inbox's newest-first order in the dashboard and `relay status`, `relay left --recent`, the sort control.
- [ ] Read the README changes against R7 item by item.
- [ ] Full suite green; push; PR; CI green; `relay submit`.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1, R2 | 1 |
| R3 | 2 |
| R4 | 3 |
| R5 | 4 |
| R6 | 5 |
| R7 | 6 |
| R8 | 1 to 5, 6 |
