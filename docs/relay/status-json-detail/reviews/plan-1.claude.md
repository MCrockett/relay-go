---
{
  "at": "2026-11-27T19:07:45-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 115.4,
  "effort": "medium",
  "head": "1e7d0e05b6acf43814beba570f7cfc23931e3086",
  "inputs": {
    "plan": "636bbb38c0e4d9bbea7659e2c04a530916a5e29c612e6d99ed0851901ea733ad",
    "spec": "d1b55338d7e46567a2b5e545563533230608bbf140ddc9fe5c6a40651f33fb09"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: 5-hour limit used up until Fri 19:44"
  ],
  "tokens": {
    "cached": 338034,
    "input": 413642,
    "output": 10650
  },
  "verdict": "NO-GO"
}
---

Plan review, round 1: NO-GO on one blocking finding. I read the spec and plan and checked them against `relaylib/status.py`, `scancache.py`, `gitops.py`, `freshness.py`, `state.py` and the existing tests. I ran nothing.

## Coverage

| Spec item | Plan task | Verdict |
|---|---|---|
| R1 `excerpt.full` | Task 2 | covered |
| R2 `owner_action_items` on every row | Task 1 (`_row`, `_remote_row`, `_error_row`) | covered |
| R3 merged item, list length equals count | Task 1 | covered, tested for a local row only |
| R4 existing fields unchanged | Tasks 1 and 2 | covered |
| R5 text output and hub unchanged | Tasks 2 and 3 | covered |
| R6 store and memo in `cmd_status` | Task 3 | covered |
| R7 same rows with and without the store | Task 3 | covered |
| R8 no `git show`, one `cat-file` per blob | Task 3 | **test cannot observe the cat-file half (R1-1)** |
| R9 `docs/reference.md` field list | Task 4 | covered |
| R10 peer message to the hub after build GO | Task 4, acceptance ledger | covered, and consistent with the 2026-10-01 owner rule |
| F1 unreadable transcript | Task 2 | covered |
| F2 malformed entries | Task 1 | covered |
| F3 failed git read | Task 3 | partly covered, see notes |

Every task names its files and tests, the order is buildable (the tasks are independent), and I found no scope beyond the spec.

## Blocking

**R1-1: the R8 call-count test cannot see `git cat-file`.** Task 3 says to patch `gitops.run`, record argv, and check that each distinct state blob is asked for at most once. But `gitops.cat_files` (`relaylib/gitops.py:296`) calls `subprocess.run` directly and never goes through `gitops.run`. The recorder will hold zero cat-file calls, so the "at most once" assertion passes whatever the code does. The spec's R8 requires a test that counts those calls, and this one counts nothing.

The fix is small: keep the `gitops.run` recorder for the `git show` half, and wrap `gitops.cat_files` with a mock to count the blob ids it is asked for across calls. Also assert the state blob was requested at least once, so the test cannot pass empty.

## Notes

- **Fetch in the middle of a run.** `_build_flags` (`relaylib/status.py:75`) fetches during the scan for ready-to-merge rows. Under a store, the origin refs were read before that fetch and are not re-read. The dashboard avoids this by fetching first and calling `scancache.restamp` (`relaylib/ui/snapshot.py:660`). So in a run whose own fetch moves origin refs:
  - the origin-only pass lists features as of before the fetch;
  - an origin-only ready-to-merge row hashes spec and plan from the pre-fetch tree, while its code diff uses the post-fetch ref.

  The next run corrects both, and R7 only promises equality "for the same git state", so I am not blocking. A `scancache.restamp(gitops.common_dir(repo_dir))` after the fetch would close it, or the spec could record it as accepted. The R7 test tree has no row that fetches, so no test would show it either way.
- **R6 test checks the store only.** It should also assert `gitops.memo_active()`, since R6 requires both.
- **F3 test covers one of three outcomes.** It checks the flag and `scan_error` on a `cat_files` failure. It does not check the `?` row for a repo left with no rows, or a failed `ls-tree` listing. Existing scancache and snapshot tests may cover those paths, but not through `cmd_status`.
- **R3 on an origin-only row is untested.** `_remote_row` gets the merged item too, but the test uses a local feature only. One assertion on the R7 tree's merged feature would cover it.
- **Task 4's field list is incomplete.** It leaves out fields that `--json` rows carry today: `path`, `hidden`, `updated`, `checkout`, and `scan_error` when present. Either list them or say the list covers only the main fields.
- **Task 1 skips more than F2 says.** The helper skips an entry whose `action` is not a string; F2 names only "no action". This is consistent with R2's `<str>` and fine, but the F2 test could include that case.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/status-json-detail/plan.md:53 - The R8 test records calls by patching gitops.run, but gitops.cat_files (relaylib/gitops.py:296) calls subprocess.run directly, so no cat-file call is ever recorded and the "at most one cat-file per distinct state blob" assertion passes vacuously. R8's required call count is unproven. Count through a wrapped gitops.cat_files (blob ids across calls, with at least one state blob requested) and keep the gitops.run recorder for the git show half.
notes:
  - cmd_status fetches mid-scan in _build_flags (relaylib/status.py:75); under a store the origin refs are not re-read after that fetch (the dashboard restamps at relaylib/ui/snapshot.py:660). In a run whose fetch moves refs, the origin-only pass and an origin-only row's spec and plan hashes use pre-fetch state while the code diff uses post-fetch. It corrects on the next run. Consider scancache.restamp after the fetch, or record it as accepted.
  - The R6 test should also assert gitops.memo_active(), not only scancache.active().
  - The F3 test covers the flag and scan_error on a cat_files failure only, not the "?" row for a repo left with no rows or a failed ls-tree listing.
  - R3 is tested on a local row only; add an origin-only merged row assertion.
  - Task 4's field list omits path, hidden, updated, checkout and scan_error, which --json rows carry today.
  - Task 1 skips an entry whose action is not a string; F2 names only a missing action. Consistent with R2, worth one test case.
```
