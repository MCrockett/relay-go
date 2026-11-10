# ci-modes: spec

## Why

relay's build gate assumes GitHub CI. `relay submit` and every build review call `require_pr_ready` (relaylib/commands.py), which needs the newest completed GitHub result for the PR's code to be green. Merge (`owneractions.merge_readiness`) needs the same. The only escape is `[build] require_ci = false`, and it drops the test gate entirely: with no GitHub result (`none`), the PR merges on the build GO alone.

The owner's portfolio CI policy (2026-10-23) is that tests run locally and GitHub CI runs only on releases. So `require_ci = false` is now the normal case, not the exception: it is set in the owner's global `~/.relay/config.toml` and in the repo configs of countermind (and its five worktree copies), ohsnap, upstream-studio and UpstreamPartners. Today none of those merges is checked against a test run. See idea.md.

Facts checked on 2026-11-09 and 2026-11-10 that shape the design:
- relay-go's full suite (933 tests) took 875 s (14.6 min) locally at load average about 4 on 10 cores, 47 min the day before at load about 250, and about 3.5 min on GitHub CI. A submit that waits for the suite would hold the agent's tool call past its 10-minute limit.
- `tests/test_usage.py::test_capture_runtime_and_lock_contention` fails on unchanged develop under heavy load (a known local flake).
- Config loading (`config.load`) merges the global file, then the repo's `docs/relay/config.toml`, key by key.

## Decisions

Owner answers, 2026-11-10, recorded as asked:
- Q1 Where the test command comes from: from config, a `test_command` under `[build]`, set per repo with an optional global default. relay never reads it from AGENTS.md or other docs.
- Q2 Slow suites: submit starts the local run in the background and the review starts at once; only merge (and the "ready to merge" view) waits for a green result. A repo's `test_command` may name a fast subset.
- Q3 Migration: `require_ci = true` maps to `pr`; `require_ci = false` maps to an explicit `none`. Nothing silently gains or loses a gate.
- Q4 The owner's global mode after this ships: `release`.

Design decisions:
- D1. **One setting, four modes.** `[build] ci` is one of `pr`, `local`, `release`, `none`. The default (no setting anywhere) is `pr`, today's default behavior.
  - `pr`: GitHub CI on the PR must be green. Exactly today's `require_ci = true`.
  - `local`: relay runs the repo's `test_command` and gates merge on its result. GitHub CI is neither required nor read for the gate.
  - `release`: `pr` for a PR whose base branch is `main`; `local` for any other base.
  - `none`: no test gate. Exactly today's `require_ci = false`, and said so in status (R12).
