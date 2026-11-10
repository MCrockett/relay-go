---
{
  "at": "2026-11-10T15:08:28-05:00",
  "base_ref": "origin/develop",
  "base_sha": "54d1991d5a80d1241bb1d5001f101005f9b85485",
  "confirmation": false,
  "duration_s": 190.4,
  "effort": "medium",
  "head": "9044c4a126b994e86a00dcc57fabe7f6d45fd874",
  "inputs": {
    "plan": "e1f4d649eb3d72a6c65513abd1397ae5b5efebcfd33251396fa8b13af467f43c",
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
    "cached": 728541,
    "input": 846025,
    "output": 17279
  },
  "verdict": "NO-GO"
}
---

## Review: ci-modes build, PR #33, round 1

I read the full diff, spec and plan. CI is green on the head: the `unit` check for 9044c4a finished with SUCCESS. I did not run the suite myself (read-only review). There is no WORKFLOW.md, so merging deploys nothing by itself.

**One blocking finding: a build review requested from the dashboard or hub crashes in local mode.**

- `require_pr_ready` passes `c.branch` to `localtests.ensure` in local mode (`relaylib/commands.py:164`).
- Dashboard and hub reviews use `reviewjobs.WorkCtx`, which has no `branch` attribute (`relaylib/reviewjobs.py:136`).
- `reviewjobs._run` calls `review_current` with no gate, so every such review reaches that line and raises `AttributeError`. The job reports a failure and publishes nothing.
- Once the global config is `ci = "release"` (owner decision Q4), this hits every PR into develop. Spec D8 requires every build review to ensure a run.
- No test covers it: `test_run_survives_a_removed_calling_worktree` exercises `localtests` only, never a `reviewjobs` job in local mode.
- Fix: use `c.st["branch"]` in the gate, and add a `reviewjobs` test in local mode.

**What holds**

- **Plan tasks 1 to 8 and notes N1 to N13:** all present in the diff. N14 is recorded in the PR body.
- **Base-branch settings (D3):** submit, review, `relay test`, status and merge all read `origin/<base>` through `cimode.settings`. No `require_ci` reads remain outside `normalize_build`.
- **Merge gate (R10):** local mode needs a pass for the code and command, never reads GitHub CI, and has tests for both directions of review focus 4.
- **Run safety:** the lock is not inherited by the test command, a killed runner reads `stopped`, and a timeout or worktree failure records `fail`.
- **Going live:** with today's global `require_ci = false` the mode is `none`, so no gate changes until the owner switches to `release`.
- **Docs:** no em-dashes in the added text.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 relaylib/commands.py:164 - require_pr_ready passes c.branch to localtests.ensure in local mode, but reviewjobs.WorkCtx (relaylib/reviewjobs.py:136) has no branch attribute, so every dashboard or hub build review in local mode raises AttributeError and publishes nothing; breaks D8 once the global mode is release, and no test runs a reviewjobs job in local mode
notes:
  - relaylib/commands.py:478 - relay test also records c.branch; run from the main folder with --feature it stores develop, so cleanup treats the feature as done and the runner's ready-to-merge notification is skipped. Use c.st["branch"] here too.
  - relaylib/localtests.py:125 - the runner takes its lock once, non-blocking, while live() probes (status scans, and _wait_started every 20 ms on a rerun) briefly hold the same lock. A collision makes the runner exit without running while start still returns "started". It fails safe (merge refuses, relay test reruns) but a short retry in run() would close it. The same probe can make _start_locked return "running" with no run.
  - relaylib/localtests.py:164 - Ctrl-C during relay test --wait is recorded as fail with an empty "runner error", not stopped.
  - relaylib/localtests.py - a runner killed with SIGKILL leaves the test command running in its own session with no timeout; the next cleanup removes its worktree from under it.
  - relaylib/localtests.py:196 - the <kid>.start gate files are never removed by cleanup.
  - relaylib/commands.py:471 - relay test with no PR surfaces gh's error rather than "open the PR first, then relay test"; --wait does not stop an older run of the feature (D10) and would fail on rec['log'] if run() returns None.
  - relaylib/commands.py:659 - the "go" event text ("waiting for the owner's merge") is still queued while local tests are pending; only notify.send is held back.
  - relaylib/status.py:118 - tests_flag makes one gh pr view per build-stage feature that is not ready-to-merge on every status scan, in every mode including none.
  - relaylib/cimode.py:123 - the workflow warning misses the block-list form (on: followed by "- pull_request"); acceptable since it only warns.
  - A pass that lands between the GO save and the runner's fetch can send the ready-to-merge notification twice.
  - CI: unit check SUCCESS on head 9044c4a. No WORKFLOW.md, so merging deploys nothing by itself.
```
