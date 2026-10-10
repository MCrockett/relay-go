# ci-on-submit Implementation Plan

**Goal:** a build PR runs CI once per submit, on the final commit: the PR stays a draft while the agent works, `relay submit` marks it ready, and it goes back to draft when the work goes back to the agent. Runs GitHub never started, and commits whose jobs were all skipped, stop counting as CI results.

**Architecture:**
- **CI evidence (`gitops`):** `commit_ci_state` learns two new outcomes for one commit, `not-started` and `skipped`. `ci_for_code` passes over both, as it passes over `cancelled`, and returns `not-started` when that is all it found (D5, D6).
- **PR draft calls (`gitops`):** `PR_FIELDS` gains `isDraft`; `pr_ready(root, pr, undo=False)` runs `gh pr ready`.
- **Marking the head (`ciskip`):** `mark_head(root, st, cfg)` makes the empty marked `relay: mark PR ready` commit when D4 needs one.
- **Submit flow (`commands`):** a new step, `ready_draft(c)`, runs before `require_pr_ready` in build submit (D2, D4, D6).
- **Back to draft (`commands`, `reviewjobs`):** a build NO-GO that leaves `changes-requested` converts the PR after the verdict is published: at once from the CLI, after the lease push on the dashboard (D3).
- **`relay commit` (`commands`):** commits and pushes the agent's own edits under `docs/relay/<slug>/` through `ciskip` (D7a).
- **Docs and workflow:** relay-build and relay-plan skills, README, `.github/workflows/test.yml`.

**Tech Stack:** Python 3.11 standard library, `unittest`, temp git repos with a bare `origin`, the fake `gh` from `tests/helpers.py`.

**Spec:** `docs/relay/ci-on-submit/spec.md` (GO in round 3, after an owner-granted extra round). Executors read both.

## Global Constraints

- Python 3.11 standard library only; plain English, no em-dashes.
- Commits `<type>: short summary` with the session's attribution lines.
- Tests set `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME` to temporary folders. They use the fake `gh` (`RELAY_GH_BIN`) and never real Claude, Codex or GitHub.
- A draft conversion never changes a command's outcome (D3, R7): it prints one warning line on failure.

## Spec clarifications

- **One commit's outcome (`commit_ci_state`, used with `cancelled="skip"` by `ci_for_code` only).** In that mode a commit is, in order:
  1. `failing` when any completed check run or the legacy status failed and is not a not-started run;
  2. `pending` when any check is pending;
  3. `not-started` when any check run is not-started;
  4. `cancelled` when any was cancelled;
  5. `skipped` when every check run has conclusion `skipped` and there is no legacy status (`status.total_count == 0`);
  6. otherwise `green` (a skipped job next to a success stays green, as today).
  Today's callers that use the default mode (`cancelled="failing"`) see no change: not-started stays `failing` and all-skipped stays `green` there.
- **Not-started detection (D5.1, R10).** A completed check run with conclusion `failure`, `app.slug == "github-actions"` and `output.annotations_count > 0` triggers one call: `gh api repos/{owner}/{repo}/check-runs/<id>/annotations`. It is not-started when an annotation has `annotation_level == "failure"` and a `message` starting with "The job was not started". If that call fails (RelayError or bad JSON), the run is a real failure. No other check run causes an annotations call.
- **`ci_for_code` results.** It skips candidates whose state is `cancelled`, `skipped` or `not-started`, remembering whether it saw a not-started one. It returns the first `green` or `failing`; else `pending` if any candidate was pending; else `not-started` if any was not-started; else `none`. `pending` outranks `not-started` because a run is still going.
- **Callers of `ci_for_code`:**
  - `require_pr_ready` raises the D6 message for `not-started`: "GitHub did not start CI for the code at <sha8> (a billing or spending-limit problem). Fix it under Settings, Billing and plans, then `gh run rerun <run>` or run `relay submit` again." The run id is not known to `ci_for_code`, so the message says `gh run list --branch <branch>` to find it.
  - `owneractions.merge_readiness` already refuses anything but `green`; its message for `not-started` names billing.
  - The dashboard CI column shows `not started`, styled like `failing`.
  - `ciskip.EVIDENCE` stays `green`, `failing`, `pending`, so `not-started` allows no marker (R9).
