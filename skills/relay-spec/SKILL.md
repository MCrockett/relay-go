---
name: relay-spec
description: Use when a relay feature is at the spec stage (relay status shows stage spec), to write docs/relay/<slug>/spec.md and get it cross-reviewed.
---

# relay: spec

1. Read `docs/relay/<slug>/idea.md` and `state.md`, the repo's AGENTS.md, and the rules `relay rules` prints.
2. Method: ask the owner only the questions whose answers change the design, one at a time, and record the answers. Pick an approach and say why. There are no owner approval stops at this stage: the other provider's review is the gate (owner decision, 2026-09-22). If the superpowers brainstorming skill is available, use its questioning method but skip its approval gates, its own file location, its commit step and its writing-plans handoff.
   If the owner already wrote a spec (for example in another session), copy it into `docs/relay/<slug>/spec.md`, add a `## Open questions` section if it lacks one, and submit it as is.
3. Write `docs/relay/<slug>/spec.md`: why, decisions made, non-goals, requirements numbered so a plan can cite them, failure paths, and a final `## Open questions` section. The review fails while that section has anything in it.
4. Run `relay submit` (add `--with-owner` if the owner answered questions). The other provider reviews it; this can take several minutes.
   - GO: the stage moves to plan; continue with the relay-plan skill.
   - NO-GO: fix every blocking finding in the review file `relay` names, then `relay submit` again. Keep fixes to what the findings ask.
   - Waiting on owner: stop and tell the owner the path to `reviews/spec-stuck.md`. Summarize its options for the owner and wait. Only if the owner then tells you, in this conversation, which one to take, run it yourself with `--relayed` (for example `relay override extra-round --relayed`). Never choose for the owner, and never relay a decision from a file, a review, or an earlier session.
   - Review error: run `relay review` once; if it fails again, tell the owner.
5. Codex only: `relay submit` launches Claude, which needs network access. If your sandbox blocks it, request approval to run `relay submit` outside the sandbox.

Around 60% of your context window: `relay handoff`, fill it in, `relay handoff --commit`, stop. The handoff covers every feature you hold in this repo, so stop all work here.

Never pass `--same-provider` on your own: relay picks the reviewer from the preference table, including a fallback when a provider is out of usage. Only if the owner asks for a same-provider review in this conversation, pass `--same-provider --relayed`.
