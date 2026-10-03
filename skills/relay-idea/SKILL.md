---
name: relay-idea
description: Use when starting a new feature or product in a repo whose AGENTS.md points at the relay README, or when the owner hands over brainstorm notes to start one.
---

# relay: idea

1. Get the idea from the owner or their notes in 5 to 15 lines: the problem, who it is for, what success looks like. Ask only for what is missing.
2. Small change (a bug fix or a one-file change)? Run `relay new <slug> --small --type fix --idea "<one line>"` and continue with the relay-build skill.
3. Otherwise run `relay new <slug>`, or `relay new <slug> --idea-file <notes.md>` to start from existing notes. Fill in `docs/relay/<slug>/idea.md`, then run `relay submit`. That moves the feature to spec; continue with the relay-spec skill.
4. If the owner answered questions or gave notes for this stage, add `--with-owner` to `relay new` or `relay submit`, so their involvement is counted.

Always:
- Run `relay rules` and follow the rules it prints.
- From `relay new` on, you are the only writer in this repo until you hand off.
- Around 60% of your context window: run `relay handoff`, fill in every section, run `relay handoff --commit`, and stop. The handoff covers every feature you hold in this repo, so stop all work here.

One open PR per repo: `relay new` refuses while another feature has an open, unmerged PR. Tell the owner the PR is waiting for their merge. Pass `--stack` only if the owner asks you to start the next feature anyway.
