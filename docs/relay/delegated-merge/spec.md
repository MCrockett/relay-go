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
   `UpstreamPartners`, matched on the GitHub repository's name (the part after the owner, from the `origin` URL as
   `github_repo` reads it, never the folder name),
   ignoring case. A repo cannot opt itself out of that rule.
5. **The worktree stays while its session is live** (owner, 2026-11-26), for delegated merges
   only. The owner's own merges tidy as today, so they behave exactly as before.
6. **The feature's own session is told of a merge it did not cause** (owner, 2026-11-26; the
   hub's proposal). This holds for every merge, the owner's included, since the gap is the same:
   today merge events go only to registered hub sessions.
7. **`relay merge --repo <path>`** (owner, 2026-11-26), so `release` can merge in any repo from
   its one working folder. It works for every caller.
8. **A delegated merge pushes its own merge commit to the checked base** (owner, 2026-11-26, asked
   after the spec review's round 2: "Push the merge"). It is the only way to bind the merge to
   the base relay checked, since GitHub cannot tell relay from the owner. The cost: a repo whose
   non-production base accepts only pull requests cannot use D1 until the owner relaxes that
   rule.

## Non-goals

- Turning D1 on in any repo. Each opts in by its own PR, which the owner merges.
- hordle: the `release` role, and the change to its hook, which today refuses `relay merge` in
  every role run (hordle foundation spec R28). They follow in hordle, by PRs the owner merges.
- Merges into production branches, and anything else the charter reserves.
- Changing the owner's merge or the `--relayed` merge, beyond R14 and R15.

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
- **R6. A reviewed GO.** The feature's build GO came from a review, not from `relay override go`,
  and from a reviewer the owner listed:
  - **Evidence** is read from what is published, never the caller's checkout: the review files
    under `docs/relay/<slug>/reviews/` at the published revision `merge` already uses
    (`seen["commit"]` on `origin/<feature branch>`), read with `git show`. The one used is the
    `build-<round>.*.md` file with the highest round; when several share it, the one whose
    front matter `at` is latest; when that ties too, or an `at` does not parse, the evidence is
    malformed. Its front matter must parse, have `verdict` `GO`, a
    `reviewer` and a `model`, and a `head` equal to the head in the feature's build snapshot
    (`st["reviewed"]["build"]`).
  - **The table** is the owner's: `config.load(repo_root=None)`, which is the owner's relay config
    and the owner's timed tables, never a repo's `config.toml` (a PR could edit that).
    `config.review_preferences(cfg, author)` gives its entries for the feature's build author.
  - **Matching** is on provider and model: the review's `reviewer` equals an entry's provider,
    and its `model` equals that entry's model, or, for an entry that names no model, the model
    `config.reviewer_model` gives that provider. Effort is not compared.
  - A same-provider or fallback GO counts when it matches an entry (owner rule of 2026-10-06).
  - Missing, unreadable or malformed evidence refuses: `no review evidence for the build GO`. A
    review that does not match: `the build GO was not a review from the preference table`.
- **R7. Tests that ran.** The resolved CI mode for the base is `pr` or `local`, with its pass as
  `merge_readiness` requires it. `none`: `ci = none never qualifies for D1`.
- **R8. No protected path.** No file changed between the merge base of `origin/<base>` and the PR
  head (`git diff --name-only origin/<base>...<head>`, renames counted on both sides) is named
  `AGENTS.md`, `CLAUDE.md` or `WORKFLOW.md` in any folder, is `docs/relay/config.toml`, or lies
  under `.github/`. The message names the first such path: `touches <path>, which the owner
  merges`.
- **R9. One check, one place.** R3 to R8 live in one function that returns the first failure or
  none, so a test can drive each alone, and `relay merge` calls it inside the action lock, after
  `merge_readiness` and before the merge of R10.
- **R10. The merge lands on the checked base, by construction** (owner, 2026-11-26: "Push the
  merge"). `gh pr merge` cannot be bound to a base: a PR retargeted between relay's last read and
  GitHub's merge would land wherever it then points, and GitHub cannot tell relay from the owner
  because both use the owner's login. So a delegated merge does not call `gh pr merge`. Instead,
  in a temporary detached worktree of `origin/<checked base>` at the base commit relay checked:
  1. `git merge --no-ff --no-edit` of the checked PR head, with the message `Merge pull request
     #<n> from <owner>/<branch>` and a trailer `Delegated-merge: D1`. A conflict refuses: `not
     delegated (D1): the PR does not merge cleanly into <base>`.
  2. `git push origin <merge commit>:refs/heads/<checked base>
     --force-with-lease=refs/heads/<checked base>:<checked base commit>`. The lease and the
     fast-forward together refuse the push when the base moved. A refused push, for that or for
     a GitHub rule (a base that accepts only pull requests), refuses with the push's own message:
     `not delegated (D1): GitHub refused the push to <base>: <message>`. Nothing has merged.
  3. **The record does not wait on GitHub.** A push that succeeded means the checked code is on
     the checked base. relay then writes R11's comment and R12's event and hub log line, whatever
     the PR shows next.
  4. **Then a bounded wait.** GitHub marks a PR merged some time after its head reaches its base.
     relay reads the PR every 2 seconds for at most 60, until its state is not `OPEN` or its base
     or head differs from the checked one. The outcome is the first that holds:
     - `MERGED`: relay deletes the remote feature branch (`git push origin --delete <branch>`, a
       warning if refused), then R13 and R14. Exit 0.
     - `OPEN` with another base (retargeted after the checks): `pushed the merge to <checked
       base>; PR #<n> now targets <its base> and stays open: tell the owner`. Exit 1, a desktop
       notification as relay's other alerts send, no branch delete, no tidy, the note of R14 with
       that text. Nothing reached the other branch.
     - `OPEN` with another head (pushed to after the checks): `merged the checked head <sha> into
       <base>; PR #<n> has later commits that are not merged and stays open: tell the owner`.
       Exit 1, the same notification, no branch delete, no tidy, the R14 note.
     - `CLOSED`: `merged into <base>, but PR #<n> was closed without GitHub marking it merged:
       tell the owner`. Exit 1, the same notification, no tidy.
     - `OPEN`, unchanged, when the 60 seconds end: `merged into <base>; GitHub has not marked PR
       #<n> merged yet`. Exit 0 with that warning. The branch is not deleted (deleting it before
       GitHub sees the merge can close the PR unmerged) and tidy is skipped; the next `relay
       status` finds the merge as it does for any merge another session made.
     - A read that fails: as the timeout, with `GitHub could not be read to confirm PR #<n>:
       <error>`, and exit 1 with the notification.
     In every exit-1 case the R12 record says which outcome it was (`outcome` in the event and
     hub log line). A feature whose checked code is on its base while its PR stays open shows in
     `relay status` as it does now, ready to merge; the owner decides.
  5. **Just before the push** relay reads the PR once more and refuses, with nothing merged, when
     its state is not `OPEN`, it is a draft, or its base or head differs from the checked one:
     `not delegated (D1): the PR changed while it was checked`. A draft also refuses at R2's
     checks: `not delegated (D1): the PR is a draft`.
  6. **Identity and hooks.** The merge commit uses the repo's own git identity (the owner's, as
     for the commits relay makes today). git runs with `-c core.hooksPath=/dev/null` in the
     temporary worktree, for the merge and the push: a local hook must not change what was
     checked.
  The temporary worktree is removed whatever happens. The owner's and relayed merges still use
  `gh pr merge`, unchanged.

### The record

- **R11. On the PR.** After a delegated merge, relay comments `Merged by relay under D1
  (delegated), run by the hordle release role. Checked: <the base>, not production; <the CI
  mode> tests green; GO from <reviewer>; no protected path.` A failed comment is a warning, as
  the relayed comment's is today.
- **R12. In relay's log.** The merged or released event (`events.queue`) carries `delegated:
  true` and its text says `delegated under D1`. The hub log line holds the same field. The
  feature's `owner_actions` cannot record it (the merge deletes the branch, as today). When
  another resolver saw the merge first (`mark_merged` returns not claimed, so no event is queued,
  as today), relay still appends one hub log line with `delegated: true` for this PR. The PR
  comment (R11) is the durable record either way.

### Tidy and notices

- **R13. The live session keeps its worktree.** In a delegated merge, tidy keeps a worktree whose
  path is the feature's `owner.worktree` while the session in `owner` is live (the same test
  `events._inbox` uses: its record is valid and its shown state is neither `ended` nor `stopped`).
  The line reads `kept worktree <path>: <provider> session <id> is working in it; it removes it
  when it next runs`. When the live session's folder is the main checkout, tidy also leaves the
  main checkout on its branch and keeps the local branch, saying so. Every other tidy step runs
  as today. The owner's and relayed merges are unchanged. Which folder the session is in comes
  from the feature's `owner.worktree` matched against `git worktree list`, as the plan settles.
- **R14. Telling the feature's session.** After any merge, relay sends the merged or released
  event's text to the feature's `owner` session through `notes.send` with relay's event prefix
  (`events.PREFIX`, so it reads as relay's, not the owner's), unless that session caused
  the merge (the event's cause) or is a registered hub session (it already gets the event). For a
  delegated merge whose worktree was kept, the note adds `Remove the worktree: git worktree
  remove <path>`; when the kept folder is the main checkout, it adds instead `Switch this
  checkout to <base>, pull, and delete the local branch <branch>`. A send that fails is a warning, never a failed merge.
- **R15. `--repo <path>`.** `relay merge --repo <path> <target>` works as if run from `<path>`:
  the repo root is found from it. A path that is not inside a git checkout is refused: `<path> is
  not a git checkout`. Tidy's "the folder you ran this from" line uses the real working folder.

### Docs

- **R16.** `docs/reference.md` gets a "Delegated merges (D1)" section: who may, the opt-in key,
  each condition and its refusal, the record, R13 and R14. `README.md` names it in one line. The
  `relay merge` help line says the release role may merge an opted-in repo's PR without the owner.
- **R17.** `docs/relay/delegated-merge/acceptance.md` holds what waits on the owner or on later
  features: hordle's hook change and `release` role; the first repo's opt-in PR, with the owner
  checking that its non-production bases accept the owner's push (no pull-request-only rule)
  and that its production branches keep their protection (R10); and one real delegated
  merge seen end to end in that repo.

## Failure paths

- **A repo with no list, an empty list or a bad file:** refused before anything merges (R3).
- **A control-plane repo, a production base, a protected path, `ci = none`, an override GO:** each
  refused alone, naming it (R4 to R8).
- **A push to the PR or the base after the checks:** the merge commit holds the checked head, and
  the lease refuses a moved base (R10).
- **A retarget after the checks:** the merge lands only on the checked base; the PR stays open and
  relay reports it, exit 1 (R10).
- **A base that accepts only pull requests:** the push is refused and nothing merges; the repo
  cannot use D1 until the owner changes that rule (R10, R17).
- **GitHub slow to mark the PR merged:** relay waits up to 60 seconds, then exits 0 with a
  warning, keeps the branch and skips tidy (R10).
- **GitHub unreadable after the push:** the merge stands and is recorded, exit 1, no tidy (R10).
- **The PR pushed to, closed or made a draft during the checks:** refused before the push; after
  it, the outcome is named and recorded (R10).
- **Review evidence missing or not from the owner's table:** refused (R6).
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
the list itself refused by R8; R6 with an override GO, a review whose model is not in the
owner's table, a repo `config.toml` that adds the reviewer (still refused), and a missing or
malformed review file; R10 with a base moved after the checks (push refused, nothing
merged), a PR retargeted after the checks (merge only on the checked base, PR open, exit 1), a
conflict, a push refused by a rule, a failed read after the push, a PR that shows `OPEN` for a
few reads and then `MERGED`, one that stays `OPEN` past the 60 seconds, a head that moved, a
closed PR and a draft, each with its exit code and record; R7 for a `ci = "release"` repo (a
non-main base is `local`); a local hook that would fail, which does not run; the owner's and relayed merges unchanged; R13 with a live and an
ended owner session; R14's note sent, not sent to the cause, not sent to a hub session; `--repo`
from another folder and with a bad path (R15). No test calls real Claude, Codex or GitHub.

## Open questions
