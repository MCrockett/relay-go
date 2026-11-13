# Dashboard scan load: spec

## Why

`relay ui` rebuilds its whole snapshot, waits 30 seconds, and rebuilds again. On 2026-11-12 the dashboard ran at 116 to 145% CPU for about two days on the Mac that also runs agent sessions and bottomsup's time-critical game capture, and the owner had to kill it.

### Measured on 2026-11-12 (one `snapshot.build()`, this Mac, nice 10)

- 121 to 174 s per build, 10,551 processes per build.
- By command: 8,856 `git show`, 660 `git ls-tree`, 514 `git rev-parse`, 183 `gh pr view`, 81 `git worktree`, 81 `git remote`, 32 `git fetch`, the rest under 40 each.
- By step: 4,620 processes in `status._remote_features` (the scan of origin branches), 3,318 in `owneractions.published` (called again for every row by `feature()` through `fingerprint`), 1,777 more inside `_enrich` (reviews, handoffs, ls-tree per feature), 212 in `state_at`, 179 in `fingerprint` (the `gh pr view` calls), 98 in fetch, 160 in `status.checkouts` (run twice per build, two git processes per checkout).
- Wall time by step: fetch 19 s, scan 104 s, `_enrich` 443 s summed over 8 workers.
- CPU (own plus child processes) of the steps outside the feature scan, each run on its own: left-off list 5.0 s, other sessions 2.3 s, reviewers 2.0 s, Codex usage sample and prune 0.9 s, usage 0.7 s. Most of it is `status.checkouts` (two git processes per checkout, run by three of these steps), transcript discovery (1.2 s), and parsing the 69,000-line usage samples file, which happens twice per build.
- `sessions.alive` reads the process table with `ps` 12 times per build.
- 367 rows, of which 363 are done. The "All features" table hides done rows unless "Show done" is ticked.
- 180 of the 183 `gh pr view` calls come from `fingerprint` reading PRs for features that are already merged.
- 34 repositories, 47 checkouts, 427 origin branches. Every origin branch carries a copy of every older feature's `state.md`, so the scan reads about 4,150 state files (UpstreamPartners alone 1,015, proteindiary 588, bottomsup 573).

### What grew

Not ci-modes: its tests flag accounts for 1 gh call in the profile. The cost grows with done features times origin branches, which climbs with every feature merged in every project. Each build re-reads, file by file and process by process, history that cannot have changed since the last build, and asks GitHub about PRs that merged weeks ago.

## Decisions

- D1. **Reuse only what is keyed by an immutable id or a file stamp.** A result cached across builds is keyed by one of two things. The first is a git object id (commit, tree or blob), whose content cannot change. The second is a file stamp: the (path, inode, size, mtime in nanoseconds) of every file the result was read from, taken with `os.stat` and no process. Each build re-takes the stamps. A result is reused only when every stamp in its key is unchanged, so an edit to any input file is seen in the next build. No cross-build cache is keyed by a ref name alone, a time-to-live or a guess. Rejected: a time-based cache of whole rows, which can show stale state with no way to tell.
- D1a. **Per-checkout git facts are cached by file stamp.** A checkout's current branch, HEAD commit, origin URL, common git directory, worktree list and origin branch commits come from git processes today, read again on every build. They are cached under the checkout's stamp. The stamp covers the checkout's own `HEAD` file, the common directory's `config`, `packed-refs` and `worktrees` folder (its entries and each entry's `HEAD` and `gitdir`), and every loose ref file under `refs/heads` and `refs/remotes/origin`. `FETCH_HEAD` is left out, so a fetch that moves no ref keeps the facts. When the stamp changes, that checkout's facts are read again with git.
- D2. **Batch reads per repository.** One `git ls-tree -r` per new commit gives every relay file with its blob id, and one `git cat-file --batch` per repository per build reads every blob not already cached. This replaces one `git show` per file. Rejected: keeping per-file `git show` and only memoizing it (still thousands of processes on the first build and on every hub build).
- D3. **Done features cost nothing while their commit is unchanged.** A feature whose stage is done, or whose PR the merged record already shows as merged, is built once and its row and detail are reused while its published commit is the same. It makes no GitHub call. Done rows stay in the snapshot so "Show done" and the details panel work as today.
- D4. **Fetch on two schedules.** A repository is active when at least one of its features is not done, or when any of its checkouts' stamps (D1a, plus the checkout's `index` file) changed since the last build. An active repository is fetched every 60 s. Every other repository is fetched every 10 minutes. Refresh fetches every repository. Reason: new work almost always starts in a local checkout (`relay new`, `relay take`), which marks the repository active before anything is pushed. The cost is that a push from another machine to a repository with no live feature can take up to about 11 minutes to show without Refresh. Rejected: `git ls-remote` probes, which are still one network process per repository per minute.
- D5. **Rebuild every 60 s, reading GitHub only for live PRs.** The idle interval goes from 30 s to 60 s. For each PR of a feature that is not done, the builder makes each distinct GitHub read it needs (PR view, check runs, commit status) at most once per build. A PR the builder finds closed (not merged) is read again only when its repository is fetched, because it can be reopened. Refresh, owner actions and review completion still wake an immediate build, as today.
- D6. **Owner actions never use the cross-build cache.** Merge, review, take and the other actions keep calling `fingerprint` with a fresh fetch and fresh GitHub reads. Only the snapshot builder uses the caches in D1 to D3.
- D7. **The dashboard runs at low priority.** `relay ui` lowers its own priority by 10 (`os.nice`) at start, so every git and gh process it starts inherits it. The owner's other jobs take the CPU first. This also lowers reviewer runs, merges and local test runs started from the dashboard. That is accepted: they wait on the network and on the reviewer model, not on the CPU, and the owner's time-critical jobs matter more.
- D7a. **The other reads share the build's results.** Writing totals (`writerusage` and `holds`), the left-off and running lists, other sessions and the reviewers tab read checkouts, refs, file lists and state text through the same per-build results and cross-build caches as the feature scan, instead of calling git themselves. Files they parse (the usage samples, the ledger, session records, transcript folders) are parsed once per build and reused across builds while the file stamp is unchanged.
- D8. **Every build reports its cost.** Each build records its duration, its CPU time (own plus child processes), the processes it started (git, gh and other), the repositories it fetched and its cache hits. The snapshot carries these figures, and the page shows them in one muted line. That line is the one change to what the dashboard shows. `relay ui` prints them to stderr only when a build takes over 20 s or starts over 300 processes, so a regression shows in the terminal without a line every minute.
- D9. **The one-shot hub build benefits from D2 and D3 only.** `hub.py` calls `snapshot.build()` in a fresh process, so it has no cross-build memory. Batching and skipping GitHub for merged PRs still cut its process count by most of the 10,551. Rejected: an on-disk cache for the hub, which adds a file format and cleanup rules for a command the owner runs a few times a day.

