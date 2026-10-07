# waiting-visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Say what the owner needs to do, and for how long it has waited, the same way in the dashboard and `relay status`; show what a waiting agent asked; stop a reopened Claude session from hiding a wait; put the real owner buttons on inbox cards.

**Architecture:** Two new modules, one responsibility each. `relaylib/agentask.py` reads what a session last said from the CLI's local log, live and never stored (D3). `relaylib/waiting.py` turns a feature's state, flags and session health into asks with their `since` (D4, D6). `relaylib/health.py` takes over `session_health` from the UI so `relay status` can use it (D2). `status.py`, the snapshot and the page are thin callers.

**Tech Stack:** Python 3.11 standard library, `unittest`, temp git repos from `tests/helpers.py`, Node for the page's pure functions (skipped without Node).

**Spec:** `docs/relay/waiting-visibility/spec.md` (GO in round 2, `reviews/spec-2.codex.md`). Executors read both.

## Global Constraints

- Python 3.11 standard library only; one module per responsibility in `relaylib/`.
- Plain English in copy and docs; no em-dashes.
- Commits: `<type>: short summary`, ending with the session's attribution line.
- Tests: `python3.11 -m unittest discover -s tests -t . -v`. Every new or changed test sets `RELAY_HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` to temporary folders (R12). Never call real Claude or Codex.
- Conversation text is never written to `~/.relay`, git, notifications or any cache, and never included in `relay status --json` beyond the excerpt (D3, R6).
- The page sets agent text with `textContent` only.

## Spec clarifications

- **Claude SessionStart and the merge notice:** today `SessionStart` is in `sessions.WORKING["claude"]`, which also triggers the merge notice in `capture`. It leaves `WORKING`; a new `NOTICE = {"claude": {"UserPromptSubmit", "SessionStart"}, "codex": {"UserPromptSubmit"}}` keeps the notice exactly as it is.
- **Stuck file name:** `write_stuck` uses `write_md` with `unique=True`, so a second stop on one stage writes `<stage>-stuck-2.md`. The reason comes from the highest-numbered `<stage>-stuck*.md` on the ref (D4 item 2).
- **Where the stuck reason is read:** dashboard rows read it at the published commit the snapshot already shows (`seen.commit`); `relay status` reads it at the ref the row came from (`HEAD` of the checkout for local rows, the origin branch for remote rows). Both through `gitops.ls_files` and `gitops.show`.
- **Clearing `review_error`:** in `state.write_state`, the one place every state write goes through: when `status != "review-error"`, `review_error` is removed. This covers every path out of review-error (R5) without touching each caller.
- **`session_health` return value:** `health.session_health(repo, st, records, now)` returns `(health, activity, record)`; the record is needed for the ask kind (record state waiting or permission) and for the session id `agentask` reads.
- **Which asks have buttons (D7):** `ASK_ACTIONS = {decide: [go, extra-round, reset-rounds], review-failed: [go, review], merge: [merge]}`, each filtered by the row's `actions` (which already apply `owneractions.applicable`, merge readiness and running reviews).
- **`health_inbox`:** kept in rows for compatibility; the page stops using it.
- **Two orders (R6, R11):** `status.scan` and the snapshot keep today's row order (waiting first, then repo and feature), so the dashboard's features table is unchanged. Only two views sort by wait: `relay status`'s printed and `--json` output (`cmd_status` sorts with `waiting.sort_key` before rendering) and the page's inbox list (sorted in the page by `wait_since`).
- **Repository-error rows:** `status.scan` builds a row with `feature == "?"` directly when a checkout cannot be read, and `snapshot._enrich` returns early for it. Both paths set `asks = waiting.asks(row, None, None, None, None)` (the error ask, no `since`), `wait_since = None`, `excerpt = None` and `waiting_on_owner = True`.

## Review Focus

