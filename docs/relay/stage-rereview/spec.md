# stage-rereview: spec

## Why

Once a feature is past a stage, the owner cannot ask for that stage to be reviewed again. bottomsup nba-experiments reached build with its spec and plan approved by Claude reviewing Claude's work, because Codex was unavailable at the time; with Codex back, the owner wanted it to review both before the build started, and relay refused. The pieces exist: the owner-requested build review (`relay override review`, the dashboard's Request review) already runs an independent review with a reviewer the owner picks, in an isolated worktree, and `machine.mark_for_refresh` already re-reviews an approved stage without a new round. This feature lets the owner point them at an approved spec or plan. See idea.md.

## Decisions

- D1. **An owner action, not the holder's command.** The idea named `relay review --stage`; that command belongs to the session holding the repo and re-reviews in its checkout. A re-review on the owner's say-so belongs with the other owner decisions, so it extends the owner-requested review: `relay override review --stage spec|plan|build` (default `build`, unchanged) in the owner's terminal, or by an agent with `--relayed` when the owner asked in that conversation, and the dashboard's review request with a stage. It runs as that path does today: in an isolated worktree at the published commit the owner saw, published by lease, recorded as an owner decision (`owner requested a spec re-review from codex:gpt-6-astra`, plus who relayed it).
- D2. **When it is allowed.** For stage S in spec or plan: S has a GO (`verdicts[S] == "GO"`), S is not skipped, the feature's current stage comes after S (plan or build for spec; build for plan), the feature is not done or merged, its status is not in-review, and no owner review job is running for it. No PR is needed. Anything else is refused with the reason and changes nothing. Build keeps today's rules.
- D3. **Independent, without a new round.** The job runs `mark_for_refresh(st, S)` and sets `confirming`, exactly as the owner build review does, so the reviewer reviews S fresh: no prior-findings accounting and no tagged-new-finding rule. The review file is the refresh kind (`spec-<n>r.<provider>.md`).
- D4. **A spec re-review carries on to the plan.** The plan's GO was given on the spec, so `mark_for_refresh` clears it today. When S is spec, the spec gets a GO, and the plan had a GO before the request, the same job then re-reviews the plan the same way, with the same reviewer. The author is never asked to resubmit an unchanged plan. The job stops at the first NO-GO or error.
- D5. **Where it lands.** All GO: the feature is at the stage after the last re-reviewed one, drafting, which is build for a feature that was at build (the author resubmits the build when the build had already been reviewed, as with any upstream change). A feature that was at plan when the spec was re-reviewed is at plan, drafting. A NO-GO: that stage is changes-requested (the round counts, as a refresh NO-GO does today) or waiting-owner when the stop rules say so; the author fixes and resubmits. A review error: nothing is published, as today.
- D6. **Default reviewer.** The dialog and `--reviewer` work as for build. The default is the first entry of the preference table for S's author (`authors[S]`), which for Claude-written work is Codex when Codex is first.
- D7. **Only on request.** relay never suggests, offers or starts a re-review on its own (owner rule 2026-10-06: a same-provider GO is a valid GO). No flag, ask, notification or inbox card points to it.

## Requirements

### Terminal
- R1. `relay override review --stage {spec,plan,build}` (default build). Owner terminal, or an agent with `--relayed`, as today; `--reviewer` as today. For spec or plan it checks D2 against the fetched, published state and refuses with the reason otherwise.
- R2. It prints who is reviewing which stage, then the outcome, for example "spec and plan GO from codex:gpt-6-astra; back at build, drafting" or "spec NO-GO from codex:gpt-6-astra; spec is changes-requested", and on a relayed request comments on the PR when there is one, as today.

### Review job
- R3. `reviewjobs.prepare(repo, slug, seen, reviewer=None, relayed_by=None, stage="build")` validates D2 for spec or plan (build unchanged: an open PR) and picks the default reviewer per D6.
- R4. `_run` for spec or plan: records the owner decision, runs `mark_for_refresh` and `confirming` for S, reviews S with the chosen reviewer, and, per D4, the plan next. One publish by lease at the end; nothing is published on an error or if the branch moved. The job's message states the outcome per D5.
- R5. `owneractions.applicable` adds `review-spec` and `review-plan` when D2 allows them (state checks only; the job lock is checked when the request starts).

### Dashboard
- R6. `POST /api/action` with `action: "review"` accepts `stage` (spec, plan or build; default build) and passes it to `prepare`.
- R7. The feature details show "Re-review spec" and "Re-review plan" when the row's actions include them. Each opens the existing review dialog, titled with the stage, with the reviewer choices and the default for that stage's author. These buttons are never on inbox cards (D7).
- R8. Snapshot detail and rows carry the per-stage default reviewer so the dialog can preselect it.

### Tests
- R9. With fake reviewer binaries: spec re-review GO then plan GO lands at build drafting with no new rounds and two refresh review files; spec NO-GO stops before the plan and leaves spec changes-requested; plan-only re-review; a feature at plan re-reviewing its spec; refusals for an unapproved, skipped, current or later stage, in-review status, a running job, and done; `--relayed` recording and the owner-terminal rule; the branch moving during the review publishes nothing; default reviewer by the stage's author; the API with and without `stage`; page strings for both buttons, the stage in the dialog, and no re-review on cards. Every test sets `RELAY_HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` to temporary folders and never calls real Claude or Codex.
- R10. Real check after merge: the owner (or the agent with `--relayed`) runs `relay override review --stage spec` on bottomsup nba-experiments; Codex re-reviews the spec and the plan, and the feature returns to build. This is recorded in the build PR's final notes, not as a build gate (owner rule 2026-10-01).

## Failure paths

- F1. The reviewer errors or times out: the job fails, nothing is published, and the message says why; the feature is as before.
- F2. The branch moved during the review (the agent pushed): nothing is published; "the branch moved during the review; nothing was published. Request again."
- F3. The chosen reviewer is out of usage or not configured: refused before anything runs, as today.
- F4. The spec gets a GO and the plan review errors: nothing is published, so the spec's new GO is not kept either; the owner requests again. (One publish per job keeps the state consistent.)
- F5. The holding agent has local, unpushed state changes: the job uses the published state; the agent's next relay command syncs as it does after any owner action.

## Non-goals

- Re-reviewing the idea (it has no review) or re-running a build review differently from today.
- Suggesting a re-review, or confirming same-provider GOs automatically.
- Changing round limits, stop rules or the review prompts.

## Open questions
