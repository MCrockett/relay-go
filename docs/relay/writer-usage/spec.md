# writer-usage: spec

## Why

The Models tab counts only the reviews relay launches (`~/.relay/ledger.jsonl`). The writing sessions for spec, plan and build, which the owner starts in Claude Code or Codex, never appear, so the owner cannot see what a feature or a stage cost or which writer model it used. Both CLIs already log per-turn model and token counts on this machine. See idea.md.

## Decisions

- D1. **Read the CLIs' own logs, read-only.** Claude Code: `~/.claude/projects/*/<session>.jsonl` and that session's subagent transcripts in `~/.claude/projects/*/<session>/subagents/*.jsonl`. Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*-<session>.jsonl` (`CODEX_HOME` moves it, as `availability` already honors). relay reads only timestamps, model names and usage numbers, never message text, and stores only those.
- D2. **Only sessions that held a feature are read.** The sessions come from features' state history (D4). Sessions that never held a feature, including relay's own reviewer runs (already in the ledger), are never opened, so nothing is counted twice and unrelated work stays private.
- D3. **Token fields match the ledger.** input = all input including cache reads and cache writes; cached = cache reads; output = output tokens (thinking and reasoning included, as the CLIs report them). Claude: `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`, cached `cache_read_input_tokens`, deduplicated by `message.id` because a transcript repeats one message's usage on several lines. Codex: the increase in `total_token_usage` between consecutive `token_count` events (a decrease starts a new count), cached `cached_input_tokens`, model from the latest `turn_context` before the event.
- D4. **A turn belongs to the feature and stage its session held at that moment.** For each feature, relay walks the published history of its `state.md` (the commits on its origin branch, or on origin/develop and origin/main once merged) and builds hold windows: a window opens at the commit where `owner.session` becomes S and closes at the next commit where the owner changes, a handoff is committed (`handoff.md` appears), or the stage becomes done. Within a window the stage is the one in the latest state commit at or before the turn. A turn of a held session outside every window is "unattributed" for that session's provider.
- D5. **Incremental and cached.** Parsed turns per file are kept in `~/.relay/writer-usage.json` (mode 0600) with each file's size, modification time and read offset, so a refresh reads only new bytes. State history per feature is cached by commit, which never changes. A file that shrinks or changes identity is read again from the start.
- D6. **Unreadable is visible.** A file or line relay cannot parse is skipped and counted. When a held session's log cannot be found or none of its lines parse, the tables show it under "unreadable" with the reason, never as zero usage.
- D7. **Active minutes, not wall time.** A session's minutes are the gaps between its consecutive turns, each gap capped at 5 minutes, so a session left open overnight does not count the night.
- D8. **Shown on the Models tab after Review runs, same period select.** Two tables, "Writing sessions by feature" (repo, feature, stage, provider) and "Writing sessions by model", with columns Sessions, Turns, Input tokens, Cached share, Output tokens, Minutes, sortable like Review runs. `relay cost` prints a writing section below the review rows with the same `--since`.

## Requirements

### Reading logs
- R1. relay finds a held session's log files by provider and session id as in D1, including Claude subagent transcripts; a session with no file is reported as D6 "log not found".
- R2. Claude turns: one turn per distinct `message.id` with `usage`, at the entry's `timestamp`, with `message.model` and the D3 token fields. Entries without `usage`, without an id, or with a model of `<synthetic>` are skipped without counting as unreadable.
- R3. Codex turns: one turn per `token_count` event with `info.total_token_usage`, its tokens the D3 increase, at the event's `timestamp`, with the model from the latest `turn_context`; events with no increase are skipped.
- R4. Only the fields in D1 are read into memory beyond the current line and only those are stored (D5). A test asserts the cache file contains no message text from a fixture.
- R5. Reading is incremental (D5): a second refresh with no new bytes parses nothing; appended lines are parsed once; a truncated or replaced file is read from the start. A partial last line is left for the next refresh.

### Attribution
- R6. Hold windows come from the `state.md` history as in D4, for every feature `relay status` lists (checked out or only on origin) and for merged features whose history is on origin/develop or origin/main within the selected period.
- R7. A `relay take` commit moves the window to the new session at that commit's time; a `relay: handoff` commit closes the old session's window; the stage in effect is the latest state commit at or before the turn.
- R8. Turns in a held session outside its windows are counted as unattributed (D4). Turns of sessions that never held a feature are never read (D2).

### Showing it
- R9. The snapshot gains `writing` with, for 7 and 30 days, rows by (repo, feature, stage, provider) and by model, each with sessions, turns, input, cached, output, cached_share and minutes (D7), plus `unattributed` per provider and `unreadable` (session, provider, reason) entries.
- R10. The Models tab shows the two D8 tables after Review runs, using the existing period select and sort rules, with a line naming the unattributed totals and any unreadable sessions. Empty data says "No writing sessions in this period".
- R11. `relay cost --since <n>` prints a writing section: repo, feature, stage, model, sessions, turns, input, cached, output, minutes, then unattributed and unreadable lines.
- R12. README describes what is counted, where it comes from, the D2 privacy rule, and the known limits (mixed-session work, hand-written work, unpublished log formats).

### Tests
- R13. Fixture transcripts in both formats (a Claude transcript with repeated message ids, a subagent file and a synthetic entry; a Codex rollout with repeated and reset totals) prove R2, R3 and D3. A temp git repo with state commits for new, submit, take and handoff proves R6 to R8. Incremental reads (R5), the no-text cache (R4), unreadable reporting (D6), the snapshot shape (R9), page strings and the sorting reuse (R10), and `relay cost` (R11) are tested. Every test sets `RELAY_HOME`, `CODEX_HOME` and the Claude projects folder to temporary folders.

## Failure paths

- F1. A log line that is not JSON or lacks the expected fields: skipped, counted per file; reported only if no line of that session parses (D6).
- F2. A held session's log is missing (deleted, another machine, owner wrote by hand): "log not found" under unreadable; the feature shows no writing rows for that session.
- F3. The cache file is missing, corrupt or from another version: rebuilt from the logs; never fatal.
- F4. A CLI changes its log format: lines stop parsing, and the session appears under unreadable with the reason instead of quietly reading zero.
- F5. A repo cannot be read by git: its features get no writing rows and the existing row error shows; other repos are unaffected.
- F6. A very large transcript: read in a bounded loop from the stored offset; a snapshot build never re-reads unchanged files.

## Non-goals

- Launching writers from relay or counting work done outside Claude Code and Codex sessions.
- Splitting a session's non-relay work out of a held feature.
- Costs in money, or limits and alerts based on writer usage.
- Usage from other machines.

## Open questions
