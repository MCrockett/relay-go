# stage-rereview: acceptance

Checks that depend on events after the build (owner rule 2026-10-01). The build is reviewed on code, tests and the checks possible today.

| Check | Trigger | Status |
|---|---|---|
| Re-review bottomsup nba-experiments' spec and plan with Codex (`relay override review --stage spec`, from the owner's terminal or relayed by the holding agent); Codex reviews the spec, then the plan, and the feature returns to build. | After this PR is merged and relay-go is updated. | Done 2026-10-08. The command ran end to end; Codex gave the spec a NO-GO with 3 blocking findings, so the plan was not reviewed. The NO-GO counted against the spec's old round limit and stopped the loop; rereview-rounds fixes that. |
