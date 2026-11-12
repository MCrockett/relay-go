# Dashboard scan load: plan

Implements `spec.md`. Python 3.11 standard library only. Run the suite with `python3.11 -m unittest discover -s tests -t . -v` after every task. Each task is one test cycle: write the test, see it fail, write the code, see it pass, commit.

## Shape

- New module `relaylib/scancache.py`: the cross-build store (file stamps, checkout facts, commit trees, blob texts, parsed states, reusable done rows, closed-PR answers, merge-base answers). The builder activates it for the length of one build through a context variable, the same pattern as `gitops.read_memo`. Outside a build, nothing sees it. That is how owner actions stay on their fresh paths (D6, R10).
- New module `relaylib/buildstats.py`: per-build process counts and CPU time.
- `gitops` read helpers (`current_branch`, `head_sha`, `origin_url`, `common_dir`, `worktrees`, `remote_branches`, `ls_files`, `show`) ask the active store first and fall back to git. Callers in `status`, `owneractions`, `holds`, `health`, `leftoff` and `othersessions` keep their code. They get the cached answers because they call these helpers.
- `snapshot.build(full=False)` opens one context holding the read memo, the store and the stats, and that context now covers the whole build. Today the memo ends before `othersessions.listed`, `usage()` and `reviewers()`.

## Testing note

R18 names fake git and gh binaries. `tests/helpers.py` has a fake gh (`FAKE_GH`, with `FAKE_GH_LOG` for counting calls) but no fake git. As the existing tests already do, these tests use real git in temp repos from `helpers.make_repo`. They count git processes by wrapping `gitops.run` and `gitops.cat_files` with `mock.patch.object(..., wraps=...)`, and count gh calls through `FAKE_GH_LOG`. No test calls real Claude or Codex.

## Tasks

### Task 1: build stats counter

- Files: `relaylib/buildstats.py` (new), `relaylib/gitops.py` (`run`, `cat_files`, `network_env`), `relaylib/sessions.py` (`_ps`), `tests/test_buildstats.py` (new).
- `buildstats.collect()` is a context manager that holds `{"git": n, "gh": n, "other": n}`, the start time, and `resource.getrusage` for self and children at the start. `buildstats.count(argv)` adds one process to the active collector, if there is one, by the argv's first word. Each place that starts a process calls `count`: `gitops.run`, the raw `subprocess.run` calls in `cat_files` and `network_env`, and `sessions._ps`. `finish()` returns `{"seconds", "cpu_seconds", "processes": {...}, "fetched": [...], "cache": {"hits": n, "misses": n}}`. `fetched` and `cache` are filled by later tasks through `buildstats.note_fetch(repo)` and `buildstats.hit()` / `miss()`.
- Test: inside `collect()`, a `gitops.git` call, a `cat_files` call and a `_ps` call count git 2 and other 1. Outside `collect()`, `count` does nothing. `cpu_seconds` is a non-negative float.
- Covers: R12.

### Task 2: file stamps and checkout facts

- Files: `relaylib/scancache.py` (new), `relaylib/gitops.py`, `tests/test_scancache.py` (new).
- `scancache.Store` holds the cross-build dicts. `scancache.active()` returns the store of the running build or None. `scancache.use(store)` is the context manager that sets it.
- `stamp(paths)` returns a tuple of `(path, st_ino, st_size, st_mtime_ns)`, with `None` for a path that cannot be stat'ed (F2a: a missing file counts as a change).
- `checkout_stamp(checkout)` covers these files, read without a process:
  - From the checkout's git directory (the `.git` folder, or the folder a worktree's `.git` file points to): `HEAD`.
  - From the common directory: `config` and `packed-refs`.
  - From the common directory's `worktrees/`: its entry names, and each entry's `HEAD` and `gitdir`.
  - Every loose ref file under `refs/heads` and `refs/remotes/origin`, walked with `os.scandir`.
  - `FETCH_HEAD` is left out (D1a).
  - The common directory is found the first time with `git rev-parse --git-common-dir`, then kept with the facts and checked through the stamp.
