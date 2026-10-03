# Review: release promotion to $base_ref

PR #$pr promotes to $base_ref. Diff: `git diff $base_sha...$head_sha`. Commits: `git log --oneline $base_sha..$head_sha`. Feature folder, if any: $feature_dir.

Check:
1. Version and build numbers are bumped consistently everywhere the repo keeps them.
2. Signing, entitlements, permissions, store or platform metadata: every change is intended.
3. Data migrations: what happens to existing users' data on upgrade.
4. The changelog matches the commit range; nothing shipped is missing from it.
5. The repo's release record (target, version, source SHA, state, rollback) can be filled from this PR.
