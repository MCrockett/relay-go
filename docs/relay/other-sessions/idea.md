# other-sessions

## Problem
relay only shows sessions through their features. When a session is waiting on the owner for work that has no open relay feature, neither `relay status` nor the dashboard shows it. On 2026-10-08 a proteindiary Claude session had finished its feature (play-release-automation, PR #70 merged) and was waiting on the owner for a Play publisher key path and a RevenueCat key before uploading a release; `relay status` said nothing in proteindiary was open. The data is already on the machine: relay's session hooks keep a record per Claude and Codex session (working folder, waiting or needing approval, since when), and relay already reads a session's last message from its transcript (waiting-visibility).

## Who it is for
The owner, looking at `relay status` or the dashboard to find every agent that is waiting on them, whether or not its work is a relay feature.

## What success looks like
1. `relay status` lists "Other sessions waiting on you": each with its repo or folder, provider, how long it has waited, and the agent's last message, as feature rows show it.
2. The dashboard inbox shows the same sessions as cards, with Open terminal and no relay actions (there is no feature state to act on).
3. Only sessions whose record says waiting or waiting for approval, within a recent window (for example 24 hours), and not ended. Old records are ignored.
4. A session already shown under one of its features is not listed again.
5. Read-only and local: nothing is written to repos or published, and the last message is read live as today, never stored.
6. Folders are matched to repos, including worktrees (for example relay-go-dev belongs to relay-go).

## Known limits
- Sessions started before relay's hooks were installed have no record and stay invisible until resumed.
- Usage stays per workstation: only this machine's sessions.
