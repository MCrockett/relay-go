---
{
  "at": "2026-11-12T18:30:29-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 89.3,
  "effort": "medium",
  "head": "5e3e619311f99f4f48b12a4d7ac4187c97a4e8dc",
  "inputs": {
    "plan": "d6bc9da3ff4e149d2fd10862c396254e97033353cb096b41389496bc9112b19d",
    "spec": "a3a832fb418e3aebe04a5165012169c624b95c34e18eaec5a8c08c218e2885a7"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 2,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: 5-hour limit used up until Thu 20:18"
  ],
  "tokens": {
    "cached": 458932,
    "input": 537285,
    "output": 8281
  },
  "verdict": "GO"
}
---

Finished reading the revised plan against the spec, the round 1 review and the code it touches; writing up the verdict now.

# Plan review: dashboard-scan, round 2

Verdict: GO. All three round 1 blockers are fixed in the plan text, and the revision introduced no new blocker. I could not run commands in this session, so I compared the revision to round 1 by reading `plan.md`, `reviews/plan-1.claude.md` and the code, not by diff.

## Prior findings

- **R1-1 (prefetch and prune undo each other): resolved.** Prune now keeps every tree the build resolved and every blob and parsed state those trees reference, read or not (plan.md:63). Task 3 adds the three-build test (plan.md:72). A repository whose scan failed keeps its entries (plan.md:64).
- **R1-2 (R7b for transcripts and session records): resolved.** Transcript discovery is one `{session id: [files]}` index per provider, kept across builds under the stamps of the folders walked (plan.md:135-136). Session record files go through `parsed_file`, and the build reads records once (plan.md:137). Task 7 tests the transcript index on a second build and after a new file.
- **R1-3 (double nice on background launch): resolved.** `cmd_ui` skips `os.nice` in the `--serve-child` process, so the child inherits 10 from the parent (plan.md:195). The tests cover foreground, background and `--serve-child`.

## Checks

1. **Coverage:** every requirement R1 to R18 and failure path F1 to F7 has a task, and the plan's coverage table matches the task text. F2 now has implementation files (Task 4, `status.py`) and F7 has a test (Task 13).
2. **Files and tests:** every task names its files and a test. Task 12 is tests only, which is right for R10.
3. **Order:** buildable, with one small wrinkle in the notes (the `Cache` builder signature).
4. **Scope:** nothing beyond the spec. The merge-base and `merged_at` caches and the closed and gone row reuse serve R16 and D5.
5. **Failure paths:** F1, F2 (for-each-ref, ls-tree, cat-file), F2a, F3, F5, F6 (a real build raising) and F7 each have a test. F4 is tested for a corrupt `state.md`.

The one design point I checked in the code is closed-row reuse (plan.md:105). It is safe against a reopened PR: `scan_with_claims` calls `status.finished` for every row on every build, before `_enrich`. A fetch build or a Refresh re-asks GitHub, and a reopened PR stops being done and is rebuilt.

```text
verdict: GO
prior:
  - id: R1-1 status: resolved
  - id: R1-2 status: resolved
  - id: R1-3 status: resolved
blocking:
notes:
  - Task 4 has Cache pass store to the builder, but the "pass only what the builder accepts" check (inspect.signature) is in Task 9; the existing test lambdas take no arguments, so move that check to Task 4 or the suite fails in between
  - Task 6 says closed and gone rows are reused "the same way"; state that reuse is decided after the scan's status.finished call for that build, so a reopened PR is rebuilt on the next fetch build or Refresh (the code order already gives this)
  - Transcript index stamp: a subagents folder created later inside an existing <sid> folder changes only the <sid> folder's mtime; stamp the <sid> folders too (and the Codex year, month and day levels), or new subagent files are missed until something else changes
  - Task 7 lists tests/test_sessions.py but names no test for session records through parsed_file (unchanged record not re-parsed, changed one re-read); the ledger has none either; add one each
  - F4's error cache is tested for a corrupt state.md only; the cached RelayError must be raised as a fresh copy or with its message, since eight worker threads may raise the same object
  - Risks still says the blob cache is "a few MB at most"; with every docs/relay blob of every origin tree kept it is likely tens of MB; correct the estimate and record the real size in Task 14
  - prefetch reads every docs/relay blob as text; say what happens to a non-text file under docs/relay (skip it by extension or decode with errors="replace") so one binary cannot fail a repository's batch
  - The reuse key uses checkout stamps where R6 says checkout facts; that is stricter and safe, but any local commit in a repository rebuilds all its done rows; confirm in Task 14 that such a build stays within the R16 figures
  - Task 14 should say how the measurement run avoids the live dashboard on the main folder (another port, or stop the live one first), so the two do not both fetch and skew the numbers
  - Task 1's count sites cover every subprocess call the builder reaches today (gitops.run, cat_files, network_env, sessions._ps); buildstats must reach worker threads through the copied context like the read memo, worth one assertion in Task 4
```
