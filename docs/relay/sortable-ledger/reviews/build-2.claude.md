---
{
  "at": "2026-10-06T13:19:50-04:00",
  "base_ref": "origin/develop",
  "base_sha": "0047f238abff50f5577606c0e41b54e4f8cb0bdc",
  "confirmation": false,
  "duration_s": 131.2,
  "effort": "medium",
  "head": "db5d17f1d2bfb140a9c34fffe00236bfb9de4a61",
  "inputs": {
    "idea": "ab3ced9cb401b1a7c29ee6461619a2c1ded80ab1f84a58b943e6922a27244851"
  },
  "model": "claude-fable-5-1",
  "refresh": false,
  "reviewer": "claude",
  "round": 2,
  "same_provider": true,
  "skipped_reviewers": [],
  "tokens": {
    "cached": 252825,
    "input": 290887,
    "output": 3976
  },
  "verdict": "GO"
}
---

Verdict: GO. R1-1 is resolved, and CI is green for the head commit.

- **R1-1 (focus lost on re-render):** fixed. `renderLedger` now records which header button has focus (table and column) before rebuilding and focuses the matching new button afterwards. This covers both the header click and the 15 second poll. I confirmed this by reading the code, not in a browser.
- **Sort behaviour:** the new node test exercises the real `sortLedger` and `nextSort` extracted from the page. It covers A to Z ignoring case, largest first, ties by name, the reverse toggle, and that the data is not mutated.
- **Scope:** the diff touches only `relaylib/ui/page.html`, `tests/test_ui_server.py` and the relay docs, which matches idea.md.
- **CI:** the `unit` check on PR #2 is SUCCESS for head db5d17f (run 37502433049). It was still running when I first looked, so I waited for it.
- **Local tests:** `tests.test_ui_server` passes (20 tests, node present). I did not run the full suite locally.
- **Deployment:** there is no WORKFLOW.md, so merging to develop deploys nothing.

One thing worth a follow-up, not blocking: the focus restore calls `.focus()` without `preventScroll`. If a header has focus and is scrolled out of view, each 15 second poll will likely scroll the page back to it. Passing `{preventScroll:true}` would avoid that. With the current short Usage view this is hard to hit.

```text
verdict: GO
prior:
  - id: R1-1 status: resolved
blocking:
notes:
  - relaylib/ui/page.html:69 - the focus restore calls .focus() without preventScroll. If a sort header has focus and is scrolled out of view, each 15 second poll will likely scroll the page back to it. Pass {preventScroll:true}. Hard to hit with the current short Usage view. Not verified in a browser.
  - Because the focused button is replaced on every poll, some screen readers may re-announce the focused header every 15 seconds. Updating the tables in place, or skipping the rebuild when the ledger data has not changed, would avoid it.
  - Focus restore is only covered by a string check ("sortKind&&" appears in the page). The sort rules have a real behavioural test in node; focus does not.
  - test_sort_rules_run_in_node is skipped where node is missing. It ran in CI and locally.
  - CI: unit check on PR 2 is SUCCESS for head db5d17f (run 37502433049). Locally tests.test_ui_server passes (20 tests); the full local suite was not run.
  - No WORKFLOW.md in the repo, so no deployment effect from merging to develop.
```