- `facts(checkout)` returns the cached facts when the stamp is unchanged. Otherwise it reads them with git and stores them under the new stamp: current branch, HEAD commit, origin URL, common directory, worktree list, and `{origin ref: commit}`. That last one comes from one `git for-each-ref --format=%(refname:short) %(objectname) refs/remotes/origin`.
- In `gitops`, when a store is active:
  - `current_branch(root)`, `head_sha(root)` (HEAD), `origin_url`, `common_dir`, `worktrees` and `remote_branches` return the facts.
  - `head_sha(root, "origin/x")` returns the commit from the facts map. Any other ref goes to git as today.
- Test (temp repo with a bare origin and a linked worktree): a second `facts()` with nothing changed starts no git process. Each of these is seen on the next call:
  - a local commit,
  - `git switch`,
  - `git worktree add`,
  - a push that moves `origin/x` (a fetch writes the loose ref or `packed-refs`),
  - an `origin` URL change.
  A fetch that moves no ref keeps the cached facts. A deleted checkout gives a changed stamp, and the git error surfaces as it does today.
- Covers: R7 (facts), R2 (ref resolution), F2a.

### Task 3: commit trees, batched blobs, parsed states

- Files: `relaylib/scancache.py`, `relaylib/gitops.py` (`ls_files`, `show`), `relaylib/state.py` (a cached parse helper), `tests/test_scancache.py`.
- `tree(repo, commit)` returns `{path: blob id}` for every path under `docs/relay` (matched case-insensitively, as `ls_files` does). It comes from one `git ls-tree -r <commit>` and is cached by commit id. A failure raises `RelayError`; the caller flags the repository (F2) and nothing is cached.
- `prefetch(repo, commits)` collects every blob id in those commits' trees that is not cached. It reads them all with one `git cat-file --batch` for the repository, and caches the text by blob id. A blob missing from the batch output is cached as absent for this build only (F3).
- `parsed(blob id, path)` returns the result of `state.parse_state` for that blob, including a `RelayError`, which is re-raised on every later read. It is cached by blob id (F4).
- When a store is active:
  - `gitops.ls_files(root, ref, prefix)` resolves `ref` to a commit (facts for origin refs, otherwise `rev-parse` through the memo), then filters `tree()`.
  - `gitops.show(root, ref, path)` resolves the same way and answers from the blob cache when the path is under `docs/relay`. Anything else falls back to `git show`.
- `Store.prune(used)` runs after each build and drops trees, blobs and parsed states the build did not touch (R14). Each read marks its key as used.
- Test:
  - Two origin branches share an inherited `state.md`: the shared blob is fetched once.
  - A second scan with unchanged refs starts no `ls-tree`, `show` or `cat-file`.
  - A new commit on one branch reads only that commit's tree and its new blobs.
  - A corrupt `state.md` gives the same error twice with one read.
  - `cat-file` output missing a blob makes `show` return None.
  - After prune, an entry the last build did not touch is gone.
- Covers: R1, R3, R5, R14, F3, F4.

### Task 4: one build context, one checkouts list

- Files: `relaylib/ui/snapshot.py` (`build`, `allowed_repo`), `relaylib/status.py` (`checkouts`, `scan_with_claims`), `tests/test_ui_snapshot.py`.
- `build(full=False, store=None)` opens `gitops.read_memo()`, `scancache.use(store)` and `buildstats.collect()` together around the whole build, including `othersessions.listed`, `usage()`, `reviewers()` and the per-repo `config.load` loop. The default store is a module-level `Store`, so `relay ui` keeps one for its lifetime and `hub.py`'s one-shot call gets a fresh one (D9).
- `status.checkouts(root)` is memoized for the build in the store's per-build dict. `allowed_repo` checks against that set when a build is running, instead of globbing once per row.
- `scan_with_claims(root, records=None, fetched=None)` takes the build's records and `fetched` dict, so the scan neither reads session records a second time nor fetches a second time from `_build_flags`.
- After the fetches, the builder calls `scancache.prefetch` per repository for every origin commit, plus the origin/develop and origin/main heads. Every later `show` and `ls_files` in that build is then a cache hit (R2, R3).
- Test: a second build with nothing changed starts no git process for checkout facts and no `ls-tree`, `show` or `cat-file` for the scan. `status.checkouts` globs once per build.
- Covers: R2, R7, R3.

