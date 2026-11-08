# five-hour-usage: spec

## Why

On 2026-11-08 the dashboard showed Codex "Out of usage" at 51% of its weekly limit. The cause was the 5-hour limit (100% used), which no relay view shows. Both providers already hand relay the 5-hour window: Codex logs it on the `rate_limits` line relay reads for the weekly limit (`window_minutes` 300, read by `availability.codex_window`), and Claude's status-line sink saves `five_hour` in `~/.relay/claude-usage.json` (`usage.capture_claude`), which nothing reads. The owner wants to see, on the dashboard and from the hub, the 5-hour figure and which limit stopped a provider. See idea.md.

## Decisions

- D1. **Read live, store nothing new.** The 5-hour figure is read at snapshot time: Codex from `availability.codex_window(300)`, Claude from the `five_hour` entry of `claude-usage.json`. `usage.jsonl` keeps only weekly samples; no 5-hour history and no budget for it. (Owner's open decision from the handoff, settled as live only: the display needs the current window, not history.)
- D2. **One window shape for both providers.** `five_hour = {used_pct, resets_at, sampled_at, state}` where state is:
  - `current`: a sample exists, its window has not reset, and it is at most 60 minutes old;
  - `stale`: as current but older than 60 minutes (shown with its sample time; another machine or a session that has not run since may have changed it);
  - `reset`: `resets_at` has passed, so the window is new and relay has no sample of it; `used_pct` is shown as 0 with the words "reset since last sample";
  - `unknown`: no readable sample (no Codex log line with a 300-minute window, or no, unreadable or invalid `claude-usage.json`); `used_pct`, `resets_at` and `sampled_at` are null.
  60 minutes is a constant, not a setting: the window is 300 minutes, and an hour-old sample is still a fair guide but no longer "now".
- D3. **Out of usage names its limit.** `availability.blocked` keeps its `(bool, reason)` return and its callers. The reason now starts with the limit:
  - A recorded failure (`unavailable.json`) stores the limit with its end time: `{"until": <epoch>, "limit": "5-hour" | "weekly" | "unknown"}`. `record_out` sets `limit` from the same check that picks the Codex reset today (weekly used up gives "weekly", otherwise a known future 5-hour reset gives "5-hour", otherwise "unknown"). For Claude it uses the `claude-usage.json` snapshot the same way to name the limit (weekly at 100% gives "weekly", 5-hour at 100% gives "5-hour", otherwise "unknown"), but keeps today's one-hour wait (non-goal N2). An old entry that is a bare number reads as limit "unknown".
  - Reasons: "5-hour limit used up until Sun 18:40", "weekly limit used up until Mon 09:00", "a review failed on a usage limit; waiting until Sun 18:40" (unknown limit), and the existing live "weekly limit N% used until ..." for Codex's `weekly_stop_pct`.
  - New live check, both providers: a `current` or `stale` 5-hour sample at 100% with a future reset blocks with "5-hour limit used up until ...". relay then stops launching a reviewer that is certain to fail until the window resets, the same way the weekly check does. Order: recorded failure, then live weekly, then live 5-hour.
- D4. **Dashboard card.** Each Usage card shows, under the weekly block, a 5-hour block: "N% of 5-hour limit" with a meter, then "Resets <time> · Sample <time>", with "Stale sample", "Reset since last sample" or "5-hour usage not available" for the other states. The badge text stays "Out of usage"; the reason line under it carries the D3 text, so the limit is named. The top usage strip adds "5h N%" (or "5h ?") after each provider's weekly figure.
- D5. **Hub usage line.** `relay hub` (text, JSON and the phone page) ends, after the closing line, with one usage line per provider built from the snapshot it already loads (`data["usage"]["providers"]`):
  `Codex: out of usage, 5-hour limit used up until Sun 18:40 · weekly 51% · 5-hour 100%, resets 18:40`
  `Claude: available · weekly 34% · 5-hour 12%, resets 20:05`
  State words from D2 replace the 5-hour figure when it is not current ("5-hour stale 40% as of 15:10", "5-hour reset since last sample", "5-hour unknown"). JSON adds `"usage": {"codex": {...}, "claude": {...}}` with the D2 shape plus `blocked`, `reason` and `weekly_pct`. (Owner answer 2026-11-08: add a hub line.)
- D6. **`relay roles` Availability** shows the same per-provider line as the hub, so the terminal and the phone say the same thing.

## Requirements

### Reading
- R1. `availability.five_hour(provider, now)` returns the D2 shape for "codex" (from `codex_window(300)`) and "claude" (from `claude-usage.json`), with the state rules of D2. A `claude-usage.json` that is missing, not JSON, lacks `five_hour`, or has a used percent outside 0 to 100 or a non-positive reset gives `unknown`; it never raises.
- R2. Nothing new is written to `usage.jsonl`; `capture_claude` and `record_codex_sample` are unchanged.

### Out of usage
- R3. `record_out(provider, until=None)` stores `{"until", "limit"}` as D3. Reading `unavailable.json` accepts both the new object and the old bare number (limit "unknown"); an entry with an invalid `until` is ignored as today.
- R4. `blocked` returns the D3 reasons in the D3 order, including the live 5-hour check for both providers. Callers (`reviewjobs`, `commands`, `status`, `snapshot`) need no change beyond showing the new text.

### Showing it
- R5. The snapshot's `usage.providers[p]` gains `five_hour` (R1 shape). The existing `weekly`, `blocked`, `reason` and `budget` keys are unchanged.
- R6. The Usage card and top strip render D4 for every D2 state; a snapshot without `five_hour` (an older dashboard) renders as unknown.
- R7. `relay hub` text, `--json` and `--page` render D5. A snapshot without `usage` or `five_hour` (for example from a dashboard still running older code) gives "usage not available" for that provider instead of failing the digest.
- R8. `relay roles` prints the D6 lines under "Availability now".
- R9. README's usage section describes the 5-hour figure, its states, the live 5-hour block, and that it reads only this machine.

### Tests
- R10. Unit tests cover R1 for both providers and every state (including the 60-minute stale edge and a passed reset), R3 for old and new `unavailable.json` entries, and R4 for each reason and their order. Snapshot tests cover R5. Page string tests cover R6. Hub tests cover R7 text, JSON and page, including a snapshot without `usage`. A `relay roles` test covers R8. Every test sets `RELAY_HOME` and `CODEX_HOME` to temporary folders.

## Failure paths

- F1. No Codex log line with a 300-minute window (old Codex, no recent session): Codex 5-hour is unknown; the weekly figure and blocking work as today.
- F2. Claude status line not set up, or `claude-usage.json` unreadable or invalid: Claude 5-hour is unknown; nothing else changes.
- F3. Old `unavailable.json` entries (bare numbers) keep blocking until their time, with the unknown-limit reason.
- F4. The hub reads a snapshot from a dashboard running older code: usage lines say "usage not available" or "5-hour unknown"; the digest's items and counts are unaffected.
- F5. A stale 5-hour sample at 100% still blocks until its reset (D3): the window cannot have recovered before `resets_at`, whatever happened since the sample.

## Non-goals

- N1. A 5-hour history, chart or budget, or 5-hour figures in `usage.jsonl` (D1).
- N2. Changing how long relay waits after a Claude usage-limit failure (stays one hour); only its reason names the limit.
- N3. A setting for the 5-hour stop point; it blocks only at 100%.
- N4. Usage from other machines or logins.
- N5. Push alerts or polling from the hub; the line appears only when the hub is asked.

## Open questions
