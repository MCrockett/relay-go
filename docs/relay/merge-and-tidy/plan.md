# merge-and-tidy: plan

Spec: `docs/relay/merge-and-tidy/spec.md` (GO from codex, round 2). Build in the worktree
`.claude/worktrees/merge-and-tidy`; the main relay-go folder stays on `develop` because it is the live relay.

Every task is test first: write the failing test, run it, implement, run the full suite
(`python3.11 -m unittest discover -s tests -t . -v`), commit with `<type>: summary`. Tests use temporary
`RELAY_HOME` and `HOME`, temp git repos with a bare `origin` (`tests/helpers.make_repo`, `clone`) and the fake
`gh` from `tests/helpers.fake_bin`; never real Claude, Codex or GitHub (R11, every task).

## Spec review notes handled here

- Round 2 note on D5 step 3: switching or pulling the main checkout can overwrite an ignored file that the
  target tracks. Task 5 guards both moves against the actual target with `ignored_collisions` (defined there)
  and skips the move, reporting the paths, when anything would be overwritten.
- Round 2 note on D9 and D11: the "only after `gh pr merge` succeeds" rule applies to `relay merge` and the
  merge buttons. A merge found on GitHub by the resolver (D11) emits its event when the resolver marks it
  merged, with no `gh pr merge` involved. Both go through one claim (task 1), so a PR gets exactly one event.

## Event claim and ordering (plan review round 1)

- **One claim.** `merged.mark_merged(key)` writes `true` for the PR under the merged-cache file lock and
  returns True only for the call that changed it from not-`true` to `true`. Every path that learns of a merge
  calls it: the resolver when `gh` says MERGED (from any caller, including `is_done`, status and the
  snapshot), and `owneractions.merge` after `gh pr merge` succeeds. Only a call that gets True queues the
  `merged` or `released` event. A PR already cached `true` before this feature never claims, so upgrading
  does not announce old merges. If the cache cannot be written, `owneractions.merge` queues its event anyway
  (with the D6 warning), since it knows it just merged; the resolver queues nothing (it will try again).
- **Ordering (D10).** Code that decides an event calls `events.queue(...)`, which only records it in the
  current operation's batch (see the queue ownership note below). The operation's `batch.flush()` delivers
  them (hub log, then notes): `commands.main` after the command has printed its result, in a `finally`; the
  dashboard on the request or job thread after the reply is sent; the hub-run review and merge, which run
  inside `commands.main`, after printing.
  So the result is always shown before any delivery wait.

## Build order and plan review round 2 notes

- Build order: task 3 (events) first, then 1, 2, 4 to 10. Task 1 uses `events.queue`.
- Queue ownership: `events.queue` appends to a queue owned by the current operation, not the process. Each
  operation opens one with `with events.collect() as batch:` (a `contextvars.ContextVar`, so each dashboard
  request thread, review job thread and snapshot worker has its own) and calls `batch.flush()` after it has
  reported its result. `queue` outside any `collect` delivers at once (no result to wait for). One
  operation's flush never drains another's events. Test: two threads each collecting, one flushing, the
  other's events still queued.
- Task 4 also touches `relaylib/ui/snapshot.py` and `tests/test_ui_snapshot.py` (flush after each refresh).
- Extra tests (task 4): the fallback GO's wording names the pending confirmation; `review-error` emits
  `waiting`; a plan-stage stall emits with `pr` null; the CLI `relay submit` path and a hub-run review
  (`relay hub act <n> review`) each emit once; across two competing processes exactly one merge event is
  delivered to the hub log (count the log lines, not queued events).

## Tasks

### Task 1: PR resolver and cache (merged.py)

- Files: `relaylib/merged.py`, `tests/test_merged.py`.
- Add `resolve(repo_dir, st)` returning exactly `(outcome, reason)`, outcome one of `open`, `merged`,
  `closed`, `gone`, `unknown` (D11), `reason` None except for gone and closed. `gh pr view N --json
  state,baseRefName` in one call: MERGED, CLOSED, OPEN map directly; a `RelayError`
  whose text contains "Could not resolve to a PullRequest" is `gone`; any other error is `unknown`.
- Gone reason: "PR #N is not in <owner/repo>" (`owneractions.github_repo`; the origin URL when that fails),
  plus "; this feature was copied from <st.repo>" when `st["repo"]` differs from this repo's name as
  `relay new` computes it (the same helper `cmd_new` uses).
- Cache: `true` for merged in `merged.json` (unchanged), gone reasons in `prgone.json` (see Deployment),
  nothing for closed. Readers check
  `is True`. Writes go through a file lock (`~/.relay/merged.lock`, `fcntl.flock`) as well as the thread lock,
  read-modify-write inside it.
