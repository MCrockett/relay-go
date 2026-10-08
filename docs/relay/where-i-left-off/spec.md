# where-i-left-off: spec

## Why

After the owner closes a project's terminals, or the workstation restarts, nothing says where each project stopped. other-sessions lists only sessions still waiting on the owner within `[ui] other_sessions_hours`; a session that ended cleanly, was closed mid-task, or died with the machine drops out of view, and resuming it means remembering the folder and finding the session id. relay already has what is needed on this machine: the hooks keep a record per Claude and Codex session in `~/.relay/sessions` (provider, session id, folder, state, `since`, `at`), pruned after 7 days of no writes (`sessions.prune`), and `agentask.last_words` reads a session's last message live. This feature shows, per project, the newest sessions there and how to resume each. See idea.md.

## Decisions

- D1. **Owner's sessions only.** Most records on a working machine come from automated runs (on 2026-10-08, about 350 of 401: relay's own reviews through `codex exec` and `claude -p`). A session is the owner's when its transcript says it is interactive:
  - Claude: the transcript's first record carrying `entrypoint` has a value that does not start with `sdk-` (`claude -p` says `sdk-cli`, the Agent SDK `sdk-ts` or `sdk-py`; interactive sessions say `cli`).
  - Codex: the transcript's first record is `session_meta` whose `payload.originator` is not `codex_exec` and whose `payload.source` is not `exec`.
  A session whose transcript cannot be found or read is left out (it would have no last message to show anyway). The check reads only the start of the transcript (the first 64 KB) and is a new function `agentask.interactive(provider, session_id)` next to `last_words`, using the same transcript lookup.
- D2. **Which sessions are shown.** Every record `sessions.read_records()` returns, in any state (`waiting`, `permission`, `working`, `ended`), that is the owner's (D1) and has a last message (`agentask.last_words`) or, for a `permission` session, pending tools. A session that never said anything is left out. No time window beyond the records' own 7-day pruning.
- D3. **Projects.** Sessions are grouped by project: the repository name for a folder inside a checkout `status.checkouts` finds (so `relay-go` and its worktree `relay-go-dev` are one project), otherwise the folder as other-sessions labels it (`~/x`, or the path), and "Unknown folder" for a record without a folder. The checkout folder is shown on the session when it differs from the repository's name, as in other-sessions D3. Projects are ordered by their newest session's last activity (newest first); sessions within a project by last activity (newest first), ties by session id.
- D4. **Last activity and state words.** Last activity is the record's `at` (the newest hook write). Each session shows one state word: `waiting on you` (`waiting`), `needs approval: <tools>` (`permission`), `ended` (`ended`), or `was working` (`working`: the session was mid-task at its last write, which after a restart or a closed terminal means it stopped there). relay cannot tell whether a terminal still holds a session; the words describe the last recorded state only.
- D5. **How many.** The newest 3 shown sessions per project by default. `relay left --all` shows all of them. The dashboard shows 3 per project and, when there are more, the line "N more: run `relay left --all`". Last messages are read only for the sessions shown, walking each project newest first until 3 are found (D2 can skip some), so the snapshot cost stays bounded.
- D6. **Feature mark.** A session is marked with a feature when it is that feature's owner session (`st["owner"]["session"]`) or the record health matched for its row, for any feature `status.scan_with_claims` lists, done ones included. The mark is `feature <slug>`, with ` (done)` for a done feature. Collected where D2 of other-sessions collects claims: `status.scan_with_claims` keeps returning `(rows, claimed)`; a new `status.scan_with_marks(root)` returns `(rows, claimed, marks)` with `marks` mapping session id to `(slug, done)`, and `scan_with_claims` returns its first two values. When two features claim one session, the not-done one wins, then the newest `updated`.
- D7. **Resume line.** Each session shows `cd <folder> && claude --resume <id>` or `cd <folder> && codex resume <id>`, the folder as `~/...` under the home directory and quoted with `shlex.quote` when needed (`~` itself stays unquoted). Claude only finds a session from its own folder, so the `cd` is part of the line. Without a folder (or a folder that no longer exists) the line is the command alone.
- D8. **Terminal: `relay left`.** A new command, `relay left [--all]`, prints per project a header `<project> · last active <age> ago`, then per session:
  ```
    <provider> · <state word> · <age> ago[ · <checkout folder>][ · feature <slug>[ (done)]]
      Summary: <excerpt>        (or Agent: <excerpt>, as relay status shows it)
      Resume: <resume line>
  ```
  A blank line separates projects. With nothing to show it prints "No sessions to show. relay sees sessions started after its hooks were installed, for 7 days." `relay status` is unchanged (owner decision 2026-10-08: own command and dashboard section).
