# where-i-left-off: spec

## Why

After the owner closes a project's terminals, or the workstation restarts, nothing says where each project stopped. other-sessions lists only sessions still waiting on the owner within `[ui] other_sessions_hours`; a session that ended cleanly, was closed mid-task, or died with the machine drops out of view, and resuming it means remembering the folder and finding the session id. relay already has what is needed on this machine: the hooks keep a record per Claude and Codex session in `~/.relay/sessions` (provider, session id, folder, state, `since`, `at`), pruned after 7 days of no writes (`sessions.prune`), and `agentask.last_words` reads a session's last message live. This feature shows, per project, the newest sessions there and how to resume each. See idea.md.

## Decisions

- D1. **Owner's sessions only.** Most records on a working machine come from automated runs (on 2026-10-08, about 350 of 401: relay's own reviews through `codex exec` and `claude -p`). A session is the owner's when its transcript says it is interactive:
  - Claude: the transcript's first record carrying `entrypoint` has a value that does not start with `sdk-` (`claude -p` says `sdk-cli`, the Agent SDK `sdk-ts` or `sdk-py`; interactive sessions say `cli`).
  - Codex: the transcript's first record is `session_meta` whose `payload.originator` is not `codex_exec` and whose `payload.source` is not `exec`.
  A session whose transcript cannot be found or read is left out (it would have no last message to show anyway). The check reads only the start of the transcript (the first 64 KB; an incomplete line is ignored) and is a new function `agentask.interactive(provider, session_id)` next to `last_words`, using the same transcript lookup. A session's origin never changes, so the answer is kept in memory for the life of the process (the dashboard server rebuilds its snapshot often); a "not found" answer is not kept, since the transcript may appear later.
- D2. **Which sessions are shown.** Every record `sessions.read_records()` returns, in any state (`waiting`, `permission`, `working`, `ended`), that is the owner's (D1) and has a last message (`agentask.last_words`) or, for a `permission` session, pending tools. A session that never said anything is left out. No time window beyond the records' own 7-day pruning.
- D3. **Projects.** Sessions are grouped by project: the repository name for a folder inside a checkout `status.checkouts` finds (so `relay-go` and its worktree `relay-go-dev` are one project), otherwise the folder as other-sessions labels it (`~/x`, or the path), and "Unknown folder" for a record without a folder. The checkout folder is shown on the session when it differs from the repository's name, as in other-sessions D3. Projects are ordered by their newest session's last activity (newest first); sessions within a project by last activity (newest first), ties by session id.
- D4. **Last activity and state words.** Last activity is the record's `at` (the newest hook write). Each session shows one state word: `waiting on you` (`waiting`), `needs approval: <tools>` (`permission`), `ended` (`ended`), or `was working` (`working`: the session was mid-task at its last write, which after a restart or a closed terminal means it stopped there). relay cannot tell whether a terminal still holds a session; the words describe the last recorded state only.
- D5. **How many.** The newest 3 shown sessions per project by default. `relay left --all` shows all of them. Last messages are read only for the sessions shown: each project's owner's sessions (D1) are walked newest first until 3 with a last message or pending tools are found (D2 can skip some), so the cost stays bounded. `more` is the number of the project's owner's sessions (D1) not walked; their last messages are not read, so some of them may turn out to have none. The dashboard shows, when `more` is above 0, the line "Up to N more: run `relay left --all`", and `relay left` without `--all` ends such a project with "  Up to N more: relay left --all". The wording "Up to" is the contract: `more` is an upper bound, exact only with `--all`.
- D6. **Feature mark.** A session is marked `feature <slug>`, with ` (done)` for a done feature, when it is that feature's owner session (`st["owner"]["session"]`). `marks` maps a session id to `{slug, done}`; when two features name one session, the not-done one wins, then the newest `updated`. Where the marks come from:
  - `relay left`: local only, no network and no git command that changes anything. A new `status.local_marks(root)` reads each checkout's feature states with `state.list_features` (the files in the working tree) for every checkout `status.checkouts(root)` finds. Done means the state's own `stage` or `status` is `done`; a feature whose PR merged on GitHub but whose state was not yet updated shows without ` (done)`. Features that exist only on origin branches are not read. `relay status`, `scan` and `scan_with_claims` are unchanged.
  - The dashboard: from the feature details the snapshot already builds (it already fetches, as today): each detail's `owner_session.session` and `session_record.session_id`, done when the detail's stage is `done` or its PR is merged, the same test the snapshot's other-sessions claims use. No extra fetch or GitHub request.