- `mark_merged(repo_dir, pr)` is the one claim (see "Event claim and ordering"): returns `(claimed,
  warning)`. When `resolve` sees MERGED it calls `mark_merged` and, if claimed, `events.queue`s `released`
  (base `main`, from the same `gh` answer) or `merged`.

- `is_done` becomes `status == "done" or resolve(...)[0] == "merged"`; it now applies to any feature with a
  `pr`, not only ready-to-merge.
- Tests: each outcome from fake `gh` answers; the copied reason; merged and gone cached, closed asked again;
  an old cache file with `true` values reads as merged; a `{"gone": ...}` entry is not merged; two processes
  (`multiprocessing`) resolving the same merged PR queue exactly one event between them; `is_done` and
  `status.scan` discovering a merge queue the event (the claim is not lost behind `is_done`); a PR already
  cached `true` queues nothing; `mark_merged` on an unwritable `RELAY_HOME` returns the D6 warning and not
  claimed; a release is labelled from the same `gh` answer.
- Covers: R9, R5.

### Task 2: Merge notice reads only `true` (notices.py)

- Files: `relaylib/notices.py`, `tests/test_hookinstall.py` or the existing notice tests (whichever holds
  `merged_features` tests today).
- `merged_features` keeps a feature only when `cache.get(key) is True`.
- Test: a session owning a feature whose key holds `{"gone": ...}` gets no merge notice; `true` still does.
- Covers: R9.

### Task 3: Events module (events.py)

- Files: `relaylib/events.py` (new), `tests/test_events.py`.
- `queue(kind, repo, slug, stage, pr, text, cause=None)` records an event in the current operation's batch
  (`events.collect()`, see "Build order and plan review round 2 notes"); `batch.flush()` delivers that batch's
  events and clears it, returning warnings. Kind is `go`, `waiting`, `merged` or
  `released`. Delivery writes `{"command": "event", "event", "repo", "slug", "pr", "text"}` through `hub.log` (it adds `at`), then
  for each `(provider, sid)` in `hub.registered()` except `cause` calls `notes.send(provider, sid, text,
  inbox, prefix="relay event:")`. `inbox` comes from the session's record when the session is running, the
  same way `hub.deliverable` decides it, without its "listed" checks (hub sessions are always targets).
  `flush` never raises.
- Text helpers for D9 wording, including the spec/plan form with no PR (`pr` null, stage named).
- Tests: nothing is delivered before `flush`; the log line's fields; a note to each hub session but not to `cause`; posted to a fake inbox socket
  versus queued; the "relay event:" prefix; a log write failure and a notes failure each give a warning and
  no exception; the no-PR wording.
- Covers: R8.

### Task 4: Emit review events after publication (commands.py, reviewjobs.py)

- Files: `relaylib/commands.py`, `relaylib/reviewjobs.py`, `tests/test_commands.py`,
  `tests/test_reviewjobs.py`.
- `review_current` (the CLI and review jobs alike): after `c.save(...)` of a build GO (`go`), a stall
  (`waiting`) or a review error that leaves `review-error` (`waiting`), the event is queued through
  `c.after_publish(...)`. The CLI's `Ctx.after_publish` runs it only when the save's push reached origin
  (`Ctx.published`), so a failed push queues nothing. `commands.main`
  runs each command inside `events.collect()` and flushes that batch in a `finally` after the command
  printed its result.
- Dashboard and hub reviews (`reviewjobs`): register the `queue` with `WorkCtx.after_publish`, so it runs
  only after the lease push succeeds (ci-on-submit R6); a discarded review queues nothing. The job thread
  flushes after it records its outcome.
- Resolver discovery is handled inside `merged.resolve` (task 1); the dashboard's snapshot refresh flushes
  after each refresh, and `relay status` flushes after printing.
- Tests: a CLI build GO emits one `go`; a stall emits `waiting`; a spec-stage stall emits with `pr` null; a
  push failure emits nothing; a dashboard review whose lease push fails emits nothing; a successful one emits
  once; a merge found by `relay status` emits `merged` once across two status runs, and only after the status
  table is printed (captured output order).
- Covers: R8.

### Task 5: Tidy (tidy.py)

