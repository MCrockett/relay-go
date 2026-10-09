# session-notify: spec

## Why

The dashboard shows every session on this machine (Running now, the inbox, other sessions, "Where you left off"), but to tell one of them something the owner has to find its terminal and type there. The owner asked (2026-10-08) for the dashboard to send a note to a session. See idea.md.

Research on this machine (Claude Code 2.1.295, Codex CLI 0.160):
- Claude Code gives each session an inbox socket for cross-session messaging (documented, v2.1.224 or later on macOS and Linux). It exports the socket's path to hooks as `CLAUDE_CODE_MESSAGING_SOCKET`, before any hook runs. A message posted there is read between tool calls during a turn, and starts a new turn when the session is idle. A message from a process that is not the session's own child runs through the session's inbound controls: delivered when the session prompts for permissions, held for the owner's approval (a dialog in that terminal) when it bypasses them, dropped when the owner set `crossSessionInbound` to `refuse`. A message can never approve a permission prompt or change configuration. The auth line is optional on macOS and Linux.
- The line format for a message is not in the public docs. Claude Code's own debug log gives it as one JSON line, `{"type":"user","message":{"role":"user","content":"<text>"}}`.
- Claude Code hooks can add context the agent sees through `hookSpecificOutput.additionalContext` on `UserPromptSubmit` and `PostToolUse`, among others. relay's merge notice already uses this on `UserPromptSubmit` and `SessionStart` for Claude and on `UserPromptSubmit` for Codex.
- Codex has no documented way for an outside program to reach an interactive session except its app-server's control socket (JSON-RPC `turn/steer`, `turn/start`), which is version-tied and not documented for outside clients.

## Decisions

- D1. **Two routes.** A note reaches a session one of two ways:
  - **Inbox (Claude only):** relay posts the note to the session's inbox socket. It arrives at once: between tool calls if the session is working, as a new turn if it is idle.
  - **Next hook event (Claude and Codex):** relay queues the note, and relay's hook adds it to the session's context at the next event that can carry it: `UserPromptSubmit` or `PostToolUse` for Claude, `UserPromptSubmit` for Codex (the event relay's merge notice already uses for Codex).
  The dashboard tries the inbox first for a Claude session that has a recorded socket (D2); when there is none, or posting fails (D4), it queues the note. Codex notes are always queued.
- D2. **Recording the inbox.** The Claude hook records `inbox: <path>` in the session record when `CLAUDE_CODE_MESSAGING_SOCKET` is set to an absolute path, and drops the field when it is not set. Only the path is recorded: relay never reads, stores or sends `CLAUDE_CODE_MESSAGING_TOKEN`, and never reads Claude Code's own session registry. `sessions._valid` accepts a record without `inbox`; an `inbox` that is not a non-empty absolute path string makes the record invalid, as other malformed fields do.
- D3. **Posting.** relay connects to the recorded path only when it is a Unix socket (not a symlink) owned by the current user, sends one line `{"type":"user","message":{"role":"user","content":"<text>"}}` and closes, with a 2-second timeout. The text is the note prefixed with `Note from the owner, sent from the relay dashboard:` and a newline, so the agent knows where it came from. A successful send means the socket accepted it; whether the session delivers or holds it is up to the session's inbound controls, and the dashboard says so (D6).
- D4. **Falling back.** The post counts as failed when the path is missing, not a socket, not the owner's, a symlink, refuses the connection, or times out. The note is then queued (D5) and the dashboard says it will arrive with the session's next prompt or tool call.
- D5. **The queue.** Queued notes live in `~/.relay/notes/<provider>-<session_id>.json` (folder mode 0700, file 0600), a list of `{id, text, at}`. The hook, at an event from D1, takes every queued note for the session, removes the file, and prints them in one `additionalContext` together with any merge notice, each note on its own lines with the same prefix as D3. A note is printed at most once: the file is removed under relay's sessions lock before printing. Notes older than 7 days are pruned with the session records. A note longer than 2,000 characters is refused when it is written.
- D6. **The dashboard.** Every session dialog (inbox other-sessions, Running now, "Where you left off") gets a "Send a note" box with a text field and a Send button. After sending it shows one line:
  - inbox route: "Sent to the session. If it skips permission prompts, it asks you to approve the note in its terminal.";
  - queued route: "Queued. It reaches the session with its next prompt or tool call.";
  - for a session shown as `ended` or `stopped`: the box says before sending "This session is not running. The note waits until you resume it." and the note is queued, never sent to the inbox.
  The dialog lists the session's queued notes with a Remove button each. `POST /api/note` (`{provider, session_id, text}`) answers only for listed sessions, as `/api/session` does, and `POST /api/note/remove` (`{provider, session_id, id}`) removes one queued note; both use the dashboard's existing token and Host checks.
- D7. **Terminal.** `relay note <provider> <session_id> "<text>"` does the same as the dashboard's Send, and prints the line from D6. `relay status` and `relay left` show `· note queued` on a session with queued notes.
- D8. **Not included.** relay never starts or resumes a session to deliver a note (no `claude --resume -p`, no `codex exec resume`): a second process on a session still open elsewhere could write to the same conversation.
- D9. **Local only.** Notes stay on this machine: the inbox socket is local, and the queue is a file under `~/.relay`. Nothing is published or sent to any service by relay.

## Requirements

- R1. The hook records `inbox` per D2, including an unset variable, a relative path and a Codex record (never recorded).
- R2. Posting per D3 and D4: a fake Unix socket server in the test receives exactly the D3 line; a missing path, a regular file, a symlink to a socket, a refused connection and a timeout each fall back to the queue.
- R3. The queue per D5: written with the right modes, delivered once at `UserPromptSubmit` and `PostToolUse` for Claude and at `UserPromptSubmit` for Codex, combined with a merge notice in one output, not delivered at other events, removed after delivery, pruned after 7 days, a note over 2,000 characters refused; a hook that cannot read the queue still writes the record and never fails the agent.
- R4. `POST /api/note` and `/api/note/remove` per D6: 200 for listed sessions, 404 for unlisted ones, 400 for an empty or over-long note, token and Host checks as the other POST routes; the snapshot carries each session's queued notes (id, text, at).
- R5. The page per D6: the Send a note box in every session dialog, the three lines, the not-running wording, the queued notes with Remove; Node tests for the line chosen per route and state.
- R6. `relay note` and the `· note queued` mark per D7.
- R7. README: sending a note, the two routes, what the owner sees in the session's terminal, and the permission-bypass approval.
- R8. Tests with temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, fake sockets and fake records, never real Claude or Codex.
- R9. Before the build is submitted, a manual check on this machine: a note sent from the dashboard to a throwaway interactive Claude session arrives through the inbox, both while it works and while it is idle. If the D3 line format is refused, the inbox route is dropped from the build (Claude notes are always queued, D1) and the spec is updated.

## Failure paths

- F1. Posting fails: the note is queued (D4).
- F2. The session refuses or holds peer messages: the post succeeds but the note is held or dropped by Claude Code. The dashboard's line after sending names the approval case; relay cannot see the outcome.
- F3. The queue cannot be written: the dashboard shows the error and nothing is sent.
- F4. The hook cannot read or remove the queue: the record is still written, no note is printed, and the note stays queued for the next event.

## Non-goals

- Reaching an idle Codex session at once (the app-server control socket). A later task, with the Codex app-server research in codex-liveness.
- Starting or resuming a session to deliver a note (D8).
- Messages from a session back to the dashboard, or reply threads.
- Notes to sessions on other machines.

## Open questions