- D7. **Resume line.** Each session shows `cd <folder> && claude --resume <id>` or `cd <folder> && codex resume <id>`. Claude only finds a session from its own folder, so the `cd` is part of the line. A folder under the home directory is written `~/` followed by the rest of the path passed through `shlex.quote`, so the tilde still expands: `/home/o/My Projects/app` gives `cd ~/'My Projects/app' && claude --resume S1`, and `/home/o/app` gives `cd ~/app && ...`. A folder elsewhere is `shlex.quote` of the whole path. The home directory itself is `cd ~`. Without a folder (or a folder that no longer exists) the line is the command alone. The session id is passed through `shlex.quote` too.
- D8. **Terminal: `relay left`.** A new command, `relay left [--all]`, prints per project a header `<project> · last active <age> ago`, then per session:
  ```
    <provider> · <state word> · <age> ago[ · <checkout folder>][ · feature <slug>[ (done)]]
      Summary: <excerpt>        (or Agent: <excerpt>, as relay status shows it)
      Resume: <resume line>
  ```
  A blank line separates projects. With nothing to show it prints "No sessions to show. relay sees sessions started after its hooks were installed, for 7 days." `relay status` is unchanged (owner decision 2026-10-08: own command and dashboard section).
- D9. **Dashboard: "Where you left off".** A section below the inbox, above "All features". The snapshot carries `left_off`: a list of projects `{project, last_active, more, sessions: [...]}` where sessions are the D5 shown ones `{provider, session_id, folder, checkout, state, since, at, pending_tools, excerpt, feature, resume}` (`feature` is `{slug, done}` or null). Per project a small heading with the project and last activity, then a card per session with the state word, age, checkout, feature mark and excerpt. Cards have no action buttons. Clicking one opens the existing session dialog (other-sessions D6): full last message, tools waiting for approval, and "Resume with:" showing the D7 line. A session no longer in the snapshot when clicked (404, for example after a refresh pruned it) shows "This session is no longer listed." and the page reloads its data; the inbox's session cards keep "This session is no longer waiting.". With no sessions the section shows "No sessions to show yet." The inbox and its counts are unchanged.
- D10. **The session API.** `GET /api/session` answers for a provider and session id listed in the snapshot's `other_sessions` or `left_off` (other-sessions D7, widened); anything else stays 404. A left-off session's reply has the same fields, `label` being its project, and `resume` the D7 line. The other-sessions `resume` value changes to the D7 line too, so both lists show the same command.
- D11. **One module.** `relaylib/leftoff.py` holds D1 to D5 and D7: `projects(records, marks, root, limit=3)` returns the D9 project list (`limit=None` for all). `relay left` and the snapshot both call it, so the terminal and the dashboard always agree on what is shown, as other-sessions does for waiting sessions. The project and checkout labels reuse `othersessions.label` logic, split so the repository name and checkout folder are available separately, so a folder gets the same name in both features.
- D12. **Local and read-only.** Nothing is written to repositories or published; `relay left` makes no network request and runs no git command that changes refs (D6); last messages are read live (the transcript's last complete lines, as `agentask.last_words` reads them) and never stored; only this machine's records are read, and nothing new is recorded by the hooks.

## Requirements

### Selection
- R1. `agentask.interactive` per D1, including a transcript without the marker fields (treated as not interactive) and a missing transcript.
- R2. `leftoff.projects` applies D2 to D5 and D7 and returns projects and sessions in D3 order, with `more` per D5.
- R3. `status.local_marks` per D6, with no network request and no fetch; the snapshot's marks from its feature details per D6; `scan`, `scan_with_claims` and `relay status` unchanged.

### Terminal
- R4. `relay left` and `relay left --all` print per D8; the empty message when nothing is shown; `relay status` output unchanged.

### Dashboard
- R5. `snapshot.build` adds `left_off` per D9; existing fields unchanged.
- R6. `GET /api/session` per D10.
- R7. The page shows the section, cards, "N more" line and dialog per D9; no action buttons on these cards; inbox, counts and feature cards unchanged.
- R8. README: the `relay left` command, the dashboard section, which sessions are shown (D1, D2, the 7 days) and the resume line.

### Tests
- R9. With temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, fake records and fake transcripts (never real Claude or Codex): interactive Claude and Codex sessions shown; `claude -p` (`sdk-cli`) and `codex exec` sessions left out; a session without a last message left out; each state word; a repository and its worktree grouped as one project with the checkout shown; a folder outside any repository; a record without a folder; project and session order; the limit of 3, `more` (counting a not-walked session that has no last message) and `--all`; feature marks for a not-done and a done feature, and two features naming one session; `relay left` runs no `git fetch` and no `gh` (fake binaries record calls); resume lines for a home folder, a home folder with a space (`cd ~/'My Projects/app' && ...`), a folder outside home, the home folder itself, no folder, and a folder that no longer exists; the empty message; the snapshot field; `/api/session` for a left-off session, the widened 404, and the page's "This session is no longer listed." on a left-off card's 404; page strings and that the cards have no action buttons (Node test, skipped without Node).

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
