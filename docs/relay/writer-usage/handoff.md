# Handoff: writer-usage

- From: claude session d60f1d60-f786-474c-b6c7-b6dde1a615d8
- At: 2026-10-06T23:56:49-04:00
- Branch: feat/writer-usage
- Base: 8693ecc7b6b8a4df237880fd9522ccc38af0496f
- Head: b27d8eccba777e5590677939b0b1b3eccbd91dbb
- Stage/status: build / drafting

## Done
- idea.md from the owner discussion (with owner).
- spec.md: GO from codex:gpt-6-astra in round 3 (spec-1 and spec-2 NO-GO, all blockers resolved: merge ends a hold, session-wide handoff and one-turn-one-feature, visible partial/unreadable logs, Codex turn_context validity).
- plan.md: GO from codex in round 3 (plan-1: 8 blockers, plan-2: 2 blockers, all resolved). Read the round-3 notes in reviews/plan-3.codex.md before starting.

## Next
- Build with the relay-build skill: plan.md Tasks 1 to 8 in order, tests first.
- First merge origin/develop into this branch: the branch starts at 8693ecc, before PR #5 (ecdee80) moved the Reviewers section below usage on the Models tab. Task 6 puts the writing tables between `#ledger-tables` and `#reviewers`, which assumes PR #5's order.
- Verify the facts the plan relies on against real logs on this machine before trusting fixtures:
  - Claude transcripts: `~/.claude/projects/<folder>/<session>.jsonl` and `<session>/subagents/*.jsonl`; assistant entries repeat `message.usage` per message id (581 usage lines for 268 ids in one transcript). The folder is the session's starting cwd, not the repo, so files_for globs every folder.
  - Codex rollouts: `~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<session>.jsonl`, first line `session_meta` with the id; `token_count` events carry `info.total_token_usage` and can have `info: null`; `turn_context` carries `model`.
  - Measured longest run of lines between usage records (300 newest files each): Claude 94, Codex 16 (basis for the 200-line threshold; see plan Spec clarifications).
- Task 6: check the page in a real browser with a throwaway server (temp RELAY_HOME, `server.Runtime` with a builder returning fixture data), as done for models-tab. That check found a real bug last time (stale snapshot overwriting a fresh save).
- Work only in ~/StudioProjects/relay-go-dev. ~/StudioProjects/relay-go is the installed copy, on develop; after the owner merges, fast-forward it with `git pull --ff-only`.

## Open decisions
- None for writer-usage. The owner approved the approach on 2026-10-06 (transcript reader, not relay-launched writers).
- Possible follow-up the owner has not asked for yet: models-tab build review notes (hand-edited review-until.json or roles-log.jsonl edge cases, a 400 on save closing the editor). Offered, not started.
- Untracked in the private relay repo: TRIAGE.md has an uncommitted entry for the `relay status` crash when a worktree vanishes mid-scan.

## Tests run (exact commands and results)
- No code changes on this branch yet (docs only).
- develop at a1ccc30 (models-tab merge): `python3.11 -m unittest discover -s tests -t .` ran 413 tests, OK. CI green on PRs #3, #4, #5.
- `tests/test_usage.py` `test_capture_runtime_and_lock_contention` has a 150 ms limit on macOS and fails locally under load, with or without changes; it passes in CI.

## Uncommitted work and where it is

~~~
clean
~~~
