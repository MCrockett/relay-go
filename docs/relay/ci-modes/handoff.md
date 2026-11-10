# Handoff: ci-modes

- From: claude session e2663922-7743-45ce-bf9d-0fd0d7b6c32b
- At: 2026-11-09T23:52:39-05:00
- Branch: feat/ci-modes
- Base: 54d1991d5a80d1241bb1d5001f101005f9b85485
- Head: 0d34cab3d410f942d13153014f7ac7fa6ce14d39
- Stage/status: spec / drafting

## Done

- Idea written from the owner's request ("Maybe we need to make it more of a 'normal' thing that I don't think certain projects need a GitHub CI build"; "start it", 2026-11-09) and submitted with --with-owner. Stage is now spec.
- Work happens in the worktree `.claude/worktrees/ci-modes` (the main relay-go folder is the live relay; keep it on develop).

## Next

- Write `docs/relay/ci-modes/spec.md` with the relay-spec skill, then `relay submit`.
- Code to read first: `relaylib/gitops.py` (`ci_for_code`), `relaylib/commands.py` (`require_pr_ready`, `ready_draft`, `cmd_submit`, `review_current`), `relaylib/owneractions.py` (`merge_readiness`, which reads `build.require_ci`), `relaylib/config.py` (defaults, project overrides in `docs/relay/config.toml`), `relaylib/status.py` (`_build_flags`), `tools/deploy_check.py`, README "relay merge" section.
- Owner's global `~/.relay/config.toml` has `require_ci = false`; portfolio CI policy 2026-10-23: tests run locally, GitHub CI only on releases. That points to `release` as the owner's global mode.

## Open decisions

Ask the owner only these, one at a time, if the code does not settle them:
1. Where the test command comes from: a `test_command` in config (global default plus per-repo override), or read from AGENTS.md's "Build and Test" block. Suggest config, with no guessing from docs.
2. Slow suites: relay-go's full suite took 47 minutes locally on this machine (load average about 250). Should `relay submit` wait for the local run, or run it in the background and gate only merge? Record the decision.
3. How `require_ci` maps: suggest `true` -> `pr`, `false` -> `local` only when a test command is set, else an explicit "none" so nothing silently loses its gate. Confirm with the owner.
4. The global mode for the owner: likely `release` per the portfolio policy.

Timing data (2026-11-10): full relay-go suite, 933 tests, all OK, took 875 s (14.6 min) locally with load average about 4 on 10 cores; 47 min the day before at load about 250; about 3.5 min on GitHub CI. Owner leans to a faster subset for local runs ("Seems like a faster subset"). Suggestion to weigh: each repo's `test_command` can name a fast subset, submit runs it in the background and only merge waits on it, and `release` mode keeps the full suite on GitHub for PRs into main.

Known local flake: `tests/test_usage.py::test_capture_runtime_and_lock_contention` fails on unchanged develop under heavy load.

## Tests run (exact commands and results)

None for this feature yet (idea stage only).

## Uncommitted work and where it is

~~~
clean
~~~
