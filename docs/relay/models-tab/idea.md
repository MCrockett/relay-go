# models-tab

## Problem
The reviewer table (who reviews whose work, in what order, at what effort) lives in ~/.relay/config.toml and changes only through `relay roles set` in a terminal. The owner wants to change it from the dashboard, often for a limited time ("all reviews through Fable until 11pm, Codex as backup"), which today needs a hand-made script to switch back.

## Who it is for
The owner, in the local dashboard and the CLI.

## What success looks like
- The Usage tab is renamed Models. At its top, a Reviewers panel shows the review table for each author (claude, codex, owner): reviewers in order with their effort, and who is available now, the same facts `relay roles` prints. The usage meters and the sortable reviewer activity stay below it.
- From the panel the owner can reorder, add and remove reviewers and set each one's effort. New models are picked from the ones relay already knows (config and the review ledger), with free text for any other `provider:model`.
- Saving asks for confirmation like every dashboard action, and is refused if the config changed on disk since the page loaded.
- A change can carry an end time: `relay roles set review.claude "..." --until 23:00` on the CLI, or "until" in the dashboard. relay keeps the end time in the owner config and goes back to the normal table by itself once it passes, with or without the dashboard running and across restarts. `relay roles` and the panel show "temporary until <time>".
- Writer roles (spec, plan, build, audit) are shown read-only: they are informational until relay launches writers.

## Owner decisions (2026-10-06)
- Timed changes live in relay itself (config plus CLI), not only in the dashboard.
- Writer roles read-only for now.
