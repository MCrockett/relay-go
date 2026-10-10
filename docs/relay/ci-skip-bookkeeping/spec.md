# ci-skip-bookkeeping: spec

## Why

Every relay bookkeeping commit is pushed to the feature branch, and each push starts the repo's full CI on a commit that changes no code. From 1 to 9 October 2026 these runs took about 2,900 of 6,000 billable GitHub Actions minutes (48%) across the owner's private repos, and the owner reached 90% of their monthly Actions budget on the 9th. See idea.md.

relay does not need these runs. Its build gate (`commands.require_pr_ready`), the dashboard's CI column and its merge (`owneractions.merge_readiness`) all use `gitops.ci_for_code`, which already takes the newest CI result on any commit with the same code as the head, outside relay's folder.

GitHub starts no `pull_request` workflow for a pull request whose head commit message contains `[skip ci]`. For `push` workflows its documentation says a marker in any commit of the push skips them, so this spec assumes a push that carries one marked commit starts no push workflow at all, even when other commits in it change code. The marker may sit anywhere in the message, including the body. Two consequences shape the design:
- A required status check on the PR's base branch stays "Expected" when the PR head skipped CI, and blocks the merge. In October, isitev, votier and idlekeeper's `main` had required checks, and isitev alone spent 571 minutes on bookkeeping commits in nine days, so leaving such repos out would give up much of the saving. The owner chose (2026-10-09) to keep their required checks and leave unmarked only the commit the owner merges on (D10).
- A squash or rebase merge copies commit messages into the base branch, so the marker can travel there; a merge commit (relay's own merge uses `gh pr merge --merge`) does not include commit bodies.

## Decisions

- D1. **The marker.** A bookkeeping commit that may skip CI (D2) gets a body line `[skip ci]` after a blank line. The subject stays exactly as today, so nothing that reads subjects changes (for example `holds.py`, which matches `relay: handoff <slug>`).
- D2. **When.** relay adds the marker only when all of these hold, checked just before the commit:
  1. The setting (D3) is `auto`.
  2. The commit changes only files under the feature's relay folder. This is true of every commit `gitops.commit_paths_under` makes.
  3. origin already has the code: HEAD's tree outside the relay folder (`docs/relay/`, the prefix `ci_for_code` uses) equals that of `origin/<branch>`. When origin has no such branch yet, the comparison is with `origin/<base>`, where base is the PR's base branch, else `develop`. If neither ref exists, no marker. So a push that carries code origin does not have yet always starts CI.
  4. GitHub already has CI evidence for that code: `gitops.ci_for_code(root, HEAD, prefix)` is `green`, `failing` or `pending`. With `none` (for example a repo whose CI runs only on pull requests, before the PR is open) there is no marker, so the first PR head still starts CI.
  5. Either no required status checks guard the branch the work will merge into (D4), or the feature's status after this commit is not `ready-to-merge` (D10).
- D3. **The setting.** `[build] skip_ci` in `docs/relay/config.toml`: `"auto"` (the default) or `"never"`. Any other value is a config error, reported the way other bad settings are. `relay roles`, which already shows `require_ci`, shows it next to that.
- D4. **Required checks.** The branches checked are the PR's base branch when the feature has a PR, else both `develop` and `main`. For each one that exists on origin, relay asks GitHub:
  - rulesets: `gh api repos/{owner}/{repo}/rules/branches/<branch>`; any rule of type `required_status_checks` counts;
  - classic branch protection: `gh api repos/{owner}/{repo}/branches/<branch>`; a `protection.required_status_checks` with a non-empty `contexts` or `checks` list, or an `enforcement_level` other than `off`, counts.
  A branch with a required check turns on D10 for the commit. When either call fails for a reason other than the branch not existing, there is no marker. Both calls go through `gitops.gh_json`, so they are memoized only inside a `read_memo` context, like every other `gh` read, and read again otherwise. The long-running dashboard therefore sees a protection change at its next owner action.
- D5. **Where.** Every place relay makes a bookkeeping commit uses D2:
  - `Ctx.save` in `commands.py` (submit, reviews, overrides, handoff, re-review on a changed GO);
  - `relay new`, `relay adopt`, `relay take`;
  - `owneractions.run_override` (dashboard overrides);
  - `reviewjobs.WorkCtx.save` (dashboard reviews).
  The decision lives in one new function, `ciskip.marker_ok(root, branch, pr, cfg, status)`, where `status` is the feature's status as this commit records it. `gitops.commit_paths_under` gains a `skip_ci=False` argument that adds the body line.
- D6. **Marked commits do not use up the limit.** `ci_for_code` asks GitHub about at most `limit` (20) candidates, newest first: the commits from the head back to the code commit (`code_sha`) whose code equals the head's. CI evidence can sit on the code commit or on any unmarked bookkeeping commit after it (a push runs CI on its tip only). A commit whose message carries the marker never has a run of its own, so `ci_for_code` passes over marked candidates without asking GitHub and without counting them toward the limit. The newest unmarked candidate with a completed result still wins, so a newer failing result is never hidden by an older green one, however many marked commits sit on top.
- D7. **A marked commit never travels with code.** A marked commit is pushed right after it is made. When relay's push of a marked commit fails, relay removes the marker from that commit before reporting the failure, with `git commit --amend`, which rewrites only its message. It amends only when both hold: HEAD is still that commit, and origin does not have it (`git ls-remote origin refs/heads/<branch>` answers with another commit or no branch, so a push that reached origin but lost its answer is never rewritten). When either check fails or cannot be made, the commit is left as it is, and F7 applies. A later push that carries it together with new code then starts CI as today. The sites that commit in a temporary worktree (dashboard overrides and reviews) throw the commit away when the push fails, so they need nothing more. The same holds for `relay new`, `relay take` and `Ctx.save`, whether the push is best-effort or required.
- D8. **Cancelled is not failing.** `gitops.ci_state` counts a check whose conclusion is `CANCELLED` as failing today. A workflow with `cancel-in-progress` cancels a run when a newer push to the same branch arrives, so the code commit would read as failing while the newer run is still pending. For each candidate `ci_for_code` reads the checks this way: any completed check that failed for a reason other than cancellation makes the candidate `failing`, which is returned at once, as today; otherwise, a candidate with any cancelled check counts as having no completed result, and the search goes on to older candidates; with no other result, the answer is `pending` if any candidate is pending, else `none`. `ci_state` itself is unchanged for other callers.
- D10. **Required checks: the merge head runs CI.** In a repo where D4 finds a required check, relay still marks bookkeeping commits, except a commit after which the feature's status is `ready-to-merge`: the build GO (from `relay submit`, `relay review`, a fallback confirmation or a dashboard review) and the owner's `go` override. Every commit made while the feature stays `ready-to-merge` (a handoff, `relay take`, a re-review that gives GO again) is unmarked too, because its status after the commit is still `ready-to-merge`. The owner can merge only a `ready-to-merge` feature, and relay's own commits are the only way a feature becomes or stays `ready-to-merge`, so the head the owner merges on always has its own CI run and the required checks report. A commit that moves the feature out of `ready-to-merge` (a NO-GO after a stale GO, for example) may be marked again; the feature cannot be merged until a later unmarked GO commit. In repos without required checks, D10 does not apply and the GO commit is marked like any other.
- D9. **Never in the way.** Any error while deciding (git, `gh`, network) means no marker, and the command continues as today. relay prints nothing about the marker.

## Requirements

- R1. `ciskip.marker_ok` per D2 and D4: true only when every condition holds. Each condition alone makes it false:
  - the setting is `never`;
  - local code differs from `origin/<branch>`;
  - no `origin/<branch>` and code differs from `origin/<base>`;
  - neither ref exists;
  - `ci_for_code` is `none`;
  - a ruleset requires checks and `status` is `ready-to-merge`;
  - classic protection requires checks and `status` is `ready-to-merge`;
  - either `gh` call fails.
  With a ruleset or classic protection requiring checks and any other `status` (for example `review`, `waiting-owner`, `author`), the answer follows the other conditions.
  A branch that does not exist on origin is skipped in the D4 check.
- R2. `gitops.commit_paths_under(..., skip_ci=True)` writes the subject unchanged and `[skip ci]` as the body; `skip_ci=False` writes today's message exactly.
- R3. Every commit site in D5 passes `marker_ok`'s answer. Tests cover at least:
  - `relay submit` with the code already on origin and green CI: the bookkeeping commit carries the marker;
  - `relay submit` with an unpushed code commit: no marker;
  - a dashboard override;
  - a dashboard review's published commit;
  - in a repo with a required check (fake `gh`): `relay submit` for the build is marked, the build GO commit is not, a NO-GO commit is, a handoff while `ready-to-merge` is not, and a dashboard `go` override is not;
  - in a repo without required checks: the build GO commit is marked.
- R4. `[build] skip_ci` per D3: default `auto`; `never` turns it off; a bad value is a config error; `relay roles`, which already shows `require_ci`, shows it next to that.
- R5. `ci_for_code` per D6 and D8, each case a test:
  - 25 marked commits after a green code commit give `green`;
  - 25 marked commits after a green unmarked bookkeeping commit (CI ran only on that bookkeeping commit) give `green`;
  - a failing result on an unmarked bookkeeping commit newer than a green code commit gives `failing`;
  - marked commits cause no `gh` call;
  - a cancelled check on the newest candidate with a green older candidate gives `green`;
  - a newest candidate with one `FAILURE` and one `CANCELLED` check, over a green older candidate, gives `failing`;
  - a cancelled check on the only candidate with a pending one gives `pending`, and with nothing else gives `none`;
  - D7's guards: no amend when HEAD moved after the commit, when origin already has the commit, or when `git ls-remote` fails.
- R6. D7: when the push of a marked commit fails, the local commit no longer carries the marker, its subject and files are unchanged, and the command reports the push failure as today. Tested for `Ctx.save` with a best-effort push and with a required push, and for `relay new` and `relay take`.
- R10. D9: a failing `gh` or git call during the decision gives no marker and the command succeeds.
- R7. README, in plain words:
  - what relay skips and why;
  - the `skip_ci` setting;
  - repos with required status checks are left alone;
  - merge with a merge commit, because a squash or rebase merge can carry the marker into the base branch;
  - how to start CI by hand when needed: make a new commit without the marker and push it, for example `git commit --allow-empty -m "chore: run CI"` and `git push`. Setting `skip_ci = "never"` alone starts nothing for a head that is already marked.
- R8. Tests use temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, temp git repos with a bare `origin` and the fake `gh` from `tests/helpers.py`; never real Claude, Codex or GitHub.
- R9. Manual checks during build, on this repo's own PR (public, so free), with results recorded in the plan's build notes:
  - a pushed commit whose body is `[skip ci]` starts no workflow run, and a later unmarked code push does;
  - one push carrying a marked commit followed by a code commit: whether a push workflow and the PR workflow start. D7 holds whatever the answer; the result tells whether F7 is real.

## Failure paths

- F1. A stale `origin/<branch>` ref: relay compares with what it last fetched, so the worst case is no marker (origin seems to lack code it has), never a skipped code push.
- F2. Squash or rebase merges copy the marker into the base branch and skip the post-merge push CI there. relay's own merge uses a merge commit; README (R7) tells the owner to do the same.
- F3. A PR opened while its head carries the marker shows no checks on GitHub for that head. relay still finds the result on the code commit (D2.4 guarantees one exists). In repos with required checks, D4 keeps the marker off.
- F4. Required checks added to a repo after a marked commit became a PR head: the merge waits on checks that never start. The owner makes and pushes a new commit without the marker (R7); setting `skip_ci = "never"` only stops future markers.
- F5. A flaky failure on the code commit: the owner re-runs that workflow run on GitHub; the marked head needs no run of its own.
- F6. The extra `gh` calls fail or time out: no marker (D9), and CI runs as today.
- F8. A required check added while a feature is mid-review: its marked commits are fine, because the feature cannot be merged before an unmarked GO commit (D10). A required check added after that GO is F4.
- F9. CI on the unmarked GO commit fails (a flaky test, a runner problem): GitHub blocks the merge as it does today. The owner re-runs that run on GitHub; relay's own gate already reads the code commit's result, so it may show green while GitHub waits for the re-run.
- F7. A marked commit left unpushed by something D7 cannot catch (relay killed between commit and push, or a failed amend), later pushed by the author together with code: push workflows may not start for that push. A pull request's workflow still starts, because only its head commit counts, and relay's gate reads that result. In a repo whose CI runs only on push, relay reports `none` or `pending` and the owner pushes an unmarked commit (R7).

## Non-goals

- Changing any repo's workflow files (path filters, concurrency, triggers). Those are separate PRs per repo.
- Skipping CI for the author's own docs-only commits; relay does not make those.
- Detecting required checks set by organization rulesets the token cannot read, beyond D4's "a failed call means no marker".
- A message for the owner when a commit skips CI.

## Open questions
