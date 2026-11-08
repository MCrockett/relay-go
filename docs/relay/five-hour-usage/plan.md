# five-hour-usage: plan

Implements spec.md (D1 to D6, R1 to R10). Python 3.11 standard library only. Run the suite with
`python3.11 -m unittest discover -s tests -t . -v`; each task runs its own test file first, red then green.

## Task 0: bring the branch up to develop

The branch starts at `b6fea73`; develop now has PR #28 (`c4705f1`). `git merge origin/develop` (a merge commit, no
rebase and no force-push), then run the full suite to confirm a green start.

Files: none of our own. Test: full suite. Covers: none (setup).

## Task 1: read the 5-hour window (D1, D2)

`relaylib/availability.py`:
- `STALE_S = 3600`.
- `_claude_snapshot()` reads `relay_home()/claude-usage.json`; returns the dict or None on OSError, JSON error or a
  non-dict. No writes.
- `_claude_window(name)`: `(used_pct, resets_at, sampled_at)` from `snapshot[name]` and `snapshot["sampled_at"]`, or
  None when the entry is missing, the percent is not a number in 0 to 100 (bool rejected), the reset is not positive,
  or `sampled_at` is not a finite number. Reuses `usage.timestamp` for the reset (imported inside, as `codex_window`
  does, to avoid the import cycle).
- `five_hour(provider, now=None)` returns `{used_pct, resets_at, sampled_at, state}`:
  - source: `codex_window(FIVE_HOUR_MINUTES)` for codex, `_claude_window("five_hour")` for claude; None gives
    `unknown` with all three values None.
  - `resets_at` 0 becomes None (no reset time).
  - `reset` when `resets_at` is known and `now >= resets_at`: `used_pct` 0, the other two kept.
  - otherwise `stale` when `sampled_at` is None or `now - sampled_at > STALE_S`, else `current`.

Nothing writes to `usage.jsonl`; `capture_claude` and `record_codex_sample` are untouched (R2).

Test, `tests/test_availability.py`, new class `FiveHourTest` (same `CODEX_HOME`/`RELAY_HOME` setup):
codex current; codex at exactly 60 minutes (current) and 61 (stale); codex past reset (state reset, used 0); codex
with no `timestamp` (stale); codex with `resets_at` 0 (resets_at None, never reset); no codex log (unknown); claude
current, stale (sampled 61 minutes ago) and reset (reset in the past) from a written `claude-usage.json`; claude
file unreadable (a directory at that path), missing, not JSON, without `five_hour`, used 101, reset 0
(each unknown, no exception); a test that `usage.jsonl` is not created by any `five_hour` call.

Covers: R1, R2.

## Task 2: remember which limit a failure hit (D3 storage)

`relaylib/availability.py`:
- `_entry(value)`: normalizes one `unavailable.json` value to `{"until", "limit", "resets_at"}`; a bare number gives
  limit "unknown" and resets_at None; anything without a numeric `until` gives None (ignored, as today).
- `_cause(provider, now)` returns `(limit, resets_at)`:
  - codex: weekly used up with a future reset gives ("weekly", weekly reset); else a future 5-hour reset gives
    ("5-hour", that reset) (the same checks `_codex_reset` makes today, which now calls `_cause`);
  - claude: `_claude_window("seven_day")` at 100 with a future reset gives ("weekly", reset); else
    `_claude_window("five_hour")` at 100 with a future reset gives ("5-hour", reset);
  - otherwise ("unknown", None).
- `blocked` reads the store through `_entry` from this task on (`entry["until"] > now`), keeping today's
  "out of usage until ..." wording; Task 3 changes only the wording and adds the live check. This keeps the suite green
  after this task, since `blocked` would otherwise compare a dict with a number.
- `record_out(provider, until=None)`: `until` as today (codex: the cause's reset, else now + 3600; claude: now + 3600;
  an explicit `until` wins), then stores `{"until", "limit", "resets_at"}`. Keeps today's unlocked read-modify-write
  (D3 last bullet).

