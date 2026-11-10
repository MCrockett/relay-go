---
{
  "at": "2026-11-10T10:37:50-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 156.3,
  "effort": "medium",
  "head": "aede1be99c4de4ae57a8f8ec782bb3e160d63588",
  "inputs": {
    "spec": "531795802e785421f6faf80e0e1b73c0efd4b541ad0be2ab3aac99f984404bcb"
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
    "cached": 321848,
    "input": 383070,
    "output": 14588
  },
  "verdict": "NO-GO"
}
---

**Verdict: NO-GO.** The spec is well bounded and mostly testable, but three recovery or gate paths do not work as written when checked against the code.

## Blocking

**R1-1: the spec never says which checkout's config supplies the mode and `test_command`.**
- Submit and `relay test` run in the feature worktree, so they read the feature branch's `docs/relay/config.toml`.
- Merge reads `config.load(repo)` from whichever checkout the dashboard, hub or `relay merge` runs in (`relaylib/owneractions.py:247`), normally the main folder on develop.
- D4 puts the exact command text in the key. A PR that adds or changes `test_command` in the repo config records its pass under one command, and merge looks it up under another.
- Merge then refuses with "no local test result; run `relay test`", and running it from the worktree records under the branch's command again. It never clears.
- R17 sends every repo without a repo config down this path as soon as its `test_command` arrives in a feature PR.
- The spec needs one stated source for the command and mode, for example the PR's base revision or the published revision. That choice also decides whether a PR can weaken its own gate.

**R1-2: the "tests failed" recovery path is refused by the state machine.**
- Line 74 and R13 say the author "fixes, pushes and submits, which starts a new run".
- D7 lets the review finish before the tests, so the feature is often already `ready-to-merge` when the run fails.
- From that status `relay submit` raises "cannot submit build while it is ready-to-merge" (`relaylib/machine.py:22`), and the gate block in `cmd_submit` is skipped (`relaylib/commands.py:403`).
- The path that does work is `relay review`, which re-reviews a stale GO, but D7 and R7 start runs only at build submit. The fixed code gets a new GO and no test run.
- The spec must say what starts a run after a GO, and R13's inbox text must match it.

**R1-3: `relay test` is foreground-only, so an agent cannot use it on a slow suite.**
- The spec's own facts (line 10) give relay-go's suite 14.6 to 47 minutes against a 10-minute tool limit, with a known flake under load (line 11).
- Every non-submit recovery goes through `relay test`: a flake, a stopped run, the R1-2 case, another machine.
- An agent's call is cut off at 10 minutes, and R10 then reads the record as `stopped`, which sends the agent back to `relay test`. That is a loop.
- This undoes owner decision Q2 on the recovery path. The spec needs a background form of `relay test`, or must say this step is owner-only and word R13 that way.

## Notes

- **Workflow warning misses relay-go itself.** D9's rule looks for a `branches:` key anywhere in the file. `.github/workflows/test.yml` has `pull_request` with no branch filter, but `push: branches: [main]` supplies the key, so no warning fires. Scope the check to the `pull_request` block.
- **Empty `test_command` at merge gives wrong advice.** R9 says "run `relay test`", but the behaviour of `relay test` with no command is not specified. It should give R7's message. This hits features already in flight when the global mode flips (R17).
- **No R12 flag for `stopped` or for "no test command".** R18 asks for a test of each flag, so name them.
- **Invalid values in R3 and D2 are unspecified.** R1 states a refusal for a bad `ci`; R3 does not for a non-string `test_command` or a zero or negative `test_timeout_min`, nor D2 for a non-boolean `require_ci`.
- **No signal when tests pass after the GO.** R14 notifies only on failure. The existing GO notification can say "ready to merge" while the merge item is still disabled.
- **Test worktree details are left to the builder.**
  - D3 does not say where the worktree lives.
  - It does not say how a leftover one is cleaned up after a restart or a killed run.
  - It does not say whether `status.checkouts` and the dashboard will list it as a project copy during a run.
