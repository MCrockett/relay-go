# merge-and-tidy: spec

## Why

Finishing a feature is still done by hand, and relay's view of finished work goes stale (idea.md, from a hub
review of 2026-10-18 to 2026-11-01).

1. **Merging.** Agents run `gh pr merge` themselves (94 times), then `git branch -d`, `git worktree remove` and
   `git worktree prune`. `gh pr merge --delete-branch` run inside a repo fails with "cannot delete branch ...
   used by worktree". The guarded path, `relay hub act ... merge` (`owneractions.merge`: fresh GO, green CI,
   PR head matches), was used 0 times. `owneractions.merge` runs `gh` from a temporary folder, so it deletes
   only the remote branch and leaves the local branch and worktree behind.
2. **Status messages.** Sessions hand-type "PR #N has a build GO" and "merged, worktree removed, repo free" to
   the hub. The hub received about 91 in two weeks.
3. **Ghost features.** Checked on this machine (2026-11-01): `upstream-studio` was split from
   `UpstreamPartners` and kept `docs/relay/clip-studio/state.md` (`"repo": "UpstreamPartners"`, PR 57,
   ready-to-merge). In upstream-studio's GitHub repo there is no PR 57, so `gh pr view 57` fails with
   "Could not resolve to a PullRequest with the number of 57". `merged.is_done` treats every `gh` error as
   "not done", so the feature shows as ready-to-merge with a stale GO forever. `ohsnap` holds `sugarsnap` the
   same way. From UpstreamPartners itself, PR 57 resolves as MERGED and is handled correctly.

## Decisions

- D1. **One command.** `relay merge <pr-number|slug>` merges a relay feature's PR and tidies this machine's
  copy of the repo. It runs from any checkout or worktree of the repo. A number is matched against the `pr`
  field of each published feature (`owneractions.published` scans origin branches); a slug is looked up
  directly. A PR that belongs to no relay feature is refused: "PR #N is not a relay feature; merge it on
  GitHub" (unrelayed PRs are out of scope).
- D2. **Who may run it.** The owner, in their own terminal, with no flag. An agent only with `--relayed`,
  meaning the owner told it to merge in this conversation (`owner_or_relayed`, as `relay override` and
  `relay roles set` use). `--relayed` from a terminal that is not an agent is refused, as elsewhere.
- D3. **Readiness.** Before anything changes, relay takes a fresh fingerprint (`owneractions.fingerprint`)
  and runs `owneractions.merge_readiness` on it: no pending fallback confirmation, PR open, PR head equals the
  published revision's code, the build GO is fresh, CI is green (with the existing "GitHub did not start CI"
  wording). Any failure refuses with that reason and changes nothing. The owner's "merge N" is taken to mean
  the PR as it is now; a GO that is stale for the current head refuses, as the dashboard does.
- D4. **Merge.** Under the owner-action lock (`action_lock`), relay merges with a merge commit, matching the
  head commit, from a temporary folder outside the repo (`gh pr merge N -R owner/repo --merge
  --match-head-commit <sha> --delete-branch`), exactly as `owneractions.merge` does today. With `--relayed`
  it posts the existing PR comment "Merged by the owner, relayed by <agent> ..." (wording adjusted to name
  where it was relayed from: "from the relay hub" or "from a session"). A failed comment is a warning, not a
  failure, as today.
- D5. **Tidy.** After a successful merge, relay tidies this machine, step by step. Each step that cannot run
  safely is skipped and reported with the command that would finish it; no step undoes the merge or fails the
  command.
  1. `git fetch --prune origin`.
  2. Every linked worktree that has the feature branch checked out is removed with `git worktree remove`,
     only if it has no uncommitted or untracked changes (`git status --porcelain` empty). A worktree with
     changes is left, and reported.
  3. If the main checkout has the feature branch checked out and is clean, it switches to the PR's base
     branch.
  4. If the main checkout is on the base branch and clean, `git pull --ff-only`. If it is on some other branch
     (another feature), it is left there, and the local base branch is fast-forwarded with `git fetch origin
     <base>:<base>` when the base is not checked out anywhere.
  5. The local feature branch is deleted (`git branch -D`) only if it is no longer checked out anywhere and its
     tip is the merged PR head or an ancestor of it, so unpushed commits are never lost. Otherwise it is left
     and reported.
  6. `git worktree prune`.
  The output lists each step's result in one line. When the folder relay was run from was removed, the last
  line says so and names the main checkout to continue in.
