---
{
  "at": "2026-11-10T15:32:43-05:00",
  "base_ref": "origin/develop",
  "base_sha": "54d1991d5a80d1241bb1d5001f101005f9b85485",
  "confirmation": false,
  "duration_s": 265.9,
  "effort": "medium",
  "head": "baa610294b88eb89f72dd72eca2f0c1fbdf48b44",
  "inputs": {
    "plan": "e1f4d649eb3d72a6c65513abd1397ae5b5efebcfd33251396fa8b13af467f43c",
    "spec": "95cd81ee2cbe512efdf1bae7b07ba4803425a7a40fec1707079429d3108762a7"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 2,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: weekly limit used up until Wed 10:25"
  ],
  "tokens": {
    "cached": 788962,
    "input": 857021,
    "output": 5064
  },
  "verdict": "GO"
}
---

## Review: ci-modes build, PR #33, round 2

R1-1 is fixed and the verdict is GO. The revision since round 1 is two code lines plus tests, and I found nothing new that blocks.

**R1-1: resolved**

- `require_pr_ready` now passes `c.st["branch"]` to `localtests.ensure` (`relaylib/commands.py:164`). `reviewjobs.WorkCtx` sets `st`, `root` and `slug`, so the dashboard and hub review path no longer raises.
- I checked the other local-mode steps on that path. `ci_line` and `pending_local_tests` read `c.ci` through `getattr` and use only `c.root` and `c.st`. The remaining `c.branch` uses (`commands.py:228`, `:517`) are in CLI submit paths that a `WorkCtx` never reaches.
- A runner started from the job's temporary worktree is anchored to the main checkout (`relaylib/localtests.py:211`), so it survives the job removing its worktree.
- The new `WorkCtxLocalGateTest` runs the gate on a real `WorkCtx` in local mode and asserts the state branch is what reaches `ensure`. It mocks `ensure`, so it does not run a whole `reviewjobs` job; that is enough for this fault.
- `relay test` got the same fix (`commands.py:478`), which closes the first round 1 note.

**Checks**

- **CI:** the `unit` check on head baa6102 finished with SUCCESS (run 42895605455). It was still running when I started, so I waited for it.
- **Local run:** `tests.test_reviewjobs`, `tests.test_localtests` and `tests.test_cimode` pass here, 29 tests. I did not run the full suite.
- **Plan and spec:** nothing in the revision removes or changes a plan task or a spec requirement that round 1 confirmed.
- **Deployment:** there is no WORKFLOW.md, so merging deploys nothing by itself. With today's global `require_ci = false` the mode stays `none` until the owner switches to `release`.

```text
verdict: GO
prior:
  - id: R1-1 status: resolved
blocking:
notes:
  - tests/test_reviewjobs.py:44 - the R1-1 test mocks localtests.ensure and calls require_pr_ready directly; no test runs a full reviewjobs job in local mode end to end.
  - relaylib/localtests.py:125 - still open from round 1: the runner takes its lock once, non-blocking, and a liveness probe holding it at that moment makes the runner exit without running while start returns "started". It fails safe (merge refuses, relay test reruns).
  - relaylib/localtests.py:164 - still open from round 1: Ctrl-C during relay test --wait records fail with an empty "runner error", not stopped.
  - relaylib/localtests.py - still open from round 1: a runner killed with SIGKILL leaves the test command running with no timeout, and the <kid>.start gate files are never cleaned up.
  - relaylib/commands.py:471 - still open from round 1: relay test with no PR surfaces gh's error rather than "open the PR first, then relay test".
  - relaylib/commands.py:659 - still open from round 1: the "go" event is queued while local tests are pending; only notify.send is held back.
  - relaylib/status.py:118 - still open from round 1: tests_flag makes one gh pr view per build-stage feature on every status scan, in every mode.
  - CI: unit check SUCCESS on head baa6102. No WORKFLOW.md, so merging deploys nothing by itself.
```