Test, `tests/test_availability.py`: the existing `record_out` tests still pass, updating `remembered_until()` to read
`["until"]`; new tests: codex failure with 5-hour reset stores limit "5-hour" and that reset; codex with weekly used
up stores "weekly"; codex with neither stores "unknown" and resets_at None; claude with `five_hour` at 100 stores
"5-hour", the snapshot reset, and until about now + 3600; claude with no snapshot stores "unknown"; an old bare-number
entry still blocks; a new-format entry blocks through `blocked`; entries with an invalid `until` (a string, null, a
dict without `until`) are ignored and do not raise.

Covers: R3.

## Task 3: name the limit in the reason, and block at a full 5-hour window (D3 reasons)

`relaylib/availability.py`, `blocked(provider, limits)`, in this order:
1. recorded entry with `until > now`:
   - limit known and `resets_at == until`: `"{limit} limit used up until {when(until)}"`;
   - limit known and they differ: `"{limit} limit used up, resets {when(resets_at)}; relay tries again {when(until)}"`;
   - limit unknown: `"a review failed on a usage limit; relay tries again {when(until)}"`.
2. codex live weekly at `weekly_stop_pct`: the existing text, unchanged.
3. live 5-hour, both providers: `five_hour(provider)` with state current or stale, `used_pct >= 100`, `resets_at`
   known and in the future: `"5-hour limit used up until {when(resets_at)}"`.
Callers keep the `(bool, reason)` contract.

Test, `tests/test_availability.py`: each reason string; recorded beats live weekly, live weekly beats live 5-hour;
codex and claude 5-hour at 100 block; 99 does not; a stale 100 still blocks (F5); a 100 with reset 0 does not block;
a 100 whose reset passed does not block; a claude failure whose retry differs from its reset shows both times. The
existing `test_a_usage_limit_failure_is_remembered_until_reset` asserts "out of usage" today: change it to the new
"limit used up" wording.

Covers: R4.

## Task 4: the snapshot carries the 5-hour window (R5)

`relaylib/ui/snapshot.py`:
- Split the provider loop out of `usage()` into `providers(cfg, now)`, returning the same dict plus
  `"five_hour": availability.five_hour(provider, now)`. `usage()` calls it; `weekly`, `blocked`, `reason`, `budget`
  are unchanged.

Test, `tests/test_ui_snapshot.py`, extend `test_usage_and_ledger_groups` (or a new test beside it): with a codex log
line for the 300-minute window and a `claude-usage.json`, `usage()["providers"]` has `five_hour` for both with state
current, and the old keys are unchanged.

Covers: R5.

## Task 5: the Usage card and strip (D4)

`relaylib/ui/page.html`, `renderUsage()`:
- After the weekly block, a `five` block: eyebrow "5-hour window"; heading by state: current "N% of 5-hour limit",
  stale "N% of 5-hour limit" plus a "Stale sample" line, reset "0% · Reset since last sample", unknown "5-hour usage
  not available"; a meter of `used_pct` (none for unknown); a small line "Resets <date> · Sample <date>", with
  "Reset time unknown" / "Sample time unknown" when either is null.
- A provider with no `five_hour` key renders as unknown.
- The strip adds `node('b','5h '+(known?fmt(used)+'%':'?'))` after the weekly figure.
- The reason line under the badge already shows `value.reason` (now the D3 text): no change.

Test, `tests/test_ui_snapshot.py`, page-string test beside the existing `page.html` checks: the page contains
"5-hour window", "of 5-hour limit", "Reset since last sample", "5-hour usage not available", "Stale sample",
"Reset time unknown", "Sample time unknown" and "5h ".

Covers: R6.

## Task 6: the hub usage line (D5)

`relaylib/hub.py`:
- `usage_view(data)`: from `data.get("usage")`: not a dict with a `providers` dict gives `{"codex": None, "claude": None}`;
  otherwise, per provider, one flat object, the D2 fields plus three:
  `{"used_pct", "resets_at", "sampled_at", "state", "blocked", "reason", "weekly_pct"}`, where `weekly_pct` is
  `providers[p]["weekly"]["used_pct"]` or None, and a missing or malformed `five_hour` gives the D2 unknown values
  (state "unknown", the three numbers None). This flat object is the D5 JSON contract.
  Values are checked like `_clean` does (numbers via `_num`, strings via `_str`), so a malformed snapshot never raises.
