# codex-liveness: idea

## Problem

Running now (PR #12) decides whether a session runs by recording the agent process and checking it with `ps`. Codex sessions started from an editor run inside one long-lived Codex app-server process that hosts many sessions, so a Codex session whose turn died or finished without a hook event still looks alive for up to 2 hours. The owner asked (2026-10-08) to try for better results in a follow-up task.

## Who it is for

The owner, reading Running now and Where you left off in the dashboard and in `relay left`.

## What success looks like

- A Codex session shows in Running now only while a turn is actually in progress, and drops out within a minute or two of its turn ending, being interrupted or dying, even when its app-server process lives on.
- Claude sessions behave exactly as today.
- The check stays local and read-only, uses Codex's own files or events, and fails safe: when the new signal cannot be read, today's rules apply.

## Open points for the spec

- Which per-session signal is reliable: rollout records that mark a turn starting and finishing, the rollout's last write time, a Codex hook event, or app-server state.
- How an interrupted or crashed turn looks in each signal.
