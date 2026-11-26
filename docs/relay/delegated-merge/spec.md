# Spec: delegated-merge

## Why

Every relay PR waits for the owner's merge, even reviewed, tested work that goes nowhere near
production. The hordle charter delegates this merge as D1 (hordle design, section 6; approved as
section 14, item 1), and hordle's `release` role waits on it. This feature lets that one role
merge such a PR without the owner, in repos that opt in, with relay checking every condition
inside `relay merge`.

## Decisions

1. **Only hordle's `release` role merges without the owner** (owner, 2026-11-26). relay knows it
   by `HORDLE_ROLE=release` in the environment, with no `--relayed`. Any other `HORDLE_ROLE`, and
   every other agent session, still needs the owner's word, as today.
2. **The identity is a record, not the gate.** An agent session could set `HORDLE_ROLE=release`
   on its own command line, and relay cannot tell. So nothing rests on it: the D1 conditions
   (R4 to R10) are what relay checks, whoever runs the command, and they are the same conditions
   whether the caller is honest or not. The identity keeps the merges in one place, names them in
   the record, and lets hordle's hook allow the command for that role alone.
3. **The production list is the opt-in, with no second switch.** `[merge] production_branches` in
   `docs/relay/config.toml`, read only from the PR's base branch on origin. The design's own words
   ("by listing its production branches"), and one key means one PR to opt in. A repo whose base
   has no such list, or an empty one, is not eligible. The owner's global relay config cannot
   set it: the gate comes from the repo.
4. **Control-plane repos are named in code,** not in config: `hordle`, `relay-go` and
   `UpstreamPartners`, matched on the GitHub repository's name (the part after the owner),
   ignoring case. A repo cannot opt itself out of that rule.
5. **The worktree stays while its session is live** (owner, 2026-11-26), for delegated merges
   only. The owner's own merges tidy as today, so they behave exactly as before.
6. **The feature's own session is told of a merge it did not cause** (owner, 2026-11-26; the
   hub's proposal). This holds for every merge, the owner's included, since the gap is the same:
   today merge events go only to registered hub sessions.
7. **`relay merge --repo <path>`** (owner, 2026-11-26), so `release` can merge in any repo from
   its one working folder. It works for every caller.

## Non-goals

- Turning D1 on in any repo. Each opts in by its own PR, which the owner merges.
- hordle: the `release` role, and the change to its hook, which today refuses `relay merge` in
  every role run (hordle foundation spec R28). They follow in hordle, by PRs the owner merges.
- Merges into production branches, and anything else the charter reserves.
- Changing the owner's merge or the `--relayed` merge, beyond R13 and R15.

## Requirements

### Who is merging

- **R1. Three callers.** `relay merge` decides who is merging before anything else:
  - the owner, in their own terminal (no agent markers, no `HORDLE_ROLE`): as today;
  - an agent with `--relayed`: as today, the owner's word passed on;
  - **delegated**: `HORDLE_ROLE` is `release` and `--relayed` is not passed.
  Any other `HORDLE_ROLE` without `--relayed` is refused: `only the release role merges without
  the owner (D1); a role run cannot pass on the owner's word`. `HORDLE_ROLE` set with
  `--relayed` is refused the same way: a role run has no owner in its conversation.
