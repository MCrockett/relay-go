# writer-usage: spec

## Why

The Models tab counts only the reviews relay launches (`~/.relay/ledger.jsonl`). The writing sessions for spec, plan and build, which the owner starts in Claude Code or Codex, never appear, so the owner cannot see what a feature or a stage cost or which writer model it used. Both CLIs already log per-turn model and token counts on this machine. See idea.md.

## Decisions

- D1. **Read the CLIs' own logs, read-only.** Claude Code: `~/.claude/projects/*/<session>.jsonl` and that session's subagent transcripts in `~/.claude/projects/*/<session>/subagents/*.jsonl`. Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*-<session>.jsonl` (`CODEX_HOME` moves it, as `availability` already honors). relay reads only timestamps, model names and usage numbers, never message text, and stores only those.
- D2. **Only sessions that held a feature are read.** The sessions come from features' state history (D4). Sessions that never held a feature, including relay's own reviewer runs (already in the ledger), are never opened, so nothing is counted twice and unrelated work stays private.
- D3. **Token fields match the ledger.** input = all input including cache reads and cache writes; cached = cache reads; output = output tokens (thinking and reasoning included, as the CLIs report them). Claude: `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`, cached `cache_read_input_tokens`. A Claude transcript repeats one message's usage on several lines (seen: 581 usage lines for 268 message ids), so turns are keyed by `message.id` and the last record seen for an id wins, including one appended after an earlier refresh. Codex: the increase in `total_token_usage` between consecutive `token_count` events, cached `cached_input_tokens`, model from the latest `turn_context` before the event. Totals rather than `last_token_usage` because a repeated event then adds nothing instead of counting twice. Each rollout file starts from zero, so its first event counts its whole total; a total lower than the one before starts a new count from zero.
- D4. **A turn belongs to the feature and stage its session held at that moment.** For each feature, relay walks the published history of its `state.md` (the commits on its origin branch, or on origin/develop and origin/main once merged) and builds hold windows for each session S:
  - A window opens when `owner.session` becomes S: at `owner.since` when the state has it, otherwise at that commit's time. `since` is when the session took the feature; the commit can be published later.
  - It closes at the first of: the next commit where the owner changes; a handoff committed by S on any feature in the same repo (`relay handoff --commit` hands off everything the session holds there, so it ends all of S's windows in that repo); the commit that sets the stage to done; or the merge (D9).
  - Within a window the stage is the one in the latest state commit at or before the turn; before the window's first state commit (a window opened at `owner.since`), it is the stage of that first commit.
  - A turn of a held session outside every window is "unattributed" for that session's provider.
- D9. **Merging ends a hold.** A merge writes no state commit, so the merge time comes from the PR's `mergedAt` (asked of gh once and cached with the done flag `merged.is_done` already keeps). Without it (gh unavailable, no PR number), relay uses the time of the first merge commit on origin/develop or origin/main that contains the feature branch's last commit. When neither is known, the window closes at the feature's last state commit and the feature is flagged "merge time unknown", so later work is unattributed rather than counted to a finished feature.
- D10. **One turn, one feature.** relay take gives a session every feature it holds in a repo, so windows can overlap. A turn inside several windows counts once: for a Claude turn, to the feature whose branch matches the entry's `gitBranch`; otherwise (no match, or a Codex turn, which records no branch) to the overlapping feature with the most recent state commit at or before the turn, and on equal commit times to the feature whose (repo, slug) sorts first.
- D5. **Incremental and cached.** Parsed turns per file are kept in `~/.relay/writer-usage.json` (mode 0600) with each file's size, modification time, inode and read offset, so a refresh reads only new bytes. State history per feature is cached by commit, which never changes. A file that shrinks or changes inode is read again from the start. Updates take a lock in relay home (1 second wait) and write the cache atomically, so the dashboard and `relay cost` cannot interleave; a caller that cannot get the lock or cannot write the cache uses what it read in memory for this result and says "usage cache not saved" in the notes. A file that disappears mid-read keeps its cached turns and is reported as unreadable ("log not found") from the next refresh.
- D6. **Unreadable is visible, partial is visible.** There are two kinds of record. A usage candidate is a Claude entry of type `assistant` that has `message.usage`, or a Codex event of type `token_count` with `info`; it is valid when it has the D3 token fields as numbers and a timestamp. A context record is a Codex `turn_context`; it is valid when it has a non-empty `model`, and it never needs token fields. Claude entries R2 skips on purpose (no usage, no id, model `<synthetic>`) and every other entry (prompts, tool results, metadata, Codex events with `info: null`) are ignored, not failures. A healthy session therefore shows no warning. A session is listed under "unreadable" with a reason and counts when: its log is missing; any of its files has lines but no valid candidates; any candidate fails validation (shown as "partial: N of M usage records unreadable", with the totals that were read still shown); or any context record fails validation; or 200 consecutive new lines in a file that had valid candidates contain neither a usage candidate nor a context record (a format change). The 200-line threshold is far above the longest run of tool output between two assistant turns in the transcripts on this machine, so it does not fire on healthy sessions; the cost is that a format change is noticed only after 200 lines, and fewer unsupported lines than that leave totals short without a warning until then. Never as zero usage without that note.
- D7. **Active minutes, not wall time.** A session's minutes are the gaps between its consecutive turns, each gap capped at 5 minutes, so a session left open overnight does not count the night. A gap belongs to the later turn's feature, stage, model and period. Claude subagent transcripts are part of their parent session: their turns count toward the parent's session and windows, and the session is counted once.
- D8. **Shown on the Models tab after Review runs, same period select.** Two tables, "Writing sessions by feature" (repo, feature, stage, provider) and "Writing sessions by model", with columns Sessions, Turns, Input tokens, Cached share, Output tokens, Minutes, sortable like Review runs. `relay cost` prints a writing section below the review rows with the same `--since`.

## Requirements

### Reading logs
- R1. relay finds a held session's log files by provider and session id as in D1, including Claude subagent transcripts; a session with no file is reported as D6 "log not found".
- R2. Claude turns: one turn per distinct `message.id` with `usage`, at the entry's `timestamp`, with `message.model` and the D3 token fields. Entries without `usage`, without an id, or with a model of `<synthetic>` are skipped without counting as unreadable.
- R3. Codex turns: one turn per `token_count` event with `info.total_token_usage`, its tokens the D3 increase, at the event's `timestamp`, with the model from the latest `turn_context`; events with no increase are skipped.
- R4. Beyond the current line, relay keeps and stores (D5) only: timestamps, model names, usage numbers, Claude message ids (for D3 deduplication), Claude `gitBranch` (for D10), session ids and file paths. Never message text, prompts, tool input or output. A test asserts the cache file contains no message text from a fixture.
- R5. Reading is incremental (D5): a second refresh with no new bytes parses nothing; appended lines are parsed once; a truncated or replaced file is read from the start. A partial last line is left for the next refresh.

### Attribution
- R6. Hold windows come from the `state.md` history as in D4, for every feature `relay status` lists (checked out or only on origin) and for merged features whose history is on origin/develop or origin/main within the selected period.
- R7. Windows open and close as in D4 and D9; a handoff on one feature closes the same session's windows on its other features in that repo; overlapping windows count each turn once (D10).
- R8. Turns in a held session outside its windows are counted as unattributed (D4). Turns of sessions that never held a feature are never read (D2).
- R8a. History comes from the origin refs as of the last fetch: the dashboard fetches as it does today, `relay cost` does not fetch. When a repo's fetch failed, its rows carry the existing fetch-failed flag and use the cached history.

### Showing it
- R9. The snapshot gains `writing` with, for 7 and 30 days, rows by (repo, feature, stage, provider) and by model, each with sessions, turns, input, cached, output, cached_share and minutes (D7), plus `unattributed` per provider and `unreadable` (session, provider, reason) entries.
- R10. The Models tab shows the two D8 tables after Review runs, using the existing period select and sort rules, with a line naming the unattributed totals and any unreadable sessions. Empty data says "No writing sessions in this period".
- R11. `relay cost --since <n>` prints a writing section: repo, feature, stage, model, sessions, turns, input, cached, output, minutes, then unattributed and unreadable lines.
- R12. README describes what is counted, where it comes from, the D2 privacy rule, and the known limits (mixed-session work, hand-written work, unpublished log formats).

### Tests
- R13. Fixture transcripts in both formats (a Claude transcript with repeated message ids, a subagent file and a synthetic entry; a Codex rollout with repeated and reset totals) prove R2, R3 and D3. A temp git repo with state commits for new, submit, take and handoff proves R6 to R8, including: a merged feature whose state still says build / ready-to-merge (window ends at the merge, later turns unattributed, and the unknown-merge-time fallback); two features held by one session with a handoff committed on only one (both windows close); overlapping windows counting a turn once by `gitBranch` and by the latest-commit rule. D6 is tested with a readable parent transcript plus an unreadable subagent file, and with a valid file followed by appended lines of an unknown format. Incremental reads (R5), the no-text cache (R4), unreadable reporting (D6), the snapshot shape (R9), page strings and the sorting reuse (R10), and `relay cost` (R11) are tested. Every test sets `RELAY_HOME`, `CODEX_HOME` and the Claude projects folder to temporary folders.

## Failure paths

- F1. A log line that is not JSON, or a usage candidate without valid fields: skipped and counted; the session is reported as partial or unreadable per D6, with the totals that were read.
- F2. A held session's log is missing (deleted, another machine, owner wrote by hand): "log not found" under unreadable; the feature shows no writing rows for that session.
- F3. The cache file is missing, corrupt or from another version: rebuilt from the logs; never fatal.
- F4. A CLI changes its log format: lines stop parsing, and the session appears under unreadable with the reason instead of quietly reading zero.
- F5. A repo cannot be read by git: its features get no writing rows and the existing row error shows; other repos are unaffected. A failed fetch is not this case: the last fetched history is used (R8a).
- F7. Merge time unknown: the window closes at the last state commit and the feature is flagged (D9).
- F6. A very large transcript: read in a bounded loop from the stored offset; a snapshot build never re-reads unchanged files.

## Non-goals

- Launching writers from relay or counting work done outside Claude Code and Codex sessions.
- Splitting a session's non-relay work out of a held feature.
- Costs in money, or limits and alerts based on writer usage.
- Usage from other machines.

## Open questions
