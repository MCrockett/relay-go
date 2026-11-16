---
{
  "at": "2026-11-12T18:11:58-05:00",
  "base_ref": null,
  "base_sha": null,
  "confirmation": false,
  "duration_s": 236.4,
  "effort": "medium",
  "head": "bb8bf12754593a945602f7cd433cb55a744e0fb5",
  "inputs": {
    "spec": "7957b6f64829723257a9dd608e67318fd9e188c776fb167f851a1b58abb97362"
  },
  "model": "claude-fable-5-2",
  "refresh": false,
  "reviewer": "claude",
  "round": 1,
  "same_provider": true,
  "skipped_reviewers": [
    "codex:gpt-6-astra hit its usage limit during the review"
  ],
  "tokens": {
    "cached": 514597,
    "input": 621414,
    "output": 19526
  },
  "verdict": "NO-GO"
}
---

**Verdict: NO-GO.** The spec's own target (R16: under 60 processes a minute when idle) cannot be reached from requirements R1 to R9 as written. I did not run a build; both findings come from reading the code against the spec's measured profile.

**R1-1: writing totals lose the scan's memo.** `writerusage.summary` runs inside every build and calls `holds._features` and `holds._history`. Those walk every origin branch with `ls-tree`, `show` and `rev-parse` (`relaylib/holds.py:45-67`). Today they cost nothing because they hit the per-build read memo that the scan fills. Once R2 to R4 move the scan off those commands, `holds` starts them itself: about 427 `ls-tree` plus several hundred `show` and `rev-parse` on every idle build. No requirement covers `holds.py`, and the non-goal at `spec.md:38` puts the usage reads out of scope.

**R1-2: per-checkout reads are uncovered, and D1 forbids the cache they need.** With R1-1 fixed, an idle build still starts roughly 175 processes before any live-feature work:

| Source | Processes per build |
|---|---|
| `git for-each-ref`, one per repository (R2) | 34 |
| `current_branch`, one per checkout (`relaylib/status.py:244`) | 47 |
| `origin_url`, one per checkout (`relaylib/status.py:254`) | 47 |
| `common_dir`, one per checkout (`relaylib/ui/snapshot.py:447`) | 47 |

- **Contradiction with D1:** reaching 60 needs cross-build reuse of facts that have no git object id, which D1 (`spec.md:23`) rules out. R7 already breaks D1 by keying on folder state.
- **Undefined trigger:** D4's "a checkout that has changed since the last build" has no stated signal or cost.
- **Unmeasured CPU:** the 5% target allows about 3 CPU-seconds per build, but the steps the spec excludes are only described as "under 10 s" wall time.

The spec needs to state what per-checkout state is reused, keyed by what, and show the idle budget adding up.

The other checks pass: scope matches idea.md, the Open questions section exists and is empty, and owner actions keep their fresh paths. Smaller items are in the notes.

```text
verdict: NO-GO
prior:
blocking:
  - id: R1-1 docs/relay/dashboard-scan/spec.md:38 - writerusage.summary runs in every build and holds._features/_history (relaylib/holds.py:45-67) walk every origin branch with ls-tree, show and rev-parse; today they are free only because the scan fills the per-build read memo. R2 to R4 take the scan off those commands, no requirement moves holds onto the new cache, and the non-goal excludes usage reads, so each idle build would start roughly 427 ls-tree plus hundreds of show and rev-parse and R16 cannot be met
  - id: R1-2 docs/relay/dashboard-scan/spec.md:58 - R16 (under 60 processes a minute) is not reachable from R1 to R9: an idle build still starts about 34 for-each-ref (R2), 47 current_branch (relaylib/status.py:244), 47 origin_url (relaylib/status.py:254) and 47 common_dir (relaylib/ui/snapshot.py:447) before fetches and live-feature work. Cutting them needs cross-build reuse keyed by file state, which D1 (spec.md:23) forbids while R7 already does it; D4's "checkout that has changed" signal is also undefined. The spec must say what per-checkout state is reused, keyed by what, and show the idle process and CPU budget adding up (excluded steps are only given as "under 10 s" wall, against about 3 CPU-seconds allowed)
notes:
  - D4/R17 narrow idea.md's "a change shows within about a minute": a push from another machine to a repository with no live feature can take about 11 minutes without Refresh. State that bound so the owner sees it.
  - R9: say what happens when Refresh arrives while a build is running. The "fetch everything" request must carry to the next build, not be lost when the wake flag is cleared (relaylib/ui/snapshot.py:548).
  - R2: say the refs are resolved after that build's fetches; "at the start of each build" can be read as before them, which would add a full interval to R17.
  - R6 vs F1 and R15: a reused done row cannot show a new fetch error, local-change flags or the "not checked out" flag, and its review choices go stale. Say which fields are overlaid fresh, or that done rows are exempt.
  - R4/R6: "published commit" is not defined for a merged feature whose branch is deleted. Today that is the origin/develop or origin/main head (relaylib/ui/snapshot.py:83), and the scan skips those inherited copies (relaylib/status.py:160). Say so, and that such rows rebuild whenever the base moves.
  - D3 vs D5: "done" means stage done or merged record, but rows for closed or gone PRs also show stage done. Say whether the builder still asks GitHub about closed PRs each build, as merged.resolve does today.
  - D5 "read from GitHub once per build" is unclear: a live PR also needs the gh api check-run and status calls in ci_for_code. Say the limit is one read of each distinct call, not one gh call per PR.
  - F2 says rows show the error "as a failed scan does today", but today a failed remote scan is skipped silently (relaylib/status.py:264). State where the error appears, including for a repository with no rows.
  - D7: os.nice(10) also lowers reviewer runs, merges and any test runs started from the dashboard process (relaylib/reviewjobs.py:235), which have timeouts. State that this is accepted. Also keep the call out of code paths the tests run in-process, since a raised nice value cannot be lowered again.
  - R15: "compares the old and new builders" does not say whether the old builder stays in the tree or the test uses recorded output.
  - R12's muted page line is a small exception to the non-goal "changing what the dashboard shows"; idea.md asked for a log or debug line. Fine, but name it as the exception.
```
