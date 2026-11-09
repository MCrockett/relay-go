# five-hour-usage

## Problem

The dashboard's Usage cards show only the weekly limit and today's budget. On 2026-11-08 Codex showed "Out of usage" at 51% of its weekly limit, and the owner could not tell why from the dashboard: Codex had hit its 5-hour limit (100% used). Both providers already give relay the 5-hour window. Codex logs it on the same `rate_limits` line relay reads for the weekly limit (`window_minutes` 300). Claude's status-line snapshot saves `five_hour` next to `seven_day` (`relaylib/usage.py`). The dashboard shows neither.

## Who it is for

The owner, checking from the dashboard (and the hub on a phone) whether a reviewer can run now, and when it comes back.

## What success looks like

- Each Usage card shows the 5-hour window under the weekly one: percent used and when it resets, with the sample time, marked stale or unknown when relay has no current sample.
- When a provider is out of usage, the card says which limit caused it (5-hour, weekly, or a failed review) and when it ends.
- The hub's usage line shows the 5-hour figure too.
- Reads only this machine's logs and snapshots, like the weekly figure. No new setup for the owner.