- Files: `relaylib/tidy.py` (new), `tests/test_tidy.py`.
- `run(repo, branch, base, pr_head, cwd)` returns a list of one-line results, never raises for a step
  (D5 steps 1 to 6, F3). Main checkout is the first entry of `git worktree list --porcelain`.
  - Linked worktree on `branch`: removed only when `git status --porcelain --ignored` is empty; otherwise
    kept, listing up to five paths and `git worktree remove --force <path>`.
  - `ignored_collisions(checkout, target)`: the ignored files present (`git ls-files --others --ignored
    --exclude-standard`, file by file) checked against the target tree (`git ls-tree -r --name-only
    <target>`). A collision is an ignored path the target tracks as a file, an ignored path that is a folder
    in the target (a tracked file under it), or an ignored file under a path the target tracks as a file.
  - Main checkout on `branch` and `git status --porcelain` empty: switch to `base`. The target is the local
    `base` when it exists (what `git switch` checks out), else `origin/<base>`. Any collision skips the
    switch and the pull, reporting up to five paths.
  - Main on `base` and clean: `git pull --ff-only`, after the fetch, guarded with `ignored_collisions(main,
    origin/<base>)` the same way. Main elsewhere: `git fetch origin <base>:<base>` when `base` is not checked
    out in any worktree (no files change).
  - Local `branch` deleted with `git branch -D` only when not checked out anywhere and
    `git merge-base --is-ancestor <tip> <pr_head>`; otherwise kept and reported.
  - `git worktree prune`. A last line when `cwd` was inside a removed worktree, naming the main checkout.
- Tests, each with a temp repo, bare origin and a merged branch: clean linked worktree removed; worktree
  with an uncommitted file, an untracked file, and only an ignored file each kept with paths and the force
  command; main on the feature branch switched and pulled; main with an ignored file that the local base
  tracks (local base ahead of origin) not switched and the file's contents unchanged; an ignored file where
  the target has a folder, and an ignored file under a path the target has as a file, each not switched with
  contents unchanged; a pull whose incoming commit tracks an ignored local file not pulled, contents
  unchanged; main on another branch left there and base fast-forwarded; local branch with an unpushed commit
  kept; prune; the cwd line; a failing git step reported, not raised.
- Covers: R4.

### Task 6: Merge with tidy, record and event (owneractions.py)

- Files: `relaylib/owneractions.py`, `tests/test_owneractions.py`.
- `merge(repo, slug, seen, relayed_by=None, source="hub", cause=None)`: unchanged readiness and `gh pr merge`
  (D3, D4); the relayed comment says "from the relay hub" or "from a session" by `source`; then
  `merged.mark_merged` (D6 warning on failure), `tidy.run`, and `events.queue` (`released` when the base is
  `main`, else `merged`; text lists what tidy left) through `events.queue`, only when `mark_merged`
  claimed or could not write (see "Event claim and ordering"). Returns `{"message", "tidy": [...],
  "warning"}`; delivery happens at the caller's flush.
- Tests: refusals per `merge_readiness` make no `gh pr merge` call (fake `gh` records calls); a failed
  `gh pr merge` tidies nothing and queues nothing (F2); success records the cache, tidies, and queues one
  event; a resolver that marked the PR merged first (between `gh pr merge` and the claim) leaves one event in
  total; base `main` emits `released`; a failed comment and an unwritable cache are warnings with the merge
  reported; the comment's source wording.
- Covers: R2, R3, R5, R8.

### Task 7: `relay merge` command (commands.py)

- Files: `relaylib/commands.py`, `tests/test_commands.py` (or a new `tests/test_merge_cmd.py`).
- Subcommand `merge <target> [--relayed] [--by]`. A number is matched against published features' `pr`
  (scan `gitops.remote_branches` as `owneractions.published` does); a slug through `published`. Not found:
  "PR #N is not a relay feature; merge it on GitHub" or "no relay feature <slug>". `owner_or_relayed`
  decides who may run it (D2). `seen` is a fresh `fingerprint`. Calls `owneractions.merge(...,
  source="session", cause=<this session>)`, prints the message, tidy lines and warnings. Exit 0 after a
  merge, non-zero on refusal or `gh` failure (F1, F2, F4).
- Tests: the result lines are printed before any event is delivered; by number and by slug; unknown PR and slug; agent without `--relayed` and `--relayed` outside an
  agent refused; a stale GO refused with no `gh pr merge`; a full run in a temp repo with a worktree ends with
  main on develop, the worktree and branch gone, and exit 0; a busy owner-action lock refused.
- Covers: R1, R2, R4.

### Task 8: Dashboard and hub merge use the same path

- Files: `relaylib/ui/server.py`, `relaylib/ui/page.html`, `relaylib/commands.py` (`cmd_hub_act`),
  `tests/test_ui_server.py`, `tests/test_hub.py`.
- The dashboard route passes `source="dashboard"` (comment only when relayed, so none) and shows the tidy
  lines under the result. `cmd_hub_act` passes `source="hub"` and `cause=<the hub session>` and prints the
  tidy lines.
- Tests: the dashboard merge reply carries tidy lines; `relay hub act <n> merge` tidies and does not send
  the event note back to the hub that ran it.
