# Handoff: dashboard-scan

- From: claude session d3261ebc-e2e2-4111-8ee1-0923ba61d03b
- At: 2026-11-12T17:23:52-05:00
- Branch: perf/dashboard-scan
- Base: 9e560e52e65a33bc9736a4e7d6c461d8690c11cb
- Head: b56995a43883868746e8dfe40216cb4c84d66f86
- Stage/status: spec / drafting

## Done

- Idea written from the owner's request (2026-11-12: "start the dashboard scan fix as a relay feature"), reviewed by the owner, submitted with --with-owner. Stage is now spec.
- Work happens in the worktree `.claude/worktrees/dashboard-scan` (the main relay-go folder is the live relay; keep it on develop).
- The owner killed the running dashboard (pid 44724) on 2026-11-12; no `relay ui` is running. The owner restarts it with `relay ui --background` in their own terminal; agents cannot start it.

## Next

- Write `docs/relay/dashboard-scan/spec.md` with the relay-spec skill, then `relay submit --with-owner` if the owner answers questions.
- Code to read first: `relaylib/ui/snapshot.py` (`build` at about line 433: fetches every repo group via `_fetch` in `_parallel`, then `status.scan_with_claims`, then `_enrich` per row; the `_run` loop at about line 546 waits `interval=30` s after each build), `relaylib/status.py` (`checkouts`, `_row`, `_remote_features`, `tests_flag` added by ci-modes), `relaylib/gitops.py` (`read_memo`, `pr_info`, `ci_for_code`), `relaylib/ui/server.py` (refresh path), `relaylib/owneractions.py` (`fingerprint`, `merge_readiness`: owner actions must stay fresh).
- Measure before deciding: profile one build by step and count subprocesses per step (git vs gh, fetch vs log/show). A profile command that worked: `python3.11 -c "import cProfile; from relaylib.ui import snapshot; cProfile.run('snapshot.build()', '<file>')"` from the live folder; note cProfile sees only the main thread, and `_parallel` workers do most of the work.

## Open decisions

Ask the owner only these, one at a time, if measurement does not settle them:
1. Acceptable staleness when idle: the idea says a change shows within about a minute; confirm, or allow longer (for example 2 to 5 minutes) for GitHub-only changes.
2. Fetch policy: should the dashboard fetch every repo at all, or only repos with relay features, or rely on agents' and relay commands' own fetches plus a slow periodic fetch?
3. Low priority: run the snapshot work under `os.nice` by default?

## Tests run (exact commands and results)

None for this feature yet (idea stage). Measurement on 2026-11-12, live folder at 9e560e5: one `snapshot.build()` took 181.7 s wall with 4,924 subprocess spawns (cProfile `fork_exec` count); main thread time was mostly `poll` (95.9 s) and lock waits (59.6 s), i.e. waiting on subprocesses and worker threads. The killed dashboard ran 116 to 145% CPU from 2026-11-10 18:39 for about 46 h.

## Uncommitted work and where it is

~~~
clean
~~~
