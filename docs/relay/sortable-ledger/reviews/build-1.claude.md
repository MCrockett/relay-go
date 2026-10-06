---
{
  "at": "2026-10-06T13:09:11-04:00",
  "base_ref": "origin/develop",
  "base_sha": "0047f238abff50f5577606c0e41b54e4f8cb0bdc",
  "confirmation": false,
  "duration_s": 378.5,
  "effort": "medium",
  "head": "ddfd45ffaedeb06eee7c3f721ccf982354da5d8b",
  "inputs": {
    "idea": "ab3ced9cb401b1a7c29ee6461619a2c1ded80ab1f84a58b943e6922a27244851"
  },
  "model": "claude-fable-5-1",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [],
  "tokens": {
    "cached": 349569,
    "input": 390772,
    "output": 5914
  },
  "verdict": "NO-GO"
}
---

Verdict: NO-GO, with one blocker: sorting works as idea.md describes, but the keyboard half of the goal does not hold.

**The blocker.** `renderLedger` rebuilds both tables from scratch (`box.replaceChildren()`), and it runs on every header click and on every 15 second poll (`setInterval(load,15000)` → `renderUsage` → `renderLedger`). The button that has focus is destroyed each time, so:
- A keyboard user who presses Enter on a header loses focus and cannot press Enter again to reverse without tabbing back to it.
- Focus is also dropped unprompted every 15 seconds while tabbing through the headers.
- A screen reader does not announce the new `aria-sort` state, because the focused element is gone.

Restoring focus to the matching header (same table and column) after each rebuild would fix it. I established this by reading the code, not by running it in a browser.

**What holds.**
- **Sort behaviour:** numbers sort largest first on the first click, names A to Z, a second click reverses, and ties break by name.
- **Persistence:** the choice is kept per table and survives the poll refresh and the period switch.
- **Default order:** unchanged, since the server already sorts by name.
- **Scope:** the diff touches only `page.html`, one test and the relay docs, which matches idea.md.
- **CI:** the `unit` check on PR #2 completed with SUCCESS for head ddfd45f.
- **Local tests:** `tests.test_ui_server` passes (19 tests). I lost the summary of my full-suite run, so I cannot confirm that one locally.
- **Deployment:** there is no WORKFLOW.md, so merging to develop deploys nothing.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 relaylib/ui/page.html:69 - renderLedger rebuilds both tables on every header click and on every 15 second poll (load -> renderUsage -> renderLedger), destroying the focused sort button. A keyboard user loses focus after each sort, cannot press Enter again to reverse, and has focus dropped unprompted every 15 seconds; the aria-sort change is not announced to screen readers. This defeats the idea.md requirement that headers work for keyboard and screen readers. Restore focus to the same table and column header after re-render.
notes:
  - The new test only checks that strings such as ledgerSort and aria-sort appear in the page. It does not exercise sort order, the reverse toggle, or focus, so it would not catch R1-1 or a broken comparator.
  - The sort arrow is part of the button text, so screen readers will read the triangle glyph as part of the header name. aria-sort already carries the state; consider moving the arrow to a span with aria-hidden.
  - CI: the unit check on PR 2 is SUCCESS for head ddfd45f. Locally tests.test_ui_server passes (19 tests); the full local suite result was not captured.
  - No WORKFLOW.md in the repo, so no deployment effect from merging to develop.
  - Default order is unchanged: the server already sorts by name and the client default is name ascending.
```
