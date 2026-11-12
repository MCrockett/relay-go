# Handoff: dashboard-scan

- From: claude session 961514ef-de9a-4cb1-9036-a7ad35d65517
- At: 2026-11-12T18:30:45-05:00
- Branch: perf/dashboard-scan
- Base: 9e560e52e65a33bc9736a4e7d6c461d8690c11cb
- Head: 11735939d90402ada59e650d66c61702a083a058
- Stage/status: build / drafting

## Done

- Measured one real `snapshot.build()` on 2026-11-12: 121 to 174 s, 10,551 processes (8,856 `git show`), 367 rows of which 363 are done. The figures are in spec.md under "Measured". The profiling scripts were scratch files and are not in the repo. To re-measure, wrap `subprocess.Popen.__init__` to count argv[0:2] and call `snapshot.build()` under `nice -n 10`.
- spec.md: GO in round 2 (claude fallback reviewer; codex was out of usage). Round 1's NO-GO (writing totals and other steps not covered, idle budget) is fixed.
- plan.md: GO in round 2. Round 1's NO-GO (prune by reachability, transcript index, nice applied twice in background launch) is fixed. Reviews are in reviews/.

## Next

- Build stage: follow plan.md Tasks 1 to 14 in order, with the relay-build skill. Each task is one test cycle with its own commit. Run `python3.11 -m unittest discover -s tests -t . -v` after each task.
- Start with Task 1 (`relaylib/buildstats.py`), then Task 2 (`relaylib/scancache.py` facts). Tasks 2 to 7 change what gitops helpers return inside a build. Task 13's equivalence test is the safety net, so consider writing its fixture early.
- Task 14 needs the owner's Mac and a 10-minute idle `relay ui` run. Record the numbers in the PR against R16.
- Do not restart or deploy the live dashboard from this worktree. The owner killed the running one, and deploy is tools/deploy_check.py plus fast-forwarding the live folder after merge.

## Open decisions

- None for the owner. The one deliberate content change is `seen.pr_head` = None for done features inside a build (plan Task 6). The plan states it and the reviewer accepted it.

## Tests run (exact commands and results)

- No code changed yet, so no suite run this session. Only docs/relay/dashboard-scan/ files were written (spec, plan, reviews, state).

## Uncommitted work and where it is

~~~
clean
~~~
