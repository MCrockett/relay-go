# stage-rereview

## Problem
Once a feature is past a stage, the owner has no way to ask for that stage to be reviewed again. On 2026-10-07 bottomsup nba-experiments was at build (drafting, no PR yet) with spec and plan both approved by Claude reviewing Claude's own work ("same provider as the author"), because Codex was unavailable then. Codex was available again and the owner wanted it to review the spec and plan before the build started. The agent found no way: `relay review` only re-reviews after a review error or a stale GO, and `relay override review` (the dashboard's Request review) is build-only and needs a PR. Its only suggestions were to start the build anyway, or to run Codex on the files outside relay, where nothing is recorded.

relay already has the mechanism: when an approved spec or plan changes, `refresh_upstream` marks that stage for a refresh (`machine.mark_for_refresh`), re-reviews it without a new round, clears the later stages' GOs, and the feature comes back through them. What is missing is a way for the owner to start that on purpose, without editing the document.

## Who it is for
The owner, deciding that an approved stage deserves another look (usually a different reviewer now that the first choice is available again), and the agent holding the feature when the owner asks it to.

## What success looks like
1. Terminal: `relay review --stage spec` or `relay review --stage plan` re-reviews that approved stage with the reviewer from the preference table (or one the owner picks), as a refresh: no new round. It is the owner's call: an agent runs it only with `--relayed` when the owner asked in that conversation, and it is recorded as an owner decision.
2. Dashboard: the feature details offer "Re-review spec" and "Re-review plan" for approved stages, with the same reviewer picker as Request review, and it runs as an owner action like the others.
3. On GO the feature comes back to where it was; a spec re-review is followed by the plan's (its GO depended on the spec), and the agent is not asked to resubmit an unchanged plan. On NO-GO the feature goes back to that stage for fixes, as any NO-GO does.
4. Refused, with a clear reason, while a review is running, for a stage that was skipped or not yet approved, and once the feature is done.
5. Tested end to end on nba-experiments: Codex re-reviews its spec and plan, and the feature returns to build.

## Known limits (owner discussion 2026-10-07)
- relay never suggests or starts a re-review on its own: a same-provider GO is a valid GO (owner rule 2026-10-06). This is only an owner tool.
- A re-review after the build has a PR also invalidates the build GO, as any upstream change does today.