- **Record handling.**
  - R10's "process is gone" check can be fooled by a reused process id.
  - Record writes are not stated to be atomic, though submit, the background run and `relay test` can all touch one.
- **Pruning can delete a live pass.** R11 keeps the newest 20 logs per repo, which can remove the passing record of a feature still waiting for the owner. Spare the current record of any open feature.
- **A PR can weaken its own gate without a code change.** `docs/relay/config.toml` sits under the bookkeeping prefix (`relaylib/freshness.py:41`), so setting `test_command = "true"` or `ci = "none"` does not make a GO stale. The same exposure exists for `require_ci` today; R1-1's fix can close it.
- **Build prompt still points at GitHub CI.** `prompts/build.md:11` tells the reviewer to read GitHub CI and not assume it passed. In `local` mode there may be none, or a failing run the gate ignores. The non-goal leaves the prompt alone; a line in the spec accepting that would help.
- **Checks that passed.**
  - Scope matches idea.md. The additions (`none`, `relay test`, the timeout, the failure notification, pruning) are owner decisions or needed by the design.
  - D5's move away from state.md is stated and justified.
  - "## Open questions" exists and is empty.
  - No em-dashes.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/ci-modes/spec.md:30 - D4 keys the result by the exact test_command text, but the spec never says which checkout or revision supplies the mode and command. Submit and relay test read the feature worktree's docs/relay/config.toml; merge_readiness reads config.load(repo) from the dashboard's or owner's checkout (relaylib/owneractions.py:247), usually develop. A PR that adds or changes test_command passes under one key and is looked up under another, so merge refuses with "no local test result" and relay test cannot fix it.
  - id: R1-2 docs/relay/ci-modes/spec.md:74 - The failure path "fix, push and submit starts a new run" fails once the review has given a GO, which D7 allows before the tests finish: relay submit raises "cannot submit build while it is ready-to-merge" (relaylib/machine.py:22) and relay review, the path that works, never starts a run because D7 and R7 start runs only at build submit. R13's "fix and relay submit" text is wrong for the same reason.
  - id: R1-3 docs/relay/ci-modes/spec.md:34 - relay test is foreground only, but the spec's own facts put relay-go's suite at 14.6 to 47 minutes against a 10-minute agent tool limit. Every non-submit recovery (flake, stopped run, re-review after a GO, other machine) goes through it; an agent's call is cut off, the record reads stopped, and R13 sends it back to relay test. This undoes owner decision Q2 on the recovery path.
notes:
  - D9's text rule misses relay-go's own .github/workflows/test.yml: pull_request has no branch filter, but push has a branches key, so no warning fires. Scope the check to the pull_request block.
  - R9 tells the owner to run relay test when test_command is empty; relay test with no command is unspecified and should give R7's message. This hits in-flight features when the global mode flips (R17).
  - R12 has no flag text for a stopped run or for local mode with no test_command.
  - R3 and D2 do not say what happens with a non-string test_command, a zero or negative test_timeout_min, or a non-boolean require_ci.
  - R14 notifies only on failure; nothing tells the owner when tests pass after the GO, and the GO notification can say ready to merge while the merge item is disabled.
  - D3 does not say where the test worktree lives, how a leftover one is cleaned up after a restart or kill, or whether status.checkouts and the dashboard will list it as a project copy during a run.
  - R10's process-gone check can be fooled by a reused process id, and record writes are not stated to be atomic though submit, the background run and relay test can all touch one.
  - R11's newest-20 pruning can delete the passing record of a feature still waiting for merge; spare the current record of open features.
  - docs/relay/config.toml is under the bookkeeping prefix (relaylib/freshness.py:41), so a PR can set test_command = "true" or ci = "none" without making a GO stale. Same exposure as require_ci today; R1-1's fix can close it.
  - prompts/build.md:11 tells the reviewer to read GitHub CI; in local mode there may be none or a failing run the gate ignores. The non-goal leaves the prompt alone; say in the spec that this is accepted.
  - Scope matches idea.md, the D5 change from state.md is stated and justified, the Open questions section exists and is empty, and there are no em-dashes.
```
