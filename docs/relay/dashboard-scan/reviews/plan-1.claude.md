---
{
  "at": "2026-11-12T18:27:38-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 271.0,
  "effort": "medium",
  "head": "a1c7dddf2ba04a5452cdae1f98ed786efa757921",
  "inputs": {
    "plan": "b45068c7ab911ceafee4b27b30de40893136552feeb08824c0359a1f6bb1bf9c",
    "spec": "a3a832fb418e3aebe04a5165012169c624b95c34e18eaec5a8c08c218e2885a7"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: 5-hour limit used up until Thu 20:18"
  ],
  "tokens": {
    "cached": 516074,
    "input": 633613,
    "output": 25911
  },
  "verdict": "NO-GO"
}
---

# Plan review: dashboard-scan, round 1

Verdict: NO-GO. Three blocking findings, each a small plan edit. The overall design (context-scoped store, cached `gitops` helpers, stats collector) is sound and the order is buildable apart from one test noted below.

## 1. Coverage

| Requirement | Tasks | Result |
|---|---|---|
| R1 | 3 | covered |
| R2 | 2, 4 | covered, but see R1-1 |
| R3 | 3, 4 | covered, but see R1-1 |
| R4 | 5 | covered (note on locally checked-out rows) |
| R5 | 3, 5 | covered |
| R6, R6a | 6 | covered |
| R7 | 2, 4 | covered |
| R7a | 7 | covered |
| R7b | 7 | partial, see R1-2 |
| R8 | 8 | covered |
| R9 | 9 | covered |
| R10 | 12 | covered |
| R11 | 11 | wrong on the background path, see R1-3 |
| R12, R13 | 1, 10 | covered |
| R14 | 3 | covered, but conflicts with prefetch, see R1-1 |
| R15 | 13 | covered |
| R16, R17 | 8, 9, 14 | covered |
| R18 | 1 to 13 | covered (notes on gaps) |
| F1, F2a, F3, F4, F5, F6 | 8, 2, 3, 3, 11, 9 | covered |
| F2 | 13 | test only, no implementation files named (note) |
| F7 | 6 | no direct test (note) |

## Blocking findings

**R1-1: prefetch and prune undo each other (plan.md:54, 59, 75).**
- `prefetch` reads every `docs/relay` blob in every origin commit's tree that is not cached.
- `prune` drops every blob the build did not read, and only reads mark a blob as used.
- The builder never reads `idea.md`, `spec.md` or `plan.md`, and with Task 6 it stops reading the reviews and handoffs of reused done rows.
- So those blobs are dropped after each build and read again by the next one: one `cat-file --batch` per repository per build, carrying most of the docs text.
- With 34 repositories that is about 34 extra processes a minute on top of the 40 budgeted, which misses R16's 60 and breaks R2.
- The planned tests only look at a second build, and the fixtures may hold only `state.md`, so this can pass unnoticed.
- Fix: either prefetch only what the build will read (state files for the scan, reviews and handoff for rows being rebuilt), or define "used" as "referenced by a tree the build resolved". Add a third-build assertion, and correct the "few MB" estimate in Risks if the second option is taken.

**R1-2: R7b is not implemented for transcript listings or session records (plan.md:113, 120).**
- The spec asks that these be parsed once per build and reused across builds while their stamps are unchanged.
- The plan memoizes `transcripts.files_for(provider, sid)` per build only.
- `read_sessions` calls `files_for` once per distinct held session (relaylib/transcripts.py:243), each a glob over every projects folder (relaylib/transcripts.py:30), so a per-key memo saves almost nothing.
- That is the 1.2 s "transcript discovery" the spec measured, and it would remain in every idle build against a 2 CPU-second target.
- `sessions.read_records` is in the file list, but the task says nothing about what changes there.
- Fix: list the transcript folders once into a `{session id: files}` index keyed by the folders' stamps and kept across builds, state the stamp rule for session records, and add a test for each.

**R1-3: a background launch lowers priority twice (plan.md:175).**
- `launch(background=True)` starts a child that runs `relay ui --serve-child` (relaylib/ui/server.py:289), which goes through `cmd_ui` again.
- The parent has already called `os.nice(10)`, the child inherits it and adds 10 more, so the dashboard and everything it starts run at nice 20, not 10 (D7, R11 "once").
- The plan calls this "both are covered", and the tests patch `os.nice`, so none would catch it.
- Fix: skip the call in the parent when `args.background` is set and `args.serve_child` is not, and test that path.

## Notes (nonblocking)

**Files and tests**
- F2's behavior change needs code in `status.scan_with_claims` and `_remote_features` (relaylib/status.py:264 skips silently, and `ls_files` uses `check=False`). No task names those files for it; Task 13 lists only `scancache.py` and the test.
- F2 has no test for a `cat-file` failure or for "nothing from that repository is reused in that build". As written, `prune` would also drop a failed repository's entries, though F2 says they are kept.
- Task 6's closed-PR test needs a build that skips a fetch, which only Task 8 creates. Move that item after Task 8.
- R18 asks that a branch switch, a new worktree and a new local commit be seen by the next build. Task 2 tests them at `facts()` level only; add build-level assertions.
- F6 is tested with a fake builder. Add a case where the real `build` raises midway and `prune` does not run.
- F7 has no direct test.

