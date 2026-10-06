---
{
  "at": "2026-10-06T14:58:44-04:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 106.5,
  "effort": "medium",
  "head": "6b21aaf09b380b2da8127d360183cbab21291a2c",
  "inputs": {
    "spec": "7e778ee0c7aec0501036c7ef24fa90ad713279c852b2c6e70c20dd22e2528bd9"
  },
  "model": "claude-fable-5-1",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [],
  "tokens": {
    "cached": 193794,
    "input": 266755,
    "output": 9654
  },
  "verdict": "NO-GO"
}
---

Review is done: I read idea.md, spec.md and the code the spec leans on (`config.py`, `availability.py`, `identity.py`, `owneractions.py`, `ui/server.py`, `ui/snapshot.py`, `cmd_roles`). The verdict is NO-GO on three points, each a small fix in the spec.

## Blocking

**R1-1: the edit dialog is not defined for a row that has a timed table (R13 to R15, with R4).**
- The spec does not say whether Edit starts from the timed list or the permanent one, or which "old list" the confirmation shows.
- If it starts from the timed list with Until at "no end", one Save writes the temporary list into `config.toml` and ends the timed entry (R4).
- That overwrites the permanent table D3 promises is "untouched underneath".
- The spec needs to state what the dialog is seeded with, what Until defaults to, and what the confirmation compares.

**R1-2: the validation rules contradict each other (R2, R10, R11).**
- R2 says timed entries are "validated like `set_role` does today". Today that is only: known provider, non-empty model, at least one entry (`relaylib/config.py:127-135`).
- R11 says the dashboard is "validated like the CLI" but adds an effort list, no duplicates and a six-entry cap, none of which the CLI has.
- R10 says both go "through the same code", so the builder must guess whether `relay roles set` now refuses input that works today.
- It is also unspecified what happens to an existing entry whose effort is outside the list (the select cannot show it, and the save would be a 400).
- "Duplicate" is not defined: same `provider:model`, or same including effort.

**R1-3: `--relayed` has nowhere to record a permanent change (D5, R7).**
- D5 says `--relayed` "records that the agent is passing on the owner's words".
- R2 stores who set it only in timed entries. A permanent `relay roles set ... --relayed` writes one line in `config.toml` and leaves no trace.
- The record is the whole point of `--relayed` (README lines 62 to 64). Without it an agent can change who reviews its own work for good, unseen.
- The spec needs to say where it is recorded and where the owner sees it.

## Checklist results

1. **Testable:** mostly yes. Not testable as written: R11/R2 (above), R14/R15 for a timed row (above), D5's "records" (above), and R5 when there is nothing to end.
2. **Scope:** matches idea.md except two additions. D5 makes `relay roles set` owner-only for every key, which idea.md does not ask for. The 7-day cap in R1 is also new.
3. **Hidden decisions:** the 7-day cap, the six-entry cap and the effort list sit in requirements without a decision or reason. D2 is stated and justified, but it departs from idea.md's "keeps the end time in the owner config".
4. **Open questions:** the section exists and is empty.
5. **Failure paths:** well covered (F1 to F7). The gaps are listed in the notes below.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/models-tab/spec.md:38 - R13 to R15 do not say what the edit dialog starts from, or what the confirmation compares, when a row has a timed table; seeded from the timed list with "no end", one Save overwrites the permanent table in config.toml and ends the timed entry (R4), losing the table D3 says stays untouched
  - id: R1-2 docs/relay/models-tab/spec.md:33 - R2 ("validated like set_role does today"), R11 (effort list, no duplicates, one to six) and R10 ("same code") contradict each other: set_role validates none of the R11 rules today (relaylib/config.py:127-135), so it is undefined whether the CLI now refuses input that works, what happens to an existing entry with an effort outside the list, and what counts as a duplicate
  - id: R1-3 docs/relay/models-tab/spec.md:13 - D5 says --relayed "records" the relay, but R2 stores who set it only for timed entries; a permanent `relay roles set --relayed` leaves no record, so an agent can change its own reviewers for good with nothing for the owner to see
notes:
  - D5 (owner-only `relay roles set`, all keys) is not in idea.md; reasonable, but the owner should confirm it. No skill or prompt tells agents to run `roles set`, so nothing else breaks.
  - D2 stores timed tables in review-until.json, while idea.md says "in the owner config". The reason given is sound; the owner should confirm the wording change.
  - The 7-day cap (R1), the six-entry cap and the effort list (R11) are decisions hidden in requirements with no reason given. `xhigh` appears nowhere in the repo today; check that both reviewer CLIs accept all four values.
  - R10 should say the `seen` check and the write happen under the same D7 lock, or a CLI write can slip between them.
  - `revision` hashes file bytes, so a timed entry that expires while the page is open does not change it. R13 should say what the panel shows once the end time passes; the snapshot refreshes every 30 seconds.
  - R3/F3 cover a malformed file but not a well-formed file with a bad entry (unknown provider, empty model). `review_preferences` would raise on it in every review; say that such an entry is ignored and reported too.
  - R1: say which config supplies `[limits] timezone` for HH:MM (owner only, or with the repo overlay), and what happens for a local time that DST skips or repeats.
  - R5: say what `relay roles end` prints and returns when there is no timed entry, and that End now on an already expired entry is a harmless no-op.
  - R9: say that a missing config.toml or timed file hashes as empty, so a first save from the dashboard works.
  - Free-text models are only checked for shape. A mistyped model placed first makes every review fail with no fallback, because fallback happens only on usage limits and timeouts (relaylib/commands.py:408-424). This is the same as the CLI today; a line in the dialog would help.
  - R19: tests/helpers.py does not appear to set RELAY_HOME. Once config.load reads the timed file, add a test rule that RELAY_HOME is always a temp folder, so a live timed table on the owner's machine cannot change test results.
```
