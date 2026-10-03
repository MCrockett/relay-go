# Review: build (pull request)

Feature folder: $feature_dir. PR #$pr, base $base_ref ($base_sha), head $head_sha. Small change: $small.
Read the diff with `git diff $base_sha...$head_sha`. Read whichever of spec.md, plan.md and idea.md exist in the feature folder: a small or adopted change may have only some of them.

Check:
1. Correctness, regressions, failure paths, data loss.
2. Every plan task is present in the diff. For a small change: the diff does what idea.md says, and nothing else.
3. Spec requirements actually hold in the code, not only in comments or tests.
4. Tests cover the changed behavior, including failure paths.
5. CI: read it with `gh pr view $pr --json statusCheckRollup`. Do not assume it passed.
6. Deployment effects: if this repo's WORKFLOW.md says merging to $base_ref deploys, review what goes live.