### Task 5: `feature()` uses the scan's published commit

- Files: `relaylib/ui/snapshot.py` (`feature`, `_enrich`), `relaylib/status.py` (`_remote_features` also returns the commit), `tests/test_ui_snapshot.py`.
- The scan already finds each feature's ref, state and commit. `_enrich` passes `published=(commit, branch, st)` to `feature()`. When `published` is given, `feature()` skips `owneractions.published` and builds `seen` from it. It still makes the same PR read that `fingerprint` makes for a live PR. With no argument (the details endpoint, owner action paths), `feature()` behaves as today.
- `feature()`'s `state_at`, review, handoff and file-list reads go through Task 3's cache.
- Test: with three origin branches carrying older copies of the state, building a feature's detail starts no `for-each-ref` or per-branch `ls-tree` beyond the scan's own. The detail endpoint called with no build running still calls `owneractions.fingerprint`.
- Covers: R4, R5.

### Task 6: done features and closed PRs

- Files: `relaylib/ui/snapshot.py` (`_enrich`, `feature`), `relaylib/owneractions.py` (`fingerprint`), `relaylib/merged.py` (`resolve`), `relaylib/scancache.py`, `tests/test_ui_snapshot.py`.
- Inside a build, `fingerprint(fetch=False)` makes no `pr_info` call for a feature with status done or a PR that the merged record shows as merged. Outside a build, it is unchanged.
- A done feature's published commit follows R6a: the head of its own origin branch, else origin/develop, else origin/main, as `feature()` does today.
- `_enrich` keeps each done row and detail in the store under a reuse key:
  - the published commit,
  - the repository's checkout stamps (D1a),
  - the repository's fetch error text,
  - the stamps of the global and repo `config.toml`.

  When the key matches, the row and detail are reused and the build calls `buildstats.hit()`.
- A done feature whose branch is checked out in any checkout (from the facts) is not reused.
- `review_choices`, `review_default` and `review_defaults` are not part of the reuse. They depend on time through the timed tables and availability, so they are computed once per repository per build and laid over every row of that repository.
- In a build, `merged.resolve` stores a closed (not merged) answer in the store under the PR key, and reuses it until that repository is fetched again. A full build (Refresh) asks GitHub again (D5).
- Test:
  - A second build makes no gh call and no per-feature git call for a merged feature.
  - A new commit on a done feature's branch rebuilds it.
  - Checking out a done feature's branch rebuilds it every build and shows "local changes not published" after an edit.
  - A fetch error shows on reused rows.
  - A closed PR is read once per fetch of its repository and again on a full build.
  - `owneractions.fingerprint` called outside a build still calls `pr_info` for a merged feature.
- Covers: R6, R6a, D3, D5, F7.

### Task 7: shared reads for the other steps

- Files: `relaylib/usage.py` (`read`), `relaylib/ledger.py` (`read`), `relaylib/merged.py` (`_load`, `_read`), `relaylib/sessions.py` (`alive`, `read_records`), `relaylib/transcripts.py` (`files_for`), `relaylib/availability.py` (`codex_window`), `relaylib/holds.py` (`_is_merged`), `relaylib/scancache.py`, tests in `tests/test_usage.py`, `tests/test_sessions.py`, `tests/test_merged.py`, `tests/test_transcripts.py`, `tests/test_holds.py`, `tests/test_ui_snapshot.py`.
- `scancache.parsed_file(path, parse)` returns `parse(path)`. In a build, the result is cached across builds under the file's stamp. Outside a build, it simply calls `parse`. Writers (`append`, `prune`, `_store`, `mark_merged`) change the file, so its stamp changes and the next read parses it again.
- It is used by:
  - `usage.read`, which today runs up to 5 times per build,
  - `ledger.read`, which runs twice,
  - `merged._load` and `merged._read`, which run 2 to 4 times per done feature.
