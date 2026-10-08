# other-sessions: spec

## Why

relay shows a session only through a feature it holds. A session waiting on the owner for work outside any open feature is invisible: on 2026-10-08 a proteindiary Claude session had finished its feature (PR #70 merged) and was waiting for a Play publisher key path and a RevenueCat key, while `relay status` reported nothing open in proteindiary. The data is already on this machine: relay's hooks keep a record per Claude and Codex session (`~/.relay/sessions`: provider, session id, working folder, state, since when, pending tool approvals), and `agentask.last_words` reads a session's last message from its transcript (waiting-visibility). This feature lists those sessions next to the features. See idea.md.

## Decisions

- D1. **Which sessions are listed ("other sessions").** A session record is listed when all hold:
  - its state is `waiting` or `permission`, held longer than the health grace (`[ui] health_grace_minutes`, as feature health uses);
  - its wait started within the window: `now - since <= [ui] other_sessions_hours` hours (default 24, allowed 1 to 168, an invalid value falls back to the default with a note, like the other `[ui]` settings);
  - both settings come from the global configuration (`config.load()` without a repository) for every listed session, whether or not its folder is in a repository: one rule for all of them, since many are outside any repository. Repository overrides keep applying to feature health only;
  - it is not claimed by a feature (D2);
  - a `waiting` session has a last message (`agentask.last_words` returns text). A session that never said anything, such as one just opened, is not asking for anything. A `permission` session is listed without one, since it names the tools waiting for approval.
  Ended sessions are never listed. Records are read as today (`sessions.read_records`); nothing new is recorded.
- D2. **No session twice.** A record is claimed when its session id is the owner session of any listed feature that is not done, or it is the record health matched for a feature row. A session whose feature is done (the proteindiary case) is not claimed. The claimed ids are gathered where those are already known, never added to feature rows: `status.scan_with_claims(root)` returns `(rows, claimed)`, collecting each non-done row's `st["owner"]["session"]` and the record `with_asks` matched, and `status.scan(root)` keeps returning `rows` alone; the snapshot gathers the same from each feature detail (`state.owner.session` and `session_record`).
- D3. **Where it is.** Label: when the record's folder is inside a checkout that `status.checkouts` finds, the repository's name (its main checkout's folder name), followed by the checkout's folder in parentheses when that differs (a worktree: `relay-go (relay-go-dev)`). Otherwise the folder shown relative to the home directory (`~/Downloads/x`), or as is outside it. A record without a folder (`cwd` null, which `sessions.read_records` accepts) is labeled "Unknown folder". Folder paths are resolved before comparing, as health matching does.
- D4. **One module.** `relaylib/othersessions.py` holds the selection and labels: `listed(records, rows, root, cfg, now)` returns entries `{provider, session_id, label, folder, state, since, pending_tools, excerpt}` sorted oldest wait first. `relay status` and the dashboard both call it, so they agree (as the shared asks do).
- D5. **Terminal.** `relay status` prints the feature table as today, then, when any are listed, an "Other sessions waiting on you" section: per session one line `* <label> · <provider> · waiting <age>` (or `needs approval: <tools> · <age>`), then `    Agent: <excerpt>` or `    Summary: <excerpt>` as feature rows show it. The final line counts features and sessions together ("3 waiting on you (*)"). With no features and some sessions, the "No relay features" line is followed by the section. `--json` output is unchanged (a list of feature rows), so tools reading it keep working.
- D6. **Dashboard.** The snapshot carries `other_sessions` (the D4 entries). The inbox shows a card per session, ordered with the feature cards by when each wait started (oldest first): eyebrow the label, title "Claude session" or "Codex session", the line "Waiting on you · <age>" or "Waiting for approval: <tools> · <age>", and the excerpt. Cards have no action buttons: there is no feature state to act on, and relay does not open or focus terminals (owner decision 2026-10-08: the idea's "Open terminal" is dropped, because relay cannot tell which window holds a session and opening a new one would attach a second client to a session that is still open). The inbox count and page title count them. Clicking a card opens a dialog with the full last message (`agentask.full`, read when opened through `GET /api/session?provider=&session=`), the tools waiting for approval for a permission session, and the line "Resume with: `claude --resume <id>`" or "`codex resume <id>`" for when the original terminal is gone. In the dialog: a message that can no longer be read (or a permission session that never had one) shows "No last message to show." with the rest of the dialog; a session no longer listed (404, for example after a refresh) shows "This session is no longer waiting." and the inbox reloads.
- D7. **Only what the snapshot listed.** `/api/session` answers only for a provider and session id in the current snapshot's `other_sessions`, so the page cannot read arbitrary transcripts; anything else is 404. It requires the dashboard token as the other endpoints do.
- D8. **Local and read-only.** Nothing is written to any repository or published; last messages are read live and never stored, as in waiting-visibility. Usage stays per workstation: only this machine's records.

## Requirements

### Selection
- R1. `othersessions.listed` applies D1 and D2 and returns D4 entries sorted by `since` (oldest first, ties by session id).
- R2. Labels per D3, including a worktree of a repository under the projects folder and a folder outside it.
- R3. `[ui] other_sessions_hours` per D1, read with the existing `[ui]` settings and reported the same way when invalid.

### Terminal
- R4. `relay status` prints the section, lines and count per D5; `--all` does not change which sessions are listed; `--json` is unchanged.

### Dashboard
- R5. `snapshot.build` adds `other_sessions`; feature rows are unchanged.
- R6. `GET /api/session` per D6 and D7: `{provider, session_id, label, state, pending_tools, text, source, resume}` for a listed session (`text` and `source` null when no message can be read), 404 otherwise, and the shared authorization handler's 403 without the token.
- R7. The page shows the cards, counts and dialog per D6; no action buttons on session cards; feature cards unchanged.
- R8. README: the "Other sessions waiting on you" section and cards, what is listed and the `[ui] other_sessions_hours` setting.

### Tests
- R9. With temporary `RELAY_HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR`, fake records and fake transcripts (never real Claude or Codex): a waiting session with a last message is listed; one inside the grace, one older than the window, an ended one, a working one, and a waiting one without a last message are not; a permission session is listed with its tools; a session owning a not-done feature is not listed, and one whose feature is done is; labels for a repo, a worktree, a home folder and a folder outside home; `relay status` output with and without features, and `--json` unchanged; the snapshot field; `/api/session` for a listed session, one whose transcript is gone (text null), an unlisted one (404) and no token (403); a record without a folder; the global settings used for a session inside a repository with its own `[ui]` values; page strings for the card and dialog, and that session cards have no action buttons (Node test, skipped without Node).

## Failure paths

- F1. No session records (hooks not installed, or none in the window): no section and no cards; everything else as today.
- F2. A transcript cannot be read: a waiting session is not listed (D1 needs its message); a permission session is listed without an excerpt. Nothing fails.
- F3. A record's folder no longer exists: the label falls back to the folder path; the session is still listed.
- F4. A session that died without a SessionEnd stays listed until its wait leaves the window; the window bounds it.
- F5. Reading one session fails unexpectedly: that session is skipped; the rest of status and the snapshot are built as today.

## Non-goals

- Acting on a session from relay (answering, approving tools, ending it) or opening its terminal.
- Recording anything new in the hooks, or showing sessions from other machines.
- Opening or focusing a session's terminal (owner decision 2026-10-08, D6).
- Dismissing a listed session; the window and the session's own next prompt clear it.
- Changing feature rows, asks or `--json`.

## Open questions
