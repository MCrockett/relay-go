# running-now

## Problem
The dashboard and `relay left` show what waits on the owner and where each project stopped, but not what is running right now. A session mid-task is recorded as "working", and `relay left` can only call it "was working": a session that crashed or died with a restart never records an end, so relay cannot tell a running session from a dead one. Two ordering asks came with it (owner, 2026-10-08): the inbox should list the most recent wait first (today it is oldest first), and "Where you left off" should be sortable, with a most-recent-first view across projects (today it groups by project, so a project's place follows only its newest session).

## Who it is for
The owner, using the dashboard and the terminal as one hub: what is running, what waits on them, and where they stopped, without clicking through terminal tabs.

## What success looks like
1. A "Running now" section at the top of the dashboard, and a "Running now" block first in `relay left`: each session mid-task with its project, provider, feature, how long it has been running and its last message.
2. Running is checked against the session's process (owner decision 2026-10-08, option 2): the hook records the Claude or Codex process it runs under, and relay checks that process is still alive. Sessions with no recorded process fall back to recent activity (a hook event within about 10 minutes).
3. A dead session shows as stopped in "Where you left off" instead of "was working".
4. The dashboard inbox lists the most recent wait first.
5. "Where you left off" has a sort toggle: by project (today's view) or most recent first across projects; the dashboard remembers the choice per browser.
6. Local and read-only as before: nothing published, last messages never stored, only this machine.

## Known limits
- Only sessions that send a hook event after the update carry a process; older ones use the activity fallback.
- A Codex session hosted by a long-lived app server shares that server's process, so its liveness says less.