- `sessions.alive` reads `ps` once per build and keeps the parsed table in the store's per-build dict. Every caller in the build shares it: `snapshot.build`, `health.session_health` (twice per held feature today), `othersessions.listed` and `events._inbox` during the flush.
- `transcripts.files_for(provider, sid)` and the `availability.codex_window` glob are cached in the per-build dict, so each glob runs at most once per build.
- `holds._is_merged`'s `git merge-base --is-ancestor <ref commit> <base commit>` is cached in the store across builds under the two commit ids, because the answer cannot change.
- With Task 2 in place, `holds._features`, `_history` (`head_sha`) and `othersessions.place` (`common_dir`) already hit the facts. `holds`' on-disk `state-history.json` cache stays as it is.
- Test:
  - A second build starts no git process from `writerusage.summary`.
  - `ps` runs once per build with two held features waiting.
  - `usage.read` parses an unchanged file once across two builds and re-parses after `usage.append`.
  - The merged record is read once per build.
- Covers: R7a, R7b.

### Task 8: fetch schedule

- Files: `relaylib/ui/snapshot.py` (`build`, the fetch step), `relaylib/scancache.py`, `tests/test_ui_snapshot.py`.
- The store keeps per repository (common directory): the last fetch time, the last build's "has a not-done feature" answer, and the last checkout stamps, including each checkout's `index` file.
- A repository is active when its last build had a not-done feature or any of its stamps changed. An active repository is fetched when 60 s have passed since its last fetch; a quiet one after 600 s. `full=True` fetches every repository. A repository with no record yet (the first build) is fetched.
- Linked worktrees of one repository still share one fetch. Each fetch calls `buildstats.note_fetch`.
- A failed fetch keeps the last refs and sets the row flag as today (F1). Its time is recorded, so the next attempt follows the schedule.
- Test, with a fake clock (`build(now=...)` or a patched `time.time`):
  - An active repository is fetched at 0 s and 60 s but not at 30 s.
  - A quiet repository is fetched at 0 s and 600 s but not at 60 s.
  - A local commit in a quiet repository makes it fetch in the next build.
  - `full=True` fetches all repositories.
  - A failed fetch shows the flag and is retried on schedule.
- Covers: R8, D4, F1, R17.

### Task 9: cache loop, Refresh and interval

- Files: `relaylib/ui/snapshot.py` (`Cache`), `relaylib/ui/server.py` (`/api/refresh` calls `refresh(full=True)`), `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`.
- `Cache(builder=build, interval=60)`. `refresh(full=False)` sets `_wake`, and with `full=True` also sets a `_full` flag under the lock.
- `_run` reads and clears `_full` under the lock right before calling `builder(full=...)`, and clears `_wake` at the same point, not at the top of the loop. A Refresh that arrives during a build is then kept for the next build, which starts at once.
- Builders that take no arguments (the test lambdas) still work: `Cache` passes `full` only when the builder accepts it, checked once with `inspect.signature`.
- Owner actions and review completion keep calling `refresh()` (not full).
- A build that raises keeps the last snapshot and the store (F6).
- Test:
  - Refresh during a slow fake build gives a second build with `full=True` right after.
  - The idle wait is 60 s.
  - A builder raising leaves the store's entries in place.
  - `/api/refresh` requests a full build.
- Covers: R9, R17, F6.

### Task 10: `build_stats` in the snapshot, page and stderr

- Files: `relaylib/ui/snapshot.py` (`build` returns `build_stats`; `Cache._run` prints), `relaylib/ui/page.html` (one muted line in the footer area), `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`.
- `build()` adds `build_stats` from `buildstats.finish()`.
- The page shows it in one muted line. For example: `Last build 4.2 s, 0.8 s CPU, 38 processes (git 30, gh 6, other 2), fetched 3, cache 371 hits`.
- `Cache._run` prints the same line to stderr as `relay: build ...` only when `seconds > 20` or the process total is over 300.
- Test:
  - The snapshot carries the field with the counts from Task 1.
  - A fake build reporting 25 s prints one stderr line; one reporting 2 s and 40 processes prints none.
  - The page HTML contains the element that renders it.
- Covers: R12, R13.

### Task 11: low priority