- **R2. A delegated merge is a full merge.** It runs every check the owner's merge runs today
  (`merge_readiness`: no review running, GitHub known, the head unchanged, the PR open, a fresh
  build GO, the code the GO covered, tests by the base's CI mode), then R3 to R10. The first that
  fails refuses with `not delegated (D1): <reason>`, exit 1, and nothing is merged.

### The conditions (delegated only)

- **R3. The opt-in.** `production_branches` under `[merge]` in `docs/relay/config.toml` at
  `origin/<base>`. Missing file, missing table or key: `<repo> has not opted in: no [merge]
  production_branches on <base>`. Not a list of non-empty strings, or empty: `[merge]
  production_branches on <base> must list at least one branch`. A TOML error names the file, as
  `cimode` does today.
- **R4. Not control plane.** The repository name (decision 4) is not `hordle`, `relay-go` or
  `UpstreamPartners`: `<name> is control plane; the owner merges it`.
- **R5. Not into production.** The PR's base is not in the list: `<base> is a production branch`.
- **R6. A reviewed GO.** The feature's build GO came from a review, not from `relay override go`:
  the newest build review file under `docs/relay/<slug>/reviews/` has verdict `GO` for the head
  the GO covers, and its `reviewer` is in the owner's preference table for this author
  (`config.review_preferences`). A same-provider or fallback GO from the table counts (owner rule
  of 2026-10-06). Otherwise: `the build GO was not a review from the preference table`.
- **R7. Tests that ran.** The resolved CI mode for the base is `pr` or `local`, with its pass as
  `merge_readiness` requires it. `none`: `ci = none never qualifies for D1`.
- **R8. No protected path.** No file changed between the merge base of `origin/<base>` and the PR
  head (`git diff --name-only origin/<base>...<head>`, renames counted on both sides) is named
  `AGENTS.md`, `CLAUDE.md` or `WORKFLOW.md` in any folder, is `docs/relay/config.toml`, or lies
  under `.github/`. The message names the first such path: `touches <path>, which the owner
  merges`.
- **R9. One check, one place.** R3 to R8 live in one function that returns the first failure or
  none, so a test can drive each alone, and `relay merge` calls it inside the action lock, after
  `merge_readiness` and before `gh pr merge`.
- **R10. Same head.** The merge still passes `--match-head-commit` with the head R2 to R8 checked,
  as today, so a push between the checks and the merge makes GitHub refuse it.

### The record

- **R11. On the PR.** After a delegated merge, relay comments `Merged by relay under D1
  (delegated), run by the hordle release role. Checked: <the base>, not production; <the CI
  mode> tests green; GO from <reviewer>; no protected path.` A failed comment is a warning, as
  the relayed comment's is today.
- **R12. In relay's log.** The merged or released event (`events.queue`) carries `delegated:
  true` and its text says `delegated under D1`. The hub log line holds the same field. The
  feature's `owner_actions` cannot record it (the merge deletes the branch, as today).

### Tidy and notices

- **R13. The live session keeps its worktree.** In a delegated merge, tidy keeps a worktree whose
  path is the feature's `owner.worktree` while the session in `owner` is live (the same test
  `events._inbox` uses: its record is valid and its shown state is neither `ended` nor `stopped`).
  The line reads `kept worktree <path>: <provider> session <id> is working in it; it removes it
  when it next runs`. Every other tidy step runs as today. The owner's and relayed merges are
  unchanged.
- **R14. Telling the feature's session.** After any merge, relay sends the merged or released
  event's text to the feature's `owner` session through `notes.send`, unless that session caused
  the merge (the event's cause) or is a registered hub session (it already gets the event). For a
  delegated merge whose worktree was kept, the note adds `Remove the worktree: git worktree
  remove <path>`. A send that fails is a warning, never a failed merge.
- **R15. `--repo <path>`.** `relay merge --repo <path> <target>` works as if run from `<path>`:
  the repo root is found from it. A path that is not inside a git checkout is refused: `<path> is
  not a git checkout`. Tidy's "the folder you ran this from" line uses the real working folder.

### Docs

- **R16.** `docs/reference.md` gets a "Delegated merges (D1)" section: who may, the opt-in key,
  each condition and its refusal, the record, R13 and R14. `README.md` names it in one line. The
  `relay merge` help line says the release role may merge an opted-in repo's PR without the owner.
- **R17.** `docs/relay/delegated-merge/acceptance.md` holds what waits on the owner or on later
  features: hordle's hook change and `release` role; the first repo's opt-in PR; and one real
  delegated merge seen end to end in that repo.

## Failure paths

- **A repo with no list, an empty list or a bad file:** refused before anything merges (R3).
- **A control-plane repo, a production base, a protected path, `ci = none`, an override GO:** each
  refused alone, naming it (R4 to R8).
- **A push after the checks:** GitHub refuses the merge by the head match (R10).
- **The comment, the hub log line or a note fails:** the merge stands; each is a warning (R11, R12,
  R14).
- **The feature's session ended:** tidy removes the worktree as today, and the note is queued for
  that session's next hook event, as `notes.send` does (R13, R14).
- **A spoofed `HORDLE_ROLE=release`:** the merge happens only when every D1 condition holds, the
  same as for the real role; the record says "hordle release role", which is then untrue. That is
  the accepted limit (decision 2).
- **`--repo` names a folder that is gone or not a checkout:** refused, nothing merged (R15).

## Tests

`unittest`, in the files for the modules changed, with temp git repos, a bare origin and the fake
`gh` from `tests/helpers.py`. At least: each caller of R1, including the two refusals; a delegated
merge that meets every condition, merged and recorded (R2, R11, R12); each of R3 to R8 failing
alone, with nothing merged; the opt-in read from the base, not the PR branch, with a PR that adds
the list itself refused by R8; the owner's and relayed merges unchanged; R13 with a live and an
ended owner session; R14's note sent, not sent to the cause, not sent to a hub session; `--repo`
from another folder and with a bad path (R15). No test calls real Claude, Codex or GitHub.

## Open questions
