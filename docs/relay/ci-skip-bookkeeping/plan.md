# ci-skip-bookkeeping Implementation Plan

**Goal:** relay's bookkeeping commits stop starting GitHub Actions runs when CI has nothing new to test, while the commit the owner merges on still runs CI in repos with required checks.

**Architecture:**
- **Marker:** `gitops.commit_paths_under(..., skip_ci=False)` writes the `[skip ci]` body line (D1).
- **Decision:** a new module, `relaylib/ciskip.py`, holds:
  - `marker_ok(root, branch, pr, cfg, status)`, which decides D2, D4 and D10;
  - `required_checks(root, branches)` for D4;
  - `unmark(root, branch, sha)`, the guarded amend after a failed push (D7).
- **CI evidence:** `gitops.ci_for_code` passes over marked candidates without counting them (D6) and reads cancelled checks per D8.
- **Commit sites:** every site in D5 asks `marker_ok`, passes the answer to `commit_paths_under`, and calls `unmark` when its push fails.

**Tech Stack:** Python 3.11 standard library, `unittest`, temp git repos with a bare `origin`, the fake `gh` from `tests/helpers.py`.

**Spec:** `docs/relay/ci-skip-bookkeeping/spec.md` (GO in round 4). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution line.
- Tests set `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME` to temporary folders. They use the fake `gh` (`RELAY_GH_BIN`) and never real Claude, Codex or GitHub.
- Deciding never fails a command (D9): `marker_ok` wraps its whole body and returns False on any exception.

## Spec clarifications

- **The prefix.** "Code" means the tree outside `docs/relay/` (`state.RELAY_DIR + "/"`), the prefix `ci_for_code` callers already use for the dashboard. Code equality is `git diff --quiet <ref> HEAD -- . ':(exclude,icase)docs/relay/'`.
- **Order in `marker_ok`, cheapest first:**
  1. the setting;
  2. find the ref: `origin/<branch>` if it exists, else `origin/<base>`, where base is the PR's `baseRefName` when `pr` is set, else `develop`; none exists means False;
  3. code equality with that ref;
  4. `ci_for_code(root, "HEAD", prefix)` in `green`, `failing` or `pending`;
  5. `required_checks`, for every status. A failed call means False whatever the status (D4, D9). A successful answer with a required check means False only when `status == "ready-to-merge"` (D10).
- **`required_checks(root, branches)`.** Branches are `[baseRefName]` when there is a PR, else `["develop", "main"]`. A branch with no local `origin/<branch>` ref is skipped, which is D4's "does not exist on origin". relay fetches before every write, so the remote refs are current. For each remaining branch it calls `gitops.gh_json(root, ["api", f"repos/{{owner}}/{{repo}}/rules/branches/{b}"])` and `gitops.gh_json(root, ["api", f"repos/{{owner}}/{{repo}}/branches/{b}"])` and applies D4's tests. Any `RelayError` propagates and becomes False in `marker_ok`.
- **Marked commits in `ci_for_code`.** One `git log --format=%H%x00%B%x01` over the candidate range gives every message. A candidate is marked when a line of its message is exactly `[skip ci]`, the spec's marker (D1). Marked candidates are passed over without a `gh` call, and `limit` counts only candidates actually asked.
- **Cancelled checks (D8).** `gitops.ci_state(info, cancelled="failing")` keeps today's behaviour for its other callers. `ci_for_code` reads candidates through `commit_ci_state(root, sha, cancelled="skip")`. In that mode a candidate is:
  - `failing` when any completed check failed for a reason other than `CANCELLED`;
  - otherwise `pending` when any check is pending;
  - otherwise `cancelled` when any check was cancelled;
  - otherwise `green`.

  `ci_for_code` treats `cancelled` like `none` and moves on to older candidates.
- **`unmark(root, branch, sha)` (D7).** Run only when the push failed and the commit was marked. It returns without changes unless both hold:
  - `git rev-parse HEAD` equals `sha`;
  - `gitops.network_git(root, "ls-remote", "origin", f"refs/heads/{branch}")` succeeds and names a commit other than `sha`, or nothing.

  It then rewrites the message with `git commit --amend --only -q -m <message without the marker line>`. `--only` with no paths amends the message alone, so anything staged stays staged. Every exception is swallowed: F7 covers what is left.
- **Status at each site.** `status` is `st["status"]` after the change the commit records:
  - `Ctx.save` and `WorkCtx.save` pass `self.st["status"]`;
  - `relay new` passes the new state's status;
  - `relay adopt` passes `drafting`;
  - `relay take` passes the feature's status on that branch;
  - `owneractions.run_override` passes `st["status"]` after `apply_override`.
- **Config for each site.**
  - `Ctx` and `WorkCtx` have `cfg`.
  - `relay new`, `relay adopt` and `relay take` call `config.load(root)`, already loaded as `c.cfg` where a `Ctx` exists.
  - `owneractions.run_override` calls `config.load(work)` in its worktree.
