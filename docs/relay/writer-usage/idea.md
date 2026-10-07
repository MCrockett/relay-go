# writer-usage

## Problem
The Models tab's activity tables count only the reviews relay launches (relay's ledger). The writing sessions for spec, plan and build, usually most of the tokens, never appear there, because the owner starts them in Claude Code or Codex. The owner sees their cost only as a share of the weekly meters, with no split by feature, stage or model.

## Who it is for
The owner, in the dashboard (and `relay cost`), deciding which models to use for which stage.

## What success looks like
- The Models tab shows writing-session usage next to Review runs: by feature and stage, and by model, for 7 or 30 days, with the same columns (runs or sessions, input, cached share, output, minutes).
- relay reads the token counts the CLIs already log on this machine: Claude Code transcripts in ~/.claude/projects/<folder>/<session>.jsonl (per assistant message: model and usage), including subagent transcripts, and Codex rollouts in ~/.codex/sessions/YYYY/MM/DD/*.jsonl (token_count events with last_token_usage, model from turn_context). It reads only usage fields, never conversation text, and reads incrementally so large transcripts stay cheap.
- A turn counts toward the feature its session held at that time (owner.session and since in state.md, plus take and handoff) and the stage the feature was in then (stage changes are commits). Turns while the session held no feature show as unattributed.
- A log format relay cannot read shows as "unreadable" with the reason, never as zeros.

## Known limits (owner discussion 2026-10-06)
- A session's non-relay work while it holds a feature counts toward that feature.
- Work the owner writes by hand has no session and is not counted.
- Neither log format is a published contract; a CLI update can change it.
- Launching writers from relay (exact ledger rows) stays out of scope.
