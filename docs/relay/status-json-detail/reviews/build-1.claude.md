---
{
  "at": "2026-11-27T19:35:06-05:00",
  "base_ref": "origin/develop",
  "base_sha": "3acfafa187dc63054b1927ad43413d4c71666080",
  "confirmation": false,
  "duration_s": 353.4,
  "effort": "medium",
  "head": "68b3eefc67bb72daac7dfb6e805fa0e83aa65c0d",
  "inputs": {
    "plan": "89ab931887efaa64d072018bc1a85fb4ec0ffddc307f5bfd390931c902d85fcb",
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
    "cached": 1127967,
    "input": 1234798,
    "output": 13002
  },
  "verdict": "GO"
}
---

## Review: status-json-detail build, round 1

**Verdict: GO.** The diff does what the spec and plan say, and I found nothing blocking.

### What I checked

1. **Correctness.**
   - `_action_items` is called with the same `was_merged` that adds one to the count in both `_row` and `_remote_row`, so list and count cannot drift. The unreadable row gets `[]` and 0.
   - `excerpt.full` is built from the same text as `excerpt.text`; `render` and the hub read `text` only, so the text output and hub items do not change.
   - `cmd_status` wraps only the scan in the memo and a fresh store; both are reset on exit, including on an exception.
   - The mid-scan fetch is the one path the dashboard never exercised. After a successful fetch, `forget_memo()` empties the memo in place and `restamp` drops the repo's stamps, so later reads see the new origin refs.
   - Dashboard side effect: its parallel fetches now also clear the shared memo and restamp from the worker thread. That costs a few repeated reads early in a build and is otherwise harmless.

2. **Plan tasks.** All four are in the diff: T1 (helper and three row kinds), T2 (`with_asks`, updated waiting test), T3 (store in `cmd_status`, `forget_memo`, fetch change), T4 (reference list, acceptance item).

3. **Spec requirements.** R1 to R9 hold in the code, and F1 to F3 hold as written. R10 sits in `acceptance.md` with its trigger, which is right under the owner rule on future events.

4. **Tests.** Every case the plan lists is covered, including F2 and the three F3 cases with a clean follow-up run. One difference: the merged cases patch `merged.resolve` instead of using the fake gh, which still tests the row logic.

5. **CI.** The PR rollup is empty because the head `68b3eef` is a `[skip ci]` bookkeeping commit that changes only `state.md`. The `unit` job on `eb4c78c`, the commit with all the code, finished with success (run 46088118766). I did not run the suite locally.

6. **Deployment.** There is no `WORKFLOW.md`, so merging does not deploy by itself. Once the live folder is fast-forwarded, every `relay status` runs inside a store, so a repo whose relay files git cannot read shows the "cannot read origin branches" flag or a `?` row (spec F3).

```text
verdict: GO
prior:
blocking:
notes:
  - relaylib/gitops.py:262 - after a successful fetch inside a store, common_dir() can raise RelayError if repo facts and the rev-parse fallback both fail; the caller would then report a fetch failure that did not happen. Very unlikely; a try/except around the restamp would close it.
  - relaylib/gitops.py:256-259 - a fetch that fails or times out after moving some refs leaves the memo and stamps as they were, so the rest of that run can read pre-fetch refs for that repo. The row already says the fetch failed.
  - relaylib/status.py:75 - status's own mid-scan fetch does not call scancache.fetched(), so a closed-PR answer is not asked again after it within the run. The store lives for one run, so the effect is nil today.
  - relaylib/ui/snapshot.py:582 - a dashboard row whose enrichment raises keeps the scan's excerpt, which now includes full (up to 4,000 characters) in the snapshot. Spec D2 says dashboard rows do not carry it; nothing shows it.
  - tests/test_status.py:519 - JsonDetailTest and JsonDetailHubTest are defined after the `if __name__ == "__main__": unittest.main()` block, so running the file directly skips them. Discovery and CI run them.
  - tests/test_status.py:698 - the hub test passes trivially because hub._feature_item never reads excerpt or owner_action_items; it guards against a future change only.
  - CI: the rollup on head 68b3eef is empty because that commit is [skip ci] bookkeeping (state.md only); the unit job passed on eb4c78c, which has all the code.
  - Deployment: once live, relay status takes dashboard-scan F2 behavior (scan_error flag, or a ? row waiting on the owner) when git cannot read a repo's relay files, as spec F3 records.
```