- **Precedence at a draft submit (review note 1).** `ready_draft` reads `ci_for_code` for the PR head before anything else. With `not-started` it raises the D6 message and does not mark the PR ready: a new run would hit the same billing block. Once billing is fixed, the next submit marks it ready and the `ready_for_review` run starts by itself, so no rerun is needed.
- **`ready_draft(c)`, step by step,** for build submit with status `drafting` or `changes-requested`. It runs after the PR-open and HEAD-pushed checks, which move from `require_pr_ready` into a small `require_pr_open(c)` that both use:
  1. `info = pr_info`; if `not info["isDraft"]`, return (the normal gate follows).
  2. `ci = ci_for_code(head's code)`.
  3. `not-started`: raise the D6 message.
  4. `green`: `sha = ciskip.mark_head(...)`; if a commit was made, `ciskip.push(root, branch, sha)` (a failed push raises, after D7's unmark). Then `pr_ready`. Return; the normal gate then sees green.
  5. `none` with `require_ci` false: `pr_ready`, return.
  6. Anything else: `pr_ready`, then raise "PR #N is now ready for review and CI has started for <sha8>. Wait for it (`gh pr checks N --watch`), then run `relay submit` again."
  A `pr_ready` failure raises "could not mark PR #N ready: <gh stderr>". Nothing in `state.md` changes before `pr_ready` succeeds (R4).
- **`ciskip.mark_head(root, st, cfg)`.** Returns None, without a commit, when HEAD's message is already marked (`gitops.marked`) or `marker_ok(root, st["branch"], st["pr"], cfg, st["status"])` is False. Otherwise it makes the empty commit without touching the index or working tree: `git commit-tree HEAD^{tree} -p HEAD -m "relay: mark PR ready" -m "[skip ci]"`, then `git update-ref HEAD <new> <old>`. Staged changes stay staged. Returns the new sha.
- **Retries (R15 and review note 2).**
  - The push of `relay: mark PR ready` fails before reaching origin: `ciskip.push` unmarks it (D7), so the next submit sees an unmarked head and makes a new marked commit on top. That is acceptable: both are empty.
  - The push reached origin but the reply was lost: `unmark` sees origin at that sha and leaves it marked. The next submit fetches, finds the head marked, adds nothing, and calls `pr_ready` again.
  - `pr_ready` fails: the head stays marked; the next submit adds nothing and retries.
  - GitHub marked it ready but the reply was lost: the next submit reads `isDraft` false and takes the normal gate.
- **Failing CI at submit (R5).** In `cmd_submit`, if `require_pr_ready` raises for `failing`, relay calls `to_draft(c)` then re-raises. `require_pr_ready` raises a `CiFailing(RelayError)` subclass so the caller can tell; other callers are unchanged.
- **`to_draft(root, pr)`.** Calls `pr_ready(root, pr, undo=True)`; on RelayError prints `relay: warning: could not turn PR #N back into a draft: <error>` to stderr and returns False.
- **Back to draft after a NO-GO (R6).** `review_current`'s NO-GO path, for `stage == "build"` only, after `c.save(...)` and only when `st["status"] == "changes-requested"` (not the stall path), calls `c.after_publish(lambda: to_draft(c.root, st["pr"]))`.
  - `Ctx.save` records whether its push succeeded (`self.published`). `Ctx.after_publish(fn)` runs `fn` at once when the last save was published, else does nothing (the NO-GO is not on origin yet; the next save that publishes is a later command, which handles its own state).
  - `WorkCtx.after_publish(fn)` appends to `self.pending`. `reviewjobs._run` runs `job`'s `c.pending` after the lease push succeeds, and never when the review is discarded (branch moved, stopped).
- **`relay commit "<message>"` (R14).** `cmd_commit(args)`: a blank message is refused ("give a commit message"); `Ctx(args)`, `c.sync()`, `ownership.check_can_write`; `marked = ciskip.commit(c.root, c.slug, c.st, c.cfg, message)`; nothing changed under `docs/relay/<slug>/` refuses ("nothing to commit under docs/relay/<slug>/"); then `ciskip.push(c.root, c.branch, marked)`, a failed push raises. `commit_paths_under` already commits only its paths (pathspec commit), so other staged changes stay staged. It does not touch `state.md` beyond what the agent changed.
- **Fake gh additions (`tests/helpers.py`).** The `FAKE_GH_API` table already answers any argument string by its longest matching key, so tests use keys `pr ready`, `pr ready 7 --undo`, `annotations` and `pr view`. No new variable is needed: `FAKE_GH_LOG` already records every call, in order. Where a test needs `pr ready` to flip `isDraft`, it rewrites the table between calls.

## Review Focus

- `ci_for_code` must never turn a draft's all-skipped head into `green` (Why, second fact).
- Nothing may be recorded in `state.md` before `pr_ready` succeeds.
- The dashboard converts to draft only after its lease push succeeds.
- The default `ci_state` and `commit_ci_state` behaviour for other callers is unchanged.

## Tasks

### Task 1: CI evidence learns not-started and all-skipped

**Files:** `relaylib/gitops.py`, `tests/test_gitops.py`

1. Tests first, in a new `NotStartedSkippedTest`:
   - a failing check run with a not-started annotation reads `not-started` with `cancelled="skip"` and `failing` by default;
   - a failing run without annotations, and one whose annotations call fails, read `failing`, and only failing `github-actions` runs with annotations cause an annotations call (from `FAKE_GH_LOG`);
   - a real failure beside a not-started run reads `failing`;
   - all-skipped reads `skipped` with `cancelled="skip"` and `green` by default; skipped plus success reads `green`; all-skipped plus a legacy status reads by the status;
   - `ci_for_code`: the burned-web case (a not-started run on a docs-only head, green on the code commit below) gives `green`; an all-skipped head with a green older same-code commit gives `green`; an all-skipped head alone gives `none`; a not-started head alone gives `not-started`; a pending candidate beside a not-started one gives `pending`.
2. Implement per the clarifications. Keep `limit` counting asked candidates.
3. `python3.11 -m unittest tests.test_gitops -v` passes.

Covers: R8, R9 (the state), R10, R13

### Task 2: PR draft calls

**Files:** `relaylib/gitops.py`, `tests/test_gitops.py`

1. Tests: `PR_FIELDS` includes `isDraft`; `pr_ready(root, 7)` runs `gh pr ready 7` and `pr_ready(root, 7, undo=True)` runs `gh pr ready 7 --undo` (from `FAKE_GH_LOG`); a failing gh raises RelayError carrying gh's stderr.
2. Implement `pr_ready` like `pr_comment` (bounded by `GH_TIMEOUT_S`; an OSError becomes RelayError).

Covers: R2

### Task 3: mark_head

**Files:** `relaylib/ciskip.py`, `tests/test_ciskip.py`

1. Tests: with an unmarked head and `marker_ok` true, `mark_head` makes one empty commit `relay: mark PR ready` with the marker, the tree is unchanged, and a staged file stays staged and uncommitted; with a marked head it returns None and makes nothing; with `marker_ok` false (for example `skip_ci = "never"`) it returns None and makes nothing.
2. Implement per the clarifications.

Covers: R3 (part), R15 (part)

### Task 4: Submit marks a draft ready

**Files:** `relaylib/commands.py`, `tests/test_commands.py`

1. Tests in a new `DraftSubmitTest` (build stage, PR 7, fake gh table with `pr view` returning `isDraft` and `pr ready`):
   - draft, no result (only skipped runs on the head): `gh pr ready 7` is called, submit exits non-zero with the "CI has started" message, and `state.md`, the round count and the commit count are unchanged;
   - draft, green on the code: one `relay: mark PR ready` commit (marked) is pushed, then `pr ready`, then the submit commit and the review, in one call; the `pr ready` call comes after the push (log order);
   - draft, green, head already marked (after a NO-GO commit): no `relay: mark PR ready` commit;
   - draft, `none`, `require_ci = false`: `pr ready`, then the submit goes on;
   - draft, `not-started`: the D6 message, no `pr ready` call, nothing recorded;
   - `pr ready` failing: the error names the PR and gh's message; status, rounds and history unchanged; a second submit makes no second `relay: mark PR ready` commit and calls `pr ready` again;
   - the mark-ready push rejected (pre-receive hook): submit fails, the commit is unmarked (D7), nothing recorded;
   - the mark-ready push reached origin but the command reported failure (simulate with a push wrapper that pushes then exits 1): the next submit adds no commit and calls `pr ready`;
   - not a draft: no `pr ready` call, behaviour as today;
   - not a draft, CI failing: `pr ready 7 --undo` is called, and the existing "CI is failing" error is raised; when the undo fails, one warning line and the same error.
2. Implement `require_pr_open`, `ready_draft`, `CiFailing`, `to_draft`, and the D6 message.

Covers: R3, R4, R5, R7, R9 (submit message), R13, R15

### Task 5: Back to draft after a build NO-GO

**Files:** `relaylib/commands.py`, `relaylib/reviewjobs.py`, `tests/test_commands.py`, `tests/test_reviewjobs.py`

1. Tests:
   - CLI: a build NO-GO from `relay submit` and from `relay review` calls `pr ready 7 --undo` after the NO-GO commit is on origin (log order against origin's head);
   - a NO-GO that stalls (`waiting-owner`) calls no undo; a spec or plan NO-GO calls no undo;
   - a NO-GO whose push failed (`push="best"` warning) calls no undo;
   - dashboard: a build NO-GO job calls undo after the lease push; a job discarded because the branch moved, or stopped by shutdown, calls none;
   - a failing undo prints one warning and the NO-GO stands.
2. Implement `Ctx.published`, `Ctx.after_publish`, `WorkCtx.after_publish` and `pending`, and the call in `review_current` and `_run`.

Covers: R6, R7, R13

### Task 6: relay commit

**Files:** `relaylib/commands.py`, `tests/test_commands.py`

1. Tests: a plan edit with green CI on origin's code is committed marked and pushed; with code origin lacks, unmarked; a staged change outside `docs/relay/<slug>/` stays staged and uncommitted; a blank message and nothing to commit are refused; another session's feature is refused; a rejected push raises and unmarks.
2. Implement `cmd_commit` and its parser entry (`relay commit MESSAGE [--feature] [--by]`).

Covers: R14, R13

### Task 7: Not-started in merge readiness and the dashboard

**Files:** `relaylib/owneractions.py`, `relaylib/ui/snapshot.py`, `relaylib/ui/page.html` (only if the CI label needs it), `tests/test_owneractions.py`, `tests/test_ui_snapshot.py`

1. Tests: merge readiness with `not-started` refuses with a message naming billing; the snapshot's CI value for a not-started head reads `not started` and is not green.
2. Implement.

Covers: R9

### Task 8: Skills

**Files:** `skills/relay-build/SKILL.md`, `skills/relay-plan/SKILL.md`

1. relay-build step 3: `gh pr create --base develop --fill --draft`, falling back to no `--draft` if GitHub refuses; no "wait for CI" before the first submit. Step 4: if submit says CI started, wait with `gh pr checks <n> --watch`, then submit again; after a NO-GO the PR is back in draft, so fix pushes start no CI. Both skills: commit edits under `docs/relay/<slug>/` with `relay commit "<message>"`, not `git commit`.
2. Check: plain English, no em-dashes (`grep -n "—"` finds nothing).

Covers: R1, R14

### Task 9: relay-go's workflow and README

**Files:** `.github/workflows/test.yml`, `README.md`

1. Workflow per R11. Check it parses: `python3.11 -c` reading it is not possible without a YAML library, so check by eye and by the PR's own CI run.
2. README "CI on relay's commits" section: the draft flow, the not-started and all-skipped rules, `relay commit`, and the workflow snippet from R11 for other repos.

Covers: R11, R12

### Task 10: Full suite and live check

1. `python3.11 -m unittest discover -s tests -t . -v` passes.
2. On this feature's own PR (opened as a draft): pushes while draft start no billed run (the job shows as skipped), and `relay submit` marks it ready and starts one run. Record what happened in Build notes.

Covers: R13 (all), R11 (live)

## Build notes
