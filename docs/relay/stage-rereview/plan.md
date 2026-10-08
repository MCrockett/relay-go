# stage-rereview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner ask for an approved spec or plan to be reviewed again, from the terminal (`relay override review --stage spec|plan`) or the dashboard, as an independent owner-requested review that carries on from the spec to the plan and returns the feature to where it was.

**Architecture:** No new module. The owner-requested build review in `relaylib/reviewjobs.py` gains a `stage`: `prepare` validates it (D2) and picks the default reviewer for that stage's author (D6); `_run` refreshes and reviews the stage, then the plan after a spec GO (D4), and publishes once by lease. `owneractions.applicable` lists `review-spec` and `review-plan`; the CLI, the API and the page pass the stage through.

**Tech Stack:** Python 3.11 standard library, `unittest`, temp git repos and fake reviewer binaries from `tests/helpers.py`, Node for page functions (skipped without Node).

**Spec:** `docs/relay/stage-rereview/spec.md` (GO in round 1, `reviews/spec-1.codex.md`). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution line.
- Tests: `python3.11 -m unittest discover -s tests -t . -v`; every test sets `RELAY_HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` to temporary folders; never real Claude or Codex.
- relay never suggests a re-review (D7): no flag, ask, notification or inbox card for it.
- Build behavior is unchanged when `stage` is build or omitted.

## Spec clarifications