## Non-goals

- Deleting old origin branches or changing how branches carry relay files.
- A file watcher, push notifications from GitHub, or a long-lived git process.
- Changing what the dashboard shows, its layout, or the "Show done" behavior, apart from the `build_stats` line (D8).
- Making any single cold build (the first one, a Refresh, or a hub build) fast. The targets are for the idle dashboard.
- Changing owner action semantics or their freshness checks.

## Requirements

- R1. Across builds the snapshot keeps, per repository, the relay file list and parsed state of each origin commit it has read, keyed by commit id, and the text of each relay blob it has read, keyed by blob id (D1).
- R2. After that build's fetches, each repository's origin branches are resolved to commits from the checkout facts (D1a). One `git for-each-ref` runs only for a repository whose stamp changed. Commits already in the cache start no further git process for the scan (D1).
- R3. Relay files at a new commit are listed with one `git ls-tree -r` per commit (blob ids included), and all blobs the build needs that are not cached are read with at most one `git cat-file --batch` per repository per build (D2).
- R4. `feature()` inside the builder finds a feature's published commit and state from the scan's results for that build, not by walking origin branches again (removes the `published` and `state_at` re-reads).
- R5. Review files, handoff text and file lists for a feature's detail come from the same batched reads as R3.
- R6. A done feature (stage done, or PR shown merged by the merged record) makes no GitHub call in the builder. Its row and detail from the last build are reused while all of these are unchanged: its published commit, the repository's checkout facts (D1a), the repository's fetch error text, and the stamps of the config files its review choices come from. A done feature whose branch is checked out in any checkout is rebuilt every build, so its local-change flags stay right. Any other change rebuilds it in full (D3).
- R6a. A done feature's published commit is the head of its own origin branch while that branch exists. When the branch is gone (deleted after merge), it is the origin/develop head, else the origin/main head, as `feature()` does today. Such rows are rebuilt when that base head moves. The scan's existing rule stays: inherited copies on other branches are skipped.
- R7. `status.checkouts` runs once per build and its result is shared by every step in that build. Every per-checkout git fact (current branch, HEAD, origin URL, common directory, worktree list, origin branch commits) is read from the D1a cache, so a checkout whose stamp is unchanged starts no git process for them.
- R7a. Writing totals, the left-off and running lists, other sessions and the reviewers tab get checkouts, refs, file lists and state text from R1 to R7, not from their own git calls (D7a). `holds._history` keeps its existing on-disk log cache, keyed by the head commit it already uses.
- R7b. The usage samples file, the ledger, session records and transcript folder listings are each parsed at most once per build, and reused across builds while their file stamps are unchanged (D7a). The process table is read with `ps` at most once per build, and every step that needs liveness shares that read.
- R8. Repositories are fetched on the schedule in D4: active ones every 60 s, others every 10 minutes, all of them on Refresh. Linked worktrees of one repository are fetched once, as today.
- R9. The idle rebuild interval is 60 s. Refresh, owner actions and the end of a review job wake a build at once. A build woken by Refresh fetches every repository and reads GitHub for every live and closed PR (D5). A Refresh that arrives while a build is running is kept and served by the next build, which starts as soon as the running one ends.
- R10. Owner actions (`merge`, `review`, and the rest in `owneractions` and `server.py`) read through the existing fresh paths and never through the cross-build cache (D6).
- R11. `relay ui` calls `os.nice(10)` once at start, in the command's entry point and not in `Server` or `Cache`, so tests that build those in-process are not lowered. If the call fails, it prints one warning and carries on (D7).
- R12. Each build counts the processes it starts by kind (git, gh, other). The count covers every process started through `gitops.run`, `gitops.cat_files`, and the other subprocess calls the builder reaches (for example `sessions._ps` and `network_env`). Together with the build's duration, CPU time (`resource.getrusage` for the process and its children, before and after), repositories fetched and cache hits, the counts go in the snapshot under `build_stats`, and the page shows them in one muted line (D8).
- R13. `relay ui` prints the `build_stats` line to stderr when a build takes over 20 s or starts over 300 processes (D8).
- R14. Cache memory stays bounded: after each build, entries for commits and blobs the build did not use are dropped.
- R15. The snapshot's content does not change: for the same repositories and refs, the rows, features and other fields equal what the current builder produces, apart from the new `build_stats` field, timing-dependent values and the F2 error rows. A test builds the fixture repositories with the caches off (each cache can be switched off for tests) and with them on, both cold and after a second build, and compares the three snapshots. No copy of the old builder is kept in the tree.
- R16. Targets, measured on the owner's Mac with today's projects root and the dashboard left idle for 10 minutes after its first build:
  - average CPU under 5% of one core;
  - fewer than 60 processes started per minute on average;
  - a later idle build finishing in under 10 s and using under 2 CPU-seconds, by its `build_stats`.

  The expected idle budget per minute, with today's projects (34 repositories, 4 features not done): about 3 repositories active, so 3 fetches plus 34 / 10 quiet fetches, about 7; no per-checkout git process while stamps are unchanged; for each live feature about 8 processes (PR view, check runs, status, and the local status and diff of its checkouts), about 32; one `ps`. That totals about 40 processes a minute.
  The build review checks these with the `build_stats` figures and `ps` sampling, and records the numbers in the PR.