- `usage_lines(view, now)`: one line per provider in the D5 forms:
  `Codex: out of usage, <reason> · weekly 51% · 5-hour 100%, resets 18:40`,
  `Claude: available · weekly 34% · 5-hour 12%, resets 20:05`;
  None gives `Codex: usage not available`; weekly None gives "weekly ?"; the 5-hour part by state: current as shown,
  stale "5-hour stale 40% as of 15:10" (or "as of unknown time"), reset "5-hour reset since last sample", unknown
  "5-hour unknown"; a null reset drops ", resets ...". Times use `availability._when`-style local "%H:%M" (with the
  day when not today).
- `digest()` computes `view = usage_view(data)` once and passes it on:
  - `render_text(..., usage=view)` appends the lines after the closing line;
  - `render_json(..., usage=view)` adds `"usage": view`;
  - `_page` / `hubpage.render(..., usage=view)` adds one escaped `<p class="usage">` per line after the closing
    paragraph, through the page's existing `_Text` redaction.

Test, `tests/test_hub.py`, `OutputTest` and `tests/test_hubpage.py`. Their fixtures set both `RELAY_HOME` and
`CODEX_HOME` to temporary folders (the shared `HubHome` fixture gains `CODEX_HOME`). JSON `usage` is asserted with
exact equality for a populated provider (all seven keys) and for one without `five_hour` (state "unknown", numbers
None). Text ends with both provider lines for a
snapshot with usage; blocked codex line includes the reason; stale, reset, unknown and null-reset forms; a snapshot
with no `usage` gives "usage not available" for both and JSON `{"codex": null, "claude": null}`; a provider without
`five_hour` gives "5-hour unknown" and the unknown shape in JSON; a malformed `usage` (strings where numbers belong)
does not abort the digest; in each fallback case the digest's items and counts equal those of the same snapshot with
full usage; the page shows the same lines (existing `test_page_matches_the_digest` extended).

Covers: R7.

## Task 7: `relay roles` Availability (D6)

`relaylib/commands.py`, the "Availability now" block of the roles command: build
`{"usage": {"providers": snapshot.providers(cfg, now)}}` (after `samples.record_codex_sample()`, in a try that
ignores its errors, as the snapshot build does) and print `hub.usage_lines(hub.usage_view(...), now)` indented two
spaces, replacing the `provider: available/reason` lines.

Test, `tests/test_commands.py`, extend `test_roles_shows_the_table_and_who_is_out`: the output has a "Codex:" line
with "weekly" and "5-hour", and a blocked provider's line carries its reason.

Covers: R8.

## Task 8: README (R9)

`README.md`: in the paragraph at the top about limits, and the `[review.prefer]` paragraph, say that relay also skips
a provider whose 5-hour window is at 100% (both providers; Claude needs the status line), and that the out-of-usage
reason names the limit and, for Claude, both the reset and relay's retry time. In the usage section near the
status-line setup, describe the 5-hour figure on the dashboard, hub and `relay roles`: read live from this machine
only, never stored, and its states (current, stale after 60 minutes, reset since last sample, unknown).

Test: a grep of README.md for the em-dash character finds no more than before (no new em-dashes); the full suite.

Covers: R9.

## Task 9: whole suite

Run `python3.11 -m unittest discover -s tests -t . -v`; every test, including R10's list across Tasks 1 to 7,
passes. Then `relay submit` for the build.

Covers: R10 (its tests live in Tasks 1 to 7: R1 in 1, R3 in 2, R4 in 3, R5 in 4, R6 in 5, R7 in 6, R8 in 7).

## Coverage

| Requirement | Tasks |
| --- | --- |
| R1 | 1 |
| R2 | 1 |
| R3 | 2 |
| R4 | 3 |
| R5 | 4 |
| R6 | 5 |
| R7 | 6 |
| R8 | 7 |
| R9 | 8 |
| R10 | 1 to 7, 9 |
