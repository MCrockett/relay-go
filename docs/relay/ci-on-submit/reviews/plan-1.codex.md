---
{
  "at": "2026-10-10T09:31:12-04:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 57.1,
  "effort": "medium",
  "head": "fb92fb4b9e1ee148d2de624b6e282285214f2ef8",
  "inputs": {
    "plan": "152cd0fce6cf8487182c770c5f066a78550def7cd250f7c90f7218858c259af2",
    "spec": "9659d4b080090f98681fff74767d03053d46a066894f98fc49c704fbcf087e32"
  },
  "model": "gpt-6-astra",
  "refresh": false,
  "reviewer": "codex",
  "round": 1,
  "same_provider": false,
  "skipped_reviewers": [],
  "tokens": {
    "cached": 126208,
    "input": 157935,
    "output": 1521
  },
  "verdict": "NO-GO"
}
---

Every numbered requirement has a task, but Task 4 contradicts the draft-submit flow and can prevent CI from restarting after billing is fixed.

| Spec requirement | Plan coverage |
|---|---|
| R1: Draft PR instructions and agent workflow | Task 8 |
| R2: `isDraft` in PR fields | Task 2 |
| R3: Draft submit branches and marked head | Tasks 3–4; `not-started` branch conflicts with spec |
| R4: Ready failure preserves state and permits retry | Task 4 |
| R5: Failing CI converts PR to draft | Task 4 |
| R6: Published build NO-GO converts to draft, including dashboard | Task 5 |
| R7: Conversion failure warns without changing outcome | Tasks 4–5 |
| R8: Pass over not-started and all-skipped results | Task 1 |
| R9: New state, billing message, marker exclusion, merge/dashboard behavior | Tasks 1, 4, 7 |
| R10: Restricted annotation reads and conservative failures | Task 1 |
| R11: Workflow triggers, draft condition, concurrency | Tasks 9–10 |
| R12: README documentation | Task 9 |
| R13: Fake-gh tests and burned-web regression | Tasks 1–7, 10 |
| R14: Scoped `relay commit` and skill instructions | Tasks 6, 8 |
| R15: Ready retry paths | Task 4; lost-reply scenario should be explicit |

Tasks 1–9 name their files. Task 10 should name `docs/relay/ci-on-submit/plan.md`, where it records Build notes. The order is buildable: CI evidence, PR calls, and marking precede submit integration; publication hooks precede final validation. No unrelated feature scope is added.

Failure-path coverage is substantial: annotation failures, rejected pushes, lost push replies, ready failures, failed draft conversions, discarded dashboard reviews, and ownership refusal. Documentation checks are weaker: Task 8 checks wording style without explicitly verifying its required instructions, and Task 9 relies on manual review and live CI.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/ci-on-submit/plan.md:42 - The plan refuses a draft with not-started CI before calling gh pr ready, contradicting D2/R3. Fixing billing does not change the recorded check result, so the promised next submit still sees not-started and refuses again; no ready_for_review event starts CI. Follow the specified ready transition and add a recovery test that preserves the historical not-started result after billing is fixed.
notes:
  - Task 4 should explicitly test GitHub marking the PR ready while gh reports failure, followed by a successful retry through the normal gate. The generic ready-PR test does not expressly cover the R15 retry sequence.
  - Task 6 should specify how it detects nothing to commit. Existing ciskip.commit returns None for both a successful unmarked commit and no changes; check paths before committing or compare HEAD before and after.
  - Add an explicit marker-exclusion assertion for not-started and an unstaged-outside-folder preservation case for relay commit.
  - Task 10 lacks an explicit touched-file list. Tasks 8–9 should include content checks against R1, R11, R12 and R14.
```
