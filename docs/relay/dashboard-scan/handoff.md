# Handoff: dashboard-scan

- From: claude session 6770b8cd-ab26-4428-b743-d3cafb7b7f61
- At: 2026-11-12T23:04:10-05:00
- Branch: perf/dashboard-scan
- Base: 9e560e52e65a33bc9736a4e7d6c461d8690c11cb
- Head: 1259a4c86b4955a7c43cc9734e37de855d1109d6
- Stage/status: build / drafting

## Done

- Draft PR #34, pushed to 1259a4c. Tasks 1 to 6 and Task 7 part 1 are from earlier sessions (see git log). This session:
- Task 7 rest (bc49780): `transcripts.index(provider)`: one `os.scandir` walk per provider, kept in `Store.transcripts` under each walked folder's (inode, mtime), stamped before it is listed. `files_for` answers from it in a build (globs outside one). `availability.codex_window` lists from the Codex index.
- Task 8 (d83a71f): `scancache.fetch_due / fetch_done / fetch_live` in `Store.fetches`; `snapshot._now()` is the clock (tests patch it; `StoreBase.build(at=, full=)` in tests/test_ui_snapshot.py). Active (a not-done row, or a checkout's HEAD, index or local refs changed since the last fetch) every 60 s, quiet every 600 s, full fetches all. A failed fetch's error is kept between fetches. `merged.resolve` keeps a CLOSED answer under `scancache.closed_key` (repo + PR + fetch generation) via `recall/remember`. `buildstats.note_fetch` is now called.
- Tasks 9 and 10 (268095f): `Cache(interval=60)`, `refresh(full=False)`, `_full` read and `_wake` cleared under the lock at the start of a build; builder gets `full` only if its signature has it. `/api/refresh` sends full=True. `build()` adds `build_stats`; `snapshot.build_line()`; stderr `relay: build ...` when over 20 s or 300 processes. Page: `#build-stats` line under main, `buildLine()` in page.html. The "Snapshot is N seconds old" note now starts at 120 s, not 60 (60 s builds would trip it).
- Task 11 (363c4d8): `cmd_ui` calls `os.nice(10)` unless `--serve-child`; warns on OSError.
- Task 12 (6c5ffb2): tests/test_owneractions.py FreshAfterBuildTest.
- Task 13 (ab6c81f): tests/test_scan_equivalence.py (disabled, cold, warm store agree, time frozen; done rows' seen.pr_head asserted on its own; F7). An unreadable origin state.md is skipped, as before this branch.
- Task 14 docs (e79b17e): README `relay ui` paragraph.
- CPU fix (1259a4c): `parsed_file(..., copier=dict)` for the merged record (was a deep copy of 751 entries, 1,808 times a build); `transcripts._advance` returns an unchanged file's entry without re-parsing.

## Next

- R16 is not met yet. Real-machine one-shot builds with one store, 60 s apart (scratchpad script, same path as `relay hub`): cold 12 s / 21.6 CPU-s / 821 processes; idle 5.3 s / 7.3 CPU-s / 42 processes (git 39, gh 2), fetched 2 (relay-go, bottomsup). Before this branch: ~182 s and ~4,900 processes. Wall time and processes meet R16; CPU does not (target under 2 CPU-s and 5% of a core; 7.3 s every 60 s is about 12%).
- Cause, from a profile: 8.7 s of the CPU is the dashboard's own Python, 0.6 s git. A build with no fetch costs about 2.9 CPU-s. A fetch restamps the repository, and Task 6's done-row reuse key includes the repository stamp (refs), so every done row in a fetched repository is rebuilt, and active repositories are fetched every minute. relay-go and bottomsup hold most of the 367 rows.
- Suggested fix: key a done row on its own branch's origin commit and its checkouts' own stamps, not the whole refs stamp (D1a). That changes the approved plan's reuse key, so raise it as a plan note (`relay commit "docs: ..."`) or ask the owner, then build it with a test: a fetch that moves only another branch reuses the done rows. Then measure again (copy the measure script idea: three `snapshot.build(store=store)` calls 60 s apart, print `build_line`).
- Smaller CPU left: `transcripts.read_sessions` (0.7 s: `FileState.from_json` validation of ~1,900 cached entries every build), `holds._feature_windows` / `merged.merged_at` (0.7 s), `state.parse_at` deep copies (0.5 s).
- Task 14 by hand still to do on the owner's Mac: a 10-minute idle `relay ui` CPU sample (`ps -o %cpu,time -p <pid>`), a push seen within ~70 s, Refresh showing a quiet repo's push. Do not restart the live dashboard from this worktree; ask the owner. Put the numbers in the PR description against R16.
- Then `relay submit` (local CI mode: tests run in the background; `relay test --status`).

## Open decisions

- Whether to change the done-row reuse key (above) or accept the CPU miss on R16. Not yet asked of the owner.

## Tests run (exact commands and results)

- `python3.11 -m unittest discover -s tests -t .` at 1259a4c: Ran 1065 tests in 724 s, OK.

## Uncommitted work and where it is

~~~
clean
~~~
