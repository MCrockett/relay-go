# Review: release promotion to $base_ref

PR #$pr promotes to $base_ref. Diff: `git diff $base_sha...$head_sha`. Commits: `git log --oneline $base_sha..$head_sha`. Feature folder, if any: $feature_dir.

Check:
1. Version and build numbers are bumped consistently everywhere the repo keeps them, or set at build time by a documented rule. Build numbers only go up and are never reused.
2. Signing, entitlements, permissions, store or platform metadata: every change is intended.
3. Declarations a store requires for submission are present and match what the app does: privacy manifests and required-reason APIs, permission usage strings, export compliance, data-collection answers.
4. Data migrations: what happens to existing users' data on upgrade.
5. The changelog matches the commit range; nothing shipped is missing from it.
6. The repo's release record (target, version, source SHA, state, rollback) can be filled from this PR.
7. If the owner rules below name a release runbook or skill for this project, the release follows it.