- **The dashboard review's push.** `reviewjobs._run` pushes the whole chain with one lease push. A spec or plan re-review chain can make two commits, and neither is ever `ready-to-merge`. A build review makes one commit. So a single push never mixes a marked commit with the unmarked GO commit in the D10 case.
- **A rejected push in tests.** The bare origin gets a `hooks/pre-receive` that exits 1. The push fails, while `ls-remote` still works and shows that origin lacks the commit. An unreachable origin (its path renamed) makes both fail.
- **Fake `gh`.** `FAKE_GH` in `tests/helpers.py` gains `FAKE_GH_API`, a JSON file mapping a path substring to either a response object or `{"__rc": 1}` (exit 1 with a message). It is checked before the existing fallbacks, so every current test keeps its behaviour.

## Review Focus

1. A push that carries new code never carries a marker: D2.3 at commit time, D7 after a failed push.
2. In a repo with required checks, every commit whose status after it is `ready-to-merge` is unmarked.
3. `ci_for_code` never reports `green` over a newer real failure, whatever is marked or cancelled.
4. No failure in deciding stops a command.

## Tasks

### Task 1: Marker and CI evidence in gitops

Covers: R2, R5.

Files: `relaylib/gitops.py`, `tests/test_gitops.py`.

- [ ] Failing tests:
  - `commit_paths_under(..., skip_ci=True)` gives subject unchanged plus body `[skip ci]`; `skip_ci=False` gives today's message exactly.
  - `ci_for_code`, with commits made by `commit_paths_under(..., skip_ci=True)`:
    - 25 marked commits after a green code commit give `green`;
    - 25 marked commits after a green unmarked bookkeeping commit (runs only on that commit) give `green`;
    - a failing unmarked bookkeeping commit newer than a green code commit gives `failing`;
    - the `FAKE_GH_LOG` shows no `check-runs` call for any marked sha;
    - a newest candidate with a cancelled check over a green older one gives `green`;
    - `FAILURE` plus `CANCELLED` on the newest over a green older one gives `failing`;
    - cancelled only plus a pending candidate gives `pending`, and cancelled alone gives `none`.
  - `ci_state` still calls a lone `CANCELLED` check failing for its other callers.
- [ ] Implement per the clarifications. Run `python3.11 -m unittest tests.test_gitops -v`, then commit.

### Task 2: The setting

Covers: R4.

Files: `relaylib/config.py`, `relaylib/commands.py` (`cmd_roles` output), `tests/test_config.py`, `tests/test_commands.py`.

- [ ] Failing tests:
  - `DEFAULTS["build"]["skip_ci"] == "auto"`;
  - `never` loads;
  - `skip_ci = "sometimes"` in a repo config raises `RelayError` naming the file and the allowed values, like the TOML error path in `config.load`;
  - `relay roles` prints `skip_ci auto` next to `require_ci`.
- [ ] Implement; run the two modules; commit.

### Task 3: The decision

Covers: R1, R10 (function level; the command level is in Task 5), and D10 at the function level.

Files: `relaylib/ciskip.py` (new), `tests/helpers.py` (`FAKE_GH_API`), `tests/test_ciskip.py` (new).

- [ ] Failing tests, in a temp repo with a bare origin and the fake `gh`:
  - true when everything holds (code pushed, green CI, no required checks) for status `review`, and for `ready-to-merge` with no required checks;
  - false for each condition alone: `never`; local code ahead of `origin/<branch>`; no `origin/<branch>` with code differing from `origin/develop`; neither ref; `ci_for_code` `none`;
  - with a ruleset requiring checks: false for `ready-to-merge`, true for `review`, `author` and `waiting-owner`. The same four results hold with classic protection requiring checks (non-empty `contexts`, non-empty `checks`, and `enforcement_level` `everyone`), while `enforcement_level` `off` with empty lists counts as no requirement;
  - a failing rules call, and separately a failing branch call, gives false for `review`, `author`, `waiting-owner` and `ready-to-merge`;
  - a branch with no `origin/<branch>` is skipped (only `develop` checked when `main` is missing);
  - with a PR, only its base is checked;
  - an exception from git inside the function (patched `gitops.git` raising) gives false and does not raise.
- [ ] Implement `marker_ok` and `required_checks`. Run; commit.

### Task 4: Unmarking after a failed push

Covers: R6 at the function level.

Files: `relaylib/ciskip.py`, `tests/test_ciskip.py`.

- [ ] Failing tests:
  - after a marked commit and a push rejected by origin's `pre-receive` hook, `unmark` leaves subject and tree unchanged, removes the marker and keeps staged changes staged;
  - no change when HEAD moved after the commit;
  - no change when origin already has the commit, which simulates a push that landed but lost its answer;
  - no change when `ls-remote` fails (origin unreachable);
  - no exception in any case.
- [ ] Implement; run; commit.

### Task 5: Command commit sites

Covers: R3, R6, R10.

Files: `relaylib/commands.py` (`Ctx.save`, `cmd_new`, `cmd_adopt`, `cmd_take`), `tests/test_commands.py`.

