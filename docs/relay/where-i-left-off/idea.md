# where-i-left-off

## Problem
After the owner closes a project's terminals, or the workstation restarts, there is no single place that says where each project stopped. other-sessions lists only sessions still waiting on the owner within `[ui] other_sessions_hours`; a session that ended cleanly, was closed mid-task, or died with the machine drops out of view, and the owner has to remember which terminal held what and find the session id to resume it. The data is already on this machine: relay's hooks keep a record per Claude and Codex session (folder, state, since when) for 7 days, and relay reads a session's last message from its transcript.

## Who it is for
The owner, coming back to work after a break, a closed project or a restart, who wants one view of what they were doing in each project and how to pick it up, while still working in the terminal.

## What success looks like
1. A "where I left off" view, in the terminal and the dashboard, grouped by project (repo, including its worktrees, or folder): the most recent sessions there, whatever their state (waiting, working when last seen, ended), with how long ago each was last active and its last message.
2. Each session shows how to resume it (`claude --resume <id>` or `codex resume <id>`), and whether it still looks open or was ended or lost.
3. Sessions that belong to an open relay feature are marked with that feature, so the view and the inbox do not disagree.
4. It goes back as far as relay's session records (7 days today), and stays readable when many sessions exist (a few per project, newest first).
5. Read-only and local: nothing written to repos or published, last messages read live and never stored, only this machine's sessions.

## Known limits
- Sessions started before relay's hooks were installed, or older than the records' 7 days, are not shown.
- relay cannot tell whether a terminal still holds a session; "open" is a guess from the last recorded state.
