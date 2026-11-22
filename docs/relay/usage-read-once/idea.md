# usage-read-once

Follow-ups from PR 38 and 39 reviews: one session-log read per dashboard build, prune writer-usage.json of turns no total uses, and a non-text writer setting no longer breaks the Reviewers panel

Scope change (2026-11-21, owner's choice after build round 2): pruning writer-usage.json is dropped from this change. `relay cost --since <period>` reads that same history, so any cutoff is a retention policy, and the owner chose to keep all history for now. The cache costs about 0.1 CPU-s per build to load. This change keeps the one read per build and the Reviewers fix.