- Files: `relaylib/commands.py` (`cmd_ui`), `tests/test_commands.py` (or the existing UI command test file).
- `cmd_ui` calls `os.nice(10)` before `launch(...)`. A background launch starts its child through `cmd_ui`, so both are covered. `Server`, `Runtime` and `Cache` do not call it, so in-process tests are not lowered.
- On `OSError`, `cmd_ui` prints `relay: warning: could not lower the dashboard's priority: <e>` and carries on (F5).
- Test, with `os.nice` patched:
  - `cmd_ui` calls it with 10 before `launch`.
  - A raising `os.nice` prints the warning and still launches.
  - Constructing `Runtime` never calls it.
- Covers: R11, F5, D7.

### Task 12: owner actions stay fresh

- Files: `tests/test_ui_server.py`, `tests/test_owneractions.py` (tests only, unless one fails).
- After a build has filled the store, an `/api/action` merge still runs `git fetch` and `gh pr view`, and `owneractions.fingerprint` still walks the refs with git. This checks that no store is active outside a build.
- Test: build, push a new commit to origin from another clone, then call the merge action. It sees the new commit (via `fetch`) and refuses as today with a stale-state reason.
- Covers: R10, D6.

### Task 13: cached output equals uncached output

- Files: `relaylib/scancache.py` (a `Store(enabled=False)` mode where every lookup misses and nothing is kept), `tests/test_scan_equivalence.py` (new).
- The fixture has two repositories under a temp projects root:
  - one live feature with an open PR (fake gh),
  - one done feature whose PR is merged,
  - one feature on an origin-only branch,
  - one stacked branch with inherited copies,
  - a linked worktree,
  - one origin branch whose `state.md` is unreadable.
- Build three times: with a disabled store, with a cold enabled store, and with the same store again. Compare the three snapshots after removing `build_stats` and fields that depend on the time (ages and `wait_since` are frozen with a patched clock). They must be equal, apart from the F2 error row, which the test asserts on its own.
- The F2 path: a repository whose `for-each-ref` fails shows a "?" row with the error. A repository whose `ls-tree` fails for one commit flags that repository's rows.
- Covers: R15, F2, R18.

### Task 14: real-machine measurement and docs

- Files: `README.md` (the `relay ui` section: the 60 s interval, the fetch schedule, Refresh, nice and the build line), `docs/relay/dashboard-scan/acceptance.md` is not needed (everything is checkable today).
- On the owner's Mac, run `relay ui` against the real projects root:
  - Wait for the first build, then leave it idle for 10 minutes.
  - Sample its CPU with `ps -o %cpu,time -p <pid>` at the start and end of the 10 minutes; CPU is the change in `time` over the 10 minutes.
  - Record each build's `build_stats`.
  - Run `relay hub` once (or its one-shot `snapshot.build()` path) and record its stats.
- Put the numbers in the PR description against R16:
  - CPU under 5% of one core,
  - under 60 processes a minute,
  - an idle build under 10 s and 2 CPU-seconds.
- Also check R17 by hand: push a commit to an active repository and see it on the page within about 70 s; press Refresh and see a quiet repository's push at once.
- Covers: R16, R17, D9.

## Coverage

| Requirement | Tasks |
|---|---|
| R1 | 3 |
| R2 | 2, 4 |
| R3 | 3, 4 |
| R4 | 5 |
| R5 | 3, 5 |
| R6, R6a | 6 |
| R7 | 2, 4 |
| R7a | 7 |
| R7b | 7 |
| R8 | 8 |
| R9 | 9 |
| R10 | 12 |
| R11 | 11 |
| R12 | 1, 10 |
| R13 | 10 |
| R14 | 3 |
| R15 | 13 |
| R16 | 14 |
| R17 | 8, 9, 14 |
| R18 | 1 to 13 (see Testing note) |
| F1 to F7, F2a | 8, 13, 3, 3, 11, 9, 6, 2 |

## Risks

- `gitops` helpers answering from the store change behavior for every caller inside a build. Task 13's equivalence test is the guard. Any helper whose cached answer differs is caught there.
- `os.scandir` over `refs/remotes/origin` on repositories with many loose refs is still no process and runs in milliseconds; packed refs are a single file.
- The store grows with the number of done features (about 370 rows today) and their blobs (a few MB at most); R14 pruning keeps it at the size of one build.
