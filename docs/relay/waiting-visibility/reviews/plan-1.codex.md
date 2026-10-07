---
{
  "at": "2026-10-07T12:33:14-04:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 57.5,
  "effort": "medium",
  "head": "c84d8ae68f22b86348c8553aba6fc569db9cfd62",
  "inputs": {
    "plan": "ca5e661625d9e00b5e8d2430c45227ea8b56629a5f46d6e2037b42d7465e1113",
    "spec": "8a25773f13cec0972a03acd07bf96f546814d031fe784a534f7b7b8b177813f2"
  },
  "model": "gpt-6-astra",
  "refresh": false,
  "reviewer": "codex",
  "round": 1,
  "same_provider": false,
  "skipped_reviewers": [],
  "tokens": {
    "cached": 121728,
    "input": 153775,
    "output": 1273
  },
  "verdict": "NO-GO"
}
---

The plan needs two corrections: preserve the features table’s order, and route repository-error rows through the ask logic.

| Requirement | Tasks | Coverage |
|---|---|---|
| R1: SessionStart behavior | 1 | Covered |
| R2: Read bounded parent transcripts | 3 | Covered |
| R3: Excerpt and full-text limits | 3 | Covered |
| R4: Ask kinds, order and timestamps | 5–7 | Error-row integration missing |
| R5: Save and clear review errors | 2 | Covered |
| R6: Terminal asks, count, sorting and JSON | 6 | Error-row integration missing |
| R7: No additional fetches | 6 | Covered |
| R8: Snapshot asks and detail text | 7–8 | Error-row integration missing |
| R9: Card text and buttons | 8 | Covered |
| R10: Plain-text detail and pending tools | 8 | Covered |
| R11: Inbox sorting; unchanged table order | 7–8 | Plan contradicts table requirement |
| R12: Tests and isolation | 1–9 | Broad coverage; gaps below |

Every task names its files and verification. The dependency order is buildable, and no material additional scope is proposed.

Failure tests cover F1, F3, F4 and F5 directly. F2 has an activity-only unit case and existing record-reading tests, but no combined status/dashboard case with unreadable records. F6 retains the existing confirmation path and server conflict test; the planned browser check does not explicitly exercise a stale card.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/waiting-visibility/plan.md:176 - Task 7 sorts shared snapshot rows by wait age, while the features table renders those rows directly. Task 6 also changes the order inherited from status.scan. This violates R11's unchanged table order. Preserve the table's existing ordering separately, sort only the inbox by wait age, and test both orders with fixtures where they differ.
  - id: R1-2 docs/relay/waiting-visibility/plan.md:162 - Ask integration is assigned to _row and _remote_row, but repository-read failures create rows directly in status.scan, and snapshot._enrich returns immediately for feature "?". These rows bypass every planned ask call, leaving out the required error ask and snapshot fields. Add explicit error-row normalization and status/dashboard tests proving "Fix: <error>", no age, consistent counts, and placement after dated waits.
notes:
  - Add a combined F2 test with missing or unreadable hook records, proving state asks and activity-only check asks survive without answer or approve asks.
  - Extend the card browser check to submit an action after the feature changes and verify the existing conflict message.
```
