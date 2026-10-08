# waiting-visibility

## Problem
When relay is waiting on the owner, the dashboard and `relay status` say so poorly or not at all, and never say what for. On 2026-10-07 two features were waiting on the owner:
- bottomsup nba-experiments: the agent ended its turn with two questions. The dashboard card said only "waiting on you 16h"; `relay status` said "0 waiting on you".
- UpstreamPartners kpi-collection: the agent ended with a "Still open" list asking the owner to decide whether to start the build. The Stop hook recorded "waiting", but a later Claude `SessionStart` (the session being reopened, with no prompt) set it back to "working", so the dashboard showed "no activity 23h" and `relay status` again said nothing.
Inbox cards for stuck or failed reviews show the raw status (`waiting-owner`, `review-error`) and leave the reason and options in `reviews/<stage>-stuck.md`.

## Who it is for
The owner, scanning the dashboard inbox or `relay status` to see what needs them and what to do about it.

## What success looks like
1. Fix: a Claude `SessionStart` with source resume, startup or clear no longer marks a waiting or needs-permission session as working; only `compact` (which happens mid-turn) and real activity (a prompt, tool use) do. kpi-collection's case shows "waiting on you".
2. What it is waiting for: a card whose session is waiting on the owner or needs permission shows a short excerpt of what the agent asked: Claude Code's own `away_summary` record when there is one, otherwise the agent's last message (Claude transcript or Codex rollout). The full text shows when the owner opens the feature; for a permission wait, the tool that needs approval. The text is read live from the local logs when the page is built and is never written to `~/.relay` or any cache.
3. `relay status` applies the same session check as the dashboard, so both agree on what is waiting, and prints the excerpt under the row.
4. Every inbox card says in plain words what the owner needs to do (for example "Decide: review rounds ran out on the build", with the stuck reason; "Review failed: <why>"; "Merge PR #6"; "Take the handoff"), and shows how long it has waited. The inbox is sorted oldest wait first.

## Known limits (owner discussion 2026-10-07)
- Usage and session data stay per workstation; nothing is combined across machines or logins.
- Showing conversation text in the dashboard is approved by the owner, as long as it is read live and not stored.
- Neither transcript format is a published contract; when relay cannot read the text it falls back to today's "waiting on you <age>".
