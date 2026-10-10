# ci-on-submit

## Problem

After ci-skip-bookkeeping (PR #16) and the per-repo workflow changes, relay's own commits and docs-only pushes to main no longer start CI. Pull requests still run full CI on every push the agent makes during a build: each fix commit, and each docs-only commit the agent writes itself, such as a plan note. GitHub cannot filter these with `paths-ignore`, because on pull requests it compares the whole PR diff, which includes code. Concurrency cancels a run only while it is still in progress, so pushes a few minutes apart each run in full.

On 2026-10-10 in burned-web, the agent committed a one-file plan edit (`docs/relay/live-polish/plan.md`) on PR #20. GitHub refused to start the run because the spending limit was reached. relay counted that refused run as a failure, because it was the newest run on that code, so the build gate stayed blocked. Commit `4086bca` already had green CI on the same code outside `docs/relay/`. Unblocking it meant raising the budget and rerunning full CI on a plan edit.

## Who it is for

The owner, who pays for Actions minutes in every private repo relay works in, and the agent sessions that stop when CI is blocked.

## Idea

The owner's rule: CI on GitHub runs only for real code changes, and only on the final change of a session that opens or updates a PR.

- **PR CI starts at submit.** The build skill opens the PR as a draft, and repo workflows skip their jobs on draft PRs. `relay submit` marks the PR ready, which starts one CI run on the final commit. After a NO-GO, relay turns the PR back into a draft while the agent fixes it, and the next submit marks it ready again.
- **Runs GitHub never started don't count.** A run refused for billing or a spending limit is not a CI result. relay looks past it to the last real result on the same code, as it already does for cancelled runs.
- **Agent edits under `docs/relay/` start no CI.** When the agent commits only files under `docs/relay/` and origin already has the same code, the commit is marked like relay's bookkeeping commits.

The per-repo workflow change (skip jobs on drafts) goes out as one PR per repo after this ships, as last time.

## What success looks like

- During a build, a PR gets one CI run per submit, on the final commit, not one per push.
- A plan or spec edit pushed to a PR starts no CI run.
- A run GitHub refused to start never blocks relay's build gate when the same code already has a real green result.
- Repos with required status checks can still merge.
