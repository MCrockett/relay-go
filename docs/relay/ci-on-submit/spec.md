# ci-on-submit: spec

## Why

ci-skip-bookkeeping (PR #16) stopped relay's own commits from starting CI, and the per-repo workflow PRs stopped docs-only and develop pushes. A pull request still runs full CI on every push the agent makes during a build: each fix commit, and each commit of a plan or spec note. `paths-ignore` cannot help on pull requests, because GitHub compares the whole PR diff, and that includes code. Concurrency cancels a run only while it is still in progress. The owner's rule (2026-10-10): CI on GitHub runs only for real code changes, and only on the final change of a session that opens or updates a PR. See idea.md.

Two facts checked on 2026-10-10 shape the design:
- **A run GitHub refuses to start is recorded as a failure.** burned-web run 38047465805 (PR #20, head `75f4467`, a plan-only commit) has check runs `gates` and `e2e` with conclusion `failure`. They lasted about two seconds and ran no steps. Each has a check-run annotation of level `failure` whose message begins "The job was not started because recent account payments have failed or your spending limit needs to be increased". `gitops.ci_for_code` took that as the newest result for the code and returned `failing`. Yet `4086bca`, with the same code outside `docs/relay/`, had a green run, so the build gate was blocked by a run that never tested anything.
- **relay counts a skipped job as green.** `gitops.GREEN` includes `SKIPPED`. If workflows skip their jobs on draft PRs, a draft head with only skipped jobs would pass relay's build gate with no tests run. That has to change before drafts can gate CI.

GitHub's draft pull requests give the hook the owner's rule needs. A workflow listening for `pull_request` types `opened, synchronize, reopened, ready_for_review` can skip its jobs while `github.event.pull_request.draft` is true. Marking the PR ready then fires one `ready_for_review` run on the head commit. Skipped jobs are not billed. Draft PRs cannot be merged.

## Decisions

- D1. **The agent opens the build PR as a draft.** The relay-build skill says `gh pr create --base develop --fill --draft`. If GitHub refuses a draft (a plan without draft PRs for private repos), the agent creates it without `--draft`; everything below then works as it does today, minus the savings.
- D2. **relay submit marks a draft ready.** At build submit (status `drafting` or `changes-requested`), after the existing checks that the PR is open and has HEAD's code, relay reads the PR's `isDraft`. When it is true:
  1. relay runs `gh pr ready <number>`.
  2. If the code at the PR head already has a real green result (D5), submit goes on in the same call (see D4 for the order).
  3. If `[build] require_ci` is false and there is no result at all (`none`), submit also goes on, as today for a PR without CI.
  4. Otherwise submit stops before recording anything, with a message that the PR is now ready and CI has started for the head commit, and that the agent should wait for it (`gh pr checks <number> --watch`) and then run `relay submit` again. The exit status is non-zero, as for today's "CI is pending" refusal. The next submit finds a ready PR and applies the existing gate.
  relay does not wait for CI inside `submit`. Agent tool calls have time limits (Claude Code's is 10 minutes), and CI plus a review can run past them.
- D3. **The PR goes back to draft when the work goes back to the agent.** relay runs `gh pr ready <number> --undo` when:
  - a build review's NO-GO leaves the status `changes-requested` (a NO-GO that ends in `waiting-owner` does not convert; if the owner then grants an extra round or resets rounds, the PR stays ready and fix pushes run CI as today), and
  - build submit refuses because CI is `failing` for the PR head's code.
  It does not do this for `waiting-owner`, `review-error` or `ready-to-merge`: the code has a green run and no fix is pending. A failure to convert is reported as a one-line warning and changes nothing else; the PR then simply runs CI on each push, as today.
- D4. **The order of ready and the submit commit.**
  - When the draft's code has no real result yet (D2.4), relay marks the PR ready and makes no commit, so the `ready_for_review` run tests the agent's own head commit. The `relay: submit build` commit comes in the next submit, which finds green CI, so ci-skip-bookkeeping D2 marks it and it starts no second run.
  - When the draft's code already has a real green result (D2.2, for example after a NO-GO whose fix touched only docs), relay first makes and pushes the `relay: submit build` commit, which ci-skip-bookkeeping D2 marks, and only then marks the PR ready. GitHub honors `[skip ci]` on the head commit for `pull_request` workflows, so the `ready_for_review` event starts no run on code that already passed. The build checks this on GitHub with a live probe before it relies on it (R15).
  - In a repo with required status checks the commit that records the GO stays unmarked (ci-skip-bookkeeping D10), so such repos get one more run per GO, on the commit the owner merges. That is the owner's 2026-10-09 decision and the one exception to "one run per submit".
- D5. **What counts as a result.** `gitops.ci_for_code` already passes over cancelled runs (ci-skip-bookkeeping D8). It now also passes over two more kinds of result, in the same way: they are neither green nor failing, a real failure on the same commit still wins, and the search goes on to older commits with the same code.
  1. **Not started.** A failing check run from the `github-actions` app whose annotations include one of level `failure` with a message beginning "The job was not started". relay reads annotations only for failing check runs with `output.annotations_count > 0`, and only inside `ci_for_code`. If the annotations call fails, the run counts as a real failure.
  2. **All skipped.** A commit whose latest check runs all have conclusion `skipped`, and which has no legacy commit status. A commit with at least one success beside its skipped jobs stays green, as today.
- D6. **When nothing real is found.** If `ci_for_code` finds no real result but did pass over a not-started run, it returns a new state `not-started`. Build submit then refuses with a message that GitHub did not start CI (a billing or spending-limit problem), that the owner must fix it under Settings, Billing and plans, and that the run can then be restarted with `gh run rerun`. Everywhere else, `not-started` reads like `pending`: no marker (ci-skip-bookkeeping D2.4 lists the states that allow one, and this is not one of them), and the dashboard and merge checks treat it as not green.
- D7a. **Agent edits under the feature's relay folder.** A new command `relay commit "<message>"` commits the agent's changes under `docs/relay/<slug>/` (plan notes, spec edits) and pushes them, through the same `ciskip.commit` and `ciskip.push` that relay's bookkeeping uses. So the commit carries the skip marker exactly when ci-skip-bookkeeping D2 allows it: origin already has the code, and GitHub already has a result for it. It commits nothing outside that folder, refuses when there is nothing to commit there, and refuses when the caller does not own the feature (as `relay submit` does). The relay-build skill tells the agent to commit such edits with `relay commit`, not `git commit`. This covers the case drafts do not: a plan edit on a PR that is ready, for example after a GO.
- D7. **relay-go's own workflow** follows the pattern, as the first repo to use it: `pull_request` types including `ready_for_review`, the job skipped on drafts, push CI on `main` only, and the concurrency group from the per-repo PRs.
- D8. **README.** The "CI on relay's commits" section gains the draft flow and the workflow snippet a repo needs, so the per-repo PRs after this ships can copy it.

## Requirements

- R1. The relay-build skill tells the agent to open the PR with `--draft`, and to fall back to a normal PR if GitHub refuses a draft. It no longer tells the agent to wait for CI before the first `relay submit`. It says what to do when submit reports that CI has started: wait with `gh pr checks <number> --watch`, then submit again. After a NO-GO it says the PR is back in draft, so fix pushes start no CI. (D1, D2, D3)
- R2. `gitops.PR_FIELDS` includes `isDraft`. (D2)
- R3. Build submit on a draft PR:
  - with a real green result for the head's code: commits and pushes `relay: submit build` (marked per ci-skip-bookkeeping D2), then runs `gh pr ready`, then reviews, in one call;
  - with no result and `require_ci` false: runs `gh pr ready` and continues as today;
  - otherwise: runs `gh pr ready` and stops with the D2 message, a non-zero exit, and no state change or commit. (D2, D4)
- R4. If `gh pr ready` fails, submit stops with a message that names the PR and gh's error, and records nothing. (D2)
- R5. Build submit that refuses for `failing` CI converts the PR to draft, then raises its existing error. (D3)
- R6. A build NO-GO that leaves the status `changes-requested` converts the PR to draft, after the verdict is published (committed and pushed). This covers `relay submit`, `relay review`, and reviews started from the dashboard. A dashboard review that is discarded (the branch moved, or the dashboard stopped) publishes nothing and converts nothing. A NO-GO that ends in `waiting-owner` converts nothing. (D3)
- R7. A failed draft conversion prints one warning line and does not change the outcome of the command. (D3)
- R8. `ci_for_code` passes over not-started check runs (D5.1) and all-skipped commits (D5.2) as it passes over cancelled ones. A real failure on the same commit wins over a not-started run. (D5)
- R9. `ci_for_code` returns `not-started` when it found no green or failing result and passed over at least one not-started run. Build submit refuses with the D6 message. ci-skip-bookkeeping's marker is not added. The dashboard and merge readiness treat it as not green. (D6)
- R10. Annotation reads happen only for failing `github-actions` check runs with annotations. A failed read counts as a real failure. (D5)
- R11. `.github/workflows/test.yml` in relay-go: `pull_request` with types `opened, synchronize, reopened, ready_for_review`; the job has `if: github.event_name != 'pull_request' || !github.event.pull_request.draft`; push on `main` only; concurrency `ci-${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`. (D7)
- R12. README documents the draft flow, the not-started and all-skipped rules, and the workflow snippet from R11. (D8)
- R14. `relay commit "<message>"` commits only changes under `docs/relay/<slug>/`, marks the commit when ci-skip-bookkeeping D2 allows, pushes it (with ci-skip-bookkeeping D7 on a failed push), refuses with nothing to commit, and refuses for a caller that does not own the feature. The relay-build and relay-plan skills tell the agent to use it for edits under the feature's folder. (D7a)
- R15. Before relying on D4's second case, the build records in the plan's build notes a live probe on a scratch PR in relay-go: a draft PR whose head commit carries `[skip ci]`, marked ready, starts no run. If GitHub does start a run, D4's second case is dropped (relay marks ready without the commit first) and the build notes say so. (D4)
- R13. Tests use the fake `gh` from `tests/helpers.py`, extended for `pr ready`, `pr ready --undo`, `isDraft` and check-run annotations. They cover R3 to R10 and R14, including the burned-web case: a not-started run on a docs-only head with green CI on the code commit gives `green`.

## Failure paths

- **`require_ci = false` and no workflow.** Submit marks the draft ready and goes on (D2.3).

- **No draft support.** The agent opens a normal PR (R1). Submit sees `isDraft` false and behaves as today.
- **A repo without the draft workflow change.** Draft pushes run CI as before, so the draft's code may already be green: submit marks it ready and continues (D2.2). Nothing breaks; there is no saving until that repo's workflow changes.
- **`gh pr ready` fails.** Submit stops and records nothing (R4).
- **The CI run that `ready` starts fails.** The next submit refuses for `failing`, converts the PR to draft (R5), and the agent fixes and pushes for free.
- **The draft conversion fails** after a NO-GO or failing CI. One warning (R7); fix pushes run CI as today.
- **GitHub refuses the run (billing).** Older real results on the same code still count (D5.1). With none, submit says plainly that billing blocked CI (D6) instead of reporting a code failure.
- **Every job is skipped for a reason other than draft** (for example a job-level path condition). The commit counts as no result (D5.2), and relay looks for an older result on the same code. If there is none, the gate reports no CI, as for a repo without CI.
- **The agent forgets `relay submit` and pushes after a GO.** The PR is ready, so each push runs CI, as today. A stale GO is re-reviewed by `relay review`, as today.
- **Required status checks.** A draft cannot be merged, so skipped required checks on a draft do no harm. Marking it ready runs the real checks on the head. ci-skip-bookkeeping D10 still leaves the ready-to-merge commit unmarked.

## Non-goals

- **Agent commits outside the feature's relay folder that change only docs** (README, other Markdown). While the PR is a draft they cost nothing; on a ready PR they run CI as today.
- **Waiting for CI inside relay** (D2).
- **Changing other repos' workflows.** That is one PR per repo after this ships, as last time.
- **Making drafts optional per repo.** The draft calls are harmless where the workflow ignores drafts.

## Open questions
