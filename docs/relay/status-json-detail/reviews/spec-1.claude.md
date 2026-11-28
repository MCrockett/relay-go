---
{
  "at": "2026-11-27T19:02:42-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 111.6,
  "effort": "medium",
  "head": "ab8eac4a47b487f1999ca234ab0bfd5b3d42c298",
  "inputs": {
    "spec": "b2b1bf2b2b3313d122199c3386d7c787e904d1386333499c5b7b94bf6f9ea664"
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
    "cached": 447486,
    "input": 527933,
    "output": 10655
  },
  "verdict": "NO-GO"
}
---

Still reviewing: I've read the idea, the spec and the code it names, and I'm writing up the verdict now.

**Verdict: NO-GO on round 1, with two blocking findings.** The fields, the count rule and the store decision are otherwise clear and testable, and the open questions section exists and is empty.

**Blocking**

- **R1-1: F3 describes the wrong behavior for a failed read.** The spec says scancache "falls back to single reads" and "the rows are the same, just slower". The code does not do that once a store is active:
  - A failed `cat-file` or `ls-tree` records the repo as failed, and that build treats the blobs as absent (`relaylib/scancache.py:303`, `:327`, `:386-398`).
  - `status.scan_with_claims` then adds a "cannot read origin branches" flag and a `scan_error` key to every row of that repo (`relaylib/status.py:294-313`).
  - A repo with no rows gets a `?` row with `waiting_on_owner` true, which shows as a `*` row and raises the "N waiting on you" count.
  - Origin-only features whose state could not be read can drop out.

  This is dashboard-scan F2, which calls it a deliberate behavior change. Without a store none of it happens today, so R6 changes what the owner sees on this failure in both text and `--json`. That contradicts F3, R5 and R7 as written. The spec needs to state the real behavior and scope R5 and R7 to runs with no read failure, or say how `cmd_status` suppresses it.

- **R1-2: an idea success item is missing.** idea.md lists "The hub is told the final field names, because hordle's pm reads them." The spec has no requirement, non-goal or acceptance-ledger item for it. hordle's pm is the reader this feature exists for, so the step should be in the spec, with who does it and when.

**Notes**

- **D2 and R1 disagree with the dashboard code.** D2 says the dashboard's rows get the new fields too. `relaylib/ui/snapshot.py:580-582` rebuilds `excerpt` with only `source` and `text`, so `full` is dropped on enriched rows unless the builder also changes that file. The idea says "in `--json` output only". The spec should say whether dashboard rows must carry `excerpt.full`; keeping it out is simpler and avoids sending 4,000 characters per waiting row in every snapshot, since the detail view already has `agent_text`.
- **This reverses an earlier requirement without saying so.** waiting-visibility R6 says `relay status --json` "never includes the full text". The idea asks for the reversal, so it is not a finding, but the spec should name what it supersedes. The text is still never stored, so D3 there holds.
- **D1 does not say whether `cmd_status` prefetches.** It says "the same way a dashboard build does", and the dashboard also calls `scancache.prefetch` per repo (`snapshot.py:468`, `:667`). The spec should say whether status does, and which setup the 11s figure measured. I did not rerun the profile.
- **R9 points at a place that does not exist.** `docs/reference.md` only mentions `--json` in passing at line 116 and has no field description. R9 should say a short field list is added there.
- **R2 and F2 leave `at` unspecified when it is missing or not a string.** One line saying it becomes null would close it.
- **D4 says the list length equals the count "always", but F2 allows an exception.** R3 has the right wording.
- **R8 is testable but weak.** "Fewer git subprocesses" passes with a saving of one call; a bound tied to the branch count would be a stronger test.
- **The R7 fixture has no branch that shares a state blob with another.** The blob-id parse cache does most of its work on stacked branches, so that case is worth adding.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/status-json-detail/spec.md:72 - F3 says a failed store read falls back to single reads and leaves rows the same; with a store active the code marks the repo failed, adds a "cannot read origin branches" flag and scan_error to its rows, adds a "?" waiting row for a repo with no rows, and can drop origin-only features (relaylib/status.py:294-313, relaylib/scancache.py:386-398). R6 therefore changes status output on this failure path, contradicting F3, R5 and R7. State the real behavior and scope R5/R7, or say how cmd_status avoids it.
  - id: R1-2 docs/relay/status-json-detail/spec.md:42 - idea.md success item "The hub is told the final field names, because hordle's pm reads them" has no requirement, non-goal or acceptance-ledger entry, so the step the first consumer depends on would be dropped. Add it with who does it and when.
notes:
  - D2/R1: relaylib/ui/snapshot.py:580-582 rebuilds excerpt without full, so dashboard rows will not carry excerpt.full unless that file changes; idea says "--json output only". Say which is wanted (keeping full out of the snapshot is simpler).
  - This reverses waiting-visibility R6 ("relay status --json ... never includes the full text"); say so in the spec. The text is still never stored.
  - D1 does not say whether cmd_status calls scancache.prefetch like the dashboard build (snapshot.py:468,667), or which setup the 11s figure measured.
  - R9: docs/reference.md has no description of --json fields today (only a passing mention at line 116); say a field list is added there.
  - R2/F2: an entry with a missing or non-string "at" is not covered; say it becomes null.
  - D4 says the list length equals the count "always" while F2 allows an exception; align D4 with R3's wording.
  - R8 "fewer git subprocesses" is testable but weak; a bound tied to branch count would be stronger.
  - R7 fixture should include an origin branch that shares a state blob with another branch (stacked copy), since the blob-id cache is exercised there.
```
