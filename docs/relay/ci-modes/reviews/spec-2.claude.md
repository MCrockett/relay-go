---
{
  "at": "2026-11-10T10:41:37-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 120.3,
  "effort": "medium",
  "head": "155375aed78d6ff5f5a383c3658b4bf53e164c9c",
  "inputs": {
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
    "cached": 490130,
    "input": 549736,
    "output": 11264
  },
  "verdict": "GO"
}
---

Review is done: I read the spec, idea, the round 1 review, and checked the revised design against `commands.py`, `owneractions.py`, `gitops.py`, `freshness.py`, `config.py`, `status.py` and the workflow file. I could not run shell commands in this session, so everything below is from reading, not from running git.

**Verdict: GO.** All three round 1 blockers are fixed, and the revision introduced nothing blocking.

## Prior findings

- **R1-1, resolved.** D3 names one source for the mode, command and timeout: the global config merged with `origin/<base>:docs/relay/config.toml`. Submit, review, `relay test`, status and merge now build the same key, and a PR cannot weaken its own gate. R4 puts this in one helper that replaces every `require_ci` read.
- **R1-2, resolved.** D8 starts a run at every build gate, including `relay review` on a stale GO. That path does reach `require_pr_ready` (`relaylib/commands.py:454` to `:501`). R16 and the failure path now name the right command for each status.
- **R1-3, resolved.** D7 and D9 make `relay test` start a detached run and return at once. `--status` reports, and `--wait` is for the owner's terminal only. R19 puts that rule in the relay-build skill.

## Checks

1. **Testable:** yes. Every requirement has stated message or flag text, and R22 lists the cases.
2. **Scope:** matches idea.md. `none`, `relay test`, the timeout, notifications and cleanup come from owner answers Q1 to Q4 or from the design. D6 states and justifies the move away from state.md.
3. **Architecture decisions:** D1 to D13 are each stated with a reason.
4. **Open questions:** the section exists and is empty.
5. **Failure paths:** covered, apart from the gaps in the notes.

## Notes (nonblocking, for the plan)

- **Worktree path inside `.git`.** D4 runs tests under `<git common dir>/relay/test-runs/`. Some test runners skip any path containing `/.git/` (vitest's default exclude, jest's file crawl, as far as I know). A JS repo could then find no tests. That most likely fails closed, but the plan should check one non-Python repo or pick a path outside `.git`.
- **Ignored files are missing from the clean checkout.** Repos whose tests need gitignored local files (an `.env`, generated config) will fail in the D4 worktree. D4 says the command includes its own setup; the README should say this plainly.
- **Invalid config on the base branch.** If a bad `[build]` value ever reaches the base branch, every PR gate refuses, including the PR that fixes it. That can only happen outside relay, since relay commands in the feature checkout refuse it first. The spec should name the recovery: fix the base directly.
- **Stacked PRs.** D3 reads the config of the PR's base, which for a stacked PR is an unmerged feature branch. That branch can set the gate for the PR above it.
- **Rerun of a passed key.** D9 says `relay test` replaces any finished record, so it reruns a `pass` and blocks merge until the rerun ends. Consider reporting the pass instead, with a force flag.
- **Failed record at the gate.** D8 restarts a run when the record for the same key is `fail`. That is a retry on every submit or review, which sits oddly with the "no automatic retry" non-goal. Say it is intended.
- **Flake wording in the inbox.** R16's failed-run text says "fix, push and `relay review`". For a flake with a fresh GO, `relay review` does nothing; the text should also mention `relay test`.
- **Simultaneous starts.** D9 and D10 do not say what happens when two commands start a run for the same key at once. The D11 lock should decide it; say so.
- **Offline behaviour.** `relay test` and the R15 flag need the PR's base from GitHub. The spec should say they fail with today's GitHub or fetch error. A stale local `origin/<base>` is used when the dashboard has not fetched.
- **Base moved after a pass.** The key is the PR head's code, so a pass stays valid when only the base moves. This matches how `ci_for_code` treats GitHub results today.
- **`ready_draft` in local mode.** R8 does not say whether a draft PR that already has green GitHub CI gets the skip marker first, as it does today. State that local mode marks ready without reading CI.
- **R20 creates a file.** relay-go has no `docs/relay/config.toml` today, so R20 creates it rather than edits it.

```text
verdict: GO
prior:
  - id: R1-1 status: resolved
  - id: R1-2 status: resolved
  - id: R1-3 status: resolved
blocking:
notes:
  - D4 puts the test worktree under <git common dir>/relay/test-runs/, a path containing /.git/. Some test runners skip such paths (vitest default exclude, jest file crawl), so a JS repo may find no tests. Likely fails closed; the plan should check one non-Python repo or use a path outside .git. Not verified by running; shell was unavailable in this review.
  - The D4 clean checkout has no gitignored files, so repos whose tests need local untracked files will fail there. D4 says the command includes setup; say this plainly in the README.
  - An invalid [build] value on the base branch makes every PR gate refuse, including the PR that fixes it. It can only land outside relay, but the spec should name the recovery (fix the base directly).
  - For a stacked PR, D3 reads the config of an unmerged feature branch as the base, so that branch can set the gate for the PR above it.
  - D9 lets relay test replace a pass record with a new run, which blocks merge until it ends. Consider reporting the pass and needing a force flag.
  - D8 restarts a run at every submit or review when the record for the same key is fail. This is a retry and sits oddly with the "no automatic retry" non-goal; say it is intended.
  - R16's failed-run text says "fix, push and relay review"; for a flake with a fresh GO, relay review does nothing, so the text should also mention relay test.
  - Two commands starting a run for the same key at once is not specified; the D11 lock should decide it, and the spec should say so.
  - relay test and the R15 flag need the PR's base from GitHub; offline behavior should be stated as today's GitHub or fetch error. A stale local origin/<base> is used when the dashboard has not fetched.
  - The key is the PR head's code, so a pass stays valid when only the base branch moves. This matches how ci_for_code treats GitHub results today.
  - R8 does not say whether ready_draft in local mode still applies the skip marker when GitHub CI is already green; state that local mode marks ready without reading CI.
  - R20 creates docs/relay/config.toml in relay-go; the file does not exist today.
```
