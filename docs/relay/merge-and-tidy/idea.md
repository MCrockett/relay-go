# Merge and tidy

## Owner decisions (2026-11-01)

- From the hub, the owner chose "Start recommended" for this feature, then confirmed in this session:
  "yes, start merge-and-tidy".
- Evidence: a hub review of about 2,460 owner messages, 6,300 Bash calls and 101 stage reviews,
  2026-10-18 to 2026-11-01.

## Problem

Finishing a feature is still done by hand, and relay's view of finished work goes stale.

1. Merging. Agents run `gh pr merge` themselves (94 times, 64 chained with cleanup), then
   `git branch -d` (60), `git worktree remove` (55) and `git worktree prune` (18).
   `gh pr merge --delete-branch` fails with "cannot delete branch ... used by worktree" (votier 10-24,
   UpstreamPartners 10-18). The guarded `relay hub act ... merge` path (owneractions.merge, which
   checks for a fresh GO and green CI) was used 0 times. The owner says "merge N" in chat about 50
   times.
2. Status messages. Sessions hand-type "PR #N has a build GO, waiting for the owner's merge" and
   "merged, worktree removed, repo free" to the hub or to waiting sessions. The hub received about 91
   in two weeks.
3. Ghost features. UpstreamPartners clip-studio still shows "ready-to-merge ... stale GO: gh pr view
   failed: Could not resolve to a PullRequest with the number of 57" more than 10 days after PR #57
   merged on 10-18. sugarsnap shows the same with PR #88, though the work moved to MCrockett/ohsnap.

## Who it is for

The owner, who merges by saying "merge N" in chat or from the hub, and the agent sessions that carry
that out and report it.

## What relay-go does

1. `relay merge <pr|slug>`: checks merge readiness (fresh GO, green CI, as owneractions.merge does),
   merges with a merge commit, removes the feature's worktree and local branch, switches the main
   checkout to the base branch and pulls, prunes worktrees, and marks the feature merged. An agent
   carrying out the owner's "merge N" passes `--relayed`, recorded as overrides are. The build
   skill's "never merge yourself" says how an owner's "merge N" in chat is carried out: through this
   command with `--relayed`.
2. relay writes a one-line event to the hub log (`~/.relay/hub/log.jsonl`) on a GO, on a feature
   stuck or waiting on the owner, on merged, and on released. Optionally it also sends the line to
   sessions named in the repo's config. Agents stop writing these by hand.
3. When a feature's PR is merged, closed, gone or cannot be resolved, relay marks the feature merged,
   closed or moved, with a note saying why, and drops it from the inbox and `relay status`.

## Success

- An owner's "merge N" is one command that leaves the repo free, with no hand-run cleanup and no
  worktree errors.
- The hub gets GO, waiting, merged and released lines from relay, not from agents.
- No feature whose PR is merged or gone shows in the inbox or status.

## Not in this feature (filed for later)

The per-repo PR-slot queue, pre-submit spec/plan lint, doc-erratum mode, extra-round one-tap,
detecting PRs opened outside relay, CI presets, the handoff take fallback, and a gate record for
sample approvals.