- **Spec review notes (round 1):**
  - Timeouts keep today's behavior: `review_current` retries a timed-out reviewer once, and only then fails the job (F1). Tests cover both a single timeout followed by a verdict and two timeouts.
  - Refused before any review starts, changing nothing: a stage value other than spec, plan or build (the CLI's `choices` and `prepare` both check); a failed fetch (`relay override` already refuses offline); no reviewers configured (`prepare` already raises "no reviewers configured").
  - R10's real check is recorded in `docs/relay/stage-rereview/acceptance.md` with its trigger ("after this PR is merged and relay-go is updated") and status, per owner rule 2026-10-01, not as a build gate.
- **Which plan GO counts for D4:** read before `mark_for_refresh`, which clears it. `chain = [stage] + (["plan"] if stage == "spec" and st["verdicts"].get("plan") == "GO" else [])`.
- **Validating the request:** `owneractions._validate(repo, slug, action, seen)` is called with `review-spec` or `review-plan`, so the fingerprint check and `applicable` decide D2's state rules; the PR checks stay build-only.
- **Effort:** as the build owner review: the entry's own `@effort`, else `config.review_effort(cfg, True, False)`.
- **Default per stage:** `reviewjobs.default(cfg, st, options, stage="build")` uses `authors[stage]`; the pending fallback confirmation applies to build only.

## Review Focus

1. Spec GO then plan GO lands at build drafting with the rounds unchanged (Task 2).
2. A plan error after a spec GO publishes nothing (F4, Task 2).
3. No path suggests a re-review: inbox cards, asks and notifications are unchanged (Task 5).
4. Build owner reviews behave exactly as before (existing tests, unchanged).

## Tasks

### Task 1: Which stages can be re-reviewed, and by whom

Covers: R3 (validation and default), R5, D2, D6.

Files: `relaylib/owneractions.py`, `relaylib/reviewjobs.py`, `tests/test_owneractions.py`, `tests/test_reviewjobs.py`.

- [ ] Failing tests:
  - `applicable` adds `review-spec` for a feature at plan or build with a spec GO, and `review-plan` at build with a plan GO; neither for a skipped stage, a stage without a GO, the current stage, status in-review, or done.
  - `reviewjobs.default(CFG, st, stage="spec")` uses `authors["spec"]`; with `authors={"spec": "claude", "build": "codex"}` the spec default is `codex:gpt-6-astra` and the build default is unchanged; a build `confirm_with` does not affect the spec default.
- [ ] Implement `applicable` additions (`state.HOLDING_STATUSES` minus in-review, stage order from `machine.REVIEWED`), and the `stage` parameter on `default`.
- [ ] Run, commit `feat: list which approved stages the owner can re-review`.

### Task 2: The re-review job

Covers: R3, R4, D3, D4, D5, F1 to F5.

Files: `relaylib/reviewjobs.py`, `tests/test_commands.py` (owner-requested reviews section).

- [ ] Add a test helper `to_build()` (spec GO, plan GO, at build drafting, written by the test session) and `request(..., stage=...)`.
- [ ] Failing tests, each checking the published state on origin:
  - Spec re-review: codex GO on spec, then GO on plan: build drafting, `rounds` unchanged, `verdicts` spec and plan GO, review files `spec-1r.codex.md` and `plan-1r.codex.md`, owner action "owner requested a spec re-review from codex:gpt-6-astra", message "spec and plan GO from codex:gpt-6-astra; back at build, drafting", the session's checkout untouched, no worktree left.
  - Spec NO-GO: plan not reviewed (one reviewer call), spec changes-requested, spec round counted, plan GO cleared.
  - Plan-only re-review: GO lands at build drafting; NO-GO leaves plan changes-requested.
  - A feature at plan (drafting) re-reviewing its spec: GO lands at plan drafting with no plan review.
  - Spec GO then a plan reviewer error (`FAKE_RC=1` on the second call): `ok` false, nothing published (F4).
  - One timeout then a verdict succeeds; two timeouts fail and publish nothing (F1).
  - The branch moved during the review: nothing published (F2).
  - Refusals with nothing changed: stage not approved, skipped (small feature), current stage, in-review, done, a running job (`Busy`), an unknown stage value, no reviewers configured.
  - Default reviewer follows the stage's author.
- [ ] Implement: `prepare(..., stage="build")` validates `stage in ("spec", "plan", "build")`, calls `_validate` with `review` or `review-<stage>`, keeps the PR checks for build only, uses `default(..., stage=stage)`; `Job` gains `stage`; `_run` for spec or plan records the owner decision, computes `chain`, and for each stage runs `mark_for_refresh`, sets `confirming`, calls `review_current(..., candidates=[spec], discard_errors=True, announce=False)`, and stops unless that stage now has a GO. Then the existing lease publish and a message per D5.
- [ ] Run, commit `feat: re-review an approved spec or plan on the owner's request`.

### Task 3: The terminal command

Covers: R1, R2.

Files: `relaylib/commands.py`, `tests/test_commands.py`.

- [ ] Failing tests: `relay override review --stage spec` from the owner terminal runs the job and prints the outcome; `--relayed` from an agent session records "relayed by" and comments on the PR when there is one; an agent without `--relayed` is refused; `--stage idea` is refused by argparse; offline (failed fetch) refuses before any review; `--stage` omitted is the build review as before.
- [ ] Implement: `--stage` with `choices=["spec", "plan", "build"]`, default build, on `relay override` (used only by `review`; refused with any other action); pass it to `prepare`; the printed "is reviewing" line names the stage; the relayed PR comment names the stage.
- [ ] Run, commit `feat: relay override review --stage`.

### Task 4: The dashboard API and data

Covers: R6, R8.

Files: `relaylib/ui/server.py`, `relaylib/ui/snapshot.py`, `tests/test_ui_server.py`, `tests/test_ui_snapshot.py`.

- [ ] Failing tests: `POST /api/action` with `action: "review", stage: "spec"` starts a spec job (patched `reviewjobs.start`) and passes the stage; without `stage` it is build; a bad stage gives 400 with the reason. Snapshot detail and rows carry `review_defaults` `{spec, plan, build}` (each `{id, label, note}`), and a feature at build with spec and plan GO lists `review-spec` and `review-plan` in `actions`.
- [ ] Implement the `stage` pass-through and `review_defaults` (computed with `reviewjobs.default(..., stage=...)`), keeping `review_default` for build.
- [ ] Run, commit `feat: request a stage re-review from the dashboard`.

### Task 5: The page

Covers: R7, D7.

Files: `relaylib/ui/page.html`, `tests/test_ui_server.py`.

- [ ] Failing tests: page strings `'review-spec':'Re-review spec'`, `'review-plan':'Re-review plan'`, the dialog title using the stage (`rr-feature` text built from the stage), the request body carrying `stage`; `ASK_ACTIONS` unchanged (no re-review on cards); Node test that `reviewStage('review-plan') === 'plan'`, `reviewStage('review') === 'build'`.
- [ ] Implement: labels and effects for both actions; `actions()` opens `openReviewRequest(d, reviewStage(action))` for `review`, `review-spec` and `review-plan`; the dialog uses `d.review_defaults[stage]` (falling back to `d.review_default` for build) and shows "<feature> / <stage>"; the request sends `stage`.
- [ ] Browser check on a throwaway fixture server: the details of a feature at build show both buttons, each opens the dialog for its stage with the stage author's default reviewer preselected; cards show neither.
- [ ] Run, commit `feat: re-review buttons in the feature details`.

### Task 6: Docs, acceptance ledger and the full check

Covers: R9 (suite), R10.

Files: `README.md`, `docs/relay/stage-rereview/acceptance.md`.

- [ ] README: under owner decisions, `relay override review --stage spec|plan` and the details buttons; one sentence that relay never suggests it.
- [ ] `acceptance.md`: "Re-review nba-experiments' spec and plan with Codex. Trigger: after this PR is merged and relay-go is updated. Status: pending."
- [ ] Full suite green; push; PR; CI green; `relay submit`.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1, R2 | 3 |
| R3 | 1, 2 |
| R4 | 2 |
| R5 | 1 |
| R6, R8 | 4 |
| R7 | 5 |
| R9 | 1 to 5 (tests in each), 6 |
| R10 | 6 (ledger), then after merge |
