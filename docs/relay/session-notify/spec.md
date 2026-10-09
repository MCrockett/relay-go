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
- D3. **Posting.** relay connects to the recorded path only when it is a Unix socket (not a symlink) owned by the current user, sends one line `{"type":"user","message":{"role":"user","content":"<text>"}}` and closes, with a 2-second timeout. The text is the note prefixed with `Note from the owner, sent from the relay dashboard:` and a newline, so the agent knows where it came from. A successful send means only that the socket accepted the line: Claude Code then delivers it, holds it for the owner's approval, or drops it under the session's inbound controls, and relay cannot see which (D6).
- D4. **Falling back.** The post counts as failed when the path is missing, not a socket, not the owner's, a symlink, refuses the connection, or times out. The note is then queued (D5).
- D5. **The note log.** Each session's notes live in one file, `~/.relay/notes/<sha1 of "<provider>\n<session_id>">.json` (the same naming as session records, so no session id can reach outside the folder; folder mode 0700, file 0600). The file holds a list of `{id, text, at, status, status_at}`, where `status` is:
  - `queued`: waiting for the session's next hook event that carries notes (D1);
  - `delivered`: relay's hook printed it into the session's context (relay knows the agent was handed it);
  - `posted`: the inbox socket accepted it (D3); whether Claude Code delivered, held or dropped it is unknown to relay.
  Every change to a note log (adding a note, removing one, the hook taking the queued notes, pruning) is one read-modify-write under one lock, `~/.relay/notes/.lock` (the same kind of lock file relay's session records use), and is written by a temporary file and rename, so concurrent sends never overwrite each other and a note added while the hook runs is either taken by that hook or left `queued` for the next event, never lost. The hook waits at most 0.1 seconds for the lock; when it is busy the hook delivers nothing this time, notes stay `queued`, and the record is still written. The dashboard and CLI paths wait up to 2 seconds and report an error when the lock stays busy. The hook marks the notes it takes `delivered` before printing them, so a note is printed at most once. Notes older than 7 days are pruned (any status), and a log left empty is removed. The hook prints the queued notes in one `additionalContext` together with any merge notice, each note on its own lines with the D3 prefix.
- D6. **The dashboard.** Every session dialog (inbox other-sessions, Running now, "Where you left off") gets a "Send a note" box with a text field and a Send button, and lists the session's notes from the last 7 days, newest first, each with its status in words:
  - `queued`, Claude: "Queued. It reaches the session with its next prompt or tool call." Codex: "Queued. It reaches the session with your next prompt there.";
  - `delivered`: "Delivered to the session <age> ago.";
  - `posted`: "Posted to the session's inbox <age> ago. Claude Code delivers it, or, if the session skips permission prompts, asks you to approve it in its terminal; if that session refuses messages from other sessions, it is dropped. relay cannot see which happened.";
  - a queued note has a Remove button; delivered and posted notes do not.
  For a session shown as `ended` or `stopped`, the box says before sending "This session is not running. The note waits until you resume it." and the note is queued, never posted.
  `POST /api/note` (`{provider, session_id, text}`) and `POST /api/note/remove` (`{provider, session_id, id}`) answer only for sessions the snapshot lists, as `/api/session` does, using the dashboard's existing token and Host checks. Responses: 200 with the note and its status; 404 for an unlisted session; 400 when a field is missing or not a string, or the text is empty or only whitespace, or longer than 2,000 characters; 409 for removing a note that is not `queued` (already delivered or posted) or no longer exists, with the session's current notes so the page can redraw. The snapshot carries each listed session's notes (id, text, at, status, status_at).
- D7. **Dashboard only.** Sending a note is a dashboard feature, as the idea asks; there is no terminal command, and `relay status` and `relay left` are unchanged.
- D8. **Not included.** relay never starts or resumes a session to deliver a note (no `claude --resume -p`, no `codex exec resume`): a second process on a session still open elsewhere could write to the same conversation.
- D9. **Local only.** Notes stay on this machine: the inbox socket is local, and the log is a file under `~/.relay`. Nothing is published or sent to any service by relay.

## Requirements

- R1. The hook records `inbox` per D2, including an unset variable, a relative path and a Codex record (never recorded).
- R2. Posting per D3 and D4: a fake Unix socket server in the test receives exactly the D3 line and the note is `posted`; a missing path, a regular file, a symlink to a socket, a socket owned by another user (patched owner check), a refused connection and a timeout each leave the note `queued`.
- R3. The note log per D5: file name from the hash (a session id with `/` and `..` stays inside the folder), modes, the three statuses; delivered once at `UserPromptSubmit` and `PostToolUse` for Claude and at `UserPromptSubmit` for Codex, together with a merge notice in one output, not at other events; a busy lock skips delivery and keeps the notes `queued` while the record is still written; concurrent adds from several threads all survive; a note added between the hook's read and write is not lost (held lock in the test); pruning after 7 days and removal of an empty log; a hook that cannot read the log still writes the record and never fails the agent.
- R4. `POST /api/note` and `/api/note/remove` per D6, each response case listed there, and token and Host checks as the other POST routes; the snapshot carries notes.
- R5. The page per D6: the Send a note box in every session dialog, the notes list with each status's words (Claude and Codex wording for `queued`), Remove only on queued notes and its 409 redraw, the not-running wording; Node tests for the words per status and provider.
- R6. (Removed: see D7.)
- R7. README: sending a note, the two routes, the statuses and what `posted` cannot tell, what the owner sees in the session's terminal, and the permission-bypass approval.
- R8. Tests with temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, fake sockets and fake records, never real Claude or Codex.
- R9. Before the build is submitted, a manual check on this machine: a note sent from the dashboard to a throwaway interactive Claude session arrives through the inbox, both while it works and while it is idle. If the D3 line format is refused, the inbox route is dropped from the build: Claude notes are always queued, `posted` and its words are removed, and the page copy, README and tests are changed to match; the spec is updated before the build is submitted.

## Failure paths

- F1. Posting fails: the note is queued (D4).
- F2. The session refuses or holds peer messages: the post succeeds but the note is held or dropped by Claude Code. The dashboard's line after sending names the approval case; relay cannot see the outcome.
- F3. The note log cannot be written or its lock stays busy: the dashboard shows the error and nothing is sent or posted. relay prepares every write a send needs before it posts, so a storage failure found then stops the send (see the plan).
- F5. Recording a note fails after the inbox already accepted it (the last step of a send, a rename within the notes folder): a posted note cannot be taken back, so relay does not show an error as if nothing were sent. The dashboard shows "Posted to the session, but relay could not record it." and the note is left out of the log, so it never appears as `queued` and the hook never delivers it a second time. This is the one case where a sent note is missing from the history D5 and D6 describe.
- F4. The hook cannot read or update the note log: the record is still written, no note is printed, and the notes stay `queued` for the next event.

## Non-goals

- Reaching an idle Codex session at once (the app-server control socket). A later task, building on the Codex app-server findings recorded in the codex-liveness spec (on its own branch).
- Starting or resuming a session to deliver a note (D8).
- Messages from a session back to the dashboard, or reply threads.
- Notes to sessions on other machines.

## Open questions
