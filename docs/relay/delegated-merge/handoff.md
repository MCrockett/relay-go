# Handoff: delegated-merge

- From: claude session ea35512a-39cb-46ea-a1f0-80b2575d3397
- At: 2026-11-26T16:46:29-05:00
- Branch: feat/delegated-merge
- Base: 5978f882d9af08d314a83842463037e217b5d1ca
- Head: 716ec091e81320abace066bbbff4010e8c95d9c9
- Stage/status: plan / drafting

## Done

- Idea (with the owner's decisions of 2026-11-26 in `idea.md`) and spec, GO in round 4 from
  claude as fallback (`reviews/spec-4.claude.md`). Rounds 1 to 3 were NO-GO; fixed: base binding
  (R10), review evidence (R6), the wait after the push (R10 steps 3 to 6).
- Owner decisions in this session, recorded in the spec: only hordle's `release` role delegates
  (`HORDLE_ROLE=release`), worktree kept while its session is live, the feature's session is told
  of merges it did not cause, `relay merge --repo`, and "Push the merge": a delegated merge pushes
  its own merge commit to the checked base, not `gh pr merge` (decision 8).
- The relay-go main checkout stays on `develop`; work in `.claude/worktrees/delegated-merge`.

## Next

- `relay take --feature delegated-merge`, then the relay-plan skill: `plan.md` from `spec.md`,
  every R1 to R17 covered, `relay submit`.
- The plan must settle the spec-4 review's notes (all non-blocking), in particular: claim
  `merged.mark_merged` only on `MERGED`; write the PR comment at once and queue the event after the
  outcome is known; one event per PR in the timeout case (the later resolver must not add a plain
  second event); who removes leftovers after a timeout; R14 note and branch delete for CLOSED and
  timeout; retry failed reads until the 60 seconds end; order step 5 before the push; delete the
  branch with a lease on the checked head; an injectable clock or sleep and a fake gh with a
  sequence of PR states for tests.
- Also from spec-3 notes: use `github_repo`, not `repo_name`, for R4; a test for a
  `ci = "release"` repo.
- PR into `develop`. The owner merges.
- hordle follow-up (not this feature): its hook refuses `relay merge` for roles (R28); add the
  change and the `release` role to hordle's `TODO.md` build order.

## Open decisions

- None open for the owner.

## Tests run (exact commands and results)

- None: no code yet.

## Uncommitted work and where it is

~~~
clean
~~~
