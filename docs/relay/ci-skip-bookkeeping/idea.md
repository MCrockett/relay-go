# ci-skip-bookkeeping

## Problem

Every relay bookkeeping commit ("relay: submit build", "relay: build GO (codex)", "relay: build NO-GO", "relay: submit spec", overrides, handoffs) is pushed to the feature branch, and each push starts the repo's full CI on a commit that changes no code. Across the owner's private repos, from 1 to 9 October 2026 these runs took about 2,900 of 6,000 billable GitHub Actions minutes (48%). The owner hit 90% of their monthly Actions budget by the 9th.

relay does not need these runs. Its build gate, its build review and its merge all use `gitops.ci_for_code`, which already looks past bookkeeping commits to the newest CI result on a commit with the same code.

## Who it is for

The owner, who pays for Actions minutes in every repo relay works in.

## Idea

When relay commits bookkeeping and origin's branch already has the same code (no difference outside relay's folder), relay adds `[skip ci]` to the commit message, so GitHub starts no workflow for that push or for the PR head. When the push would also carry code origin does not have yet, relay leaves the message alone so that code gets CI.

Repos whose branch protection or rulesets require status checks cannot use this: a `[skip ci]` PR head leaves the required checks waiting and blocks the merge. A per-repo setting turns it off.

## Success

- Bookkeeping pushes start no CI runs; code pushes still do.
- relay's build gate, build review and merge still see green CI for the code.
- Repos with required checks can turn it off and merge as before.
