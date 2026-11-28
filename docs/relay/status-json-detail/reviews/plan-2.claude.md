---
{
  "at": "2026-11-27T19:09:51-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 73.6,
  "effort": "medium",
  "head": "74482b6931cf2de1fe4fe5d6c98029249a9c9ec7",
  "inputs": {
    "plan": "89ab931887efaa64d072018bc1a85fb4ec0ffddc307f5bfd390931c902d85fcb",
    "spec": "d1b55338d7e46567a2b5e545563533230608bbf140ddc9fe5c6a40651f33fb09"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 2,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra out of usage: 5-hour limit used up until Fri 19:44"
  ],
  "tokens": {
    "cached": 323818,
    "input": 394666,
    "output": 6769
  },
  "verdict": "GO"
}
---

Plan review, round 2: GO. I read the spec, the revised plan, the round 1 review and the diff between the two plan versions, and checked the new fetch step against `relaylib/gitops.py`, `scancache.py`, `status.py`, `state.py` and `ui/snapshot.py`. I ran nothing.

## Prior finding

**R1-1 is resolved.** The R8 test now uses two recorders:
- A `gitops.run` recorder checks that no call is `git show` of a `state.md`.
- A call-through wrapper on `gitops.cat_files` collects the blob ids of every call, which is the path `scancache.prefetch_blobs` really uses (`relaylib/scancache.py:385`).

The test asserts that the stacked pair's state blob was requested and that no id was requested twice, so it can no longer pass empty.

## Coverage

| Spec item | Plan task | Verdict |
|---|---|---|
| R1 `excerpt.full` | Task 2 | covered |
| R2 `owner_action_items` on every row | Task 1 (`_row`, `_remote_row`, `_error_row`) | covered |
| R3 merged item, list length equals count | Task 1 | covered, now tested on local and origin-only rows |
| R4 existing fields unchanged | Tasks 1 and 2 | covered |
| R5 text output and hub unchanged | Tasks 2 and 3 | covered |
| R6 store and memo in `cmd_status` | Task 3 | covered, test now checks both |
| R7 same rows with and without the store | Task 3 | covered |
| R8 no `git show`, one `cat-file` per blob | Task 3 | covered |
| R9 `docs/reference.md` field list | Task 4 | covered, list now complete |
| R10 peer message to the hub after build GO | Task 4, acceptance ledger | covered |
| F1 unreadable transcript | Task 2 | covered |
| F2 malformed entries | Task 1 | covered, including a non-string `action` |
| F3 failed git read | Task 3 | covered: `cat_files` failure, `ls-tree` failure, and the `?` row |

Every task names its files and tests, and the order is buildable.

## The new fetch step

The revision adds a change to `gitops.fetch`: after a successful fetch it clears the read memo and calls `scancache.restamp`. I checked it for new problems and found none that block.

- **It works as described.** After `restamp`, the next `repo_facts` call takes a new stamp, sees the ref files changed, and reads the origin refs again. Trees and blobs are keyed by object id, so nothing stale survives.
- **It keeps status equal to today's behavior** when a mid-scan fetch moves refs, which is what R5 and R7 need. I do not count it as scope beyond the spec.
- **It also runs in the dashboard build.** `snapshot._fetch` calls `gitops.fetch` inside the build's memo and store. The effect there is a cleared memo early in the build and a second, harmless `restamp`; the rows do not change.

## Notes

None of these block.

- **`forget_memo()` must clear in place.** Dashboard fetch workers run in copied contexts that share one memo dict. `_MEMO.get().clear()` reaches all of them; `_MEMO.set({})` would change only the worker's own context and do nothing for the build.
- **Avoid an extra git call per fetch.** `common_dir(root)` runs `git rev-parse` when no store is active, and `gitops.fetch` has 11 call sites. Guard the restamp with `scancache.active()` so commands outside a build pay nothing.
- **The fetch test does not prove the memo half.** With a store active, `head_sha("origin/x")` and `remote_branches` answer from store facts, so the test passes on `restamp` alone. Add a case inside `read_memo()` with no store, or a memoized read such as `git rev-parse --verify`.
- **Dashboard call counts may move.** If any dashboard-scan test asserts exact git call counts per build, clearing the memo after each fetch could raise them. I did not check those tests.
- **The R8 wrapper should count only blob ids.** `holds.py:74` and `delegated.py:144` call `cat_files` with `<sha>:<path>` specs. If the scan reaches either, filter the "no id twice" assertion to 40-hex ids, or it could fail for an unrelated reason.

```text
verdict: GO
prior:
  - id: R1-1 status: resolved
blocking:
notes:
  - gitops.forget_memo() must clear the shared dict in place (_MEMO.get().clear()), not set a new one: dashboard fetch workers run in copied contexts that share the dict.
  - gitops.fetch calling common_dir(root) costs one git rev-parse per fetch when no store is active (11 call sites); guard the restamp with scancache.active().
  - The fetch test passes on restamp alone, because head_sha("origin/x") and remote_branches answer from store facts; add a case inside read_memo() with no store, or a memoized read, to prove the memo is cleared.
  - The gitops.fetch change also runs in the dashboard build (snapshot._fetch): rows do not change, but any dashboard-scan test that asserts exact git call counts may need a look.
  - In the R8 test, limit the "no id twice" assertion to 40-hex blob ids: holds.py:74 and delegated.py:144 call cat_files with <sha>:<path> specs.
```
