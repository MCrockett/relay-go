# merge-and-tidy: plan

Spec: `docs/relay/merge-and-tidy/spec.md` (GO from codex, round 2). Build in the worktree
`.claude/worktrees/merge-and-tidy`; the main relay-go folder stays on `develop` because it is the live relay.

Every task is test first: write the failing test, run it, implement, run the full suite
(`python3.11 -m unittest discover -s tests -t . -v`), commit with `<type>: summary`. Tests use temporary
`RELAY_HOME` and `HOME`, temp git repos with a bare `origin` (`tests/helpers.make_repo`, `clone`) and the fake
`gh` from `tests/helpers.fake_bin`; never real Claude, Codex or GitHub (R11, every task).

## Spec review notes handled here

- Round 2 note on D5 step 3: switching the main checkout can overwrite an ignored file that the base branch
  tracks. Task 5 refuses the switch in that case: before switching, it lists ignored files present in the main
  checkout (`git ls-files --others --ignored --exclude-standard`) and the paths the base tracks
  (`git ls-tree -r --name-only origin/<base>`); any path in both skips steps 3 and 4 and reports the paths.
- Round 2 note on D9 and D11: the "only after `gh pr merge` succeeds" rule applies to `relay merge` and the
  merge buttons. A merge found on GitHub by the resolver (D11) emits its event when the resolver first caches
  it, with no `gh pr merge` involved. Tests in tasks 4 and 6 hold both expectations.

## Tasks

### Task 1: PR resolver and cache (merged.py)

- Files: `relaylib/merged.py`, `tests/test_merged.py`.
- Add `resolve(repo_dir, st)` returning `(outcome, reason)`, outcome one of `open`, `merged`, `closed`,
  `gone`, `unknown` (D11). `gh pr view N --json state`: MERGED, CLOSED, OPEN map directly; a `RelayError`
  whose text contains "Could not resolve to a PullRequest" is `gone`; any other error is `unknown`.
- Gone reason: "PR #N is not in <owner/repo>" (`owneractions.github_repo`; the origin URL when that fails),
  plus "; this feature was copied from <st.repo>" when `st["repo"]` differs from this repo's name as
  `relay new` computes it (the same helper `cmd_new` uses).
- Cache: `true` for merged (unchanged), `{"gone": reason}` for gone, nothing for closed. Readers check
  `is True`. Writes go through a file lock (`~/.relay/merged.lock`, `fcntl.flock`) as well as the thread lock,
  read-modify-write inside it; `resolve` returns a third value, `first`, true only for the call that wrote
  `true` for that key.
- Add `record_merged(repo_dir, pr)` for `relay merge` (D6), returning a warning string or None.
- `is_done` becomes `status == "done" or resolve(...)[0] == "merged"`; it now applies to any feature with a
  `pr`, not only ready-to-merge.
- Tests: each outcome from fake `gh` answers; the copied reason; merged and gone cached, closed asked again;
  an old cache file with `true` values reads as merged; a `{"gone": ...}` entry is not merged; two processes
  (`multiprocessing`) resolving the same merged PR give exactly one `first`; `record_merged` on an unwritable
  `RELAY_HOME` returns the D6 warning.
- Covers: R9, R5.

### Task 2: Merge notice reads only `true` (notices.py)

- Files: `relaylib/notices.py`, `tests/test_hookinstall.py` or the existing notice tests (whichever holds
  `merged_features` tests today).
- `merged_features` keeps a feature only when `cache.get(key) is True`.
- Test: a session owning a feature whose key holds `{"gone": ...}` gets no merge notice; `true` still does.
- Covers: R9.

### Task 3: Events module (events.py)

- Files: `relaylib/events.py` (new), `tests/test_events.py`.
- `emit(kind, repo, slug, stage, pr, text, cause=None)` with kind in `go`, `waiting`, `merged`, `released`.
  Writes `{"command": "event", "event", "repo", "slug", "pr", "text"}` through `hub.log` (it adds `at`), then
  for each `(provider, sid)` in `hub.registered()` except `cause` calls `notes.send(provider, sid, text,
  inbox, prefix="relay event:")`. `inbox` comes from the session's record when the session is running, the
  same way `hub.deliverable` decides it, without its "listed" checks (hub sessions are always targets).
  Returns a list of warnings; never raises.
- Text helpers for D9 wording, including the spec/plan form with no PR (`pr` null, stage named).
- Tests: the log line's fields; a note to each hub session but not to `cause`; posted to a fake inbox socket
  versus queued; the "relay event:" prefix; a log write failure and a notes failure each give a warning and
  no exception; the no-PR wording.