- R17. A push to an active repository's feature branch, or a new review commit there, shows on the dashboard within about 70 s (one fetch interval plus one build). A push to a repository with no live feature shows within about 11 minutes (D4). A local commit, checkout or worktree change shows in the next build, at most about 70 s. Refresh shows any of them as soon as the woken build finishes.
- R18. Tests use temp repositories and the fake git and gh binaries from `tests/helpers.py`. They cover: a second build with unchanged refs and files starts no git process for checkout facts and no `git show`, `ls-tree` or `gh pr view` for done features (R2, R6, R7); a changed branch commit, a new local commit, a branch switch and a new worktree are each seen by the next build (R1, R6, R7); writing totals start no git process of their own on a second build (R7a); a changed usage samples file is re-read and an unchanged one is not (R7b); a merged PR is not read from GitHub by the builder (R6); the fetch schedule (R8); Refresh fetches everything and a Refresh during a build is not lost (R9); `build_stats` counts (R12); cache pruning (R14); cached and uncached output match (R15).

## Failure paths

- F1. A fetch fails: the repository keeps its last fetched refs and its rows show the fetch error, as today. The next attempt follows the schedule in R8.
- F2. `git for-each-ref`, `ls-tree` or `cat-file` fails for a repository: each of its rows carries the error as a flag, and a repository with no rows gets one row with feature "?" holding the error, as `scan_with_claims` does today for a checkout it cannot read. (Today a failed remote scan is skipped silently; this is the one place behavior changes on purpose.) Its cache entries are kept, and nothing from that repository is reused in that build.
- F2a. `os.stat` fails on a stamp file (for example a checkout deleted during the build): the stamp counts as changed, and the checkout is read with git as today. If git fails too, the checkout is dropped or flagged as today.
- F3. `cat-file --batch` returns fewer blobs than asked, or a blob is missing: the missing files are treated as absent (as `gitops.show` returning None is today), and nothing is cached for them.
- F4. A cached commit's parsed state raises an error: it raises the same error in later builds, and the row shows it, as today. The error is cached with the commit because the input cannot change.
- F5. `os.nice` fails: one warning, normal priority (R11).
- F6. A build raises: the last good snapshot stays and its error shows, as `Cache._run` does today. The caches are not cleared, because every entry is keyed by an immutable id.
- F7. The merged record says a PR is merged but GitHub says otherwise: the builder shows it as merged, as today. Owner actions still check GitHub themselves (R10).

## Open questions