1. Stop, then SessionStart(resume) keeps waiting with the Stop's `since` (Task 1 test, the kpi-collection case).
2. An older Codex `task_complete` followed by newer assistant text: the newer text wins (Task 3 test).
3. `relay status` and the dashboard count the same waiting features for the same fixtures (Task 6 and Task 7 tests).
4. No conversation text in `~/.relay` after a dashboard build and a `relay status --json` run (Task 7 test).
5. The card for a ready-to-merge feature whose session is also waiting shows "Merge PR #n" first, then the answer ask and excerpt, with only the Merge button (Task 5 and Task 8 tests).

## File Structure

- Create `relaylib/agentask.py`: `last_words(provider, session_id)`, `excerpt(text)`, `full(text)`.
- Create `relaylib/waiting.py`: `asks(row, st, health, record, stuck_reason)`, `since(asks)`, `stuck_reason(repo, ref, slug, stage)`, `sort_key(row)`.
- Modify `relaylib/sessions.py`: D1, `NOTICE`.
- Modify `relaylib/machine.py`, `relaylib/commands.py`, `relaylib/state.py`: `review_error` (D5).
- Modify `relaylib/health.py`: `session_health` moved here from `relaylib/ui/snapshot.py`.
- Modify `relaylib/status.py`: asks, sort, render, `--json`.
- Modify `relaylib/ui/snapshot.py`: row asks, `wait_since`, `excerpt`; detail `agent_text`.
- Modify `relaylib/ui/page.html`: card asks, excerpt, buttons, no Options; detail text; inbox sort.
- Modify `README.md`: the status line and the inbox paragraph.
- Tests: `tests/test_sessions.py`, `tests/test_machine.py`, `tests/test_state.py`, `tests/test_commands.py`, new `tests/test_agentask.py`, new `tests/test_waiting.py`, `tests/test_health.py`, `tests/test_status.py`, `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`.

## Tasks

### Task 1: A session start is not work

Covers: R1 (D1).

Files: `relaylib/sessions.py`, `tests/test_sessions.py`.

- [ ] Write failing tests in `ApplyTest`:
  - Stop at 1000, then SessionStart with `source` resume at 1001: state waiting, `since` 1000, `at` 1001, `event` SessionStart.
  - Same with source startup, clear, a missing source and `source: "other"`: waiting kept.
  - PermissionRequest, then SessionStart(resume): permission kept with its pending list.
  - UserPromptSubmit, then SessionStart(compact): working.
  - No record, SessionStart(startup): waiting. No record, SessionStart(compact): working.
  - Working record, SessionStart(resume): waiting.
  - Codex SessionStart still returns None.
  - Update the existing `test_states_follow_events` line that starts with SessionStart; it still ends waiting.
- [ ] Hook tests: `test_codex_and_claude_session_start` still gets the merge notice on Claude SessionStart.
- [ ] Implement: remove `SessionStart` from `WORKING["claude"]`; add `NOTICE` and use it in `capture` for the notice; in `apply`, a Claude `SessionStart` branch:
  ```python
  elif provider == "claude" and name == "SessionStart":
      if event.get("source") == "compact":
          new_state, pending = "working", []
      elif state in ("waiting", "permission"):
          new_state = state                       # reopening is not an answer (D1)
      else:
          new_state, pending = "waiting", []
  ```
- [ ] Run `tests/test_sessions.py`, then commit `fix: keep a reopened session waiting on the owner`.

### Task 2: A failed review keeps its reason

Covers: R5 (D5).

Files: create `relaylib/redact.py` and `tests/test_redact.py`; modify `relaylib/machine.py`, `relaylib/commands.py`, `relaylib/state.py`, `tests/test_machine.py`, `tests/test_state.py`, `tests/test_commands.py`.