- D2. **Legacy `require_ci` maps per file.** While loading each config file, a `[build]` table with `require_ci` and no `ci` is read as `ci = "pr"` (true) or `ci = "none"` (false) at that file's level, so a repo file's `require_ci = false` still overrides a global `ci = "release"`. A file with both keys uses `ci` and relay prints a one-line warning naming the file and saying `require_ci` is ignored there.
- D3. **The run uses a clean checkout of the PR's code, not the author's working tree.** relay adds a detached git worktree at the commit under test, runs `test_command` there with `/bin/sh -c`, and removes the worktree after. The author can keep editing during a background run, and the result belongs to exactly that code. A command that needs setup (installing dependencies) includes it.
- D4. **A result is keyed by code, not by commit.** The key is the repo, the code commit (`gitops.code_sha` of the commit under test, which passes over relay's bookkeeping commits, as `ci_for_code` does) and the exact `test_command` text. relay's own commits after the run do not invalidate it; any code change, a merge from the base branch, or a changed command does.
- D5. **Results are local to the machine.** A result record (JSON) and its log live under the repo's git common directory, in `relay/tests/`. They are not committed: a background process committing to the feature branch would race the author's commits. This follows the owner's per-workstation principle. Merge on another machine finds no result and says to run `relay test` there (R9). The idea said "records pass or fail with the commit in state.md"; this changes where, for the reason above, and keeps what is recorded.
- D6. **One run per feature at a time.** Starting a run for new code stops an older run of the same feature that is still going (its process group is ended and its record marked `stopped`). A second start for the same key while a run is live does nothing and reports the live run.
- D7. **Submit starts the run; the review does not wait for it.** In `local` mode (and `release` with a non-main base), build submit does what it does today for a PR without CI (`ready_draft` marks a draft ready so it can be merged later), then starts the background run unless a `pass` or live `running` record already exists for the key, then goes on to the review in the same call. The build reviewer is not told the test result.
- D8. **A new command, `relay test`.** It runs the current feature's `test_command` in the foreground on the PR head's code (HEAD when there is no PR) and writes the same record. It is how an owner or agent reruns after a failure, a flake, a stopped run or on another machine. `relay test --status` prints the record for the current code without running anything.
- D9. **Workflow warning is a text check.** relay-go uses the standard library only, so no YAML parser. At build submit in `local` mode or `release` with a non-main base, relay reads `.github/workflows/*.yml` and `*.yaml` at HEAD and warns, once per submit, about each file that names `pull_request` and either has no `branches:` key or lists the PR's base branch. False positives are acceptable: it is a warning, never a refusal.

## Requirements

Config
- R1. `config.load` accepts `[build] ci` with the four values in D1 and refuses any other value with a message naming the file and the allowed values. The default is `pr`.
- R2. `config.load` applies D2: legacy `require_ci` maps per file, `ci` wins over `require_ci` in the same file, and that case prints the warning once per process.
- R3. `[build] test_command` is a string (default empty). `[build] test_timeout_min` is a positive number (default 60). Either may be set globally or per repo.
- R4. Code that reads `cfg["build"]["require_ci"]` today reads the resolved mode instead, through one helper that takes the config and the PR's base branch and returns `pr`, `local` or `none` (resolving `release`). `relay roles` (the line that prints `require_ci` today) prints `ci <mode>`, the test command or "no test command", and the timeout.

The gate by mode
- R5. `pr` mode: submit, every build review and merge behave exactly as with `require_ci = true` today, including ci-on-submit's draft handling and ci-skip-bookkeeping.
- R6. `none` mode: exactly as with `require_ci = false` today.
- R7. `local` mode at build submit: if `test_command` is empty, submit stops before recording anything with a message to set `[build] test_command` in docs/relay/config.toml or set `ci = "none"`. Otherwise it applies D7 and D9. `require_pr_ready` in `local` mode checks the PR is open and has HEAD's code, and does not read GitHub CI.
- R8. `release` mode resolves per PR from the PR's base branch, read when the gate runs (so retargeting a PR changes the mode).

Merge
- R9. `merge_readiness` in `local` mode needs a `pass` record for the key of the commit being merged (D4). Otherwise it refuses with one of: "tests are running for <sha8> (started <time>)", "tests failed for <sha8>; see <log path>", "tests stopped without a result for <sha8>; run `relay test`", or "no local test result for <sha8> on this machine; run `relay test`". The same rule applies to `relay merge` and to merges from the hub and dashboard, which share `merge_readiness`.

Runs and records
- R10. A record holds: key fields (code sha, command), the commit tested, status (`running`, `pass`, `fail`, `stopped`), exit code, start and finish times, the process id while running, and the log path. A `running` record whose process is gone is read as `stopped` (machine slept, restarted or the process was killed).
- R11. The background run is a detached process (its own session, output to the log file), so it outlives the submit's process and the agent's tool call. A run past `test_timeout_min` is ended and recorded as `fail` with "timed out after <n> min" in the record. relay keeps the newest 20 logs per repo and deletes older ones with their records, never one for a live run.

Visibility
- R12. `relay status` and the dashboard show one tests flag per build-stage feature with a PR: `tests: local, green at <sha8>`, `tests: local, running since <time>`, `tests: local, failed at <sha8>`, `tests: local, not run for <sha8>`, `tests: GitHub CI` for `pr` mode, `tests: not gated (ci = none)` for `none`. Only the current code's record counts.
- R13. A feature whose build has a GO but whose tests failed, stopped or never ran for the current code is not shown as "Merge PR #n" in the inbox. It is shown as "Tests failed: fix and `relay submit`" for a failure, and "Run tests: `relay test`" otherwise. A live run shows the merge item disabled with "tests running".
- R14. When a background run ends in `fail`, relay sends the same macOS notification it uses for other "needs you" events (when `[notify] enabled`), naming the repo, feature and log path.

Docs and rollout
- R15. README documents `ci`, `test_command`, `test_timeout_min`, the modes, `relay test`, where records and logs live, and the `require_ci` mapping. `config.example.toml` shows `ci` instead of `require_ci`.
- R16. relay-go's own `docs/relay/config.toml` sets `test_command = "python3.11 -m unittest discover -s tests -t ."` and `test_timeout_min = 90` (the full suite: it runs in the background, and 47 minutes under load has been seen). The owner may later name a subset there.
- R17. After merge and deploy, the owner's global config is changed from `require_ci = false` to `ci = "release"` (owner decision Q4). Repo configs with `require_ci = false` keep `none` through D2 until the owner changes them. Repos with no repo config (for example relay-go, chain-rules, bottomsup) then need a `test_command`, or get R7's message at their next build submit. The deploy step lists those repos so the owner can add one.

Tests
- R18. Unit tests cover config parsing and the D2 mapping, the mode helper including `release` per base branch, record keying (a bookkeeping commit keeps the result; a code change or a changed command does not), stale `running` records, the timeout, log pruning, each merge refusal in R9, the workflow warning, and each status flag in R12. Integration tests use the fake binaries and temp repos from tests/helpers.py and a `test_command` that is a small shell command (`true`, `false`, `sleep`); no test runs a real suite.

## Failure paths

- No `test_command` in `local` mode: submit stops before recording anything (R7); merge refuses (R9, "no local test result").
- The command fails: record `fail`, notification (R14), status and inbox say so (R12, R13); the author fixes, pushes and submits, which starts a new run for the new code.
- A flaky failure on unchanged code: the owner or agent runs `relay test`, which overwrites the record for the same key.
- The machine sleeps or restarts mid-run: the record reads as `stopped` (R10); `relay test` reruns.
- The worktree cannot be created (disk full, a git lock): the run is recorded as `fail` with the git error in the log, so the gate never passes on a run that never ran.
- Code changes during a run: D6 stops the older run; its record is `stopped` and no longer matches the current key anyway.
- Merge from another machine: R9 "no local test result ... on this machine".
- A config with an unknown `ci` value: every relay command that loads config fails with R1's message, as an invalid `skip_ci` does today.
- `release` mode and a PR into main with no GitHub CI: the `pr` rules apply, which stop with today's "this PR has no CI checks" message.

## Non-goals

- No remote or shared test runner, and no sharing results between machines.
- No reading test commands from AGENTS.md or any other doc.
- No editing of a repo's GitHub workflow files; D9 only warns.
- No change to how build reviews are run or to what the reviewer sees.
- No retry of failed runs and no flake detection.
- No change to `skip_ci` or ci-skip-bookkeeping.

## Open questions