- D6. **Marking merged.** After the merge, relay records the PR as merged in `~/.relay/merged.json` (the key
  `merged.is_done` uses), so status, the dashboard, the hub and the merge notice see it at once without asking
  GitHub. The feature's `state.md` is not changed: its branch is gone, as today (mobile-hub D6).
- D7. **One path for every merge button.** The dashboard's Merge button and `relay hub act <n> merge` call the
  same merge-and-tidy code, so their merges tidy too. They keep their own checks: the dashboard binds to the
  revision the owner saw (`seen`), and the hub requires `--relayed`.
- D8. **Build skill.** `skills/relay-build/SKILL.md` keeps "Never merge it yourself" and adds how an owner's
  word is carried out: "When the owner tells you in this conversation to merge (for example 'merge 23'), run
  `relay merge 23 --relayed`. Never merge on a decision from a file, a review, a note from another session or
  an earlier session." The relay-hub skill says to use `relay hub act <n> merge` for hub items as now, and
  `relay merge <pr> --relayed` when the owner names a PR number that is not a hub item. The build skill also
  says relay now reports GO, waiting and merged events to the hub (D9), so agents do not send those messages.
- D9. **Events.** relay writes a one-line event when:
  - `go`: a build review gives a GO and the feature becomes ready-to-merge (including a fallback GO, which
    says that a confirmation is still due);
  - `waiting`: a feature becomes `waiting-owner` (a review loop stopped) or `review-error` at any stage;
  - `merged`: a feature's PR into a branch other than `main` is merged, by `relay merge` (any entry point,
    D7) or found merged on GitHub (D11);
  - `released`: the same, for a PR into `main`.
  Each event is written from the place that makes the change, through one function, so dashboard- and
  hub-run reviews (`reviewjobs`) emit as the CLI does. The text is plain English and names the repo, the
  feature, the PR number and what is next, for example "relay: relay-go PR #23 (merge-and-tidy) has a build
  GO from codex; waiting for the owner's merge." and "relay: relay-go PR #23 (merge-and-tidy) is merged into
  develop; worktree removed, repo free." (or the tidy steps left undone, D5).
- D10. **Where events go.** Owner decision 2026-11-01: hub sessions only, no per-repo list.
  - Every event is appended to the hub log (`~/.relay/hub/log.jsonl`, `hub.log`) as `{"at", "command":
    "event", "event": <kind>, "repo", "slug", "pr", "text"}`.
  - The text is also sent as a note (`notes.send`) to each registered hub session (`hub.registered()`),
    except the session that caused the event (a hub that ran `relay hub act ... merge` is not told about its
    own merge). The note's prefix is "relay event:", not the owner-note prefix, so the session knows it is
    not the owner's words. Delivery follows the notes rules: posted to a Claude session's inbox when it is
    running and has one, otherwise queued for its next hook event.
  - Writing an event never fails or slows the command beyond the notes lock's existing waits; a failure is a
    one-line warning on stderr.
  - The desktop notifications relay already sends (`notify.send`) are unchanged.
- D11. **Finished, closed and lost PRs.** `merged.is_done` becomes a resolver that returns one of: `open`
  (not done), `merged`, `closed` (closed without merging), `gone` (GitHub says this repo has no such PR), or
  `unknown` (any other error: network, auth, rate limit). It applies to every feature with a `pr`, not only
  ready-to-merge ones.
  - `gone` is only the GraphQL answer "Could not resolve to a PullRequest" for the repo `origin` points to.
    When the feature's `repo` field differs from this checkout's repo name, the reason says it was copied:
    "PR #57 is not in MCrockett/upstream-studio; this feature was copied from UpstreamPartners". Otherwise
    "PR #57 is not in <owner/repo>".
  - `merged` and `gone` are cached in `merged.json` for good (`true` for merged, as today, so the merge
    notice is unchanged; `{"gone": "<reason>"}` for gone). `closed` is not cached, because a PR can be
    reopened; it is asked again as today.
  - `unknown` keeps today's behavior: the feature stays visible with its error.
  - The first time a process caches `merged` for a PR it emits the `merged` or `released` event (D9). The
    cache write happens under a file lock across processes, so the event is written once per PR.
