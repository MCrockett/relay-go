# ci-modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `[build] require_ci` with a per-project `ci` mode (`pr`, `local`, `release`, `none`), where `local` runs the repo's own `test_command` in the background on a clean checkout and gates merge on a recorded pass.

**Architecture:** Two new modules. `relaylib/cimode.py` resolves a PR's build settings from the global config plus the repo config at `origin/<base>` (spec D3), and holds the workflow warning. `relaylib/localtests.py` owns test records, keys, liveness locks, the detached runner, cleanup and the status flag text. `commands.py`, `owneractions.py`, `status.py`, `waiting.py` and the build prompt call into them.

**Tech Stack:** Python 3.11 standard library only (`tomllib`, `fcntl`, `subprocess`, `signal`, `hashlib`, `json`), `unittest`.

**Spec:** `docs/relay/ci-modes/spec.md` (GO at round 2). Read it with this plan; requirement numbers below are the spec's.

## Global Constraints

- Python 3.11 standard library only. One module per responsibility in `relaylib/`.
- Plain English in prompts, skills and docs; no em-dashes.
- Tests: `unittest`, files `tests/test_<module>.py`, temp git repos with a bare `origin` and fake binaries from `tests/helpers.py`. Never call real Claude or Codex. A `test_command` in tests is a small shell command (`true`, `false`, `sleep 30`, `exit 3`); no test runs a real suite (R22).
- Full suite: `python3.11 -m unittest discover -s tests -t . -v`. Known local flake under load: `tests/test_usage.py` `test_capture_runtime_and_lock_contention`.
- Modes: `pr`, `local`, `release`, `none`; default `pr`. `test_command` default `""`. `test_timeout_min` default `60`. Keep 20 logs per repo.
- Records live in `<git common dir>/relay/tests/`, run worktrees in `<git common dir>/relay/test-runs/` (spec D4, D6).

## Review Focus

1. A gate check from the main folder (develop checked out) and from the feature worktree must give the same mode and command for the same PR: both read `origin/<base>` (D3). Test owned by Task 2.
2. Two `relay submit` or `relay review` calls racing on the same key must end with one live run, not two, and neither may record a pass for code it did not test. Test owned by Task 4.
3. A run that is killed (SIGKILL, reboot) must never read as `running` or `pass` later: the lock is free, so it reads `stopped`. Test owned by Task 3.
4. A failing GitHub CI run on a local-mode PR must not block submit, review or merge, and a green GitHub run must not let a local-mode PR merge without a local pass. Test owned by Task 6.
5. A repo config on the feature branch that sets `ci = "none"` or `test_command = "true"` must not change that PR's own gate. Test owned by Task 2.

---

### Task 1: Config: `ci`, `test_command`, `test_timeout_min`, legacy mapping

