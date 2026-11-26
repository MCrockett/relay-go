# Handoff: delegated-merge

- From: claude session ea35512a-39cb-46ea-a1f0-80b2575d3397
- At: 2026-11-26T15:15:20-05:00
- Branch: feat/delegated-merge
- Base: 5978f882d9af08d314a83842463037e217b5d1ca
- Head: 72ad573f8b7f0d778bfdd135ba2764c6e1f3b8f9
- Stage/status: spec / drafting

## Done

- Started on the owner's word on 2026-11-26 ("start D1 in relay-go"): `relay new delegated-merge
  --with-owner` in the main checkout, which was then switched back to `develop` (the `relay`
  command runs from that checkout, so it must stay on `develop`). Work is in the worktree
  `.claude/worktrees/delegated-merge`.
- `idea.md` written from the hordle design (section 6, D1; section 14, items 1 and 6) and hordle
  `TODO.md` build order item 2, and submitted with the owner (`relay submit --with-owner`). Now at
  spec.

## Next

- From the worktree: `relay take --feature delegated-merge` (the bare slug form is refused), then
  the relay-spec skill: write `docs/relay/delegated-merge/spec.md` and `relay submit`.
- Read first: `idea.md`; `relaylib/owneractions.py` (`merge_readiness`, `merge`),
  `relaylib/commands.py` (`cmd_merge`, `owner_or_relayed`, `_hub_session`), `relaylib/cimode.py`,
  `relaylib/freshness.py`, `relaylib/events.py`, `relaylib/config.py`; the hordle design at
  `~/StudioProjects/hordle/docs/superpowers/specs/2026-11-22-hordle-agent-team-design.md`,
  section 6.
- PRs go into `develop` (`gh pr create --base develop`); the owner merges.

## Open decisions

The three questions at the end of `idea.md`, for the spec to settle or put to the owner, one at
a time:
- How a delegated merge identifies itself (`--delegated` flag, or `HORDLE_ROLE`), and who may
  use it (only a hordle role, or any agent session).
- Config key names; one switch plus the production list, or the list alone.
- The hub's proposal (not the owner's decision yet): tell a feature's own session when another
  session merges its PR. This feature or a separate one.

## Tests run (exact commands and results)

- None: no code yet.

## Uncommitted work and where it is

~~~
clean
~~~
