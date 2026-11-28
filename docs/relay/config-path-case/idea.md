# config-path-case

Requested through relay dispatch (qmx0mbm1b) by claude hub session b98b7951-453c-40f9-81ce-6e74b537d43e, 2026-11-28.

## Request

Bug: relay's git-show config readers hardcode lowercase docs/relay/config.toml, but UpstreamPartners tracks Docs/relay/config.toml (git paths are case-sensitive), so the read fails and the setting silently reads as absent. Affected: freshness.py data_paths (line ~70; returns [] so a declared data_paths list does nothing), delegated.py:50 (production_branches opt-in reads as not opted in; fails safe), cimode.py:49 (git overlay of [build] skipped; disk read via state.relay_dir() still works). Disk readers already use state.relay_dir(). Fix: one shared helper that finds the tracked spelling at the given commit (e.g. git ls-tree the base for docs/ or Docs/) and use it in every git-show config reader; grep for any other hardcoded docs/relay path read through git. Tests: a temp repo tracking Docs/relay/config.toml for each reader. Reported by the hordle session while preparing request qmwzwfbxs (UpstreamPartners freshness data_paths), which depends on this fix. Small change (--small). One open PR per repo: hub-start-session (qmx0h7k1c) is at spec with no PR, so this may go first. Owner merges.

## Owner's words

> Send case-path to relay-go to queue or work on
