---
{
  "at": "2026-11-27T21:39:06-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 86.3,
  "effort": "medium",
  "head": "8f760527d429bd49cdbc557aac36222741d01b60",
  "inputs": {
    "plan": "248ba4aaccdf5cbb4dcd36167060941f2fa1b8d01d510672dc0b8090b5ffbff1",
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
    "cached": 225571,
    "input": 274522,
    "output": 8150
  },
  "verdict": "GO"
}
---

I've read the spec, the plan and the code it touches, and I'm writing up the verdict now.

## Review: data-paths-freshness plan, round 1

### 1. Coverage

| Spec item | Plan task | Verdict |
|---|---|---|
| R1 read `[freshness] data_paths` at the base ref only | Task 1 `data_paths(root, base_commit)`; Task 2 test "a list on the PR branch only" | covered |
| R2 validation (D3, D6) | Task 1 `data_paths`, with a test per invalid form | covered |
| R3 digest leaves out covered data paths | Task 1 `is_data`; Task 2 `merge_digest(..., entries)` | covered |
| R4 a PR that touches data keeps the whole tree | Task 2 `applied`, with edit, delete and rename-out tests | covered |
| R5 snapshot records `data_paths` | Task 2 `snapshot` | covered |
| R6 `data paths changed since the GO`, plus the invalid-list suffix | Task 2 `check` | covered, with one deviation (note 1) |
| R7 the nine tests | Tasks 1 and 2 | covered (notes 3 and 4) |
| R8 docs | Task 3 | covered |
| F1 base ref unreadable | Task 2 claims it | no test named (note 2) |
| F2 no merge-base | Task 2 `applied`, with a test | covered |
| F3 config unreadable by git | Task 1 (`gitops.show` returns None, so no list) | covered in code, no test named |

No requirement is left without a task.

### 2. Files and tests
The plan header names the three files: `relaylib/freshness.py`, `tests/test_freshness.py` and `docs/reference.md`. Tasks 1 and 2 list their tests. Task 3 is docs only, which is fine.

### 3. Order
Task 1 (`data_paths`, `is_data`), then Task 2 (which uses both), then docs. This is buildable. `freshness` importing `cimode` creates no import cycle, because `cimode` imports only `config`, `gitops`, `errors` and `state`.

### 4. Scope
- The extra invalid forms (`./`, `\`, `//`, surrounding spaces) all fail closed, in line with D6.
- The docs warning about data-type files that act like code is one paragraph and does not change behaviour.
- Keeping the hashed lines in today's form stops every existing build GO going stale on upgrade, and a test covers it.

None of this is scope creep.

### 5. Failure paths
The tests cover the main failure paths:
- each invalid list form;
- a list on the PR branch only;
- a PR that edits, deletes or renames a data file;
- no merge-base;
- a non-data file under a declared folder;
- the never-data names;
- a GO recorded before this feature.

### Checked against the code
- `cimode._read_toml` raises `RelayError` on a TOML error (`relaylib/cimode.py:30`). `data_paths` must catch it and return the reason; the plan's `([], "<why>")` contract implies that.
- `gitops.show` works with a commit sha and returns None for a missing path (`relaylib/gitops.py:294`), which matches R1 and F3.
- The existing snapshot already carries `base_sha`, so a `data_paths` key beside `merge` is additive.

### Notes (none blocking)
1. **R6 suffix.** The spec says the suffix is added when the GO is stale "for any reason". The plan adds it only to reasons after the inputs check, so `spec.md changed since the GO` gets no suffix. This is harmless, because the owner re-reviews anyway and the suffix shows up on the next stale. Either record the deviation in the spec or read the list before the inputs check for build.
2. **F1 must not raise.** Today `check` returns stale when the base ref is missing, because `merge_digest` uses `check=False`. `gitops.head_sha` raises `RelayError` on an unknown ref (`relaylib/gitops.py:159`). If "resolve the base once" in `check` uses it, the status scan and owner actions would throw instead of reporting stale. Resolve with `check=False`, return `merge result against <base> changed`, and add a test for a missing base ref with a list declared.
3. **Space test does not prove the `-z` choice.** `git ls-tree` without `-z` does not quote spaces, so the "path with a space" test would pass without the `-z` listing. Use a non-ASCII or tab name instead. The build should also assert that the two listings have the same number of records before pairing them.
4. **Never-data in the digest.** R7 says `AGENTS.md` and a `.github/` file listed as data "stay in the digest". The plan tests this only through `is_data`. One integration test (base changes a listed `AGENTS.md`, GO goes stale) would match the spec's wording.
5. **List change after a GO.** There is no test for a GO taken with a list after which the base changes or removes the list. It is the same code path as "no list, then a list", so this is cheap to add.
6. **Docs section.** `docs/reference.md` has no freshness section today. Task 3 should name where the text goes, either Settings or next to Delegated merges.
7. **`.` path parts.** An entry with a `.` part in the middle (`a/./b.csv`) is not rejected and matches nothing. That is safe, but it contradicts the plan's claim that every entry either matches as written or makes the list unused.

```text
verdict: GO
prior:
blocking:
notes:
  - R6 suffix is not added to the inputs-check stale reason, though the spec says "for any reason"; harmless, but record it in the spec or read the list earlier
  - F1: resolve the base in check without raising (gitops.head_sha raises on an unknown ref at relaylib/gitops.py:159); keep the stale answer and add a test for a missing base ref with a list declared
  - the "path with a space" test does not exercise the -z listing; use a non-ASCII or tab name, and assert both listings have the same record count before pairing
  - add one integration test that a listed AGENTS.md or .github/ file changed on the base makes the GO stale (R7 says "stay in the digest")
  - add a test for a GO taken with a list after which the base changes or removes the list
  - data_paths must catch the RelayError from cimode._read_toml and return it as the reason
  - docs/reference.md has no freshness section today; Task 3 should name the section (Settings, or next to Delegated merges)
  - entries with a "." path part (a/./b.csv) are not rejected and match nothing; safe, but contradicts the plan's "matches as written or invalid" claim
```
