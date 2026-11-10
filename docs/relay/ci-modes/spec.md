# ci-modes: spec

## Why

relay's build gate assumes GitHub CI. `relay submit` and every build review call `require_pr_ready` (relaylib/commands.py), which needs the newest completed GitHub result for the PR's code to be green. Merge (`owneractions.merge_readiness`) needs the same. The only escape is `[build] require_ci = false`, and it drops the test gate entirely: with no GitHub result (`none`), the PR merges on the build GO alone.

The owner's portfolio CI policy (2026-10-23) is that tests run locally and GitHub CI runs only on releases. So `require_ci = false` is now the normal case, not the exception: it is set in the owner's global `~/.relay/config.toml` and in the repo configs of countermind (and its five worktree copies), ohsnap, upstream-studio and UpstreamPartners. Today none of those merges is checked against a test run. See idea.md.

Facts checked on 2026-11-09 and 2026-11-10 that shape the design:
- relay-go's full suite (933 tests) took 875 s (14.6 min) locally at load average about 4 on 10 cores, 47 min the day before at load about 250, and about 3.5 min on GitHub CI. Any relay command that waits for the suite would hold an agent's tool call past its 10-minute limit.
- `tests/test_usage.py::test_capture_runtime_and_lock_contention` fails on unchanged develop under heavy load (a known local flake).
- `config.load` merges the global file, then the repo's `docs/relay/config.toml` from the working tree of whichever checkout calls it. Submit runs in the feature's checkout; the dashboard, hub and `relay merge` usually run in the main folder on develop.
- `docs/relay/config.toml` is under the bookkeeping prefix (relaylib/freshness.py), so a change to it does not make a GO stale.
- `status.checkouts` lists every git worktree of every repo, wherever it lives.

## Decisions

Owner answers, 2026-11-10, recorded as asked:
- Q1 Where the test command comes from: from config, a `test_command` under `[build]`, set per repo with an optional global default. relay never reads it from AGENTS.md or other docs.
- Q2 Slow suites: the local run happens in the background and the review starts at once; only merge (and the "ready to merge" view) waits for a green result. A repo's `test_command` may name a fast subset.
- Q3 Migration: `require_ci = true` maps to `pr`; `require_ci = false` maps to an explicit `none`. Nothing silently gains or loses a gate.
- Q4 The owner's global mode after this ships: `release`.

Design decisions:
- D1. **One setting, four modes.** `[build] ci` is one of `pr`, `local`, `release`, `none`. The default (no setting anywhere) is `pr`, today's default behavior.
  - `pr`: GitHub CI on the PR must be green. Exactly today's `require_ci = true`.
  - `local`: relay runs the repo's `test_command` and gates merge on its result. GitHub CI is neither required nor read for the gate.
  - `release`: `pr` for a PR whose base branch is `main`; `local` for any other base.
  - `none`: no test gate. Exactly today's `require_ci = false`, and said so in status (R12).
