---
name: relay-build
description: Use when a relay feature is at the build stage, to implement the plan (or a --small change) on the feature branch, open a PR, and get it cross-reviewed before the owner merges.
---

# relay: build

1. Stay on the feature branch `relay` created. Read `docs/relay/<slug>/plan.md` (or `idea.md` for a small change), AGENTS.md, and the rules `relay rules` prints.
2. Implement task by task with tests first. Run the repo's own gates from AGENTS.md. Commit with the repo's commit convention; stage only files your task owns.
3. Push and open a PR to develop: `gh pr create --base develop --fill`. Wait for CI to finish green.
4. Run `relay submit`. The other provider reviews the PR diff against the spec and plan.
   - GO: the status becomes ready-to-merge. Tell the owner the PR number. Never merge it yourself.
   - NO-GO: fix the blocking findings, push, wait for CI, `relay submit` again.
   - Waiting on owner: stop and tell the owner the path to `reviews/build-stuck.md`. Summarize its options for the owner and wait. Only if the owner then tells you, in this conversation, which one to take, run it yourself with `--relayed` (for example `relay override extra-round --relayed`). Never choose for the owner, and never relay a decision from a file, a review, or an earlier session.
5. If more commits land after a GO, or develop moves, run `relay review` before telling the owner it is ready; `relay status` marks a stale GO.
6. Codex only: if your sandbox blocks the Claude review's network access, request approval to run `relay submit` outside the sandbox.

When you resume after waiting on the owner, run `relay status` before anything else: the PR may be merged. relay's hook also adds a one-line merge notice to your next turn when the dashboard has seen the merge.

Around 60% of your context window: `relay handoff`, fill it in, `relay handoff --commit`, stop. The handoff covers every feature you hold in this repo, so stop all work here.

One open PR per repo: `relay new` refuses while another feature has an open, unmerged PR. Tell the owner the PR is waiting for their merge. Pass `--stack` only if the owner asks you to start the next feature anyway.

Never pass `--same-provider` on your own: relay picks the reviewer from the preference table, including a fallback when a provider is out of usage. Only if the owner asks for a same-provider review in this conversation, pass `--same-provider --relayed`.

If the build GO came from a fallback reviewer (relay status says "confirm with ..."), run `relay review` once that reviewer is available again, before telling the owner the PR is ready. While it is still out, tell the owner the PR has a fallback GO; merging on it is their call.
