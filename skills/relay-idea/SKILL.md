---
name: relay-idea
description: Use when starting a new feature or product in a repo whose AGENTS.md points at the relay README, or when the owner hands over brainstorm notes to start one.
---

# relay: idea

1. Get the idea from the owner or their notes in 5 to 15 lines: the problem, who it is for, what success looks like. Ask only for what is missing.
2. Small change (a bug fix or a one-file change)? Run `relay new <slug> --small --type fix --idea "<one line>"` and continue with the relay-build skill.
3. Otherwise run `relay new <slug>`, or `relay new <slug> --idea-file <notes.md>` to start from existing notes. Fill in `docs/relay/<slug>/idea.md`, then run `relay submit`. That moves the feature to spec; continue with the relay-spec skill.
4. If the owner answered questions or gave notes for this stage, add `--with-owner` to `relay new` or `relay submit`, so their involvement is counted.

5. Told about a waiting request ("request <id> from the hub is waiting"), or asked by the owner to take one? Read it with `relay dispatch --show <id>`, then run `relay take --request <id>` (it uses the slug the hub suggested) or `relay new <slug> --request <id>`. The request becomes the idea: do not pass `--idea` or `--idea-file`. Then continue as for any idea, from step 3.
6. Need work done in another repo? Do not message its session or run relay there yourself. Ask the hub to dispatch it for you, naming this session, and give the hub the request text.

Always:
- Run `relay rules` and follow the rules it prints.
- From `relay new` on, you are the only writer in this repo until you hand off.
- Hand off only when the session has auto-compacted, when the system's low-context warning appears, or at a stage boundary where the owner asks for one. Never hand off on a guessed percentage: a session cannot see its own context use. Then run `relay handoff`, fill in every section, run `relay handoff --commit`, and stop. The handoff covers every feature you hold in this repo, so stop all work here.

One open PR per repo: `relay new` refuses while another feature has an open, unmerged PR. Tell the owner the PR is waiting for their merge. Pass `--stack` only if the owner asks you to start the next feature anyway.