- Covers: R8.

### Task 4: Emit review events after publication (commands.py, reviewjobs.py)

- Files: `relaylib/commands.py`, `relaylib/reviewjobs.py`, `tests/test_commands.py`,
  `tests/test_reviewjobs.py`.
- CLI (`review_current` with `announce=True`): after `c.save(...)` of a build GO (`go`), a stall
  (`waiting`) or a review error that leaves `review-error` (`waiting`), emit only when the save's push
  succeeded. `Ctx.save` returns whether the push went through; a failed push emits nothing.
- Dashboard and hub reviews (`reviewjobs`): register the emit with `WorkCtx.after_publish`, so it runs only
  after the lease push succeeds (ci-on-submit R6); a discarded review emits nothing.
- Resolver discovery: wherever `merged.resolve` reports `first` for a merged PR, emit `merged` or
  `released` (base from the state's PR info when cached, else `gh pr view --json baseRefName`; `merged` when
  it cannot be read). This needs no `gh pr merge`.
- Tests: a CLI build GO emits one `go`; a stall emits `waiting`; a spec-stage stall emits with `pr` null; a
  push failure emits nothing; a dashboard review whose lease push fails emits nothing; a successful one emits
  once; a merge found by `relay status` emits `merged` once across two status runs.
- Covers: R8.

### Task 5: Tidy (tidy.py)

- Files: `relaylib/tidy.py` (new), `tests/test_tidy.py`.
- `run(repo, branch, base, pr_head, cwd)` returns a list of one-line results, never raises for a step
  (D5 steps 1 to 6, F3). Main checkout is the first entry of `git worktree list --porcelain`.
  - Linked worktree on `branch`: removed only when `git status --porcelain --ignored` is empty; otherwise
    kept, listing up to five paths and `git worktree remove --force <path>`.
  - Main checkout on `branch` and `git status --porcelain` empty: switch to `base`, unless an ignored file
    in it is a path `origin/<base>` tracks (round 2 note), then skipped with the paths.
  - Main on `base` and clean: `git pull --ff-only`. Main elsewhere: `git fetch origin <base>:<base>` when
    `base` is not checked out in any worktree.
  - Local `branch` deleted with `git branch -D` only when not checked out anywhere and
    `git merge-base --is-ancestor <tip> <pr_head>`; otherwise kept and reported.
  - `git worktree prune`. A last line when `cwd` was inside a removed worktree, naming the main checkout.
- Tests, each with a temp repo, bare origin and a merged branch: clean linked worktree removed; worktree
  with an uncommitted file, an untracked file, and only an ignored file each kept with paths and the force
  command; main on the feature branch switched and pulled; main with an ignored file that base tracks not
  switched; main on another branch left there and base fast-forwarded; local branch with an unpushed commit
  kept; prune; the cwd line; a failing git step reported, not raised.
- Covers: R4.

### Task 6: Merge with tidy, record and event (owneractions.py)

- Files: `relaylib/owneractions.py`, `tests/test_owneractions.py`.
- `merge(repo, slug, seen, relayed_by=None, source="hub", cause=None)`: unchanged readiness and `gh pr merge`
  (D3, D4); the relayed comment says "from the relay hub" or "from a session" by `source`; then
  `merged.record_merged` (D6 warning on failure), `tidy.run`, and `events.emit` (`released` when the base is
  `main`, else `merged`; text lists what tidy left). Returns `{"message", "tidy": [...], "warning"}`.
- Tests: refusals per `merge_readiness` make no `gh pr merge` call (fake `gh` records calls); a failed
  `gh pr merge` tidies nothing and emits nothing (F2); success records the cache, tidies, and emits one
  event; base `main` emits `released`; a failed comment and an unwritable cache are warnings with the merge
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
- Tests: by number and by slug; unknown PR and slug; agent without `--relayed` and `--relayed` outside an
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

- Files: `relaylib/status.py`, `relaylib/ui/snapshot.py`, `relaylib/ui/page.html`, `relaylib/hub.py`,
  `tests/test_status.py`, `tests/test_ui_snapshot.py`, `tests/test_hub.py`.
- `scan` resolves each row with a `pr` and drops `merged`, `closed` and `gone` from the rows; it returns the
  hidden ones with their reasons. `relay status` prints "Hidden: <repo>/<slug>: <reason>" for closed and gone,
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
  go, hidden features and how to undo a wrong `gone` (remove the entry from `~/.relay/merged.json`, F7).
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
| R8 | 3, 4, 6, 8 |
| R9 | 1, 2 |
| R10 | 9 |
| R11 | every task |