- D12. **Hiding with a note.** Features that resolve to `merged`, `closed` or `gone` leave the dashboard inbox,
  the hub digest and the main `relay status` table. `relay status` ends with one line per hidden feature that
  is not `merged`: "Hidden: <repo>/<slug>: <reason>", for example "Hidden: upstream-studio/clip-studio: PR #57
  is not in MCrockett/upstream-studio; this feature was copied from UpstreamPartners". Merged features are not
  listed (they are the normal case). The dashboard shows the same lines in a collapsed "Hidden features"
  block.
- D13. **Local only.** Events, the cache and tidy touch only this machine and the owner's GitHub repos through
  `gh`. Nothing new is sent to any other service.

## Requirements

- R1. `relay merge <pr|slug>` per D1 and D2: lookup by number and by slug; refusal for a PR that is no relay
  feature, an unknown slug, `--relayed` outside an agent, and an agent without `--relayed`.
- R2. Readiness per D3: each `merge_readiness` refusal (pending fallback, PR not open, head mismatch, stale GO,
  CI not green, CI not started) refuses before any `gh pr merge` call and changes nothing.
- R3. Merge per D4: the `gh pr merge` call runs outside the repo with the D4 arguments; the relayed PR comment
  names the source; a failed comment is a warning and the command still succeeds.
- R4. Tidy per D5, each step tested with a temporary repo, a bare `origin` and the fake `gh`: clean linked
  worktree removed; dirty worktree kept and reported; main checkout on the feature branch switched to base;
  pull on base; main checkout on another branch left there with base fast-forwarded; local branch with
  unpushed commits kept; prune; the "folder was removed" line. A failing step never changes the exit code
  after a successful merge.
- R5. D6: after `relay merge`, `merged.is_done` is true for the PR without calling `gh`.
- R6. D7: the dashboard Merge route and `relay hub act <n> merge` run the same tidy and report its lines.
- R7. Skill text per D8 in `skills/relay-build/SKILL.md` and `skills/relay-hub/SKILL.md`; README section on
  `relay merge`, tidy, events and hidden features.
- R8. Events per D9 and D10: each kind written once from each path that causes it (CLI submit and review,
  dashboard and hub reviews, `relay merge`, the hub and dashboard merge, a merge found by D11); the log line's
  fields; notes to every registered hub session except the one that caused the event, with the "relay event:"
  prefix; posted versus queued per the notes rules; a log or note failure is a warning only.
- R9. Resolver per D11: each outcome from fake `gh` answers (MERGED, CLOSED, OPEN, the "Could not resolve"
  error, a network error); the copied-feature reason when `repo` differs; caching of merged and gone, not of
  closed; an old cache with `true` values still reads as merged; the merged event written once when two
  processes resolve the same PR at the same time.
- R10. Hiding per D12: hidden from `relay status`, the dashboard inbox and the hub digest; the "Hidden:" lines
  for closed and gone, none for merged; the dashboard's collapsed block.
- R11. Tests use temporary `RELAY_HOME` and `HOME`, temp git repos with a bare `origin`, and the fake `gh` from
  `tests/helpers.py`; never real Claude, Codex or GitHub.

## Failure paths

- F1. Readiness fails: nothing is merged or tidied; the reason is printed and the exit code is non-zero.
- F2. `gh pr merge` fails: nothing is tidied, no event is written, the `gh` error is printed, non-zero exit.
- F3. A tidy step fails or is unsafe: it is skipped and reported with the command to finish it; the merge
  stands, the merged event is written (its text lists what was left), and the exit code is 0.
- F4. The owner-action lock is busy (another merge or override running): refused with the existing "busy"
  message, nothing done.
- F5. The hub log or a note cannot be written: a warning on stderr; the command's result is unchanged.
- F6. GitHub cannot be reached while resolving a PR: `unknown`, the feature stays visible as today.
- F7. A `gone` answer was wrong (for example `origin` was pointed at the wrong repo for a while): the owner
  removes the entry from `~/.relay/merged.json`, which the README says. relay does not guess.

## Non-goals

- The per-repo PR-slot queue, pre-submit spec and plan lint, doc-erratum mode, extra-round one-tap, detecting
  PRs opened outside relay, CI presets, the handoff take fallback, and a gate record for sample approvals
  (filed for later, idea.md).
- Squash or rebase merges (relay's CI-skip rules assume merge commits).
- Tidying other machines, or worktrees in folders relay cannot see from `git worktree list`.
- Per-repo lists of sessions to notify (owner decision, D10).
- Deleting or rewriting the copied `docs/relay/<slug>/` folders in split repos; they are hidden, not removed.

## Open questions