- D9. **Dashboard: "Where you left off".** A section below the inbox, above "All features". The snapshot carries `left_off`: a list of projects `{project, last_active, more, sessions: [...]}` where sessions are the D5 shown ones `{provider, session_id, folder, checkout, state, since, at, pending_tools, excerpt, feature, resume}` (`feature` is `{slug, done}` or null). Per project a small heading with the project and last activity, then a card per session with the state word, age, checkout, feature mark and excerpt. Cards have no action buttons. Clicking one opens the existing session dialog (other-sessions D6): full last message, tools waiting for approval, and "Resume with:" showing the D7 line. With no sessions the section shows "No sessions to show yet." The inbox and its counts are unchanged.
- D10. **The session API.** `GET /api/session` answers for a provider and session id listed in the snapshot's `other_sessions` or `left_off` (other-sessions D7, widened); anything else stays 404. A left-off session's reply has the same fields, `label` being its project, and `resume` the D7 line. The other-sessions `resume` value changes to the D7 line too, so both lists show the same command.
- D11. **One module.** `relaylib/leftoff.py` holds D1 to D7: `projects(records, marks, root, now, limit=3)` returns the D9 project list (`limit=None` for all). `relay left` and the snapshot both call it. The project and checkout labels reuse `othersessions.label` logic, split so the repository name and checkout folder are available separately.
- D12. **Local and read-only.** Nothing is written to repositories or published; last messages are read live and never stored, only this machine's records are read, and nothing new is recorded by the hooks.

## Requirements

### Selection
- R1. `agentask.interactive` per D1, including a transcript without the marker fields (treated as not interactive) and a missing transcript.
- R2. `leftoff.projects` applies D2 to D7 and returns projects and sessions in D3 order, with `more` counting the shown sessions left out by the limit.
- R3. `status.scan_with_marks` per D6; `scan_with_claims` and `scan` return what they return today.

### Terminal
- R4. `relay left` and `relay left --all` print per D8; the empty message when nothing is shown; `relay status` output unchanged.

### Dashboard
- R5. `snapshot.build` adds `left_off` per D9; existing fields unchanged.
- R6. `GET /api/session` per D10.
- R7. The page shows the section, cards, "N more" line and dialog per D9; no action buttons on these cards; inbox, counts and feature cards unchanged.
- R8. README: the `relay left` command, the dashboard section, which sessions are shown (D1, D2, the 7 days) and the resume line.

### Tests
- R9. With temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, fake records and fake transcripts (never real Claude or Codex): interactive Claude and Codex sessions shown; `claude -p` (`sdk-cli`) and `codex exec` sessions left out; a session without a last message left out; each state word; a repository and its worktree grouped as one project with the checkout shown; a folder outside any repository; a record without a folder; project and session order; the limit of 3, `more`, and `--all`; feature marks for a not-done and a done feature; resume lines with and without a folder and with a folder needing quotes; the empty message; the snapshot field; `/api/session` for a left-off session and the widened 404; page strings and that the cards have no action buttons (Node test, skipped without Node).

## Failure paths

- F1. No records (hooks not installed, or nothing in 7 days): the empty message, and the dashboard's "No sessions to show yet."
- F2. A transcript cannot be read: that session is left out (D1, D2).
- F3. A record's folder no longer exists: still shown under its folder path, and the resume line is the command alone.
- F4. Reading one session fails unexpectedly: that session is skipped; the rest is shown. Reading the records fails: treated as no records.
- F5. Scanning features fails: sessions are shown without feature marks.

## Non-goals

- Acting on a session from relay (resuming, answering, ending), or opening a terminal.
- Keeping records longer than the hooks' 7 days, or recording anything new.
- Sessions from other machines, or sessions started before the hooks were installed.
- Changing `relay status`, the inbox, or other-sessions' selection.
- Hiding or dismissing a session.

## Open questions
