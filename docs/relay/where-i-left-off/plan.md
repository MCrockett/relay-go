# where-i-left-off Implementation Plan

**Goal:** Show, per project, the newest sessions on this machine in any state and how to resume each, through `relay left` and a "Where you left off" dashboard section, read-only.

**Architecture:** `agentask.interactive` tells the owner's sessions from automated runs (D1). A new `relaylib/leftoff.py` selects, groups, orders and limits them and builds resume lines (D2 to D5, D7). `status.local_marks` gives feature marks without network for `relay left`; the snapshot builds marks from its feature details (D6). `relay left` prints them; the snapshot carries `left_off`; `/api/session` also answers for left-off sessions; the page shows the section and reuses the session dialog.

**Tech Stack:** Python 3.11 standard library, `unittest`, fake records and transcripts (as `tests/test_agentask.py` and `tests/test_othersessions.py` write them), temp git repos and fake `gh`/`git` call logging from `tests/helpers.py`, Node for page functions (skipped without Node).

**Spec:** `docs/relay/where-i-left-off/spec.md` (GO in round 3). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution line.
- Tests: `python3.11 -m unittest discover -s tests -t . -v`; every test sets `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` (and `HOME` where labels or resume lines depend on it) to temporary folders; never real Claude or Codex.
- Read-only and local (D12); `relay left` makes no network request and no fetch.
- `relay status`, its `--json`, the inbox and other-sessions' selection stay unchanged.
- No action buttons on left-off cards.

## Spec clarifications

- **Labels (D3, D11):** `othersessions` gains `place(cwd, checkouts)` returning `(project, checkout)`: for a folder in a checkout, the repository name and the checkout folder name (`checkout` None when equal to the name); otherwise the D3 folder label and None. `label` becomes a thin wrapper over `place`, so other-sessions output is unchanged.
- **`more` (D5):** counted over the project's records that pass D1 and were not walked. `--all` (`limit=None`) walks everything and `more` is 0.
- **Ages:** `health.age`, as `relay status` uses it.
- **Session entry state words** are built by the renderers (terminal and page) from `state` and `pending_tools`; the entry carries the raw state.

## Review Focus

1. Automated runs (`sdk-cli`, `codex exec`) never shown; an inconclusive interactive answer is not cached (Task 1).
2. `relay left` runs no `git fetch` and no `gh` (Task 3).
3. `/api/session` still cannot read a transcript the snapshot did not list (Task 4).
4. `relay status` and other-sessions unchanged (Tasks 2, 3).

## Tasks

### Task 1: Owner's sessions

Covers: R1, D1.

Files: `relaylib/agentask.py`, `tests/test_agentask.py`.

- [ ] Failing tests: Claude transcript with `entrypoint: cli` is interactive, `sdk-cli` and `sdk-ts` are not; Codex `session_meta` with `originator: codex-tui` is, `originator: codex_exec` or `source: exec` is not; missing, empty and marker-less transcripts are not; a Claude transcript whose first line is incomplete is not, then after the line is completed (same process) it is; a conclusive answer is cached (the file deleted afterwards still answers the same).
- [ ] Implement `interactive(provider, session_id)` reading at most the first 64 KB of the newest parent transcript (`transcripts.files_for`, subagents excluded as `last_words` does), complete lines only, and a module-level cache of conclusive answers.
- [ ] Run, commit `feat: tell the owner's sessions from automated runs`.

### Task 2: Selecting, grouping and resume lines

Covers: R2, D2, D3, D4, D5, D7, F2, F3, F4.

Files: `relaylib/leftoff.py` (new), `relaylib/othersessions.py`, `tests/test_leftoff.py` (new), `tests/test_othersessions.py`.