- [ ] Failing tests, through the CLI as the existing `test_commands` tests drive it (fake reviewer and fake `gh` with green check runs and `FAKE_GH_API`):
  - `relay submit` with code on origin and green CI marks its commit;
  - with an unpushed code commit, no marker;
  - in a repo whose base has a required check: the build `relay submit` commit is marked, the build GO commit is not, a NO-GO commit is, a `relay handoff` while `ready-to-merge` is not;
  - in a repo without required checks, the build GO commit is marked;
  - with origin rejecting pushes (`pre-receive` hook): `Ctx.save` with a best-effort push, and with `push="required"` (an override), leaves the local commit unmarked and reports the push failure as today. The same holds for `relay new` and `relay take`;
  - with origin made unreachable after the decision and the commit (a patched `gitops.push`, or `ciskip.unmark`'s `ls-remote`, sees the renamed origin; the command's initial fetch and `marker_ok` see it reachable): the commit keeps its marker (D7 cannot tell whether the push landed) and the push failure is reported as today;
  - R10 at the command level: with the fake `gh` failing every `api` call, and separately with a patched git failure inside `ciskip`, `relay submit` succeeds and its commit carries no marker.
- [ ] Implement: each site computes `marker_ok`, passes `skip_ci`, and on a failed push calls `ciskip.unmark` before warning or raising. Run `tests.test_commands`; commit.

### Task 6: Dashboard commit sites

Covers: R3.

Files: `relaylib/owneractions.py`, `relaylib/reviewjobs.py`, `tests/test_owneractions.py`, `tests/test_reviewjobs.py`.

- [ ] Failing tests:
  - a dashboard override published with code on origin and green CI is marked;
  - in a required-check repo, a dashboard `go` override is not marked;
  - a dashboard build review's published NO-GO commit is marked;
  - in a required-check repo, its published GO commit is not.
- [ ] Implement in `run_override` and `WorkCtx.save`. Run both modules; commit.

### Task 7: README

Covers: R7.

Files: `README.md`.

- [ ] A short section, "CI on relay's commits":
  - what relay skips and why;
  - the `skip_ci` setting;
  - in repos with required checks, the commit you merge on still runs CI;
  - merge with a merge commit, because squash and rebase can carry the marker;
  - to start CI by hand, make a new commit and push it (`git commit --allow-empty -m "chore: run CI"`, `git push`). `skip_ci = "never"` alone starts nothing.
- [ ] Check `tests.test_public` still passes (no machine paths). Commit.

### Task 8: Full suite and manual checks

Covers: R8, R9.

- [ ] Run the full suite: `python3.11 -m unittest discover -s tests -t . -v`.
- [ ] Push, open the PR, and wait for CI.
- [ ] On this PR (public repo, so free). The results go in the Build notes section of `docs/relay/ci-skip-bookkeeping/plan.md`. The probe file is `tests/fixtures/ci-probe.txt`, outside `docs/relay/`. It is added by one probe and removed by another, so the PR's net diff is unchanged.
  - Marked probe: an empty commit `chore: CI probe, marked` with body `[skip ci]`, pushed alone. Confirm with `gh run list --branch feat/ci-skip-bookkeeping` that no run starts.
  - Code probe: a commit `test: CI probe adds a fixture` that adds `tests/fixtures/ci-probe.txt` (one line), unmarked, pushed alone. Confirm a run starts.
  - Mixed probe (F7): a marked empty commit, then an unmarked commit `test: CI probe removes the fixture` that deletes `tests/fixtures/ci-probe.txt`, pushed together in one push. Record whether the PR workflow starts.
  - Limitation, recorded separately: this repo's `test.yml` runs push workflows only on `develop` and `main`. So these probes observe the `pull_request` workflow only, and the absence of a push run on the feature branch says nothing about the marker. Push-workflow behaviour for a marked push (and F7's push half) stays as documented by GitHub, not observed here.
  - The branch's head is unmarked before `relay submit`.

## Build notes

R9 manual checks on PR #16, 2026-10-09. Runs were listed with `gh run list --branch feat/ci-skip-bookkeeping`.

- Marked probe `6996e5b` (`chore: CI probe, marked`, body `[skip ci]`, pushed alone): no workflow run started.
- Code probe `b4e5b1e` (`test: CI probe adds a fixture`, adds `tests/fixtures/ci-probe.txt`, unmarked, pushed alone): a `pull_request` run started.
- Mixed probe: marked `8c4e163`, then unmarked `6a01b24` (`test: CI probe removes the fixture`), pushed together. A `pull_request` run started for `6a01b24`: a marked commit inside the push does not stop the PR workflow when the head is unmarked, as the spec expected (F7).
- Limitation: this repo's `test.yml` runs push workflows only on `develop` and `main`. So these probes observed the `pull_request` workflow only. Push-workflow behaviour for a push carrying a marked commit is not observed here; D7 keeps relay from making such pushes either way.
- Net diff of the probes: none (the fixture is added and removed). The branch head after the probes is unmarked.

Deviation from the plan's file list: the dashboard review tests (Task 6) live in `tests/test_commands.py`, next to the existing owner-requested review tests and their fixture, rather than in `tests/test_reviewjobs.py`. The dashboard override tests are in `tests/test_owneractions.py` as planned. `config.example.toml` also documents `skip_ci`.
