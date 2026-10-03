# Review: spec

Feature folder: $feature_dir. Read idea.md and spec.md there, plus the repo's AGENTS.md and any files the spec names.

Check:
1. Every requirement is clear enough to test. Name the ones that are not.
2. Scope is bounded and matches idea.md. Flag additions nobody asked for.
3. No architecture decision is hidden inside prose; each is stated and justified.
4. The "## Open questions" section exists and is empty. Any open question is blocking.
5. Failure paths a user will hit (bad input, offline, empty data, concurrent use) are specified, not left to the builder.
