---
name: relay-plan
description: Use when a relay feature is at the plan stage, to write docs/relay/<slug>/plan.md from the approved spec and get it cross-reviewed.
---

# relay: plan

1. Read `docs/relay/<slug>/spec.md`, the repo's AGENTS.md, and the rules `relay rules` prints.
2. Method: if the superpowers writing-plans skill is available, use its method and task format, but save to `docs/relay/<slug>/plan.md` and skip its execution handoff. Without it: write ordered tasks, each small enough for one test cycle.
3. Every task names the files it touches, the test that proves it, and the spec requirement numbers it covers (`Covers: R2, R5`). Every spec requirement must be covered by at least one task.
4. Run `relay submit`, and handle GO, NO-GO, waiting-on-owner and review errors exactly as in the relay-spec skill. On GO the stage moves to build; continue with the relay-build skill.
5. `relay submit` commits the plan for you. To publish an edit under `docs/relay/<slug>/` at any other time, use `relay commit "docs: ..."`, not `git commit`: it commits only that folder and skips CI when the code has not changed.
6. Codex only: if your sandbox blocks the Claude review's network access, request approval to run `relay submit` outside the sandbox.

Around 60% of your context window: `relay handoff`, fill it in, `relay handoff --commit`, stop. The handoff covers every feature you hold in this repo, so stop all work here.

Never pass `--same-provider` on your own: relay picks the reviewer from the preference table, including a fallback when a provider is out of usage. Only if the owner asks for a same-provider review in this conversation, pass `--same-provider --relayed`.