- [ ] Failing tests:
  - `machine.apply_error(st, "boom\nsecond line")` sets status review-error and `review_error == "boom"`; a long first line is cut to 200 characters; `"spawn /Users/someone/.local/bin/codex ENOENT"` is saved as `"spawn codex ENOENT"`.
  - `redact.public_line` (D5, state.md is published): the first non-empty line; paths cut to their last part (`/Users/<name>/.local/bin/codex` and `~/x/y.md` keep `codex` and `y.md`; `3/4` and `and/or` untouched); the current username (from `getpass.getuser()`, three characters or more, whole word) becomes `<user>`; email addresses become `<email>`; URLs keep only scheme and host (credentials, path and query dropped); token-like strings (24 or more letters, digits, `-` or `_`, with both a letter and a digit) become `<redacted>`; plain words unchanged; at most 200 characters; empty or None gives "".
  - `state.write_state` drops `review_error` when status is not review-error and keeps it when it is.
  - In `test_commands.py`, a review whose fake reviewer fails leaves `review_error` in the published state; a following successful `relay review` removes it.
- [ ] Implement `redact.public_line(text, limit=200)` and `apply_error(st, error)`: `st["status"] = "review-error"`, then `st["review_error"] = redact.public_line(error)` only when that is non-empty. Nothing raw is ever saved. Pass `error` at the one call site in `commands.py`. In `write_state`, `if st.get("status") != "review-error": st.pop("review_error", None)` before writing.
- [ ] Run the three test files, commit `feat: keep why a review failed`.

### Task 3: What the agent last said

Covers: R2, R3 (D3, D8).

Files: create `relaylib/agentask.py`, `tests/test_agentask.py`.

- [ ] Failing tests with fixture logs written in temp `CLAUDE_CONFIG_DIR/projects/<folder>/<session>.jsonl` and `CODEX_HOME/sessions/2026/10/07/rollout-...-<session>.jsonl`:
  - Claude: last assistant text; an `away_summary` after it wins (`source` "summary"); an `away_summary` before a newer assistant text loses; a last assistant record with only `tool_use` blocks is not a candidate (the earlier text wins); a subagent file with newer text is ignored.
  - Codex: `task_complete.last_agent_message`; an assistant `response_item` alone; an older `task_complete` followed by newer assistant text (newer wins); neither gives None.
  - More than 1 MiB of earlier lines before the answer: found, and the cut first line is skipped. A partial last line after complete records: the complete records are used.
  - Missing file, unreadable file, non-JSON content: None, no exception.
  - `excerpt`: whitespace collapsed, 160 characters plus "…" when cut, unchanged when short. `full`: at most 4,000 characters.
- [ ] Implement:
  ```python
  WINDOW, EXCERPT, FULL = 1 << 20, 160, 4000

  def last_words(provider, session_id):
      """{source, text} from the newest candidate in the session's own log, or None (D3, D8)."""
  ```
  Use `transcripts.files_for(provider, session_id)` and keep only the parent file for Claude (not under `/subagents/`). Read `min(size, WINDOW)` bytes from the end; drop the first line when the read did not start at 0; drop the last line when it does not end with a newline; parse each line, skipping non-JSON; keep the newest candidate per D3. Catch `OSError` and `ValueError` and return None.
- [ ] Run, commit `feat: read what a waiting agent last said`.

### Task 4: Session health outside the UI

Covers: groundwork for R6, R8 (D2).

Files: `relaylib/health.py`, `relaylib/ui/snapshot.py`, `tests/test_health.py`, `tests/test_ui_snapshot.py`.

- [ ] Move `session_health` and `_since_ts` to `health.py` unchanged except the return value `(health, activity, record)`. `snapshot.feature` calls `health.session_health`. Update `tests/test_ui_snapshot.py:274` to the new location and a three-item result; add a `test_health.py` test that the record returned is the one `match` picked.
- [ ] Run both files, commit `refactor: move session health out of the UI`.

### Task 5: The asks

Covers: R4 (D4, D6), F2, F3, F4.

Files: create `relaylib/waiting.py`, `tests/test_waiting.py`.

