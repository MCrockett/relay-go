# Dashboard scan load

## Problem

`relay ui` never rests. Its snapshot thread (relaylib/ui/snapshot.py) rebuilds the whole dashboard, then waits 30 seconds and rebuilds again. Each build fetches every repo under the projects root, scans every checkout, worktree and origin feature branch, and reads GitHub for each feature. On 2026-11-12 one profiled build took about 182 s and started about 4,900 git and gh processes, so the 30 s wait is a small gap between nearly continuous builds. The dashboard (pid 44724, started 2026-11-10 after ci-modes merged) ran at 116 to 145% CPU for about two days and was the largest load on the Mac. The owner killed it so bottomsup's live game capture would not miss time limits.

## Who it is for

The owner, who keeps the dashboard open all day on the same Mac that runs agent sessions, test runs and time-critical jobs such as bottomsup's game capture.

## The idea

Make the dashboard's background work proportional to what changed and capped in cost:

- Measure first: break one build down by step (fetch, scan, per-row enrich, GitHub reads, usage and transcript reads) and by process count, and find what grew since ci-modes (its tests flag adds a gh call per build-stage row).
- Rebuild on a longer, adaptive interval, and do the expensive parts (fetches, GitHub reads) on their own slower schedule or only for repos whose refs or files changed.
- Reuse results that cannot have changed (per-repo state keyed by refs and file times) instead of recomputing them every build.
- Keep the "Refresh" path and owner actions (merge, review) reading fresh data when the owner asks.
- Optionally run the dashboard's work at low priority (nice) so it yields to other jobs.

## Success

- An idle dashboard (nothing changing) uses under 5% of one core averaged over 10 minutes, and starts no more than a few dozen processes a minute.
- A change (a push, a new review, a merge) still shows on the dashboard within about a minute, and a manual refresh shows it at once.
- Merge and other owner actions still check fresh GitHub state before acting.
- The build time and process count per build are visible (log or debug line), so a regression is noticed.