- D2. **Legacy `require_ci` maps per file.** While loading each config file, a `[build]` table with `require_ci` and no `ci` is read as `ci = "pr"` (true) or `ci = "none"` (false) at that file's level, so a repo file's `require_ci = false` still overrides a global `ci = "release"`. A file with both keys uses `ci`, and relay prints a one-line warning naming the file and saying `require_ci` is ignored there.
- D3. **The gate's settings come from the PR's base, never from the PR.** For every gate decision about a PR (submit, build review, `relay test`, status, merge), the build settings (`ci`, `test_command`, `test_timeout_min`) are the global config merged with the repo config as it is at the tip of the PR's base branch on origin (`origin/<base>:docs/relay/config.toml`, read with `git show` after the fetch the command already does), not the config in the caller's working tree. Every checkout on a machine then agrees on the mode and the command, and a PR cannot weaken its own gate: a PR that changes `docs/relay/config.toml` takes effect for later PRs, after it merges. Other config (roles, limits, review tables) keeps today's loading.
- D4. **The run uses a clean checkout of the PR's code.** relay adds a detached git worktree at the commit under test in `<git common dir>/relay/test-runs/<run id>`, runs `test_command` there with `/bin/sh -c`, and removes the worktree after. The author can keep editing during a run, and the result belongs to exactly that code. A command that needs setup (installing dependencies) includes it.
- D5. **A result is keyed by code and command.** The key is the code commit (`gitops.code_sha` of the commit under test, which passes over relay's bookkeeping commits, as `ci_for_code` does) and the exact `test_command` text from D3. relay's own commits after the run do not change the key; any code change, a merge from the base branch, or a changed command on the base does.
- D6. **Results are local to the machine.** A record (JSON) and its log live in `<git common dir>/relay/tests/`. They are not committed: a background process committing to the feature branch would race the author's commits. This follows the owner's per-workstation principle. Merge on another machine finds no result and says to run `relay test` there (R9). The idea said "records pass or fail with the commit in state.md"; this changes where, for that reason, and keeps what is recorded.
- D7. **Runs always happen in the background.** No relay command waits for a test run except `relay test --wait`, which is for the owner's own terminal. A run is a detached process (its own session, output to the log file), so it outlives the command and any agent tool call that started it.
- D8. **The local gate ensures a run.** Whenever the build gate runs in local mode (build submit, and every build review including `relay review` re-reviewing a stale GO), it checks the PR is open and has HEAD's code, as today, then makes sure a run exists for the key: it starts one in the background unless a `pass` or live `running` record exists. The review then goes on without waiting. After a failure, fixing the code and running whichever of `relay submit` (drafting or changes-requested) or `relay review` (ready-to-merge with a now stale GO) the status allows starts the run for the new code. The build reviewer is not told the test result.
- D9. **`relay test` starts or reports a run.** `relay test` resolves the feature's PR and the D3 settings and starts a background run for the PR head's code, replacing any finished record for that key (this is how a flake, a stopped run, or another machine is handled). If a run for that key is live, it reports it and starts nothing. It prints the record's status, the log path and how to check (`relay test --status`) and returns at once. `relay test --status` prints the record for the current key and starts nothing. `--wait` runs in the foreground until the result. An agent uses `relay test` and `relay test --status`, never `--wait`.
- D10. **One run per feature at a time.** Starting a run for a new key stops an older live run of the same feature (its process group is ended and its record marked `stopped`).
- D11. **Liveness is a held lock, not a process id.** The run process holds an exclusive `fcntl` lock on its record's lock file for as long as it runs. A `running` record whose lock can be taken is dead and reads as `stopped`. A reused process id cannot fool this. Records are written to a temporary file and renamed into place, so a reader never sees a half-written record.
- D12. **Workflow warning is a text check of the `pull_request` trigger.** relay-go uses the standard library only, so there is no YAML parser. At a local-mode build submit, relay reads `.github/workflows/*.yml` and `*.yaml` at HEAD and finds each file's `pull_request` trigger: a `pull_request` key under `on:` (its block is the lines indented deeper than it), or `pull_request` in an inline `on:` list. It warns about each file whose trigger has no `branches:` line inside that block, or lists the PR's base branch there, or is in an inline list. A `branches:` under `push` does not count. False positives are acceptable: it is a warning, never a refusal. relay-go's own `test.yml` warns.
- D13. **The build review prompt says where the tests run.** In local mode the build prompt's CI line is replaced by one saying the gate is a local test run that relay tracks separately, so the reviewer does not judge GitHub CI. Nothing else in the prompt changes.

## Requirements

Config
- R1. `config.load` accepts `[build] ci` with the four values in D1 and refuses any other value with a message naming the file and the allowed values. The default is `pr`.
- R2. `config.load` applies D2. A non-boolean `require_ci` is refused with a message naming the file. When a file has both `ci` and `require_ci`, the warning prints once per process.
- R3. `[build] test_command` is a string (default empty). `[build] test_timeout_min` is a positive number (default 60). Either may be set globally or per repo. A non-string `test_command`, or a `test_timeout_min` that is not a number greater than zero, is refused with a message naming the file.
- R4. One helper takes the repo and the PR's base branch and returns the D3 build settings with the mode resolved to `pr`, `local` or `none` (resolving `release` from the base). Every place that reads `cfg["build"]["require_ci"]` today uses it instead. `relay roles` (which prints `require_ci` today) prints `ci <mode>`, the test command or "no test command", and the timeout, from the caller's working-tree config, with a note that PR gates use the base branch's config.

The gate by mode
- R5. `pr` mode: submit, every build review and merge behave exactly as with `require_ci = true` today, including ci-on-submit's draft handling, ci-skip-bookkeeping and the build prompt.
- R6. `none` mode: exactly as with `require_ci = false` today.
- R7. `local` mode with an empty `test_command`: build submit, a build review and `relay test` stop before recording anything with the message "set [build] test_command in docs/relay/config.toml on <base>, or set ci = \"none\"". `require_pr_ready` in local mode checks the PR is open and has HEAD's code and does not read GitHub CI.
- R8. Local mode with a command: the gate applies D8 (and D12 at submit); `ready_draft` marks a draft PR ready, as it does today for a PR without CI.
- R9. `release` resolves per PR from the PR's base branch, read when the gate runs, so retargeting a PR changes the mode.

Merge
- R10. `merge_readiness` in local mode needs a `pass` record for the key of the commit being merged. Otherwise it refuses with one of: "tests are running for <sha8> (started <time>)", "tests failed for <sha8>; see <log path>", "tests stopped without a result for <sha8>; run `relay test`", "no local test result for <sha8> on this machine; run `relay test`", or R7's message when the command is empty. The same rule applies to `relay merge` and to merges from the hub and dashboard, which share `merge_readiness`.

Runs and records
- R11. A record holds: the key (code sha, command), the commit tested, status (`running`, `pass`, `fail`, `stopped`), exit code, start and finish times, the feature slug, the log path and the lock file path. Liveness follows D11.
- R12. A run past `test_timeout_min` is ended (its process group) and recorded as `fail` with "timed out after <n> min". A run whose worktree cannot be created (disk full, a git lock) is recorded as `fail` with the git error in its log, so the gate never passes on a run that never ran.
- R13. Cleanup: at the start of each run relay removes worktrees under `relay/test-runs/` that no live run holds, and runs `git worktree prune`. It keeps the newest 20 logs per repo and deletes older logs with their records, but never a live run's record, nor the newest record of any feature whose state on origin is not `done`.
- R14. `status.checkouts`, and so `relay status` and the dashboard, skip worktrees under `<git common dir>/relay/test-runs/`.

Visibility
- R15. `relay status` and the dashboard show one tests flag per build-stage feature with a PR, for the current key only: `tests: local, green at <sha8>`, `tests: local, running since <time>`, `tests: local, failed at <sha8>`, `tests: local, stopped at <sha8>`, `tests: local, not run for <sha8>`, `tests: local, no test command`, `tests: GitHub CI` (pr mode), `tests: not gated (ci = none)`.
- R16. A feature with a build GO whose tests are not green for the current code is not shown as "Merge PR #n" in the inbox. A failed run shows "Tests failed at <sha8>: fix, push and `relay review`"; a stopped or missing run shows "Run tests: `relay test`"; a missing command shows R7's message; a live run shows the merge item disabled with "tests running".
- R17. Notifications (when `[notify] enabled`): a run ending in `fail` sends a "needs you" notification naming the repo, feature and log path. The existing ready-to-merge notification for a local-mode feature is sent only once both the GO and a `pass` for the current key exist: on the GO when tests already passed, or when a run passes after the GO.
- R18. D13: the build prompt gets the resolved mode, and in local mode its CI line reads that the gate is a local test run tracked by relay and not to judge GitHub CI.

Docs and rollout
- R19. README documents `ci`, `test_command`, `test_timeout_min`, the modes, D3's base-branch rule, `relay test` and its flags, where records and logs live, and the `require_ci` mapping. `config.example.toml` shows `ci` instead of `require_ci`. The relay-build skill tells the agent to use `relay test` and `relay test --status` and never `--wait`.
- R20. relay-go's own `docs/relay/config.toml` sets `test_command = "python3.11 -m unittest discover -s tests -t ."` and `test_timeout_min = 90` (the full suite: it runs in the background, and 47 minutes under load has been seen). Under D3 this takes effect for PRs after this one merges. The owner may later name a subset there.
- R21. After merge and deploy, the owner's global config is changed from `require_ci = false` to `ci = "release"` (owner decision Q4). Repo configs with `require_ci = false` keep `none` through D2 until the owner changes them. Repos with no repo config (for example chain-rules and bottomsup) then need a `test_command` on their base branch, or get R7's message. The deploy step lists those repos so the owner can add one.

Tests
- R22. Unit tests cover config parsing, the D2 mapping and each refusal in R1 to R3; the settings helper reading the base branch's config and resolving `release` per base; record keying (a bookkeeping commit keeps the result; a code change or a changed base command does not); D11 liveness and atomic writes; the timeout; cleanup and pruning rules; each merge refusal in R10; D8 starting a run from both submit and `relay review`; `relay test` and `--status`; the D12 warning, including a `branches:` under `push` only; each status flag in R15; and R17's notification timing. Integration tests use the fake binaries and temp repos from tests/helpers.py and a `test_command` that is a small shell command (`true`, `false`, `sleep`); no test runs a real suite.

## Failure paths

- No `test_command` in local mode: submit, review and `relay test` stop before recording anything (R7); merge refuses with the same message (R10).
- The command fails: record `fail`, notification (R17), status and inbox say so (R15, R16). The author fixes and pushes, then runs `relay review` (GO already given, now stale) or `relay submit` (still drafting or changes-requested); either starts a run for the new code (D8).
- A flaky failure on unchanged code: `relay test` starts a new run for the same key (D9).
- The machine sleeps or restarts mid-run: the lock is gone, the record reads `stopped` (D11); `relay test` reruns, and the leftover worktree is removed at the next run (R13).
- The worktree cannot be created: `fail` with the git error (R12).
- Code changes during a run: D10 stops the older run.
- Merge from another machine: "no local test result ... on this machine; run `relay test`" (R10).
- A PR changes `test_command` or `ci` in its own repo config: no effect on its own gate (D3).
- A change to the base branch's command after a pass: the key no longer matches, so merge refuses until a new run passes.
- An invalid `ci`, `require_ci`, `test_command` or `test_timeout_min`: every relay command that loads that config fails with R1 to R3's message, as an invalid `skip_ci` does today.
- `release` mode and a PR into main with no GitHub CI: the `pr` rules apply, which stop with today's "this PR has no CI checks" message.

## Non-goals

- No remote or shared test runner, and no sharing results between machines.
- No reading test commands from AGENTS.md or any other doc.
- No editing of a repo's GitHub workflow files; D12 only warns.
- No change to how build reviews are run; the reviewer's prompt changes only as D13 says.
- No automatic retry of failed runs and no flake detection.
- No change to `skip_ci` or ci-skip-bookkeeping.

## Open questions