- [ ] Failing tests: interactive sessions in each state shown with `state`, `at`, `pending_tools`, excerpt; automated ones and one without a last message left out (a `permission` one with tools but no message shown); a repo and its worktree grouped as one project with `checkout` set for the worktree; a folder outside any repo; `cwd` null ("Unknown folder"); projects newest first, sessions newest first, ties by id; limit 3 with `more` counting a not-walked session that has no message; `limit=None` shows all with `more` 0; marks applied (`feature: {slug, done}`); resume lines for `~/app`, `~/'My Projects/app'`, a folder outside home, the home folder (`cd ~`), no folder, a folder that no longer exists; one session whose read raises is skipped. `othersessions.label` tests still pass.
- [ ] Implement `othersessions.place`, `leftoff.resume_line(provider, session_id, folder)` and `leftoff.projects(records, marks, root, limit=3)`.
- [ ] Run, commit `feat: group the owner's recent sessions by project`.

### Task 3: Local marks and `relay left`

Covers: R3 (terminal part), R4, D6, D8, F1, F5.

Files: `relaylib/status.py`, `relaylib/leftoff.py`, `relaylib/commands.py`, `tests/test_leftoff.py`.

- [ ] Failing tests: `status.local_marks(root)` maps owner sessions of local features to `{slug, done}`, done from the state's `stage` or `status`, a not-done feature wins over a done one naming the same session, then the newest `updated`; a health-matched non-owner record is not marked; `relay left` output for two projects per D8 (header, session lines, Summary/Agent line, Resume line, worktree folder, feature mark, "Up to N more" line), `--all`, and the empty message; with fake `gh` and `git` call logs, `relay left` runs no `gh` and no `git fetch`; a feature scan that raises gives no marks and the sessions still print.
- [ ] Implement `local_marks`, `leftoff.render(projects, now)`, `leftoff.cmd_left(args)` (records from `sessions.read_records()`, an exception gives none), and register `left` with `--all` in `commands.py` next to `status`.
- [ ] Run, commit `feat: relay left shows where you left off`.

### Task 4: Snapshot and API

Covers: R3 (dashboard part), R5, R6, D6, D9, D10.

Files: `relaylib/ui/snapshot.py`, `relaylib/ui/server.py`, `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`.

- [ ] Failing tests: `build()` returns `left_off` (patched records and transcripts) with projects and marks, including a health-matched non-owner record marked and a merged feature marked done; existing fields unchanged; a failure inside left-off gives `[]`. `GET /api/session` for a left-off session: 200 with `label` its project and `resume` the D7 line; an other-session's `resume` is now the D7 line; a session in neither list 404; 400 and 403 as before.
- [ ] Implement: snapshot marks from details (owner session and session_record; done when stage done or PR merged), `leftoff.projects(records, marks, root)`; `other_session` looks in both lists and uses `leftoff.resume_line`.
- [ ] Run, commit `feat: where you left off in the dashboard snapshot and API`.

### Task 5: The page

Covers: R7, D9.

Files: `relaylib/ui/page.html`, `tests/test_ui_server.py`.

- [ ] Failing tests: page strings `Where you left off`, `No sessions to show yet.`, `This session is no longer listed.`, `run relay left --all`; Node tests: `leftLine(entry, now)` gives "Waiting on you · 2h ago", "Waiting for approval: Bash · 5m ago", "Ended · 3h ago", "Was working · 1d ago", with checkout and "feature x (done)" appended; a left-off card has no `.actions` buttons; the 404 path of opening a left-off card shows "This session is no longer listed." and calls `load()`; inbox count and title unchanged when only left-off sessions exist.
- [ ] Implement the section (between inbox and features), `renderLeftOff(data)`, `leftLine`, card click through `openSession` with the left-off 404 text.
- [ ] Browser check on a throwaway fixture server (server module directly; `relay ui` is owner-only): the section, cards, "Up to N more" line, dialog with the resume line.
- [ ] Run, commit `feat: where you left off in the dashboard`.

### Task 6: Docs and the full check

Covers: R8, R9.

Files: `README.md`.

- [ ] README: `relay left [--all]`, the dashboard section, which sessions are shown (owner's interactive sessions with a last message, 7 days of records), the resume line.
- [ ] Full suite green; push; PR; CI green; `relay submit`.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1 | 1 |
| R2 | 2 |
| R3 | 3, 4 |
| R4 | 3 |
| R5, R6 | 4 |
| R7 | 5 |
| R8 | 6 |
| R9 | 1 to 5, 6 |
