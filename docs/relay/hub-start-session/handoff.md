# Handoff: hub-start-session

- From: claude session 533a6442-fe90-46df-9213-a7a0e9b4b92f
- At: 2026-11-28T10:15:06-05:00
- Branch: feat/hub-start-session
- Base: 153c3de816936d993609bfdbd6a6ee071661c4e4
- Head: 6e6405d8ea73e55ccedca8e4ed9b0381aa6a407a
- Stage/status: plan / drafting

## Done

- Request qmx0h7k1c taken. Idea filled and submitted.
- Spec written and reviewed by codex over 5 rounds (round 5 was the owner's extra round). Spec GO on round 5. The two round-5 notes (R11 digest ref without hub note's deliverable check; R9 fallback error text) are folded into spec.md and pushed.
- Owner decisions in this session, recorded in spec.md: clear refuses while the old session is live, and the owner closes it first (one session per repo, D6). Extra round granted by the owner.
- The dependency D4a is met: request qmx0hasm1 merged as PR #47. Use `relaylib/basebranch.choose(root)` for the worktree base, the same function `relay new` and `relay take` use.
- `acceptance.md` holds A1 and A2 (pending, after merge).

## Next

1. In this worktree (`.claude/worktrees/hub-start-session`, never the main checkout), merge or rebase `origin/develop` first: the branch started at 153c3de, before #47 and #48.
2. Write `docs/relay/hub-start-session/plan.md` with the relay-plan skill, covering R1 to R21, then `relay submit`.
3. Build with the relay-build skill. Before relying on D1, check once by hand that `claude --bg --remote-control <name> --session-id <uuid> "<prompt>"` starts a session that shows in the owner's Remote Control list under that id. Ask the owner before launching that test session, because it appears in their list.
4. After hub-start-session merges: request qmx0ieguy (dispatch by real context use) waits on it.

## Open decisions

- None open. If the manual check in Next step 3 shows `--bg` cannot take `--remote-control` or `--session-id`, that changes D1 and D2: stop and tell the owner before changing the spec.

## Tests run (exact commands and results)

- No code is written for this feature yet. On develop after #46: `python3.11 -m unittest discover -s tests -t .`, 1429 tests OK.

## Uncommitted work and where it is

~~~
clean
~~~
