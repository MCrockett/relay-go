---
{
  "at": "2026-11-10T10:47:51-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 184.1,
  "effort": "medium",
  "head": "76b0c7f55ead240c07427e42740887a726db15be",
  "inputs": {
    "plan": "ad91cf58802866b2a94a9178d77b506bfc2c39fe7e569d655416ad1c3838c214",
    "spec": "95cd81ee2cbe512efdf1bae7b07ba4803425a7a40fec1707079429d3108762a7"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: weekly limit used up until Wed 10:25"
  ],
  "tokens": {
    "cached": 538993,
    "input": 629916,
    "output": 17284
  },
  "verdict": "GO"
}
---

**Verdict: GO.** The plan covers every spec requirement, and I checked its claims about the current code against `relaylib/` (the four `require_ci` readers, `require_pr_ready`, `ready_draft`, `merge_readiness`, `status._row`, `waiting.asks`, `snapshot.py`, `prompts/build.md`). Nothing blocks, but one ordering defect and a few test gaps are worth fixing while building. I ran no tests; this was a read-only review.

## 1. Coverage

| Requirement | Task |
|---|---|
| R1, R2, R3 (config values, legacy mapping, refusals) | 1 |
| R4 (settings helper, all readers replaced, `relay roles` line) | 2, 4, 5, 6 |
| R5, R6 (`pr` and `none` unchanged) | 4, 6, 9 |
| R7 (no command message; local gate does not read GitHub) | 2, 4, 5, 6 |
| R8 (local gate ensures a run, workflow warning, `ready_draft`) | 4 |
| R9 (`release` resolved per base) | 2 |
| R10 (merge refusals) | 6 |
| R11, R12, R13 (records, timeout, worktree failure, cleanup) | 3 |
| R14 to R18 (checkouts, flags, inbox, notifications, prompt) | 7 |
| R19, R20, R21 (docs, relay-go config, deploy list) | 8 |
| R22 (tests) | every task, 9 |

No requirement is without a task. All four places that read `require_ci` today (`commands.py:161`, `:197`, `:1177`, `owneractions.py:247`) are assigned.

## 2. Files and tests
Every task names its files and its tests. Task 4 correctly moves the three existing `require_ci = false` tests to push the config to `origin/develop`.

## 3. Order
One defect, not blocking for the final result:

- **Task 1 removes `require_ci` from the loaded config, but its readers are not replaced until Tasks 4, 5 and 6.** After Task 1, `relay roles` and the merge path with no GitHub result raise `KeyError`. Task 4 Step 4 expects the full suite to pass, which cannot hold: the roles assertion at `tests/test_commands.py:1032` is fixed in Task 5 and `owneractions.py:247` in Task 6.
- **Fix:** either keep a derived `require_ci` key until Task 6, or have Task 4 Step 4 run only `tests.test_cimode` and `tests.test_commands` minus the roles test.

## 4. Scope
- **`--again` and the `--wait` refusal in agent sessions are not in the spec.** D9 still says plain `relay test` replaces any finished record, including a pass. The plan follows the spec review's note instead, which is the safer behavior, but the two documents now disagree. The owner should know.
- **`tests: unknown (<error>)` is a ninth flag**, also from a spec review note. Fine.

## 5. Failure paths
Well covered: no command, fail, timeout, worktree failure, killed runner, older run stopped, other machine, a PR changing its own config, a changed base command, invalid config. The gaps are in the notes below.

```text
verdict: GO
prior:
blocking:
notes:
  - Order: Task 1 drops require_ci from the loaded config, but commands.py:161, :197, :1177 and owneractions.py:247 still read it until Tasks 4, 5 and 6. relay roles and the merge path raise KeyError in between, and Task 4 Step 4 ("full suite PASS") cannot hold because tests/test_commands.py:1032 and the merge tests are fixed later. Keep a derived require_ci key until Task 6, or narrow that step's expected result.
  - Spec and plan disagree on D9: the spec says relay test replaces any finished record; the plan reports a pass and needs --again. The plan's behavior is safer and comes from a spec review note, but the spec text was not changed. The --wait refusal in agent sessions is also an addition.
  - R13 says cleanup runs at the start of each run; the plan's __main__ runs it after run(). A leftover worktree from a crashed run then survives through the next run, which matters most when the disk is full. Move cleanup before run().
  - R13 tests: test_cleanup_keeps_live_and_open_features covers log pruning only. Add a case for a leftover test-runs worktree being removed (the sleep or restart failure path) and one showing a live run's record is kept.
  - R16 tests cover failed, running and green. Add asks tests for stopped, not run and no test command.
  - Task 7 says waiting_on_owner is false while tests run, but also emits a merge ask then, and status.with_asks sets waiting_on_owner = bool(asks). Both cannot hold; say which one wins and test it.
  - R17: the runner sends "ready to merge" on a pass when origin's status is ready-to-merge, without checking the GO is fresh for that code. A push after the GO followed by relay test would announce ready to merge on a stale GO. Check the GO's code matches the key.
  - R17 order: in review_current, read the record after c.save publishes the GO, so a run that passes during the publish is not missed by both sides.
  - ready_draft runs before require_pr_ready, so in local mode with no test_command the draft PR is marked ready (and may start GitHub CI) before submit refuses with R7's message. Call require_command in ready_draft first.
  - Test hygiene: tests that call localtests.start spawn detached runners that inherit the test process environment. Set RELAY_CONFIG, RELAY_HOME and RELAY_NOTIFY_BIN before every start, or a failing run reads the owner's real config and calls osascript. Stop sleep 30 runners in tearDown.
  - relaylib/ui/snapshot.py:119 still reads GitHub CI for the feature detail view in local mode. The gate is right (merge_readiness), but the view can show a failing GitHub result on a PR that may merge. Consider showing the tests flag there.
  - status.tests_flag adds a gh pr view per build-stage feature on every scan; today only ready-to-merge rows make that call. Skip done features and watch the dashboard's scan time.
  - deploycheck.needs_command reads origin/develop only. A repo whose PRs target another base, or that has ci set but no test_command, is not listed.
  - The R5 message at commands.py:162 names require_ci = false; Task 9's grep expects it gone, so Task 4 should say it becomes ci = "none".
  - Carried from the spec review and not addressed: check one non-Python repo, since some test runners skip paths containing /.git/ and the run worktree lives under the git common dir.
```