**Files:**
- Modify: `relaylib/config.py` (`DEFAULTS["build"]`, `load`, new `normalize_build`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.CI_MODES = ("pr", "local", "release", "none")`; `config.normalize_build(table: dict, path: str) -> dict` (validates and maps one file's `[build]` table, returns a new table without `require_ci`); `DEFAULTS["build"] == {"ci": "pr", "test_command": "", "test_timeout_min": 60, "skip_ci": "auto"}`. `require_ci` disappears from the loaded config.

- [ ] **Step 1: Write failing tests** in `tests/test_config.py`:
  - `test_ci_defaults_to_pr`: no files, `cfg["build"]["ci"] == "pr"`, `"require_ci" not in cfg["build"]`, `test_command == ""`, `test_timeout_min == 60`.
  - `test_require_ci_maps_per_file`: global `require_ci = false` gives `ci == "none"`; global `ci = "release"` plus repo `require_ci = false` gives `"none"`; global `require_ci = true` gives `"pr"`.
  - `test_ci_wins_over_require_ci_in_same_file_with_one_warning`: a file with `ci = "local"` and `require_ci = true` gives `"local"`; stderr has one line naming the file and "require_ci is ignored" across two `load()` calls.
  - `test_bad_build_values_refused`: `ci = "github"` raises `RelayError` whose text has the path and "pr, local, release, none"; `require_ci = "no"`, `test_command = 5`, `test_timeout_min = 0`, `test_timeout_min = -1`, `test_timeout_min = "60"` each raise `RelayError` naming the file.
  - Update the existing assertion at `tests/test_config.py:39` to `cfg["build"]["ci"] == "pr"`.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_config -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `normalize_build` and call it on each file's parsed `[build]` table inside `load` before `_merge`. Keep the existing `skip_ci` check. The once-per-process warning uses a module-level set of warned paths. `bool` is not a valid `test_timeout_min` (it is an `int` subclass).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: ci mode setting with require_ci mapping` (Covers: R1, R2, R3).

### Task 2: `cimode`: build settings from the PR's base

**Files:**
- Create: `relaylib/cimode.py`
- Test: `tests/test_cimode.py`

**Interfaces:**
- Consumes: `config.normalize_build`, `config.config_path`, `config.DEFAULTS`, `gitops.show(root, ref, path)`, `state.RELAY_DIR`.
- Produces:
  - `@dataclass(frozen=True) class Settings: mode: str; configured: str; test_command: str; timeout_min: float; base: str` where `mode` is `pr`, `local` or `none` (release resolved) and `configured` is the raw value.
  - `cimode.settings(repo: str, base: str) -> Settings`: global file, then `docs/relay/config.toml` from `gitops.show(repo, f"origin/{base}", ...)`, each through `normalize_build`; an invalid base file raises `RelayError` naming `origin/<base>:docs/relay/config.toml` (spec failure path: fix the base directly, outside relay).
  - `cimode.resolve(configured: str, base: str) -> str`: `release` is `pr` when `base == "main"`, else `local`.
  - `cimode.require_command(s: Settings) -> None`: raises `RelayError(f'set [build] test_command in docs/relay/config.toml on {s.base}, or set ci = "none"')` when `s.mode == "local"` and the command is empty.

- [ ] **Step 1: Write failing tests** in `tests/test_cimode.py` using `helpers.make_repo` and `clone`:
  - `test_release_resolves_per_base`: `resolve("release", "main") == "pr"`, `resolve("release", "develop") == "local"`, other modes pass through.
  - `test_settings_read_base_not_worktree` (Review Focus 1, 5): push `[build]\nci = "local"\ntest_command = "true"\n` to `origin/develop`; in a feature checkout write `ci = "none"` and `test_command = "false"` in the working tree and commit it on the branch. `settings(feature_checkout, "develop")` and `settings(main_checkout, "develop")` both return `mode == "local"`, `test_command == "true"`.
  - `test_settings_global_only_when_base_has_no_config`: global `ci = "release"`, no repo file: base `develop` gives `local`, base `main` gives `pr`.
  - `test_invalid_base_config_names_ref`: base file `ci = "x"` raises with `origin/develop:docs/relay/config.toml` in the text.
  - `test_require_command_message`: exact message text above.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_cimode -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `relaylib/cimode.py`. It must not import `commands`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: cimode reads build settings from the PR base` (Covers: R4 helper, R9, D3).

### Task 3: `localtests`: keys, records, liveness, runner

**Files:**
- Create: `relaylib/localtests.py`
- Test: `tests/test_localtests.py`

**Interfaces:**
- Consumes: `gitops.common_dir`, `gitops.code_sha(root, prefix, ref)`, `gitops.git`, `freshness.bookkeeping_prefix(st)`, `notify.send`, `config.load`.
- Produces:
  - `key_id(code: str, command: str) -> str`: first 16 hex of `sha256(code + "\0" + command)`.
  - `paths(repo, kid) -> dict` with `record`, `lock`, `log` under `<common>/relay/tests/<kid>.{json,lock,log}`.
  - `read(repo, code, command) -> dict | None`: the record, with `status` rewritten to `stopped` when it says `running` and its lock can be taken (D11). Record fields: `code`, `command`, `commit`, `status` (`running|pass|fail|stopped`), `exit`, `started`, `finished`, `slug`, `branch`, `repo_name`, `pid`, `log`, `lock`, `reason`.
  - `write(repo, record) -> None`: write to a temp file in the same directory, then `os.replace` (D11).
  - `live(repo, kid) -> bool`: true when the lock is held by another process (`fcntl.flock(LOCK_EX | LOCK_NB)` fails).
  - `run(repo, commit, code, command, timeout_min, slug, branch) -> dict`: the foreground body: take the lock non-blocking (return the existing record if taken), write `running`, add a detached worktree at `commit` in `<common>/relay/test-runs/<kid>-<pid>` (on failure: `fail` with the git error in the log, R12), run `/bin/sh -c command` there with `start_new_session=True`, stdout and stderr to the log, wait up to `timeout_min * 60` s (on timeout kill the command's group, `fail`, `reason = f"timed out after {n} min"`), remove the worktree, write `pass` (exit 0) or `fail`, release the lock. SIGTERM to the runner kills the command's group and writes `stopped`.
  - `start(repo, commit, code, command, timeout_min, slug, branch) -> str`: D10 first (for every live record with the same `slug` and a different key: `os.kill(pid, SIGTERM)`), then spawn `[sys.executable, "-m", "relaylib.localtests", "run", repo, commit, code, slug, branch]` with the command and timeout passed in an environment variable, `start_new_session=True`, `cwd=config.RELAY_HOME_DIR`, stdin and stdout to `/dev/null`. Returns `"started"`, or `"running"` when `live` was already true (nothing spawned).
  - `ensure(repo, commit, code, command, timeout_min, slug, branch) -> str`: D8: `"pass"` when the record is `pass`, `"running"` when live, else `start(...)`. A `fail` record for the same key is restarted on purpose (spec review note: a re-review on the same code reruns).
  - `cleanup(repo) -> None`: R13 (remove dirs under `test-runs/` whose key is not live, `git worktree prune`, keep the newest 20 logs, never delete a live record or the newest record per slug whose `origin/<branch>` state is not `done`).
  - `flag(rec: dict | None, code: str) -> str`: R15 text for local mode (`tests: local, green at <sha8>`, `running since <HH:MM>`, `failed at <sha8>`, `stopped at <sha8>`, `not run for <sha8>`).
  - `__main__` entry: `python -m relaylib.localtests run ...` calls `run`, then `cleanup`, then R17 notifications (Task 7 adds those).

- [ ] **Step 1: Write failing tests** in `tests/test_localtests.py` (temp repo with bare origin; commands `true`, `false`, `exit 3`, `sleep 30`):
  - `test_key_changes_with_code_and_command_not_bookkeeping`: a commit touching only `docs/relay/x/state.md` keeps `gitops.code_sha` and so `key_id`; a code commit or a different command changes it.
  - `test_run_pass_and_fail`: `run(... "true" ...)` gives `status == "pass"`, `exit == 0`; `"exit 3"` gives `fail`, `exit == 3`; the worktree directory is gone afterwards and `git worktree list` does not show it.
  - `test_run_uses_clean_checkout_of_commit`: the command `test -f only_in_commit && ! test -f untracked_file` passes when the author's working tree has an uncommitted `untracked_file`.
  - `test_killed_run_reads_stopped` (Review Focus 3): start via `start(... "sleep 30" ...)`, wait for `running`, `SIGKILL` the runner's process group, then `read(...)["status"] == "stopped"`.
  - `test_timeout_records_fail`: `timeout_min=0.02`, `sleep 30` gives `fail` with `reason == "timed out after 0.02 min"` within 10 s.
  - `test_worktree_failure_is_fail`: a commit sha that does not exist gives `fail` and the log has git's error.
  - `test_start_stops_older_run_of_same_feature`: start key A (`sleep 30`), then start key B for the same slug; A's record becomes `stopped` within 5 s.
  - `test_start_same_key_twice_runs_once`: two `start` calls for one live key: the second returns `"running"`; only one runner process exists.
  - `test_cleanup_keeps_live_and_open_features`: 25 finished records across slugs; after `cleanup`, 20 logs remain, plus the newest record of a slug whose origin state is `build/ready-to-merge` even when it is among the oldest.
  - `test_flag_texts`: each R15 local string for each status and for `None`.
  - `test_write_is_atomic`: `write` leaves no `*.tmp` file and `read` never fails JSON parsing while 200 writes run in a thread.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_localtests -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `relaylib/localtests.py` as specified above. Store the runner's pid in the record for D10's SIGTERM only; liveness never uses the pid.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: local test runs with records and liveness locks` (Covers: R11, R12, R13, D4, D5, D6, D7, D10, D11).

### Task 4: The gate in submit and review; workflow warning

**Files:**
- Modify: `relaylib/commands.py` (`require_pr_ready`, `ready_draft`, `cmd_submit`, `review_current`)
- Modify: `relaylib/cimode.py` (add `workflow_warnings`)
- Modify: `tests/test_commands.py` (existing `require_ci = false` tests at about lines 439, 2091, 2105 push `[build]\nrequire_ci = false\n` to `origin/develop` instead of writing it in the work tree, since the gate now reads the base)
- Test: `tests/test_commands.py`, `tests/test_cimode.py`

**Interfaces:**
- Consumes: `cimode.settings`, `cimode.require_command`, `localtests.ensure`, `gitops.code_sha`.
- Produces: `require_pr_ready(c) -> dict` keeps its return (PR info) and now also sets `c.ci: cimode.Settings` and, in local mode, `c.tests: str` (the `ensure` result). `cimode.workflow_warnings(repo: str, ref: str, base: str) -> list[str]`, one line per workflow file: `f"{path}: runs on pull_request to {base}; with ci = local that spends GitHub minutes"`.

- [ ] **Step 1: Write failing tests:**
  - In `tests/test_cimode.py`: `test_workflow_warning_scoped_to_pull_request`: relay-go's own `.github/workflows/test.yml` text (pull_request with types only, push with `branches: [main]`) warns; `pull_request:\n    branches: [main]` with base `develop` does not; `pull_request:\n    branches: [develop]` does; `on: [push, pull_request]` does; a file with no `pull_request` does not.
  - In `tests/test_commands.py` (fake `gh` reporting a failing GitHub check on the head):
    - `test_local_submit_ignores_github_ci_and_starts_run` (Review Focus 4): base config `ci = "local"`, `test_command = "true"`; `relay submit` at build reaches the review, and a `pass` record exists for the head's code shortly after.
    - `test_local_submit_without_command_refused`: base config `ci = "local"` only; submit exits non-zero with R7's message and `state.md` is unchanged.
    - `test_local_review_starts_run_after_go_goes_stale`: after a GO, push a code commit, `relay review` re-reviews and a record exists for the new code.
    - `test_concurrent_gates_one_run` (Review Focus 2): two threads call `require_pr_ready` on the same context with `test_command = "sleep 5"`; exactly one runner process, one record.
    - `test_pr_mode_unchanged`: no config: today's "this PR has no CI checks" message for a PR with no checks.
    - `test_local_submit_warns_about_workflow`: stderr has the warning line once.
    - `test_ready_draft_local_marks_ready_without_reading_ci`: a draft PR in local mode is marked ready and `ci_for_code` is not called (patched to raise).
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_cimode tests.test_commands -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** `require_pr_ready`: after `require_pr_open`, `c.ci = cimode.settings(c.root, info["baseRefName"])`; `pr` keeps today's code; `none` returns as today's `require_ci = false`; `local` calls `require_command`, then `localtests.ensure(c.root, head, gitops.code_sha(c.root, prefix, head), ...)` and returns without reading GitHub. `ready_draft` in local mode marks ready and returns before `ci_for_code` (spec review note on R8). `cmd_submit` prints each `workflow_warnings` line to stderr once, at build submit, in local mode only. `ready_draft`'s `require_ci` read becomes the `none` mode check.
- [ ] **Step 4: Run** the full suite. Expected: PASS (apart from the known flake).
- [ ] **Step 5: Commit** `feat: local and release modes in the build gate` (Covers: R4, R5, R6, R7, R8, D8, D12).

### Task 5: `relay test` command and `relay roles` line

**Files:**
- Modify: `relaylib/commands.py` (new `cmd_test`, parser entry, the `relay roles` line at about 1177)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `cimode.settings`, `cimode.require_command`, `localtests.read`, `localtests.start`, `localtests.run`, `gitops.pr_info`.
- Produces: `relay test [--feature SLUG] [--status] [--again] [--wait]`. The feature's PR is required ("open the PR first, then relay test"). The commit is the PR head; the settings come from the PR base (D3). Non-local mode: prints `tests: GitHub CI` or `tests: not gated (ci = none)` and exits 0.
  - default: a `pass` record prints it and says `--again` reruns; a live run is reported; otherwise `start`. It prints the status, the log path and "check with `relay test --status`", then returns.
  - `--status`: prints `localtests.flag(...)`, the log path and the reason; starts nothing.
  - `--again`: starts even over a `pass` (spec review note on D9).
  - `--wait`: runs `localtests.run` in the foreground; refused in an agent session (`identity.in_agent_session`) with "--wait is for your own terminal; use relay test and relay test --status".

- [ ] **Step 1: Write failing tests:** `test_relay_test_starts_and_status_reports`, `test_relay_test_reports_pass_and_again_reruns`, `test_relay_test_no_command_message` (R7 text), `test_relay_test_wait_refused_in_agent`, `test_relay_test_pr_mode_prints_github`, `test_roles_prints_ci_line` (asserts `ci pr, no test command, timeout 60 min` and "PR gates use the base branch's config"; replaces the old `require_ci True, skip_ci auto` assertion at about line 1032, keeping `skip_ci auto`).
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_commands -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `cmd_test` and the parser entry next to `review`.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat: relay test starts and reports local runs` (Covers: R4 roles line, R7, D9).

### Task 6: Merge readiness

**Files:**
- Modify: `relaylib/owneractions.py` (`merge_readiness`)
- Test: `tests/test_owneractions.py`, `tests/test_merge_cmd.py`

**Interfaces:**
- Consumes: `cimode.settings(repo, info["baseRefName"])`, `localtests.read`, `gitops.code_sha(repo, prefix, seen["commit"])`.
- Produces: in local mode, `merge_readiness` returns `info` only for a `pass` record and otherwise raises `RelayError` with exactly one of R10's five messages (times as `HH:MM`). `pr` mode is today's code; `none` is today's `require_ci = false` branch. The patch at `tests/test_owneractions.py:208` becomes a patch of `cimode.settings` returning `mode == "none"`.

- [ ] **Step 1: Write failing tests** in `tests/test_owneractions.py`: one per R10 message (`running`, `fail` with log path, `stopped`, no record, empty command); `test_local_pass_merges_even_with_failing_github` and `test_local_green_github_without_pass_refused` (Review Focus 4); `test_pass_for_old_command_refused` (record under a different command than the base's). In `tests/test_merge_cmd.py`: `relay merge` in local mode with no record prints "no local test result for <sha8> on this machine; run `relay test`".
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_owneractions tests.test_merge_cmd -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat: merge needs a local pass in local mode` (Covers: R10).

### Task 7: Status flags, inbox asks, checkouts, notifications, prompt

**Files:**
- Modify: `relaylib/status.py` (`checkouts`, `_row`, `_remote_row`), `relaylib/waiting.py` (`asks`), `relaylib/commands.py` (`review_current` GO branch, prompt context), `relaylib/localtests.py` (`__main__` notifications), `prompts/build.md:11`
- Test: `tests/test_status.py`, `tests/test_waiting.py`, `tests/test_localtests.py`, `tests/test_prompts.py`, `tests/test_commands.py`

**Interfaces:**
- Consumes: `cimode.settings`, `localtests.read`, `localtests.flag`.
- Produces:
  - `status.tests_flag(repo_dir, st, info) -> str | None`: for a build-stage feature with a PR, R15's text: `tests: GitHub CI`, `tests: not gated (ci = none)`, `tests: local, no test command`, or `localtests.flag(...)` for the PR head's code. Called from `_row` and `_remote_row`; any `RelayError` reading GitHub or the base becomes the flag `tests: unknown (<error>)` (spec review note on offline behavior).
  - `status.checkouts` skips paths under `<common>/relay/test-runs/` (R14).
  - `waiting.asks`: when status is `ready-to-merge`, not stale, and a flag starts with `tests: local,` and is not green: `failed` gives ask kind `tests` with `f"Tests failed at {sha8}: fix, push and relay review, or relay test for a flake"` (spec review note on R16); `stopped` or `not run` gives `"Run tests: relay test"`; `no test command` gives R7's message; `running` gives kind `merge` with `f"Merge PR #{pr}: tests running"`. A green flag keeps `Merge PR #{pr}`. `waiting_on_owner` is false while tests run.
  - GO in `review_current`, local mode: the "ready to merge" `notify.send` is sent only when `localtests.read` is `pass` for the head's code; otherwise it prints `relay: build GO from <reviewer>. Tests are <status>; merge waits for a pass.`
  - The runner's `__main__`: a `fail` sends `notify.send(cfg, f"relay: {repo_name} tests failed", f"{slug}: {sha8}, log {log}")`; a `pass` whose feature state on `origin/<branch>` is `ready-to-merge` sends the same "ready to merge" notification the GO sends.
  - `prompts/build.md` line 11 becomes `5. $ci_line`; `review_current` sets `ctx["ci_line"]` to today's text in `pr` and `none` modes, and in local mode to "Tests: this repo's gate is a local test run of `<command>` that relay tracks separately. Do not judge GitHub CI." (R18, D13).

- [ ] **Step 1: Write failing tests:** `test_status_tests_flag_each_text` (all eight R15 strings), `test_checkouts_skip_test_runs`, `test_asks_tests_failed_replaces_merge`, `test_asks_running_disables_merge`, `test_asks_green_keeps_merge`, `test_go_without_pass_holds_ready_notification`, `test_runner_fail_notifies`, `test_runner_pass_after_go_notifies_ready`, `test_build_prompt_ci_line_local_and_pr`.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_status tests.test_waiting tests.test_localtests tests.test_prompts tests.test_commands -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** The dashboard's merge button already takes its disabled reason from `merge_readiness` (`relaylib/ui/snapshot.py`), so it needs no change beyond Task 6.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: tests flag, inbox asks and notifications for local runs` (Covers: R14, R15, R16, R17, R18, D13).

### Task 8: Docs, relay-go's config, deploy check

**Files:**
- Modify: `README.md`, `config.example.toml` (line 38), `skills/relay-build/SKILL.md` (steps 4 and the CI failure bullets), `relaylib/deploycheck.py`
- Create: `docs/relay/config.toml`
- Test: `tests/test_deploycheck.py`, `tests/test_public.py` (if it checks docs for em-dashes, it covers the new text)

**Interfaces:**
- Consumes: `cimode.settings`, `status.checkouts`, `config.load`.
- Produces: `deploycheck.needs_command(root_dir: str) -> list[str]`: repo names under the projects root whose `origin/develop` repo config has neither `ci`, `require_ci` nor `test_command`, so they get R7's message once the global is `ci = "release"`. `deploycheck.main` prints `then: after you set ci = "release" globally, these repos need a test_command: <names>` when the list is not empty.

- [ ] **Step 1: Write failing test** `test_needs_command_lists_repos_without_repo_config` in `tests/test_deploycheck.py`: three temp repos (no config; `require_ci = false`; `test_command = "true"`): only the first is listed.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_deploycheck -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `needs_command`, then the docs:
  - README: a "CI modes" section covering the four modes, `test_command`, `test_timeout_min`, D3's base-branch rule (a PR's own config change applies to later PRs; a broken base config is fixed directly on the base), `relay test` with `--status`, `--again`, `--wait`, where records and logs live, that the run is a clean checkout with no gitignored files (the command does its own setup), and the `require_ci` mapping. The "relay merge" section names the local-pass rule.
  - `config.example.toml`: `ci = "pr"`, `test_command = ""`, `test_timeout_min = 60`, with one-line comments.
  - `skills/relay-build/SKILL.md`: in local mode, submit starts tests in the background; check with `relay test --status`; after a failure, fix, push and `relay review` (or `relay submit` before a GO); `relay test` for a flake; never `--wait`.
  - `docs/relay/config.toml`: `[build]`, `test_command = "python3.11 -m unittest discover -s tests -t ."`, `test_timeout_min = 90` (R20). Commit it with the code (it is not feature bookkeeping).
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `docs: ci modes in README, example config, build skill; relay-go test command` (Covers: R19, R20, R21).

