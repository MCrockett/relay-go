# waiting-visibility: spec

## Why

When relay is waiting on the owner, the dashboard and `relay status` say so poorly or not at all, and never say what for. On 2026-10-07 two features were waiting: nba-experiments, whose agent had asked two questions (the card said "waiting on you 16h"), and kpi-collection, whose agent had asked whether to start the build, but a reopened session flipped the record to working and the card said "no activity 23h". `relay status` showed "0 waiting on you" for both. Cards for stuck or failed reviews show raw statuses, and the Options button on a card does exactly what clicking the card does. See idea.md.

## Decisions

- D1. **A session start is not work.** Claude's `SessionStart` carries `source` (`startup`, `resume`, `clear`, `compact`). Only `compact` happens mid-turn, so only it sets working. `startup`, `resume`, `clear` or a missing or unknown source keep a waiting or needs-permission record as it is (its `since` unchanged), and otherwise record `waiting`: a session that just started or was reopened sits at the prompt until the owner types. Working still comes from `UserPromptSubmit` and tool use, as today. Codex is unchanged (it has no start event in relay's map).
- D2. **One answer to "what does the owner need to do", shared by the dashboard and the terminal.** A new module `relaylib/waiting.py` turns a feature's state, flags and session health into a list of asks, each `{kind, text, since}`. `relay status` and the dashboard both use it, so they agree on what is waiting (R6). The session health computation moves from `relaylib/ui/snapshot.py` to `relaylib/health.py` (`session_health`) so `relay status` can use it without importing the UI.
- D3. **The agent's own words, read live, never stored.** For a session waiting on the owner or needing permission, relay reads what the agent last said from the CLI's local log for the matched session record (the record's session id, which is the owner session or the cwd fallback that `health.match` already picks). Candidates, in file order: Claude, a `system` record with subtype `away_summary` (its `content`) and an `assistant` record with text blocks (joined); Codex, an `event_msg` of type `task_complete` with `last_agent_message` and a `response_item` message with role `assistant` (its `output_text` parts joined). The newest candidate in file order wins, whatever its kind, so an earlier turn's answer never stands in for a later one. Subagent transcripts are not read. Only the last 1 MiB of the file is read. The text goes into the dashboard's in-memory snapshot and the API response and `relay status` output only; it is never written to `~/.relay`, git, notifications or any cache. Excerpt: whitespace collapsed to single spaces, first 160 characters plus "…" when cut. Full text: up to 4,000 characters, shown as plain text (never rendered as HTML). The excerpt states its source: "Agent:" for a last message, "Summary:" for an away summary.
- D4. **Asks in plain words.** Kinds and text, in this order when several apply to one feature:
  1. `error`, the repo could not be read: "Fix: <error>".
  2. `decide`, status waiting-owner: "Decide: <stage> review stopped. <reason>" with the reason from `reviews/<stage>-stuck.md` frontmatter on the published branch; without it, "Decide: <stage> review stopped".
  3. `review-failed`, status review-error: "Review failed: <error>" with the error from state (D5); without it, "Review failed".
  4. `merge`, ready-to-merge and not stale: "Merge PR #<n>"; with a fallback GO flag, the flag's text is appended.
  5. `take`, a handoff waiting: "Take the handoff: open a session and run relay take".
  6. `answer`, session waiting on the owner: "Answer the <provider> session" plus the excerpt line (D3).
  7. `approve`, session needs permission: "Approve <tool> in the <provider> session", tool names from the record's pending list, plus the excerpt line.
  8. `check`, no activity: "Check the <provider> session: no activity <age>".
  A feature is waiting on the owner when it has at least one ask; the asks replace today's `waiting_on_owner` rules with the same coverage plus D1. `waiting_on_owner` stays in rows and `--json` for compatibility.
- D5. **A failed review keeps its reason.** `machine.apply_error(st, error)` saves `review_error` in state: the first line of the error, at most 200 characters. It is removed when the status leaves review-error. Older states without it show "Review failed".
- D6. **How long it has waited.** Each ask's `since`: the session record's `since` for answer and approve; the health `since` for check; the state's `updated` time for the state asks (decide, review-failed, merge, take); none for error. A feature's wait is its oldest ask's `since`. Shown as "waiting <age>" with `health.age`; with no `since` (an error ask alone) the age is omitted. The dashboard inbox and the waiting rows of `relay status` sort oldest wait first; features with no `since` come after, then by repo and feature as today.
- D7. **Cards carry the real buttons; Options goes.** The inbox card shows the buttons for its asks: Override GO, Extra round and Reset rounds for decide; Override GO and Request review for review-failed (Request review only on build, as `owneractions.applicable` already decides); Merge PR for merge. Each opens the same confirmation as in the detail panel. Answer, approve, take and check have no button: the action is in the agent's session. The Options button is removed. Release stays in the detail panel only. Clicking the card still opens the detail panel. (Owner request 2026-10-07.)
- D8. **Fail quiet, fall back to today.** A missing, unreadable or unknown-format log, or no text found in the last 1 MiB, gives the ask with no excerpt; nothing else changes and no note is added. Reading never raises into the snapshot or `relay status`.

## Requirements

### Session state
- R1. `sessions.apply` follows D1: on Claude `SessionStart`, `compact` sets working; any other source, missing or unknown, keeps a waiting or permission record unchanged except `at`, `event` and `cwd`, and turns any other or missing record into waiting. The kpi-collection sequence (Stop, then SessionStart with source resume) ends in waiting with the Stop's `since`.

### What the agent asked
- R2. A new module `relaylib/agentask.py` with `last_words(provider, session_id)` returns `{source: "summary"|"agent", text}` or None per D3 and D8, finding files with `transcripts.files_for` (parent transcript only for Claude) and reading at most the last 1 MiB. A partial first line (cut by the 1 MiB window) and a partial last line (a write in progress) are skipped; the complete records between them are used.
- R3. `excerpt(text)` and `full(text)` follow D3's limits.

### Asks
- R4. `waiting.asks(row, st, health, record, stuck_reason)` (health may be activity-only, with no record, per F2) returns the asks of D4 with D6's `since`, in D4's order; `waiting.since(asks)` returns the oldest. Callers pass the published state, so the module does no git or file reads of its own except through `agentask` for answer and approve.
- R5. `machine.apply_error` saves `review_error` per D5, and every path that leaves review-error removes it.

### Terminal
- R6. `relay status` uses `health.session_health` and `waiting.asks` for every row a session holds, marks rows with asks with `*`, counts them in "N waiting on you", and sorts waiting rows per D6. Under each waiting row it prints the first ask's text and "waiting <age>", and for answer or approve the excerpt on a second line, both indented. `relay status --json` adds `asks` (kind, text, since) and `excerpt` per row; it never includes the full text.
- R7. `relay status` keeps today's fetch behavior (only the existing fetch for ready-to-merge freshness in `status._build_flags`, with its "fetch failed" flag offline) and adds no fetches: session health and asks use the hook records, local git and local logs only.

### Dashboard
- R8. Each snapshot row carries `asks`, `wait_since` and, for answer or approve, `excerpt`; the feature detail response carries `agent_text` (full text and its source) for those kinds. The inbox count, the page title count and the inbox list use `asks`.
- R9. An inbox card shows the first ask's text in place of the raw status, the other asks after it separated by " · ", "waiting <age>", and for answer or approve the excerpt on its own line, labeled per D3. The card's buttons follow D7; the Options button and its code are removed.
- R10. The detail panel shows the full text (D3) under the Session line, labeled "Agent's last message" or "Summary while you were away", as plain text, and for approve the pending tool names.
- R11. The inbox is sorted oldest wait first (D6). The features table keeps its order.

### Tests
- R12. Unit tests: D1 sequences for each source (and missing source, and Codex unchanged); `agentask` on fixture Claude transcripts (assistant text, away_summary after and before the last assistant message, tool-use-only last message, more than 1 MiB of earlier lines) and Codex rollouts (task_complete, assistant message only, an older task_complete followed by newer assistant text, neither); a partial last line after complete records; excerpt and full limits; every ask kind and the order when several apply (ready-to-merge plus a waiting session; waiting-owner plus permission); `since` per kind and the sort; `review_error` saved, truncated and cleared; `relay status` text and `--json` for an answer ask with excerpt, and the count agreeing with the dashboard rows for the same fixtures; page strings for card asks, excerpt, card buttons per kind, no Options, and the detail text set with `textContent`. Every test sets `RELAY_HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` to temporary folders and never calls real Claude or Codex.

## Failure paths

- F1. A transcript or rollout is missing, unreadable, or in an unknown format: the ask shows without an excerpt (D8).
- F2. The hook records are missing or unreadable: no answer or approve asks. A check ask still comes from local activity alone, as `health.health` does today, and state asks still show.
- F3. The stuck file is missing or has no reason: "Decide: <stage> review stopped".
- F4. A state written before this change has no `review_error`: "Review failed".
- F5. Session health fails for one feature in `relay status`: that row shows its state asks only; other rows are unaffected.
- F6. A card button's action finds the feature changed since the page loaded: the existing "changed since you looked" conflict applies, as in the detail panel.

## Non-goals

- Answering the agent or approving a tool from the dashboard; the owner does that in the session.
- Notifications outside the page for new asks.
- Storing or caching any conversation text, or reading subagent transcripts for it.
- Combining sessions or logs across machines or logins (per workstation, owner 2026-10-07).
- Changing the reviewer flow, the stuck rules, or what Release does.

## Open questions
