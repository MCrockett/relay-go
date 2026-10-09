# ci-skip-bookkeeping: spec

## Why

Every relay bookkeeping commit is pushed to the feature branch, and each push starts the repo's full CI on a commit that changes no code. From 1 to 9 October 2026 these runs took about 2,900 of 6,000 billable GitHub Actions minutes (48%) across the owner's private repos, and the owner reached 90% of their monthly Actions budget on the 9th. See idea.md.

relay does not need these runs. Its build gate (`commands.require_pr_ready`), the dashboard's CI column and its merge (`owneractions.merge_readiness`) all use `gitops.ci_for_code`, which already takes the newest CI result on any commit with the same code as the head, outside relay's folder.

GitHub starts no `push` or `pull_request` workflow for a push whose head commit message contains `[skip ci]`, and none for a pull request whose head commit contains it. The marker may sit anywhere in the message, including the body. Two consequences shape the design:
- A required status check on the PR's base branch stays "Expected" when the PR head skipped CI, and blocks the merge.
- A squash or rebase merge copies commit messages into the base branch, so the marker can travel there; a merge commit (relay's own merge uses `gh pr merge --merge`) does not include commit bodies.

## Decisions

- D1. **The marker.** A bookkeeping commit that may skip CI (D2) gets a body line `[skip ci]` after a blank line. The subject stays exactly as today, so nothing that reads subjects changes (for example `holds.py`, which matches `relay: handoff <slug>`).
- D2. **When.** relay adds the marker only when all of these hold, checked just before the commit:
  1. The setting (D3) is `auto`.
  2. The commit changes only files under the feature's relay folder. This is true of every commit `gitops.commit_paths_under` makes.
  3. origin already has the code: HEAD's tree outside the relay folder (`docs/relay/`, the prefix `ci_for_code` uses) equals that of `origin/<branch>`. When origin has no such branch yet, the comparison is with `origin/<base>`, where base is the PR's base branch, else `develop`. If neither ref exists, no marker. So a push that carries code origin does not have yet always starts CI.
  4. GitHub already has CI evidence for that code: `gitops.ci_for_code(root, HEAD, prefix)` is `green`, `failing` or `pending`. With `none` (for example a repo whose CI runs only on pull requests, before the PR is open) there is no marker, so the first PR head still starts CI.
  5. No required status checks guard the branch the work will merge into (D4).
- D3. **The setting.** `[build] skip_ci` in `docs/relay/config.toml`: `"auto"` (the default) or `"never"`. Any other value is a config error, reported the way other bad settings are. `relay config` shows it next to `require_ci`.
- D4. **Required checks.** The branches checked are the PR's base branch when the feature has a PR, else both `develop` and `main`. For each one that exists on origin, relay asks GitHub:
  - rulesets: `gh api repos/{owner}/{repo}/rules/branches/<branch>`; any rule of type `required_status_checks` counts;
  - classic branch protection: `gh api repos/{owner}/{repo}/branches/<branch>`; a `protection.required_status_checks` with a non-empty `contexts` or `checks` list, or an `enforcement_level` other than `off`, counts.
  If any branch has a required check, or either call fails for a reason other than the branch not existing, there is no marker. These answers are memoized for one relay process, like other `gh` reads.
- D5. **Where.** Every place relay makes a bookkeeping commit uses D2:
  - `Ctx.save` in `commands.py` (submit, reviews, overrides, handoff, re-review on a changed GO);
  - `relay new`, `relay adopt`, `relay take`;
  - `owneractions.run_override` (dashboard overrides);
  - `reviewjobs.WorkCtx.save` (dashboard reviews).
  The decision lives in one new function, `ciskip.marker_ok(root, branch, pr, cfg)`. `gitops.commit_paths_under` gains a `skip_ci=False` argument that adds the body line.
- D6. **The code commit is always checked.** `ci_for_code` looks at at most `limit` (20) candidates, newest first, so today more than 19 bookkeeping commits after a code commit hide it. That never mattered while every bookkeeping head had its own CI run; with D2 it would make relay report `none`. `ci_for_code` therefore always checks the code commit itself (`code_sha`), even when the newer candidates fill the limit.
- D7. **Never in the way.** Any error while deciding (git, `gh`, network) means no marker, and the command continues as today. relay prints nothing about the marker.

## Requirements

- R1. `ciskip.marker_ok` per D2 and D4: true only when every condition holds. Each condition alone makes it false:
  - the setting is `never`;
  - local code differs from `origin/<branch>`;
  - no `origin/<branch>` and code differs from `origin/<base>`;
  - neither ref exists;
  - `ci_for_code` is `none`;
  - a ruleset requires checks;
  - classic protection requires checks;
  - either `gh` call fails.
  A branch that does not exist on origin is skipped in the D4 check.
- R2. `gitops.commit_paths_under(..., skip_ci=True)` writes the subject unchanged and `[skip ci]` as the body; `skip_ci=False` writes today's message exactly.
- R3. Every commit site in D5 passes `marker_ok`'s answer. Tests cover at least:
  - `relay submit` with the code already on origin and green CI: the bookkeeping commit carries the marker;
  - `relay submit` with an unpushed code commit: no marker;
  - a dashboard override;
  - a dashboard review's published commit.
- R4. `[build] skip_ci` per D3: default `auto`; `never` turns it off; a bad value is a config error; `relay config` shows it.
- R5. `ci_for_code` per D6: with 25 bookkeeping commits after a green code commit, the result is `green`.
- R6. D7: a failing `gh` or git call during the decision gives no marker and the command succeeds.
- R7. README, in plain words:
  - what relay skips and why;
  - the `skip_ci` setting;
  - repos with required status checks are left alone;
  - merge with a merge commit, because a squash or rebase merge can carry the marker into the base branch;
  - how to start CI by hand when needed (push any commit without the marker, for example `git commit --allow-empty -m "chore: run CI"`).
- R8. Tests use temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, temp git repos with a bare `origin` and the fake `gh` from `tests/helpers.py`; never real Claude, Codex or GitHub.
- R9. Manual check during build, on this repo's own PR: a pushed commit whose body is `[skip ci]` starts no workflow run, and a later code push does.

## Failure paths

- F1. A stale `origin/<branch>` ref: relay compares with what it last fetched, so the worst case is no marker (origin seems to lack code it has), never a skipped code push.
- F2. Squash or rebase merges copy the marker into the base branch and skip the post-merge push CI there. relay's own merge uses a merge commit; README (R7) tells the owner to do the same.
- F3. A PR opened while its head carries the marker shows no checks on GitHub for that head. relay still finds the result on the code commit (D2.4 guarantees one exists). In repos with required checks, D4 keeps the marker off.
- F4. Required checks added to a repo after a marked commit became a PR head: the merge waits on checks that never start. The owner pushes any commit without the marker (R7), or sets `skip_ci = "never"`.
- F5. A flaky failure on the code commit: the owner re-runs that workflow run on GitHub; the marked head needs no run of its own.
- F6. The extra `gh` calls fail or time out: no marker (D7), and CI runs as today.

## Non-goals

- Changing any repo's workflow files (path filters, concurrency, triggers). Those are separate PRs per repo.
- Skipping CI for the author's own docs-only commits; relay does not make those.
- Detecting required checks set by organization rulesets the token cannot read, beyond D4's "a failed call means no marker".
- A message for the owner when a commit skips CI.

## Open questions