- [ ] Failing tests, one per kind and text (D4 items 1 to 8), with `since` per D6:
  - error row (`feature == "?"`): "Fix: <flag>", no since.
  - waiting-owner with stuck reason: "Decide: build review stopped. no progress on R1-1"; without: "Decide: build review stopped".
  - review-error with and without `review_error`.
  - ready-to-merge not stale: "Merge PR #6"; with a "fallback GO: … your call" flag, that text appended after " · "; stale (a "stale" flag or the agent-confirm flag): no merge ask.
  - handoff flag: take ask.
  - record waiting with attention health: "Answer the claude session", since = record `since`.
  - record permission with two pending tools: "Approve Bash, Edit in the codex session".
  - activity-only health "no activity 23h" with no record (F2): "Check the claude session: no activity 23h", since = health `since`.
  - Order: ready-to-merge plus waiting session gives [merge, answer]; waiting-owner plus permission gives [decide, approve].
  - Done feature: no asks.
  - Error row with `st` None: only the error ask.
  - `since(asks)` is the oldest; None when none has one. `sort_key` puts waiting rows first, oldest wait first, no-since after, then repo and feature.
  - `stuck_reason` in a temp repo: reads the frontmatter `reason` of the highest-numbered `<stage>-stuck*.md` at a ref; None when absent or unparsable.
- [ ] Implement `asks(row, st, health, record, stuck_reason)` returning `[{"kind", "text", "since"}]`, using `state`'s `updated` (ISO, to a timestamp) for state asks. Flags are read from `row["flags"]` exactly as `status.py` writes them today ("handoff: … `relay take`", "stale …", "fallback GO: …").
- [ ] Run, commit `feat: say what the owner needs to do`.

### Task 6: `relay status` agrees with the dashboard

Covers: R6, R7, F5.

Files: `relaylib/status.py`, `tests/test_status.py`.

- [ ] Failing tests in a temp projects folder with hook records under `RELAY_HOME/sessions` and a fixture Claude transcript:
  - A drafting feature whose owner session record is waiting: row marked `*`, counted in "1 waiting on you", followed by the indented lines "Answer the claude session · waiting 16h" and "Agent: How do you want…".
  - Two waiting features: the older wait prints first in the text and `--json` output, while `scan()` itself returns today's order (fixtures where the two orders differ).
  - An unreadable checkout: its row prints "Fix: <error>" with no age, is counted, and comes after the dated waits.
  - Hook records folder missing, and a record file with bad JSON (F2): a ready-to-merge feature still shows its merge ask, an idle checkout still shows the check ask from local activity, and no answer or approve ask appears.
  - `--json` rows carry `asks` and `excerpt`, and never the full text (a 300-character answer appears only as its 160-character excerpt).
  - Session health raising for one feature (patched): that row keeps its state asks; the others are unaffected (F5).
  - No new fetch: `gitops.fetch` is called only from `_build_flags`, as today (patched and counted).
  - The existing tests keep passing (`test_rows_and_waiting_first`, `test_a_pushed_handoff_is_waiting_on_the_owner`, render).
- [ ] Implement: `scan` reads `sessions.read_records()` once; the error row it builds gets the error-row fields from the clarification; `_row` and `_remote_row` call `health.session_health` (inside try, F5), `waiting.stuck_reason` at their ref, `waiting.asks`, and `agentask.last_words` only for answer or approve; set `asks`, `wait_since`, `excerpt`, and `waiting_on_owner = bool(asks)`. `scan` keeps its sort; `cmd_status` sorts with `waiting.sort_key` before `render` or `--json`. `render` prints the two indented lines under waiting rows. `cmd_status --json` drops nothing new beyond `excerpt` (no full text is ever put in the row).
- [ ] Run, commit `feat: show what relay status is waiting on`.

### Task 7: Snapshot rows and detail

Covers: R8, D3 (storage), R12 (status and dashboard agree).

Files: `relaylib/ui/snapshot.py`, `tests/test_ui_snapshot.py`.

