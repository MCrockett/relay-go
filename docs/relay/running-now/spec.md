# running-now: spec

## Why

The dashboard and `relay left` show what waits on the owner and where each project stopped, but not what is running. A session mid-task is recorded as `working`, and a session that crashed or died with a restart never records an end, so relay cannot tell running from dead: `relay left` can only say "was working". The owner chose (2026-10-08, option 2) to check the session's process: the hook records the Claude or Codex process it runs under, and relay checks that the process is still alive. With the same request the owner asked for the inbox to list the most recent wait first, and for "Where you left off" to be sortable with a most-recent-first view across projects. See idea.md.

## Decisions

- D1. **Recording the process.** When the hook (`sessions.capture`) writes a record, it adds `process: {pid, started}` for the agent process it runs under, found by walking up from the hook's own process through its parents (the agent runs hooks through a shell) to the first process whose command is the agent:
  - the command's base name is `claude` or `codex`, or it is `node` with an argument containing `claude-code` or `@openai/codex` (npm installs);
  - for `claude` records only a Claude process counts, for `codex` records only a Codex process.
  `started` is the process start time as `ps` prints it (`lstart`), so a reused pid is not mistaken for the session. The walk uses one `ps -A -o pid=,ppid=,lstart=,args=` call, at most 20 steps, and stops at pid 1.
  The walk runs only when the event is `SessionStart` or `UserPromptSubmit` (a resumed session may be a new process), or when the record has no `process` yet; otherwise the old `process` is kept. When nothing is found, or `ps` fails or takes longer than 0.3 seconds, the record keeps its old `process` or has none. The hook's existing promises hold: it never fails the agent and stays within its time budget.
  `sessions._valid` accepts records with or without `process`; a `process` that is not `{pid: positive int, started: non-empty string}` makes the record invalid, as other malformed fields do today.
- D2. **Alive.** A recorded process is alive when `ps` lists a process with that pid and the same `lstart`. relay checks all records it needs with one `ps -A -o pid=,lstart=` call per `relay left` run or snapshot build (`sessions.alive(records)` returns the set of session ids whose process is alive). If `ps` fails, liveness is unknown and every record is treated as having no process (D3's fallback).
- D3. **Running.** A record is running when its state is `working`, the session is the owner's (`agentask.interactive`, where-i-left-off D1), and:
  - it has a process: the process is alive and the record was written within the last 2 hours (a long-lived Codex app server keeps one process for many sessions, so a session whose turn died inside it stays alive; 2 hours without a single hook event ends that);
  - it has no process: the record was written within the last 10 minutes.
  `permission` and `waiting` sessions are not running: they wait on the owner and are in the inbox or other sessions already.
- D4. **Stopped.** In "Where you left off", a `working` session that is not running is shown as `stopped` when its process is known dead (recorded and not alive), and as `was working` otherwise (no process and quiet for more than 10 minutes, or alive but quiet for more than 2 hours). Running sessions are not listed in "Where you left off": they are in "Running now". `more` and the limit of 3 count only sessions that are not running.
- D5. **Running now entries.** `leftoff.running(records, marks, root, alive, now)` returns `{provider, session_id, project, folder, checkout, since, at, feature, excerpt, resume}` for each running session, newest `at` first, ties by session id. `since` is when the `working` state began (how long it has been running), `excerpt` is the last message (null when it has none yet: a running session is shown whatever it has said), `project`, `checkout`, `feature` and `resume` as where-i-left-off D3, D6 and D7. No limit.
- D6. **Terminal.** `relay left` prints, before the projects, when any session runs:
  ```
  Running now
    <project> · <provider> · running <age>[ · <checkout>][ · feature <slug>]
      Summary: <excerpt>     (or Agent:, omitted without one)
  ```
  followed by a blank line. `relay left --recent` prints the sessions of "Where you left off" as one list, newest first across projects, each line led by its project: `  <project> · <provider> · <state word> · <age> ago...`, with the same Summary/Agent and Resume lines; `--recent` combines with `--all`. Without `--all`, it lists the sessions the project view would show (3 per project) and ends with "Showing the newest 3 per project: relay left --all for every session." when any project has `more`.
- D7. **Dashboard: Running now.** A section at the top of the page, above the inbox, titled "Running now", with a card per running session (eyebrow the project, title "Claude session" or "Codex session", the line "Running <age>" plus checkout and feature mark, and the excerpt). No action buttons. Clicking a card opens the session dialog (full last message, resume line). With nothing running the section shows the single line "Nothing running." The snapshot carries `running` (D5 entries). `/api/session` answers for sessions in `running` too (other-sessions D7, where-i-left-off D10 widened again); its `label` is the project.
- D8. **Recent-first inbox.** The dashboard inbox and the `*` rows of `relay status` (the README calls them your inbox) list the most recent wait first: feature rows and other-session cards by wait start, newest first; rows without a wait time after them, in their current order. `relay status --json` keeps its order (scan order). The other-sessions section of `relay status` lists newest wait first too. This reverses waiting-visibility D6 (oldest first), at the owner's request.
- D9. **Sortable Where you left off.** The dashboard section has a two-option control, "By project" (today's view) and "Most recent" (one list of the sessions the snapshot carries, newest `at` first across projects, each card's eyebrow its project). The choice is remembered per browser in `localStorage` (read and written inside try/catch; without it the default is "By project"). In "Most recent", when any project has `more`, the list ends with "Showing the newest 3 per project: run relay left --all for every session."
- D10. **Local and read-only.** Nothing is published or written to repositories; last messages are read live and never stored; `ps` is the only new system call; only this machine. The process fields stay in `~/.relay/sessions` with the rest of the record and are pruned with it.

## Requirements

### Process
- R1. `sessions.capture` records `process` per D1, including the walk through a shell, an npm (`node`) agent, a missing agent, a `ps` failure or timeout, and keeping the old `process` on other events; the record shape check per D1.
- R2. `sessions.alive` per D2, including a reused pid (same pid, different `lstart`) and a `ps` failure.

### Selection
- R3. `leftoff.running` per D3 and D5; `leftoff.projects` per D4 (stopped, was working, running sessions left out).

### Terminal
- R4. `relay left` prints "Running now" per D6; `relay left --recent` with and without `--all` per D6; `relay status` `*` rows and other sessions newest wait first per D8, `--json` unchanged.

### Dashboard
- R5. The snapshot adds `running`; `/api/session` answers for running sessions per D7.
- R6. The page: the Running now section per D7, the inbox order per D8, the sort control per D9.
- R7. README: Running now (what counts as running and the process check), the inbox order, `relay left --recent` and the sort control.

### Tests
- R8. With temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, fake records and transcripts, and `ps` output patched (never real Claude or Codex): R1 to R6 cases; a running session without a last message is shown; an automated `working` session is not running; the 10-minute and 2-hour edges; page strings and Node tests for the section, the inbox order, the sort control (including `localStorage` throwing) and that the new cards have no action buttons.

## Failure paths

- F1. `ps` fails in the hook: the record is written without a new `process` (D1). In `relay left` or the snapshot: liveness unknown, the D3 fallback applies to every record.
- F2. Reading one session fails: it is skipped; the rest is shown (where-i-left-off F4).
- F3. A record from before this change has no `process`: the fallback applies until its next hook event.
- F4. Building the running list fails in the snapshot: `running` is `[]` and the rest is built.

## Non-goals

- Starting, stopping or focusing a session from relay.
- Showing sessions from other machines, or relay's own automated runs.
- A setting for the 10-minute and 2-hour limits.
- Changing `relay status --json`.

## Open questions
