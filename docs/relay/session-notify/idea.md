# session-notify: idea

## Problem

The owner runs the dashboard (`relay ui`) as a hub for every session on this machine. It shows what is running, what waits on them and where they left off, but it can only show: to tell a session something, the owner has to find its terminal and type there. The owner asked (2026-10-08) whether the dashboard can notify a session, and chose to spec it.

## Who it is for

The owner, on this machine, with several Claude and Codex sessions open at once.

## What success looks like

- From a session's card or dialog in the dashboard, the owner writes a short note, and the session receives it.
- A running session gets the note at its next hook event (its next tool call or prompt), as extra context the agent sees, the same way relay's merge notice reaches a session today.
- A session waiting on the owner, or one that has ended or stopped, is handled honestly: the dashboard says when the note will arrive (on the next prompt) or offers a safe way to deliver it, and never runs a second copy of a session that is still open.
- The dashboard shows whether each note is pending or delivered.
- Nothing leaves this machine; notes are not stored after delivery longer than needed.

## Open points for the spec

- Which hook events deliver a note for Claude and for Codex, and the exact output shapes.
- Whether any supported channel reaches an idle session without the owner typing.
- Whether "resume with a message" for ended or stopped sessions is worth including.
