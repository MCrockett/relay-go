---
{
  "at": "2026-11-27T21:36:40-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 107.6,
  "effort": "medium",
  "head": "736bf960734016e53665016736ae2ad76729d435",
  "inputs": {
    "spec": "895b830b7b5f944c2441f29df5e7231b079dbab3a6d1c617ea00ea4cfe163bbb"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: 5-hour limit used up until Sat 00:49"
  ],
  "tokens": {
    "cached": 315743,
    "input": 359861,
    "output": 9235
  },
  "verdict": "GO"
}
---

Spec review, round 1: no blocking findings. I read `idea.md`, `spec.md`, `relaylib/freshness.py` and the protected-path code in `relaylib/delegated.py`; nothing was run.

1. **Testable:** every requirement can be tested except the two unclear spots in the notes (case of the never-data names, and odd entry forms).
2. **Scope:** it matches `idea.md`. The extension allowlist and never-data names are how the spec meets "code paths can never be declared data". Recording `data_paths` in the GO is an addition, but a needed one (see notes).
3. **Decisions:** all are stated. Two lack a written reason (see notes).
4. **Open questions:** the section exists and is empty.
5. **Failure paths:** bad list, missing base, missing merge-base and unreadable config all fail toward stale.

Checks against the code and the target repo:
- The delegated-merge claim in decision 2 holds: `delegated.protected_path` treats `docs/relay/config.toml` as owner-merged.
- The only caller of `merge_digest` is `freshness.snapshot` and `freshness.check`, so one change covers status, owner actions and delegated merges.
- The local test-run record is keyed by commit, so the non-goal about test runs costs nothing.
- UpstreamPartners' `marketing/packs/` holds only `.md` and `.json` files today, so the extension allowlist covers the folder entry the request names.

```text
verdict: GO
prior:
blocking:
notes:
  - Decision 7 says paths match case-sensitively, but decision 3 does not say whether the never-data names do. relaylib/delegated.py:96-98 lowercases before comparing (agents.md, .github/). State that never-data matching is case-insensitive, as delegated does, so `agents.md` or `Claude.md` cannot be listed as data; add one test.
  - Decision 5 and R5/R6 give no reason for recording data_paths in the GO. The reason is real: docs/relay/config.toml sits under docs/relay/, which the digest already leaves out, so a list change would otherwise be invisible. Say so in the spec.
  - The data extensions include files that act like code in some repos: package.json, requirements.txt, docker-compose.yml, SKILL.md, prompt .md files. The owner-merged list is the only guard. Consider saying this plainly in docs/reference.md (R8), or adding a few such basenames to the never-data set.
  - Entry forms that are neither valid nor listed as invalid in decision 6: a leading `./`, a backslash, `//`, surrounding spaces, a lone `/`. They would match nothing and give no reason. Either make them invalid or say they match nothing.
  - Paths with spaces or non-ASCII characters: merge_digest reads `ls-tree` without `-z`, so git quotes those names and an entry would not match. This is safe (stays in the digest) but silent. Say whether the builder should use `-z` or leave it.
  - R4 says "touches any path R3 would leave out", and R3 is defined over the merged tree. A PR that deletes a data file touches a path that is not in the merged tree. Word R4 over the diff's paths, and add tests for a PR delete and for a rename out of a declared path.
  - R7 has no test for F2 (no merge-base means whole tree), nor for R6's suffix on a GO that is stale for another reason (for example code changed while the list is invalid).
  - The spec does not say where the `data paths changed since the GO` check sits among the checks in `check` (before or after `merge result against <base> changed`). Fix the order so the reason text is stable in tests.
  - snapshot and check read the base ref several times (config, merge-base, merge-tree). A fetch in between could mix two base commits. The result can only be staler, but resolving base_ref to one sha first would avoid it.
  - With a stacked PR the base ref is another feature branch, so the list is read from an unmerged branch. The "PR base changed" check makes the GO stale when the lower PR merges, so this is safe; one sentence in decision 1 would record it.
  - The decisions are numbered 1 to 7 but cited as D3 and D6, while "D1" in decision 2 and R8 means delegated merges. Label the decisions D1 to D7 and call delegated merges by name.
  - check now makes a few more git calls per feature (show, merge-base, diff). The dashboard status scan calls check for each build feature, so the plan should keep an eye on that cost.
```