- [ ] Failing tests:
  - A snapshot row for a feature with a waiting session record and a transcript has `asks`, `wait_since` and `excerpt`; `feature()` returns `agent_text` `{source, text}` with the full text.
  - For the same fixtures, the rows that `status.scan` marks waiting equal the snapshot rows with asks.
  - After building the snapshot and running `relay status --json`, no file under `RELAY_HOME` contains a distinctive phrase from the fixture transcript.
  - A missing transcript: the answer ask shows, `excerpt` and `agent_text` are None (F1).
  - Row order is unchanged from today for fixtures where wait order differs (R11).
  - An unreadable checkout's row has the error ask, `wait_since` None and `excerpt` None, and counts as waiting, as in `relay status`.
  - Missing hook records (F2): state asks and the activity-only check ask are present; no answer or approve ask.
- [ ] Implement in `_enrich`: after the published row and health, call `waiting.stuck_reason(repo, seen["commit"], ...)` and `waiting.asks`, set `waiting_on_owner = bool(asks)`, keep `health_inbox` as today; set `excerpt` from the detail; the early return for `feature == "?"` sets the error-row fields first. In `feature()`, compute `agent_text` from `agentask.last_words` for the record when the record state is waiting or permission. Row order stays as `status.scan` returns it.
- [ ] Run, commit `feat: put the owner's asks in the dashboard snapshot`.

### Task 8: The page

Covers: R9, R10, R11 (D7).

Files: `relaylib/ui/page.html`, `tests/test_ui_server.py`.

- [ ] Failing tests:
  - Page strings: `ASK_ACTIONS`, `row.asks`, `row.excerpt`, `wait_since`, "Agent's last message", "Summary while you were away", `agent_text`; no `'Options'` button code (`node('button','Options')` absent).
  - Node test (skipped without Node) of the pure functions `cardButtons(row)` and `askLine(row)`: a ready-to-merge row with a waiting session gives buttons [merge] and the line "Merge PR #6 · Answer the claude session · waiting 2h"; a waiting-owner row gives [go, extra-round, reset-rounds]; a review-error build row gives [go, review]; an answer-only row gives no buttons and no Options; Release never appears.
  - The detail text is set through `textContent` (string check that the agent text node is built with `node(` and never `innerHTML`).
  - Node test: `inboxOrder(rows)` sorts by `wait_since`, missing last, while the features table code still iterates `data.rows` in order (string check).
  - Update `test_page_shows_session_health` for the removed Options rule.
- [ ] Implement: card reason shows `askLine(row)`; the excerpt on its own line labeled "Agent:" or "Summary:"; buttons from `cardButtons(row)` through the existing `actions(row, only)` confirmation path; remove the Options branch; inbox list sorted with `inboxOrder` (oldest `wait_since` first, missing last); the features table keeps `data.rows` order; the counts use `row.waiting_on_owner`, which is now `bool(asks)`. Detail: under the Session line, the labeled full text from `d.agent_text` and, for approve, the pending tool names.
- [ ] Check in a browser against a throwaway server with fixture data: the card text, excerpt, buttons per kind, the confirmation dialog from a card button, and the detail text. Then change the feature's state on origin (a new state commit) without refreshing the page, confirm a card action, and check the existing "changed since you looked" conflict message appears (F6).
- [ ] Run, commit `feat: show the owner's asks and real buttons on inbox cards`.

### Task 9: Docs and the full check

Covers: R12 (suite), README.

Files: `README.md`.

- [ ] README: the `relay status` line says it shows what each waiting feature needs and how long it has waited; the inbox paragraph names the asks, the agent excerpt (read live, never stored) and the card buttons.
- [ ] Full suite green. Real-data check on this machine: `relay status` lists nba-experiments with "Answer the claude session" and its excerpt; kpi-collection's record is now only fixed for new events (an existing working record stays until its next event), so check it shows "Check the claude session: no activity …" today.
- [ ] Commit `docs: describe what the inbox shows`, push, open the PR, CI green, `relay submit`.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1 | 1 |
| R2, R3 | 3 |
| R4 | 5 |
| R5 | 2 |
| R6, R7 | 6 |
| R8 | 7 |
| R9, R10, R11 | 8 |
| R12 | 1 to 8 (tests in each), 9 |
