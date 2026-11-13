# Handoff: dashboard-scan

- From: claude session 3af7cd77-bc40-41e4-bd64-68563935201e
- At: 2026-11-12T21:29:23-05:00
- Branch: perf/dashboard-scan
- Base: 9e560e52e65a33bc9736a4e7d6c461d8690c11cb
- Head: eb181e593caa8c965ce28c1ff7c19fa179be67ee
- Stage/status: build / drafting

## Done

- Draft PR #34 is open (perf/dashboard-scan into develop) and pushed to eb181e5. One commit per task.
- Task 1 (af1ae87): `relaylib/buildstats.py`. `collect()`, `count(argv)`, `note_fetch`, `hit`, `miss`, `finish()`. Counted in `gitops.run`, `cat_files`, `network_env` and `sessions._ps`.
- Task 2 (9c48e08): `relaylib/scancache.py` Store, `use()`, `active()`, `stamp()`, `checkout_stamp()`, `repo_facts()`, `checkout_facts()`, `restamp()`. The repository stamp has two parts, (config + worktrees) and (refs), so a ref move costs only one `for-each-ref`. Checkout facts use their own stamp (HEAD file, packed-refs, refs/heads). `gitops` helpers ask the facts first; new `gitops.remote_ref_exists` replaces the stacked-branch `rev-parse --verify` in `status.scan_with_claims`.
- Task 3 (0ff9c6f): `scancache.tree()`, `prefetch()`, `blob()`, `commit_of()`, `lookup()`, `parsed()`, `Store.prune()`. `gitops.show` and `ls_files` answer from it for paths under docs/relay. New `state.parse_at()` is used by `status._remote_features`, `owneractions.published`, `find_by_pr` and `state_at`.
- Task 4 (54c4e7e): `snapshot.build(full=False, store=None)` opens memo + store + stats around the whole build (body moved to `_build`), restamps and counts each fetch, prefetches every origin head per repository, prunes after. `status.checkouts` and `allowed_repo` use `scancache.per_build`. `scan_with_claims(root, records, fetched)`. F2: an unreadable origin scan flags the repository's rows (`row["scan_error"]`, re-added in `_enrich`) or adds one "?" row.
- Task 5 (52ad7cd): `snapshot.published_for()` and `_scan_ref()`; `feature(..., published=(commit, st))` uses `owneractions.seen_at()` (split out of `fingerprint`).
- Task 6 (b12d776): done rows are reused by `_done_key()` / `_reuse()` / `scancache.done_get|done_put` (key: repo stamps, each checkout's own stamp, fetch error, config stamps, fetch generation for hidden rows, scan flags). Not reused when the branch is checked out, or when the detail has health, a session record or agent text. `_review_choices()` computes the dialog choices once per repository per build and lays them over reused rows. `seen_at` skips `pr_info` in a build for done or merged-recorded features (`merged.recorded`). `merged_at`'s merge log is cached with `scancache.answer`. Also: `feature()` no longer runs `merge_readiness` (a gh call) for a merged PR.
- Task 7, first part (eb181e5): `scancache.parsed_file(path, parse)` (stats on every read, so a write in the same build is seen) used by `usage.read`, `ledger.read`, `merged._read` and `sessions.read_records`. `sessions.alive` reads `ps` once per build (`per_build`). `holds._is_ancestor` caches `merge-base --is-ancestor` under the two commits.

## Next

- Finish Task 7: the transcript index in `relaylib/transcripts.py` (one `os.scandir` walk per provider, kept under folder (inode, mtime) stamps; replaces the per-session globs in `files_for`; shared by `read_sessions`, `agentask.last_words`, `agentask.interactive`, `codex_turn`; `availability.codex_window` lists from the Codex index). Test: a second build with unchanged folders runs no glob or walk; a new transcript file is found by the next build. Plan Task 7 lists the rest.
- Tasks 8 to 14 in plan.md order. Task 8 (fetch schedule) can use `scancache.fetched()` / `generation()` already in place; closed-PR answers in `merged.resolve` go in the store keyed by PR and fetch generation. Task 9: `Cache(interval=60)`, `refresh(full)`, `/api/refresh` full. Task 10: `build()` already holds `stats` from `buildstats.collect()`; add `data["build_stats"] = stats.finish()` plus `fetched`, the page line and the stderr rule. Task 11 `os.nice` in `cmd_ui`. Task 12 tests only. Task 13 equivalence with `Store(enabled=False)` (already supported: every lookup misses). Task 14 needs the owner's Mac.
- Tests for the store use `StoreBase` in tests/test_ui_snapshot.py (counts git through wrapped `gitops.run` / `cat_files`), and tests/test_scancache.py.
- Do not restart or deploy the live dashboard from this worktree.

## Open decisions

- None for the owner. Deliberate content changes so far: `seen.pr_head` is None for done features in a build (plan Task 6), and a merged PR's detail no longer carries a merge-readiness reason in `action_reasons` (it has no actions).

## Tests run (exact commands and results)

- Baseline before any change: `python3.11 -m unittest discover -s tests -t .` passed in 12m13s.
- After Task 7 part 1: `python3.11 -m unittest tests.test_usage tests.test_ledger tests.test_merged tests.test_sessions tests.test_holds tests.test_writerusage tests.test_ui_snapshot tests.test_leftoff tests.test_othersessions tests.test_health` ran 206 tests, OK. After Task 6: test_status, test_ui_server, test_hub, test_writerusage, test_holds, test_merged, test_owneractions, test_merge_cmd ran 221 tests, OK. tests.test_scancache and tests.test_buildstats pass.
- The full suite has not been rerun since the baseline. Run it (over 10 minutes, so in the background) before `relay submit`.

## Uncommitted work and where it is

~~~
clean
~~~
