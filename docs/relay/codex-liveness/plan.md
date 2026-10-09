# codex-liveness Implementation Plan

**Goal:** Take a Codex session out of Running now as soon as its rollout shows the turn ended, or after 30 silent minutes, without changing Claude sessions.

**Architecture:** `agentask.codex_turn(session_id)` reads the last turn marker from the rollout's tail, scanning back up to 16 MB (D1). `leftoff.codex_turns(records, alive, now)` calls it once per Codex record that running-now D3 counts as running (D4). `leftoff.is_running(record, alive, now, turns=None)` applies D2. `leftoff.running` and `leftoff.projects` take `turns`; `cmd_left` and `snapshot.build` compute the map once and pass it to both.

**Tech Stack:** Python 3.11 standard library, `unittest`, fake rollout files with set mtimes (`os.utime`) under a temporary `CODEX_HOME`.

**Spec:** `docs/relay/codex-liveness/spec.md` (GO in round 3). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution line.
- Tests set `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME` to temporary folders; never real Claude or Codex.
- Every new read fails safe: any exception counts as `None` (spec D2, D5).

## Spec clarifications

- **Cost (spec review note on D4):** D1's scan back is bounded at 16 MB, so the cost per session is one bounded scan, usually a single 1 MB read (the turn markers are near the end). D4's "one tail read" means one bounded scan per Codex session D3 counts as running, shared by both lists.
- **Reading back:** `codex_turn` reads 1 MB chunks from the end. Bytes before a chunk's first newline are carried into the next (earlier) chunk, so a line split across two reads is joined and read whole; the newest chunk's text after its last newline (a partial line still being written) is dropped.
- **Newest file:** among `transcripts.files_for("codex", id)`, the one with the newest mtime.
- **`turns` default:** `is_running(record, alive, now, turns=None)`, `running(..., turns=None)` and `projects(..., turns=None)` keep today's results when `turns` is None, so existing callers and tests keep working.
- **A record not in `turns`** (a Claude record, or a Codex record D3 left out) is judged by D3 alone.

## Review Focus

1. `None` never hides a session (missing file, unreadable file, no markers in 16 MB, a reading error).
2. One `turns` map per run shared by both lists.
3. The 1800-second edge.

## Tasks

### Task 1: Reading the turn marker

Covers: R1, R5.

Files: `relaylib/agentask.py`, `tests/test_agentask.py`.

- [ ] Failing tests: `started`; `ended` for `task_complete` and for `turn_aborted`; the newest of several markers wins; non-marker records after the marker do not change it; a marker 1 to 16 MB back is found; a file with no marker in its last 16 MB gives `None`, and so does a large file with none at all; a line split across two 1 MB reads is read whole; a partial last line is ignored; a non-JSON line and a record without a dict `payload` are skipped; the newest of two rollout files for one id is read; `None` for a missing file, an unreadable file (patched `open` raising `OSError`) and a small file without markers; `mtime` is the file's mtime.
- [ ] Implement `codex_turn`.
- [ ] Run, commit `feat: read the last Codex turn marker`.

### Task 2: Running and the shared map

Covers: R2, R3, R5.

Files: `relaylib/leftoff.py`, `tests/test_leftoff.py`.

- [ ] Failing tests: `codex_turns` reads only Codex records that D3 counts as running (patched `codex_turn` records its calls: no Claude ids, no `waiting` or quiet ones) and maps a raising read to `None`; `is_running` with `turns`: `ended` leaves out, `started` with mtime 1801 s old leaves out, exactly 1800 s keeps, fresh keeps, `None` keeps, a D3-excluded record is never added, a Claude record is unchanged; `running` and `projects` with one map: a Codex session left out appears in `projects` as `was working` (and `stopped` when its process is known dead) and counts toward `more`; a rollout rewritten between the `running` and `projects` calls does not move the session, because both use the map.
- [ ] Implement `codex_turns`, the `turns` parameter on `is_running`, `running`, `projects` and `_shown_state` as needed.
- [ ] Run, commit `feat: Codex sessions leave Running now when their turn ends`.

### Task 3: Callers and docs

Covers: R3, R4, R5.

Files: `relaylib/leftoff.py` (`cmd_left`), `relaylib/ui/snapshot.py`, `tests/test_leftoff.py`, `tests/test_ui_snapshot.py`, `README.md`.

- [ ] Failing tests: `relay left` prints a Codex session whose rollout ended as `was working` under its project, not under Running now; `snapshot.build` leaves it out of `running` and puts it in `left_off`; `codex_turns` is called once per run (patched spy) in both; `codex_turns` raising leaves both lists as today (`turns` None).
- [ ] Implement: `cmd_left` and `build` compute `turns` once (guarded) and pass it to `running` and `projects`.
- [ ] README: the Codex check in the Running now section (leaves as soon as the turn ends, or after 30 silent minutes).
- [ ] Full suite green; push; PR; CI green; `relay submit`.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1 | 1 |
| R2 | 2 |
| R3 | 2, 3 |
| R4 | 3 |
| R5 | 1, 2, 3 |