### Task 9: Whole-branch check

Covers: R22 (every task's tests together), R5 and R6 (the full suite's existing pr and none tests stay green).

- [ ] **Step 1:** Run `python3.11 -m unittest discover -s tests -t . -v`. Expected: all pass, or only the known flake, rerun alone to confirm.
- [ ] **Step 2:** `grep -rn "require_ci" relaylib prompts skills` returns only `config.normalize_build` and its message. A search for the em-dash character (U+2014) in README.md, skills, prompts and docs/relay/ci-modes finds nothing.
- [ ] **Step 3:** Push, open the draft PR to develop, `relay submit` (the gate for this PR is still read from develop's config, which has none, so it runs under the owner's global setting).

## Rollout (after the owner merges; not part of the build review)

- Deploy as usual (`tools/deploy_check.py`, fast-forward the live folder). Then, with the owner, change `~/.relay/config.toml` from `require_ci = false` to `ci = "release"` (Q4) and tell them the repos `needs_command` listed (R21).

## Build notes from the plan review (round 1 GO)

These amend the tasks above; where they conflict, these win.

- N1 (order). Task 1 keeps a derived `cfg["build"]["require_ci"]` (true when `ci == "pr"`) so commands.py and owneractions.py keep working until Tasks 4 to 6 replace every read. Task 6 removes the derived key. Each task's "full suite" step must pass.
- N2 (D9). `relay test` reports an existing pass and needs `--again` to rerun; `--wait` is refused in an agent session. Both are deliberate refinements of spec D9 (spec review note) and the README says so.
- N3 (R13). The runner calls `cleanup` before `run`, not after. Add tests: a leftover worktree under `test-runs/` whose key is not live is removed; a live run's record survives cleanup.
- N4 (R16). Add asks tests for stopped, not run and no test command.
- N5 (waiting_on_owner). While tests run, no merge ask is emitted and the row is not waiting on the owner; the dashboard still shows the feature with a disabled merge reason from `merge_readiness`. Test it through `status.with_asks`.
- N6 (R17). The runner's ready-to-merge notification on a pass also checks the feature's build GO is fresh for the tested code (`freshness.check` against the state on `origin/<branch>`, at the tested commit); a stale GO sends nothing.
- N7 (R17 order). In `review_current`, read the test record after `c.save` publishes the GO.
- N8 (R7). `ready_draft` calls `cimode.require_command` first in local mode, so a draft is not marked ready before submit refuses.
- N9 (test hygiene). Tests that spawn runners set `RELAY_CONFIG`, `RELAY_HOME` and `RELAY_NOTIFY_BIN` (or the repo's existing notify override) to temp values before `start`, and stop live `sleep` runners in `tearDown`.
- N10 (snapshot). In local mode the feature detail view in `relaylib/ui/snapshot.py` shows the tests flag instead of the GitHub CI result.
- N11 (scan cost). `status.tests_flag` is computed only for build-stage features that are not done, and reuses a `pr_info` already fetched for the row.
- N12 (deploycheck). `needs_command` also lists repos whose base config sets `ci` to `local` or `release` with no `test_command`.
- N13 (message). The "this PR has no CI checks" message at commands.py:162 says `set [build] ci = "none"` instead of `require_ci = false`.
- N14 (non-Python check). Before submit, run one local test start against a non-Python repo under the projects root (read-only: a throwaway temp clone) to confirm its runner finds tests under `<common>/relay/test-runs/`; record the result in the PR body. If it finds none, stop and tell the owner.
