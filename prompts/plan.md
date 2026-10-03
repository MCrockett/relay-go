# Review: plan

Feature folder: $feature_dir. Read spec.md and plan.md there.

Check:
1. Coverage: list each spec requirement and the plan task or tasks that implement it. A requirement with no task is blocking.
2. Every task names the files it touches and the test that proves it.
3. The order is buildable: no task uses something a later task creates.
4. No task adds scope the spec does not ask for.
5. Tests exercise the spec's failure paths, not only the happy path.
