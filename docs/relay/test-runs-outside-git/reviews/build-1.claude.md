---
{
  "at": "2026-11-27T09:28:16-05:00",
  "base_ref": "origin/develop",
  "base_sha": "688ca258b224630ff30f5b4605cef197fcca214a",
  "confirmation": false,
  "duration_s": 128.4,
  "effort": "medium",
  "head": "d064fcd9aaf2f19c12b80dc2d6a903d0fb824b39",
  "inputs": {
    "idea": "a58abb02b450406b4e715b1b4a5f5b4f51bbe2f8d4424d10e76a67070437e993"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: 5-hour limit used up until Fri 12:20"
  ],
  "tokens": {
    "cached": 222707,
    "input": 268380,
    "output": 4588
  },
  "verdict": "GO"
}
---

Review is done: I read the diff and idea.md, checked the other worktree consumers, ran the two changed test files, and read CI.

## Review: build, round 1 (PR #42)

**Verdict: GO.** The diff does what idea.md says and nothing else.

- **Correctness:** run worktrees now go under `<relay home>/test-runs/<repo>-<hash of the git common dir>/`. The folder is the same from the main checkout and from any linked worktree, so the runner and `cleanup` agree on it.
- **Upgrade path:** `cleanup` empties both the new and the old folder, and checks the run lock in both, so a run started by the old code and still live at upgrade time is not removed.
- **Status:** `relaylib/status.py` skips run worktrees in both places, so they do not show up as checkouts.
- **Failure paths:** creating the folder and adding the worktree both stay inside the runner's `try`, so a failure still records a `fail` and never a pass.
- **Tests:** new tests cover the run sitting outside `.git`, one folder per repo shared with its worktrees, cleanup of the old place, and the status skip for both places. I ran `tests.test_localtests` and `tests.test_status` locally: 56 tests, OK. Tests use a temp `RELAY_HOME`, so none touch the real `~/.relay`.
- **CI:** `unit` in workflow `test` completed with SUCCESS at 14:13Z, after the 09:06 local submit.
- **Deployment:** there is no WORKFLOW.md in this repo, so merging deploys nothing by itself.
- **Docs:** `docs/reference.md` is updated in plain English with no em-dashes.

```text
verdict: GO
prior:
blocking:
notes:
  - Not confirmed against a real Vite or Vitest project; the new test only proves the run's working directory is outside .git. The new path still sits under a dot folder (~/.relay), so a tool that matches dot folders on absolute paths could still object.
  - Behavior change: a run under <repo>/.git/relay/test-runs/ could find the main checkout's node_modules or parent config files by walking up; under ~/.relay it cannot. The docs already say the command does its own setup, but a repo whose test_command quietly relied on this will start failing.
  - relaylib/localtests.py:39 - the folder id is a hash of the git common dir path, so moving or renaming a repo orphans its old folder under ~/.relay/test-runs; no cleanup reaches it. Normally empty, so low cost.
  - docs/reference.md:59 names ~/.relay/test-runs/; the real root follows RELAY_HOME when that is set.
  - state.md records the build author as claude and the earlier review attempt (reviews/build-1.claude.error.md, timed out after 1200s) as same-provider, while this prompt says the work came from a different provider. Per the 2026-10-06 owner rule this does not change the verdict.
```
