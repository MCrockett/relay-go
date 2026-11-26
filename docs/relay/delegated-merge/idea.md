# Idea: delegated-merge

## Problem

Every relay PR waits for the owner to merge it, even when it is reviewed, tested and goes
nowhere near production. On 2026-11-22, 3 reviewed PRs had waited up to 13 hours for a merge
(hordle design, section 1). `relay merge` today runs only in the owner's terminal, or in an
agent session with `--relayed` after the owner said to merge in that conversation. The hordle
charter delegates this as D1 (design section 6; approved as section 14, item 1), and the
`release` role waits on it.

## Who it is for

The owner, who wants to approve only big ideas, production and outside words; and hordle's
`release` role, which will run the merges unattended.

## What it is

An opt-in setting, repo by repo: an agent may run `relay merge` with no owner word when all of
D1's conditions hold, and relay checks every one inside the merge command, so the gate does not
rest on the agent's judgment:

- the repo lists its production branches in `docs/relay/config.toml` on the base branch (read
  from the base, so a PR cannot weaken its own gate). A repo with no list is not eligible;
- the repo is not control plane: `hordle`, `relay-go` and `UpstreamPartners` are never eligible;
- a fresh build GO for the PR head from a reviewer in the owner's preference table (a
  same-provider or fallback GO counts, owner rule of 2026-10-06);
- tests green under `pr` or `local` mode (`ci = none` never qualifies);
- the PR's base is not one of the production branches;
- the diff touches no protected path: `AGENTS.md`, `CLAUDE.md`, `WORKFLOW.md`,
  `docs/relay/config.toml` and `.github/`.

The merge is recorded on the PR, in relay's event log and in the hub as delegated under D1.

## Not in scope

- Turning D1 on in any repo. Each repo opts in by its own PR, which the owner merges.
- hordle's `release` role, and the change to hordle's hook, which today refuses `relay merge` for
  every role (foundation spec R28). Those follow in hordle.
- Merges into production branches, and anything else the charter reserves.

## Success

- In a test repo that opted in, an agent session with no owner word merges a PR that meets
  every condition, and each failed condition alone makes the merge refuse, naming it.
- A repo with no production list, a control-plane repo, and a PR that edits a protected path or
  the production list itself are refused.
- The owner's own `relay merge` and `--relayed` merges work as today.

## Sources

the hordle agent team design, sections 6 (D1),
12 (Phase 1) and 14 items 1 and 6; hordle `TODO.md`, "Next: the build order", item 2;
`relaylib/owneractions.py` (`merge_readiness`, `merge`), `relaylib/commands.py` (`cmd_merge`,
`owner_or_relayed`), `relaylib/cimode.py`, `relaylib/events.py`.

## Decided by the owner (2026-11-26, in conversation)

Asked after a walk through the flow; the owner answered "that works":

- Only hordle's `release` role merges without the owner, known by `HORDLE_ROLE=release` on its
  run. Every other session still needs the owner's word, as today.
- A delegated merge keeps a feature's worktree while that feature's session is live in it, and
  leaves the tidy to that session.
- In this feature: when a session other than the feature's own merges its PR, relay tells the
  feature's session (the hub's proposal).
- `relay merge --repo <path>`, so `release` can merge from one working folder.

## Left for the spec

- The config key name, and whether the production list alone is the opt-in.