- Covers: R6, R8.

### Task 9: Hide closed, gone and merged features

- Files: `relaylib/status.py` (holds `scan`, `scan_with_claims`, `render` and `cmd_status`, so no change in
  commands.py), `relaylib/ui/snapshot.py`, `relaylib/ui/page.html`, `relaylib/hub.py`,
  `tests/test_status.py`, `tests/test_ui_snapshot.py`, `tests/test_hub.py`.
- A new `scan_full(root)` returns `(rows, claimed, hidden)`; it resolves each feature with a `pr` and moves
  `merged`, `closed` and `gone` into `hidden` as `{repo, slug, outcome, reason}`. `scan` and
  `scan_with_claims` keep their signatures and return values by wrapping it, so existing callers are
  unchanged; only `cmd_status` and the snapshot use `hidden`. `relay status` prints "Hidden: <repo>/<slug>: <reason>" for closed and gone,
  none for merged (D12). The snapshot carries the hidden list; the page shows a collapsed "Hidden features"
  block; the hub digest leaves them out. `unknown` stays visible with its error (F6).
- Tests: a copied feature (temp repo whose state names another repo, fake `gh` "Could not resolve") is
  hidden with the copied reason; a closed PR hidden with "PR #N was closed without merging"; merged hidden
  with no line; a network error keeps the row; the snapshot's hidden list and the page block's presence; the
  hub digest without them.
- Covers: R10.

### Task 10: Skills and README

- Files: `skills/relay-build/SKILL.md`, `skills/relay-hub/SKILL.md`, `README.md`, `tests/test_public.py` if it
  checks docs wording.
- D8 text in both skills, including that relay now reports GO, waiting and merged events, so agents do not
  send those messages. README: `relay merge`, the tidy steps and what it never forces, events and where they
  go, hidden features and how to undo a wrong `gone` (remove the entry from `~/.relay/prgone.json`, where
  gone entries live, see Deployment; F7). Test (task 1): removing a `prgone.json` entry makes the next resolve
  ask GitHub again and an open PR shows the feature again.
- Test: the existing docs checks (no em-dashes, plain English) pass; a test that the build skill names
  `relay merge` with `--relayed`.
- Covers: R7.

## Coverage

| Requirement | Tasks |
|---|---|
| R1 | 7 |
| R2 | 6, 7 |
| R3 | 6 |
| R4 | 5, 7 |
| R5 | 1, 6 |
| R6 | 8 |
| R7 | 10 |
| R8 | 1, 3, 4, 6, 7, 8 |
| R9 | 1, 2 |
| R10 | 9 |
| R11 | every task |

## Deployment without interrupting sessions (owner, 2026-11-01)

Owner: "build it now, I just want the deployment to not interrupt other working sessions when the time comes".
The live `relay` is the main relay-go folder, so deploying is a pull there. Every hook run is a new process and
picks up new code at its next event; long-running processes (the dashboard, a review in flight) keep old
modules loaded and can lazily import new ones after the pull.

- Old code must stay correct when it reads files the new code writes. So the `gone` cache lives in its own
  file, `~/.relay/prgone.json`, not in `merged.json`: old `merged.is_done` and old `notices.merged_features`
  treat any truthy `merged.json` value as merged, and would announce a gone PR as merged. `merged.json`
  keeps holding only `true` (and the existing `@at` timestamps). This is a storage detail under spec D11;
  the behavior D11 describes is unchanged.
- Everything else new is additive and ignored by old code: hub log lines with `"command": "event"` (the old
  code only appends to that log), notes with the "relay event:" prefix (delivered by the old hook like any
  note), `merged.lock`.
- New code reads every file old code writes (old `merged.json` entries, old hub log lines).

### Task 11: Deploy check

- Files: `tools/deploy-check` (new, a small Python script, standard library only), `tests/test_deploy_check.py`,
  README (a short "Updating relay" paragraph).
- `tools/deploy-check` prints whether it is safe to pull the live folder now: features `in-review` across
  projects (from `status.scan`), review jobs holding a lock (`reviewjobs.status`), a held owner-action lock,
  and relay processes running (`ps`, excluding itself), each with what to wait for. Exit 0 when nothing is in
  flight. After the pull, the README step is: `relay ui` restart (stop the running dashboard, then
  `relay ui --background`) so the dashboard runs one version; sessions need nothing.
- Tests: in-review feature, running review job and held owner-action lock each reported; a running relay
  command (for example `relay submit`) blocks; the checker's own process and hook calls are not counted; a
  running dashboard does not block (it is idle between requests) and gives the "restart after the pull" note;
  `main` exits 1 when anything blocks, 0 otherwise.
- Covers: owner deployment request (no spec requirement number).
