# Handoff: models-tab

- From: claude session b524d577-2970-473e-89f2-138d123a2555
- At: 2026-10-06T15:01:13-04:00
- Branch: feat/models-tab
- Base: ded04f5006aba4bd7d128fcbd85a0dbf50a6d0a9
- Head: 6b0dcf5f7d9e164b3ef0f8f704c762ada58a7e5c
- Stage/status: plan / drafting

## Done
- idea.md from the owner's decisions (Models tab, reviewer editing, timed changes in relay itself, writer roles read-only).
- spec.md: GO from claude:claude-fable-5-1 in round 2 (reviews/spec-1.claude.md NO-GO with 3 blockers, all resolved; reviews/spec-2.claude.md GO).

## Next
- Write plan.md with the relay-plan skill. Add a short "Spec clarifications" section that settles the round-2 review notes without editing the approved spec:
  - R19: test that R4 leaves a timed entry in place (not that it ends it).
  - D9/R2a: `relay roles end` and End now are logged too, with who.
  - R14: the Until prefill is the full date and time, not bare HH:MM.
  - R2a: if the log append fails after a successful write, the write stands with a warning; a missing log or a bad line shows no "last changed" and is never fatal; read only the tail for the latest change per author.
  - R10: the request body is `{author, table, entries, until, seen}`.
  - README (R18) mentions the D11 CLI changes: duplicates refused, effort must be one lowercase word.
- Code map (from research): config.py load/_merge/set_role (line edits, no TOML writer, no lock, plain open); commands.py cmd_roles ~724 (no owner check today), review_current ~349 picks reviewers via config.review_preferences; availability.py blocked/record_out and unavailable.json (the precedent for review-until.json); usage.atomic_write and usage.locked for D7; ui/server.py do_POST ~140; owneractions.py Conflict/fingerprint pattern; ui/snapshot.py usage() and per-feature review_choices; page.html Usage view (#tab-usage, #usage-view, renderUsage, renderLedger with ledgerSort), confirm dialog (#confirm, #do-action), review-request dialog (#rr-reviewer) as the form pattern; identity.require_owner_terminal for D5.
- Work only in this worktree (~/StudioProjects/relay-go-dev). ~/StudioProjects/relay-go is the installed copy and stays on develop.

## Open decisions
- The spec reviewer asked the owner to confirm D5 (owner-only `relay roles set`) and D12 (7-day cap). Both were put to the owner in the handing-off session; ask again if no answer is recorded here.
- Until 23:00 ET on 2026-10-06 the owner's reviewer table is temporary: Fable first, Codex backup, for every author. A script (~/.relay/revert-reviewers.sh) restores the normal table at 23:00 and deletes itself. Leave it alone.

## Tests run (exact commands and results)
- No code changes yet on this branch. On develop at ded04f5: `python3.11 -m unittest discover -s tests -t .` ran 380 tests, OK.

## Uncommitted work and where it is

~~~
clean
~~~