**Likely implementation traps**
- Closed and gone PRs: Task 6 caches only `merged.resolve`'s closed answer. `feature()` still calls `pr_info` and `ci_for_code` for them every build (relaylib/ui/snapshot.py:110-124), which D5 says should wait for the next fetch. Today's profile suggests few such rows, so this is a note.
- Task 5 gets the published commit from `_remote_features`, but the scan discards those results for locally checked-out features (relaylib/status.py:269), which includes most live ones. Say how `_enrich` gets them.
- The plan does not say when stamps are re-taken inside a build. `status.checkouts` reads facts before the fetch, so facts must be re-stamped for fetched repositories afterwards (R2), without a re-stat on every helper call.
- Facts are per checkout, so one ref move re-reads about six git facts for every worktree of that repository. Sharing the origin URL, worktree list and origin refs per common directory would hold R2's "one `for-each-ref` per repository".
- Resolving a 40-hex commit "through `rev-parse`" in the cached `show` and `ls_files` costs one process per commit per build. Short-circuit full ids.
- `status.py:249` calls `rev-parse --verify origin/<branch>` directly, not through a cached helper. Route it through the facts.
- The fetch error text must persist across builds that do not fetch that repository. Otherwise `_build_flags` fetches on its own (relaylib/status.py:65), the F1 flag flickers and the done-row reuse key churns.
- Reused done rows must be copied in and out of the store; `waiting.one_ask_per_session` and `_enrich` mutate rows.
- `events._inbox` runs in `batch.flush()` after the builder returns (relaylib/ui/snapshot.py:557), so it cannot share the build's `ps` read as Task 7 claims.
- `merged.merged_at` runs `git log --merges` every build for a merged feature with no PR, or with a cached `False` time.
- `buildstats.count` is called from eight worker threads and needs a lock. Classifying by argv's first word puts a `RELAY_GH_BIN` path under "other".
- `cat_files` raises `ValueError` on truncated output (relaylib/gitops.py:238); F3 needs that handled.
- A module-level default `Store` is shared by every in-process test, and the Task 8 schedule will stop existing tests that push and then build twice within 60 s from seeing the push. Give tests a fresh store or `full=True`.
- R14 names commits and blobs only. Also bound the done-row, closed-PR, merge-base, parsed-file and facts entries for checkouts that no longer exist.

**Spec tensions, not plan defects**
- Inside a build `seen.pr_head` becomes None for done features whose branch still exists. This follows from R6 but is a small content change against R15.
- The Testing note's use of real git instead of a fake git binary matches AGENTS.md and the existing tests.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/dashboard-scan/plan.md:54 - prefetch reads every docs/relay blob not cached while prune (line 59) drops every blob the build did not read, so idea/spec/plan blobs and the reviews of reused done rows are re-read with one cat-file per repository on every build; breaks R2 and the R16 process budget, and the second-build-only tests would not catch it
  - id: R1-2 docs/relay/dashboard-scan/plan.md:120 - R7b not implemented for transcript folder listings and session records: files_for is memoized per session per build only, but read_sessions calls it once per distinct session (relaylib/transcripts.py:243), so the measured 1.2 s of discovery stays in every idle build against the 2 CPU-second target; no cross-build stamp reuse and no change described for sessions.read_records
  - id: R1-3 docs/relay/dashboard-scan/plan.md:175 - a background launch runs cmd_ui in the parent and again in the --serve-child process (relaylib/ui/server.py:289), so os.nice(10) is applied twice and the dashboard and its reviewer, merge and test runs sit at nice 20, not 10 (D7, R11); tests patch os.nice and would not see it
notes:
  - F2 implementation in status.scan_with_claims and _remote_features is not named in any task's files; no test for a cat-file failure or for "nothing reused in that build"; prune would drop a failed repository's entries
  - Task 6's closed-PR test needs a build that skips a fetch, which only Task 8 creates; move it after Task 8
  - Closed and gone PRs still cost pr_info and ci_for_code in feature() every build; only merged.resolve's answer is cached (D5)
  - Task 5 does not say how locally checked-out features get their published commit, since the scan discards _remote_features results for them (relaylib/status.py:269)
  - State when stamps are re-taken within a build: facts must be refreshed after the fetch, without a re-stat on every helper call
  - Facts per checkout mean one for-each-ref and about six git reads per worktree on any ref move; share repository-level facts by common directory
  - Short-circuit 40-hex commits in the cached show and ls_files; route status.py:249's rev-parse --verify through the facts
  - Keep the last fetch error across builds that do not fetch, or _build_flags fetches itself and the F1 flag and reuse key flicker
  - Copy reused done rows in and out of the store; rows are mutated after _enrich
  - events._inbox runs after the builder returns, so it cannot share the build's ps read as Task 7 says
  - merged.merged_at still runs git log --merges every build for a merged feature with no PR or a cached False time
  - buildstats needs a lock for worker threads and should classify RELAY_GH_BIN as gh
  - cat_files raises ValueError on truncated output; F3 needs that handled
  - A module-level default Store plus the fetch schedule will break existing tests that push and build twice; give tests a fresh store or full=True
  - R18 build-level tests for branch switch, new worktree and local commit are only at facts() level; F6 is tested with a fake builder only; F7 has no direct test
  - Bound the done-row, closed-PR, merge-base, parsed-file and vanished-checkout entries as well as commits and blobs
  - seen.pr_head becomes None for done features with a live branch inside a build; follows from R6 but is a small R15 content change
```
