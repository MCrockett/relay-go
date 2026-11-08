# Handoff: five-hour-usage

- From: claude session 28465dc7-46a4-4868-9c14-238f1ee6f34e
- At: 2026-11-08T17:09:37-05:00
- Branch: feat/five-hour-usage
- Base: b6fea7314594586e14ace1b5fa4617b2f3998a7e
- Head: e2d5a08684e09d02483c0c679bb06d25c7bb1748
- Stage/status: spec / drafting

## Done

- Owner asked for both features, stacked ("Go for both, stacked", 2026-11-08). This feature was started with `relay new five-hour-usage --stack --with-owner` while PR #28 (dead-session-asks) is open.
- idea.md written and submitted; the feature is at spec.
- The other feature this session holds, dead-session-asks (PR #28, worktree `.claude/worktrees/dead-session-asks`), has a build GO from codex in round 2 and waits for the owner's merge. Nothing to do on it unless develop moves.

## Next

- Write `docs/relay/five-hour-usage/spec.md` (relay-spec skill), then `relay submit --with-owner`.
- Where the data is today:
  - Codex: `relaylib/availability.py` `codex_window(minutes)` (added by PR #27) reads the `rate_limits` line; the 5-hour window is `window_minutes` 300. `usage.record_codex_sample()` stores only the weekly window in `~/.relay/usage.jsonl`.
  - Claude: `usage.capture_claude()` saves `seven_day` and `five_hour` in `~/.relay/claude-usage.json`; only `seven_day` goes to usage.jsonl.
  - Dashboard: `relaylib/ui/snapshot.py` `usage()` builds `providers[p] = {weekly, blocked, reason, budget}`; `relaylib/ui/page.html` `renderUsage()` draws the cards and the top strip.
  - Hub usage line: check `relaylib/hub.py` / `hubpage.py`.
- "Out of usage" reason should name the limit: `availability.blocked()` returns "out of usage until ..." (a recorded failure) or "weekly limit N% used until ...".

## Open decisions

- Whether the 5-hour figure goes into usage.jsonl history or is read live only (live read is enough for the display; history is not needed by the idea).

## Tests run (exact commands and results)

- None for this feature yet (idea stage only).

## Uncommitted work and where it is

~~~
clean
~~~

## Notes for the owner

- `relay submit` left PR #28 stuck in "in-review" twice: the first submit sets in-review and commits, then stops on "CI is pending; wait for green". The next submit refuses ("cannot submit build while it is in-review"), and no reviewer is running. `relay review` got past it both times. Worth its own fix (relaylib/commands.py submit flow around line 473 and the CI gate at 166-168).
- Known flaky test: tests/test_ui_server.py `test_action_conflicts_errors_and_success` (refresh runs in a `finally` after the reply, so the count can lag). Failed once on PR #27's CI and passed on rerun.
