# delegated-merge: plan

Spec: `docs/relay/delegated-merge/spec.md` (GO, round 4, requirements R1 to R17, decisions 1 to 8).
Build in the worktree `.claude/worktrees/delegated-merge` on `feat/delegated-merge`; the main relay-go
folder stays on `develop` because it is the live relay. The PR goes into `develop`; the owner merges it.

- **Goal.** The hordle `release` role (`HORDLE_ROLE=release`, no `--relayed`) can run `relay merge` and have
  relay merge the PR without the owner, but only when every D1 condition holds (opt-in list on the base,
  not a control-plane repo, not a production base, a reviewed GO from the owner's table, tests that ran,
  no protected path). relay lands the merge by pushing its own merge commit to the checked base, records it,
  and keeps a live session's worktree.
- **Architecture.** One new module `relaylib/delegated.py` holds the conditions (R3 to R8, one function,
  R9), the review evidence (R6), the merge-and-push mechanics (R10 steps 1, 2, 5, 6) and the bounded wait
  (R10 step 4). `owneractions.merge_delegated` is the orchestrator beside the unchanged `owneractions.merge`.
  `commands.cmd_merge` decides the caller (R1) and the folder (R15). `events`, `merged` and `tidy` each get a
  small addition (R12 to R14). `docs/reference.md` and `README.md` are R16; `acceptance.md` is R17.
- **Tech.** Python 3.11 standard library only; `unittest`; temp git repos with a bare `origin` and the fake
  `gh` from `tests/helpers.py`. No test calls real Claude, Codex or GitHub.
- **Test command.** `python3.11 -m unittest discover -s tests -t . -v`. Current count: 1097 tests; a full run takes about 11 minutes. One fails on this branch before any code
  change (see Task 0).
- **Commits.** `<type>: summary` (feat, fix, test, docs, refactor). One commit per task. Every task is test
  first: write the failing test, run it, implement, run the whole suite, commit.

## Spec conflicts

None that block. Two facts about the code the spec could not know, both worked around here and neither
changing behavior:

1. `owner.worktree` in `state.md` is only the folder's base name (`commands.py:91`,
   `os.path.basename(os.path.normpath(root))`), not a path. R13 says it is "matched against
   `git worktree list`". Task 6 matches by base name **and** by branch: the worktree whose branch is the
   feature branch and whose folder name equals `owner.worktree`. Git allows a branch in only one worktree, so
   the match is unique.
2. R6 says "for an entry that names no model, the model `config.reviewer_model` gives that provider".
   `config.parse_model_spec` refuses an entry without a model, so every entry carries one and that branch
   cannot be reached. Task 9 matches on `spec.model` only and has a test that a model-less entry is not
   in the table's output (it never was). No code for the unreachable branch.

## The review's notes, carried into tasks

Every note in `handoff.md` "Next", settled:

| Note | Decision | Task |
|---|---|---|
| Claim `merged.mark_merged` only on `MERGED` | The delegated path calls `mark_merged` only for the `merged` outcome. `merged.json` is never written for timeout, read-failed, retargeted, head-moved or closed, so such a feature still shows ready to merge (spec.md:143). A test reads `merged.json` after each. | 14, 15 |
| PR comment at once, event after the outcome | Order after a successful push: R11 comment, then the bounded wait, then per outcome: branch delete, claim, tidy, event. The event carries `outcome` and the tidy result, so it is queued last. | 14, 15 |
| One event per PR in the timeout case | The non-merged outcomes call `merged.announce(repo, pr)`, a new claim in a separate file `announced.json` (not `merged.json`: older relay treats any value in that file as merged). They queue the one delegated event. When the status resolver later claims `MERGED`, `_queue_merge_event` finds the PR announced and queues nothing. | 5, 15 |
| Who removes leftovers after a timeout | The feature's session or the owner. Nothing in relay does it later (status only marks merged; `tidy.run` is called only from a merge). The event text and the R14 note list exactly what is left (`tidy.leftovers`): remote branch, worktree, local branch. | 6, 15 |
| R14 note and branch delete for CLOSED and timeout | CLOSED: R14 note sent, no branch delete, no tidy. Timeout: R14 note sent (its text lists the leftovers), no branch delete, no tidy. Read-failed, retargeted and head-moved follow the same line: note sent, no delete, no tidy. | 15 |
| Retry failed reads until the 60 seconds end | `wait_for_merge` keeps the last read error and keeps polling. A later good read clears it. Only a failure still standing when the 60 seconds end gives `read-failed` (exit 1, notification). A single transient `gh` error does not alert the owner. | 12 |
| Step 5 numbered after the push | Built in the order merge commit, re-read of the PR, push. The re-read is the last call before the push. | 11 |
| Delete the branch with a lease on the checked head | `git push origin --force-with-lease=refs/heads/<branch>:<pr_head> :refs/heads/<branch>`. A commit pushed after GitHub marked the PR merged makes the lease refuse; a warning says the branch was kept. A branch already gone (checked with `git ls-remote --heads`) is silent. | 14 |
| Injectable clock or sleep, fake gh with a sequence | `delegated.clock` and `delegated.sleep` are module attributes read at call time; `tests/helpers.FakeClock` replaces both, so the 60 seconds run instantly. The fake `gh` serves `FAKE_GH_AFTER_PUSH` (a list of PR-state overrides) to `pr view` once a bare-origin `post-receive` hook has touched a marker file, so the count of earlier reads does not matter. | 1, 12 |
| spec-3: `github_repo`, not `repo_name`, for R4 | `delegated.control_plane(remote)` takes the string `github_repo` returns and uses the part after `/`. | 7, 13 |
| spec-3: a test for a `ci = "release"` repo | Task 7 test `test_release_mode_base_is_local_for_a_non_main_base`; task 14 test `test_release_repo_merges_with_a_local_pass`. | 7, 14 |

Other decisions the plan makes:

- **Who the cause is.** For a delegated merge the cause is the calling agent session when it can be
  detected (`_hub_session`, lenient: a detection error means no cause), so it is not told its own event.
- **Event kind.** `released` when the base is `main`, else `merged`, for every outcome, as today. The
  `outcome` field says which outcome it was.
- **PR reads for the wait.** `gitops.pr_state` calls `gh` directly (no memo, no merged-PR cache), because
  `gitops.pr_info` caches MERGED forever and a poll must always ask.
- **Pinned base.** The base commit is read once (`origin/<base>`) before `merge_readiness`, and again after
  the conditions. If it moved or the base name changed, the merge refuses `the PR changed while it was
  checked`. The pinned SHA is what R3 and R8 read and what the merge and the lease use.
- **R6 evidence files.** Names matched: `build-<n>[r].<claude|codex>[-<k>].md`; `.error.md` and any other
  name are ignored. The round is `<n>` from the file name; ties are broken by front matter `at`.
- **Draft.** `merge_readiness` stays as it is (the owner's merge is unchanged). The delegated path adds the
  draft check from the PR info that `merge_readiness` returns (`isDraft` is in `gitops.PR_FIELDS`).
- **Refusal wrapper.** A refusal from `merge_readiness` (a plain `RelayError` or a `Conflict`) is re-raised
  with the prefix `not delegated (D1): `, keeping `Conflict.fresh`.

## Baseline

`tests.test_public.PublicTest.test_shipped_files_have_no_machine_paths_or_internal_doc_links` fails today:
`docs/relay/delegated-merge/idea.md:52` links a working doc under the superpowers docs folder, which the test
bans in every file under `docs/relay/`. Task 0 fixes it. Every file this plan adds under `docs/relay/` (the plan
itself, `acceptance.md`) must also avoid absolute machine paths (a home folder or a temp folder) and the banned doc folders; the
hordle design is named by its title and section numbers, not by a path.

## Files

New: `relaylib/delegated.py`, `tests/test_delegated.py`, `tests/test_delegated_helpers.py`,
`docs/relay/delegated-merge/acceptance.md`.
Changed: `relaylib/commands.py`, `relaylib/owneractions.py`, `relaylib/events.py`, `relaylib/merged.py`,
`relaylib/tidy.py`, `relaylib/gitops.py`, `tests/helpers.py`, `tests/test_events.py`, `tests/test_merged.py`,
`tests/test_tidy.py`, `tests/test_merge_cmd.py`, `docs/reference.md`, `README.md`.

## Tasks

### Task 0: clear the baseline failure

Files: `docs/relay/delegated-merge/idea.md`.
Covers: none (keeps the suite green so each later task can be judged by a clean run).

Approach: on line 52, replace the path with the document's title and sections ("the hordle agent team design,
sections 6 (D1), ..."), keeping the rest of the line. No other change to `idea.md`.

Test: `tests.test_public.PublicTest.test_shipped_files_have_no_machine_paths_or_internal_doc_links` passes; the rest
of the suite is unchanged. Commit `docs: keep a working-doc path out of the delegated-merge idea`.

### Task 1: test helpers (fake clock, hooks on origin, fake gh sequence, live session)

Files: `tests/helpers.py`, new `tests/test_delegated_helpers.py`.
Covers: none directly; supports R10 and R13 tests.

Approach:

- `class FakeClock`: `now = 0.0`; `monotonic()` returns `now`; `sleep(seconds)` adds to `now` and appends to
  `self.sleeps`.
- `hook(origin, name, body)`: writes `<origin>/hooks/<name>` with `#!/bin/sh\n` + body, mode 0755.
  `mark_pushes(origin, marker)` is a `post-receive` hook that runs `touch "<marker>"`.
  `reject_pushes(origin, text)` is a `pre-receive` hook that prints `text` to stderr and `exit 1`.
- `live_session(provider, sid, cwd=None, state="working")`: writes a valid record to
  `sessions.record_path(provider, sid)` (fields as `tests/test_hub.py` `record()`: `provider`, `session_id`,
  `state`, `since`, `at` numbers, `event`, `pending: []`, `cwd`; no `process`, so it is never "gone").
- `FAKE_GH`: before the `pr list` branch, add
  ```python
  seq = os.environ.get("FAKE_GH_AFTER_PUSH")  # JSON list of overrides for `pr view` once the marker exists
  if args.startswith("pr view") and seq and os.path.exists(os.environ.get("FAKE_GH_PUSHED") or ""):
      states, counter = json.load(open(seq)), seq + ".n"
      n = int(open(counter).read()) if os.path.exists(counter) else 0
      open(counter, "w").write(str(n + 1))
      entry = states[min(n, len(states) - 1)]       # the last entry repeats
      if entry.get("__rc"):
          sys.stderr.write(entry.get("__err", "gh: could not reach GitHub") + "\n")
          sys.exit(entry["__rc"])
      info = json.load(open(os.environ["FAKE_GH_JSON"]))
      info.update(entry)
      sys.stdout.write(json.dumps(info))
      sys.exit(0)
  ```
  A failing `pr comment` needs no change: `FAKE_GH_API` already matches any argument substring
  (`{"pr comment": {"__rc": 1}}`).

Tests (`tests/test_delegated_helpers.py`):

- `test_fake_clock_advances_on_sleep`: two sleeps of 2 add up to `now == 4.0`, `sleeps == [2, 2]`.
- `test_post_receive_marker_appears_on_push`: `mark_pushes` on a `make_repo` origin; no marker before, marker
  after `git push`.
- `test_reject_pushes_refuses_with_text`: a push to a `reject_pushes` origin fails and its stderr holds the text.
- `test_fake_gh_serves_the_sequence_only_after_the_marker`: with `FAKE_GH_AFTER_PUSH` set to
  `[{"state":"OPEN"},{"state":"MERGED"}]`, `pr view` returns the `FAKE_GH_JSON` content before the marker
  exists; after it, the first call is `OPEN`, the second and every later one `MERGED`; an entry
  `{"__rc":1}` exits 1 with the default error.
- `test_live_session_is_live_for_events`: `events._inbox("claude", sid)` is `None` for a record with no inbox
  and the record exists (liveness itself is tested in task 4); `live_session(..., state="ended")` writes
  the state given.

### Task 2: who is merging (R1)

Files: `relaylib/commands.py`, `tests/test_merge_cmd.py`.
Covers: R1.

Approach:

- In `commands.py`, next to `owner_or_relayed`, add
  ```python
  ROLE_REFUSAL = ("only the release role merges without the owner (D1); a role run cannot pass on the "
                  "owner's word")

  def merge_caller(args, env):
      """('owner'|'relayed'|'delegated', relayed_by) for `relay merge`; refuses anything else before any other work."""
      role = (env.get("HORDLE_ROLE") or "").strip()
      if role:
          if args.relayed or role != "release":
              raise RelayError(ROLE_REFUSAL)
          return "delegated", None
      relayed_by = owner_or_relayed(args, "relay merge")
      return ("relayed" if relayed_by else "owner"), relayed_by
  ```
- `cmd_merge` starts with `kind, relayed_by = merge_caller(args, dict(os.environ))`. The cause is
  `_hub_session(env, args.by)` when `kind == "relayed"`; for `"delegated"` the same call inside
  `try/except RelayError` (a detection failure means no cause); `None` for `"owner"`.
- For `"delegated"`, until task 13 wires the real path, `cmd_merge` raises
  `RelayError("delegated merge is not built yet")` right after the cause is set. Task 13 removes it.
- Update the `--by` help to `claude or codex, when detection is ambiguous`.

Tests (`tests/test_merge_cmd.py`, `MergeCommandTest`; `HORDLE_ROLE` via `mock.patch.dict(os.environ, ...)`):

- `test_owner_terminal_merges_as_today`: markers cleared, no `HORDLE_ROLE`; `merge_caller` gives
  `("owner", None)`; `relay merge 7` exits 0 with one `pr merge` call.
- `test_relayed_agent_merges_as_today`: `--relayed`, no role; `("relayed", "claude session s1")`.
- `test_release_role_without_relayed_is_delegated`: `merge_caller` gives `("delegated", None)` (the unit call, so
  it does not depend on the stub).
- `test_another_role_is_refused`: `HORDLE_ROLE=builder`, no `--relayed`: exit 1, stderr has `only the release
  role merges without the owner (D1); a role run cannot pass on the owner's word`; no `pr merge` call.
- `test_role_with_relayed_is_refused`: `HORDLE_ROLE=release` with `--relayed` and `HORDLE_ROLE=builder` with
  `--relayed`: both exit 1 with the same text; no `pr merge` call.
- `test_blank_role_is_no_role`: `HORDLE_ROLE=" "` behaves as unset (owner terminal case).

### Task 3: `--repo <path>` (R15)

Files: `relaylib/commands.py`, `tests/test_merge_cmd.py`.
Covers: R15.

Approach:

- Parser: `mg.add_argument("--repo", help="merge in this checkout instead of the current folder")`.
- Helper in `commands.py`:
  ```python
  def merge_repo(path):
      if path is None:
          return gitops.repo_root(os.getcwd())
      real = os.path.expanduser(path)
      if not os.path.isdir(real):
          raise RelayError(f"{path} is not a git checkout")
      try:
          return gitops.repo_root(real)
      except RelayError:
          raise RelayError(f"{path} is not a git checkout")
  ```
  `cmd_merge` uses `repo = merge_repo(args.repo)`. It still passes `cwd=os.getcwd()` to the merge, so tidy's
  "the folder you ran this from was removed" line uses the real working folder.
- This runs after `merge_caller` (R1 first) and works for every caller.

Tests (`tests/test_merge_cmd.py`):

- `test_repo_flag_merges_from_another_folder`: `os.chdir(self.tmp)` (not a repo), `relay merge 7 --relayed
  --repo <work>`: exit 0, one `pr merge` call.
- `test_repo_flag_with_a_missing_path_is_refused`: `--repo <tmp>/gone`: exit 1, `<tmp>/gone is not a git
  checkout`, no `pr merge` call.
- `test_repo_flag_with_a_plain_folder_is_refused`: a folder that exists but is not a checkout: same message.
- `test_repo_flag_tidy_uses_the_real_working_folder`: add a linked worktree of `fix/tiny`, `chdir` into it, run
  with `--repo <work>`: output has `the folder you ran this from was removed; continue in`.

### Task 4: events carry the owner, fields and a delegated text (R12, R14)

Files: `relaylib/events.py`, `relaylib/owneractions.py`, `relaylib/merged.py`, `tests/test_events.py`,
`tests/test_merge_cmd.py`.
Covers: R12 (the log line field and the log-only line), R14 (the note to the feature's session).

Approach (`events.py`):

- `owner_of(st)`: `(provider, session)` when `st["owner"]` has a non-empty string `session` and a provider
  in `("claude", "codex")`, else `None`.
- `session_live(provider, session_id)`: the first half of `_inbox` extracted: read the record, `sessions._valid`,
  `leftoff._shown_state(record, sessions.alive(sessions.read_records()))` not in `("ended", "stopped")`;
  returns the record or `None`; any exception gives `None`. `_inbox` becomes
  `(session_live(...) or {}).get("inbox")`. `_inbox` keeps its name so existing tests that patch it still work.
- `event(...)` and `queue(...)` take keyword-only `owner=None, note_extra="", fields=None, log_only=False`
  and store them in the event dict.
- `deliver(ev)`:
  - the hub log line is `{..., "text": ev["text"], **(ev.get("fields") or {})}`;
  - when `ev.get("log_only")`, return after the log line (no notes at all);
  - after the hub loop, `owner = ev.get("owner")`: if set, not equal to `ev.get("cause")`, not in
    `hub.registered()` and `ev["kind"] in ("merged", "released")`, send
    `notes.send(owner[0], owner[1], ev["text"] + (" " + ev["note_extra"] if ev.get("note_extra") else ""),
    _inbox(*owner), prefix=PREFIX)`; a send error or a message becomes a warning
    `event note to <provider> session <id>: ...`, exactly as the hub loop does.
- `merged_text(repo, slug, pr, base, left=(), tidied=False, delegated=False)`: with `delegated` the head reads
  `relay: <label> is released to main, delegated under D1.` or `... is merged into <base>, delegated under
  D1.`; everything after it is unchanged. Add `delegated_text(repo, slug, pr, message, left=())`:
  `relay: <label>: <message>; delegated under D1.` plus ` Not tidied: <left joined by "; ">.` when `left`.

Approach (callers): `owneractions.merge` passes `owner=events.owner_of(st)` to its `events.queue`;
`merged._queue_merge_event` passes `owner=events.owner_of(st)`. No other change to either.

Tests:

- `tests/test_events.py::test_owner_session_gets_the_merged_event_note`: queue `merged` with
  `owner=("claude","f1")`, no hub; the owner's notes log has one queued note with prefix `relay event:` and the text.
- `test_owner_is_not_told_when_it_is_the_cause`: `cause=("claude","f1")`, same owner: no note.
- `test_owner_that_is_a_hub_session_gets_one_note`: `hubs(("claude","f1"))` and owner `f1`: exactly one note.
- `test_owner_note_only_for_merged_and_released`: a `go` event with an owner: no owner note.
- `test_note_extra_goes_to_the_owner_only`: `note_extra="Remove the worktree: x"`; the owner's note ends with it,
  a hub session's note does not.
- `test_fields_land_in_the_hub_log_line`: `fields={"delegated": True, "outcome": "merged"}`; the log line has both.
- `test_log_only_writes_the_log_line_and_no_notes`: hub and owner registered, `log_only=True`: one log line, no notes.
- `test_owner_of_needs_a_provider_and_a_session`: cases for `None`, `{}`, bad provider, good.
- `test_session_live_is_false_for_ended_and_stopped_and_true_for_working`: uses `helpers.live_session`
  with states `working`, `ended`; a `stopped` shown state (record with a dead `process`) is not live.
- `test_delegated_texts`: `merged_text(..., delegated=True)` for base `develop` and `main`; `delegated_text` with and
  without `left`.
- `tests/test_merge_cmd.py::test_owner_merge_tells_the_feature_session`: the feature's owner is
  `claude s1`; run the merge as the owner's terminal (markers cleared, so the cause is `None`): a note for
  `claude s1` is queued with the merged text. `test_relayed_merge_does_not_tell_its_own_session`: relayed by
  `claude s1` (the owner): no note.
- Existing `test_merged.py` resolver tests keep passing; add `test_resolver_event_also_tells_the_owner_session`
  in `tests/test_merged.py`.

### Task 5: one event per PR when the merge is announced but not yet merged (R12)

Files: `relaylib/merged.py`, `tests/test_merged.py`.
Covers: R12 (one event per PR; a late resolver adds no plain second event).

Approach:

- `_announced_path()` is `<relay_home>/announced.json`. `announce(repo_dir, pr)` returns `True` for the call that
  first records the PR (same `_locked()` and `_store` pattern as `mark_merged`; `True` when the cache cannot be
  written, with no exception), `False` when it is already recorded. `announced(repo_dir, pr)` reads it.
  Never write these into `merged.json`.
- `_queue_merge_event(repo_dir, st, base)` returns without queueing when `announced(repo_dir, st["pr"])`.
  `resolve` still marks the PR merged (the claim), only the event is skipped.

Tests (`tests/test_merged.py`):

- `test_announce_claims_once`: first call `True`, second `False`; `merged.json` stays empty (`_load() == {}`).
- `test_resolver_does_not_announce_a_pr_that_was_announced`: announce, then `resolve` with `gh` saying MERGED:
  returns `("merged", None)`, `recorded` is true, and no event is queued (`events.queue` mock not called).
- `test_resolver_still_announces_an_unannounced_pr`: the current behavior, unchanged.

### Task 6: tidy keeps a live session's worktree (R13)

Files: `relaylib/tidy.py`, `tests/test_tidy.py`.
Covers: R13 (tidy side; the wiring is in task 14).

Approach:

- `tidy.run(repo, branch, base, pr_head, cwd=None, keep=None)` and `_run(..., keep)`. `keep` is `None` or
  `{"path": <path>, "provider": ..., "session": ..., "main": bool}`. When `keep` is `None` nothing changes.
- In the worktree loop (`trees[1:]` on the branch): if `keep` and `os.path.realpath(wt["path"]) ==
  os.path.realpath(keep["path"])`, `r.skipped(f"kept worktree {wt['path']}: {keep['provider']} session
  {keep['session']} is working in it; it removes it when it next runs", f"worktree {wt['path']}")` and
  `continue`.
- When `main_branch == branch` and `keep` and `keep["main"]`: skip `_switch` with
  `r.skipped(f"left the main checkout on {branch} and kept local branch {branch}: {keep['provider']} session
  {keep['session']} is working in it; it switches when it next runs", "main checkout")`, and skip `_delete_branch`
  (the line already says so). Every other step runs as today (the local base fast-forward, prune).
- `leftovers(repo, branch)`: short words for what a merge that did not tidy leaves: `remote branch <branch>`
  when `git ls-remote --heads origin refs/heads/<branch>` (via `gitops.network_git`) prints a line,
  `worktree <path>` for each worktree on the branch (`worktrees()`; the main checkout counts), and `local
  branch <branch>` when `refs/heads/<branch>` exists. Used by task 15.
- `find_keep(repo, owner, branch)` lives here too: returns `None` unless `owner` has a `provider`, `session`
  and `worktree` string, `events.session_live(provider, session)` is truthy, and `worktrees(repo)` has an
  entry with `branch == branch` and `os.path.basename(path.rstrip("/")) == owner["worktree"]`; then
  `{"path": that path, "provider", "session", "main": that entry is trees[0]}`.

Tests (`tests/test_tidy.py`, using real linked worktrees as the existing tests do):

- `test_keep_leaves_a_linked_worktree_and_says_why`: `keep` for the feature's worktree: the worktree exists
  after, the line is exactly `kept worktree <path>: claude session s1 is working in it; it removes it when it
  next runs`, `left` has `worktree <path>`; the branch is kept with the existing "a worktree still has it
  checked out" line; the main checkout still moves to the base.
- `test_no_keep_removes_the_worktree_as_before`: the same setup with `keep=None`.
- `test_keep_for_the_main_checkout_leaves_it_on_the_branch`: main checkout on the feature branch, `keep["main"]`
  true: `git branch --show-current` is still the feature branch, the local branch exists, the line says so, local
  `develop` is still fast-forwarded.
- `test_keep_for_another_path_does_not_apply`: `keep["path"]` is a different folder: the feature worktree is removed.
- `test_leftovers_lists_remote_branch_worktree_and_local_branch`, and `test_leftovers_is_empty_when_all_gone`.
- `test_find_keep_needs_a_live_session_and_a_matching_folder`: with `live_session` and the worktree base name equal to
  `owner["worktree"]`, returns the dict (`main` false); with `state="ended"` returns `None`; with a different
  `owner["worktree"]` returns `None`; for the main checkout on the branch `main` is true.

### Task 7: the opt-in, control plane, production base and CI mode (R3, R4, R5, R7)

Files: new `relaylib/delegated.py`, new `tests/test_delegated.py`.
Covers: R3, R4, R5, R7.

Approach (`delegated.py`; imports `cimode`, `config`, `gitops`, `state`, `freshness`; no import of `owneractions`
at module level):

```python
PREFIX = "not delegated (D1): "
CONTROL_PLANE = {"hordle", "relay-go", "upstreampartners"}

class Refused(RelayError):
    """A D1 refusal; the text already starts with PREFIX."""

def refuse(reason): return Refused(PREFIX + reason)

def production_branches(repo, base_sha, base, name):
    """(list, None) or (None, reason). Read from the pinned base commit only, never the PR branch."""
    label = f"origin/{base}:{state.RELAY_DIR}/config.toml"
    text = gitops.show(repo, base_sha, f"{state.RELAY_DIR}/config.toml")
    if text is None: return None, f"{name} has not opted in: no [merge] production_branches on {base}"
    data = cimode._read_toml(text, label)           # a TOML error raises RelayError naming the file
    merge = data.get("merge")
    if not isinstance(merge, dict) or "production_branches" not in merge:
        return None, f"{name} has not opted in: no [merge] production_branches on {base}"
    listed = merge["production_branches"]
    if (not isinstance(listed, list) or not listed
            or not all(isinstance(b, str) and b.strip() for b in listed)):
        return None, f"[merge] production_branches on {base} must list at least one branch"
    return listed, None

def control_plane(remote):            # remote is what owneractions.github_repo returns, "owner/name"
    return remote.split("/", 1)[1].lower() in CONTROL_PLANE
```

Three small checks (`opt_in`, `not_control_plane`, `not_production`) return a reason string or `None`; task 10
composes them with R6 and R8. R7: `ci_reason(mode)` returns `ci = none never qualifies for D1` when
`mode.mode == "none"`, else `None` (the mode comes from `cimode.settings(repo, base)`, which already resolves
`release`). Messages: `<name> is control plane; the owner merges it`; `<base> is a production branch`.

Tests (`tests/test_delegated.py`, a fixture class on `tests.test_commands.CommandsTest` as in
`test_merge_cmd.py`, with `base_config` pushing the base's config):

- `test_opt_in_lists_the_production_branches`: base config `[merge]\nproduction_branches = ["main"]` gives
  `(["main"], None)`.
- `test_no_config_file_is_not_opted_in`: message `project has not opted in: no [merge] production_branches on
  develop`.
- `test_no_merge_table_and_no_key_are_not_opted_in`: a config with only `[build]`, and one with `[merge]` and no key.
- `test_a_non_list_or_empty_list_or_blank_entry_is_refused`: `production_branches = "main"`, `[]`, `["main", ""]`,
  `[1]`: each `must list at least one branch`.
- `test_a_toml_error_names_the_file`: a broken config: `RelayError` text starts with
  `origin/develop:docs/relay/config.toml:`.
- `test_the_list_is_read_from_the_base_not_the_pr_branch`: the PR branch carries a config with the list, the base has
  none: not opted in.
- `test_the_list_is_read_at_the_pinned_commit`: push a config to the base after taking `base_sha`: the old commit
  still answers not opted in.
- `test_control_plane_names_match_the_github_name_ignoring_case`: `owner/hordle`, `owner/Relay-Go`,
  `owner/UpstreamPartners` are control plane; `owner/hordle-app` and `owner/project` are not. A test with an
  origin folder named `project` and `github_repo` mocked to `owner/hordle` is control plane (the folder is not used).
- `test_a_production_base_is_refused`: base `main` in `["main"]`: `main is a production branch`; `develop` passes.
- `test_ci_none_never_qualifies`: `[build] ci = "none"`: `ci = none never qualifies for D1`.
- `test_release_mode_base_is_local_for_a_non_main_base`: `[build] ci = "release"`, `test_command = "true"`:
  `cimode.settings(work, "develop").mode == "local"` passes R7 and `main` resolves to `pr` (also passes).

### Task 8: protected paths (R8)

Files: `relaylib/delegated.py`, `tests/test_delegated.py`.
Covers: R8.

Approach:

```python
PROTECTED_NAMES = {"agents.md", "claude.md", "workflow.md"}

def protected_path(repo, base_sha, pr_head):
    out = gitops.git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base_sha}...{pr_head}").stdout
    for path in sorted(p for p in out.split("\0") if p):
        low = path.lower()
        if (os.path.basename(low) in PROTECTED_NAMES or low == f"{state.RELAY_DIR}/config.toml"
                or low.startswith(".github/")):
            return path
    return None
```

`--no-renames` makes a rename show as a delete and an add, so both the old and the new path are tested. The
reason text is `touches <path>, which the owner merges`.

Tests (`tests/test_delegated.py`, each builds a PR branch changing one path; `protected_path` is called directly):

- `test_agents_claude_and_workflow_md_in_any_folder`: `AGENTS.md`, `sub/CLAUDE.md`, `a/b/workflow.md` (lower case).
- `test_relay_config_and_github_folder`: `docs/relay/config.toml` and `.github/workflows/test.yml`.
- `test_a_rename_counts_both_sides`: rename `AGENTS.md` to `notes.md`, and `notes.md` to `AGENTS.md`: both flagged.
- `test_other_paths_pass`: `app.py`, `docs/relay/demo/spec.md`, `docs/other.md`, `docs/relay/config.toml.bak`.
- `test_diff_is_from_the_merge_base`: a protected file changed on the base after the PR branched is not counted.
- `test_the_first_path_in_order_is_named`: two protected files; the one sorting first is named.

### Task 9: a reviewed GO from the owner's table (R6)

Files: `relaylib/delegated.py`, `tests/test_delegated.py`.
Covers: R6.

Approach:

```python
REVIEW_NAME = re.compile(r"build-(\d+)r?\.(claude|codex)(?:-\d+)?\.md")

def review_evidence(repo, st, commit):
    """(meta, None) or (None, reason): the review the build GO came from, read from the published commit."""
```

Steps: take `gitops.ls_files(repo, commit, folder)` (case-insensitive prefix, full paths), keep paths whose
directory is exactly the reviews folder and whose base name fullmatches `REVIEW_NAME` (so `.error.md` and
`build-stuck.md` are never candidates). If none: `no review evidence for the build GO`. Top = highest
`<n>`; candidates = paths with that `<n>`. Read them with `gitops.cat_files([f"{commit}:{p}" ...])`. Parse the
front matter with `parse_front(text)`: the text must start with `---\n`, the JSON object runs to the next
`\n---\n`, `json.loads`, must be a dict; anything else is malformed. With more than one candidate, parse each
`at` with `datetime.datetime.fromisoformat`; the latest wins; an `at` that does not parse or a tie is malformed.
The chosen meta must have `verdict == "GO"`, non-empty string `reviewer` and `model`, and `head ==
snap.get("head")` (and `snap` non-empty). Any failure gives `no review evidence for the build GO`.

Table check:

```python
def table_reason(st, meta):
    cfg = config.load()                       # the owner's config and timed tables; never a repo config.toml
    for spec in config.review_preferences(cfg, (st.get("authors") or {}).get("build")):
        if spec.provider == meta["reviewer"] and spec.model == meta["model"]:
            return None
    return "the build GO was not a review from the preference table"
```

Effort is not compared. A `RelayError` from `config.load()` (a TOML error in the owner's file) becomes the reason
text. A same-provider or fallback GO counts when it matches an entry.

Tests (`tests/test_delegated.py`; review files are written into a temp clone with `commands.write_md`-style
front matter, pushed to the feature branch, and the published commit is passed):

- `test_a_codex_go_in_the_table_passes`: the file the normal flow writes (`ready_small_feature`), `meta["reviewer"] ==
  "codex"`, `table_reason` is `None`.
- `test_same_provider_fallback_go_passes`: author claude, review `claude`/`claude-fable-5-2` (in the table).
- `test_a_model_not_in_the_table_is_refused`: review `codex` / `gpt-4`: `the build GO was not a review from the
  preference table`.
- `test_effort_is_not_compared`: front matter `effort: "low"` while the table entry says `@high`: passes.
- `test_a_repo_config_that_adds_the_reviewer_is_still_refused`: the base and PR branch `docs/relay/config.toml`
  adds `[review.prefer] claude = ["codex:gpt-4"]`: still refused (the table is the owner's only).
- `test_the_owner_timed_table_is_used`: a timed table in the owner's config adds a model: accepted (`config.load()`
  semantics).
- `test_override_go_has_no_evidence`: run `relay override go --relayed` after a build NO-GO (latest review file
  has `verdict: NO-GO`): `no review evidence for the build GO`. Also a reviews folder with no `build-*` file.
- `test_head_must_match_the_build_snapshot`: the review's `head` differs from `st["reviewed"]["build"]["head"]`.
- `test_the_highest_round_is_used`: `build-1.codex.md` GO and `build-2.codex.md` NO-GO: refused; with
  `build-2.codex.md` GO and `build-1` NO-GO: passes.
- `test_a_tie_is_broken_by_at_and_an_equal_or_bad_at_is_malformed`: `build-1.codex.md` and `build-1r.codex.md`
  with different `at`: the later is used; equal `at`, or `at: "soon"`: refused.
- `test_error_files_and_other_names_are_ignored`: `build-3.codex.error.md` and `build-stuck.md` do not count as round 3.
- `test_missing_and_malformed_front_matter_refuse`: no `---`, bad JSON, JSON that is a list, no `verdict`, no `model`.
- `test_evidence_is_read_from_the_published_commit_not_the_checkout`: edit the review file in the working checkout
  without pushing: the published content decides.

### Task 10: one function for R3 to R8 (R9)

Files: `relaylib/delegated.py`, `tests/test_delegated.py`.
Covers: R9.

Approach: `conditions(repo, st, commit, remote, base, base_sha, pr_head)` returns `(reason, facts)`. It runs, in this
order, and returns the first failure: opt-in read (R3, a `RelayError` from a TOML error is returned as its text),
`control_plane(remote)` (R4), `base in listed` (R5), `review_evidence` then `table_reason` (R6), `cimode.settings` and
`ci_reason` (R7), `protected_path` (R8). On success `facts = {"reviewer": meta["reviewer"], "mode":
settings.mode}` and the reason is `None`. The caller adds `PREFIX`.

Tests (`tests/test_delegated.py`):

- `test_conditions_pass_when_every_one_holds`: returns `(None, {"reviewer": "codex", "mode": "pr"})`.
- `test_each_condition_fails_alone`: a table-driven test; each of the six failures (no opt-in, `hordle`, base in the
  list, evidence missing, `ci = none`, protected path) with every other condition kept true returns exactly that
  reason.
- `test_the_first_failure_wins`: a repo that is control plane, a production base and touches `AGENTS.md`: the
  opt-in reason when there is no list, else the control-plane reason (R3, R4, R5, R6, R7, R8 order).
- `test_a_pr_that_edits_the_list_is_refused_by_r8`: the base opts in; the PR branch changes
  `docs/relay/config.toml` (adds `"develop"` to the list): `touches docs/relay/config.toml, which the owner merges`.
- `test_a_pr_that_adds_the_list_is_not_opted_in`: the base has no list, the PR adds it: R3 refuses (read from the base).

### Task 11: merge commit and push, bound to the checked base (R10 steps 1, 2, 5, 6)

Files: `relaylib/gitops.py`, `relaylib/delegated.py`, `tests/test_delegated.py`.
Covers: R10 (steps 1, 2, 5, 6).

Approach:

- `gitops.py`: `HOOKS_OFF = ("-c", "core.hooksPath=/dev/null")`; `hookless(root, *args, network=False)` runs
  `["git", *HOOKS_OFF, *args]` through `run(..., check=False)`, with `env=network_env(root)` and
  `timeout=FETCH_TIMEOUT_S` when `network`. `pr_state(root, remote, number)` calls `_gh_json(root, ["pr", "view",
  str(number), "--json", "state,baseRefName,headRefOid,isDraft", "-R", remote])` directly (no memo, no
  `_MERGED_PRS`).
- `delegated.reread_pr(repo, remote, number)` is `gitops.pr_state(repo, remote, number)` (a module function so a
  test can patch it).
- `merge_and_push(repo, remote, number, branch, pr_head, base, base_sha)` returns the merge commit SHA, or raises
  `Refused`:
  1. `temp = tempfile.mkdtemp(prefix="relay-d1-")`, `work = join(temp, "work")`;
     `hookless(repo, "worktree", "add", "--detach", work, base_sha)`.
  2. `hookless(work, "merge", "--no-ff", "--no-edit", "-m", f"Merge pull request #{number} from
     {owner}/{branch}", "-m", "Delegated-merge: D1", pr_head)` with `owner = remote.split("/")[0]`. Nonzero
     whose output holds `CONFLICT` or `Automatic merge failed`: `Refused(PREFIX + f"the PR does not merge cleanly
     into {base}")`; other nonzero (for example no git identity): `Refused(PREFIX + f"could not create the merge
     commit: {message}")`. Then `merge_sha = rev-parse HEAD`; if `HEAD^1 != base_sha` or `HEAD^2 != pr_head`
     (the head was already in the base, so nothing merged): `Refused(PREFIX + f"{base} already holds the PR head;
     nothing to merge")`.
  3. Re-read: `info = reread_pr(repo, remote, number)`; a `RelayError` gives `Refused(PREFIX + f"GitHub could not
     be read: {e}")`; `state != "OPEN"` or `isDraft` or `baseRefName != base` or `headRefOid != pr_head` gives
     `Refused(PREFIX + "the PR changed while it was checked")`.
  4. `p = hookless(work, "push", "origin", f"{merge_sha}:refs/heads/{base}",
     f"--force-with-lease=refs/heads/{base}:{base_sha}", network=True)`; nonzero gives
     `Refused(PREFIX + f"GitHub refused the push to {base}: {p.stderr.strip()}")`. The text goes through
     `redact` if `relaylib/redact.py` has a text function; check its API first.
  5. `finally`: `hookless(repo, "worktree", "remove", "--force", work)` and `shutil.rmtree(temp, True)`.
- The merge commit uses the repo's own git identity because a linked worktree shares the repo config; the plan
  adds no `-c user.*`.

Tests (`tests/test_delegated.py`; the fixture has a bare origin; `merge_and_push` is called directly with the pinned values):

- `test_merge_commit_shape_and_push`: returns a SHA; `git log -1 --format=%P origin/develop` has the base SHA then
  the PR head; subject `Merge pull request #7 from owner/fix/tiny`; the body has the line `Delegated-merge: D1`;
  author name is the repo's `user.name` (`Relay Test`).
- `test_the_temporary_worktree_is_removed_on_success_and_on_every_refusal`: `git worktree list` is unchanged after
  each test below.
- `test_a_conflict_is_refused_and_nothing_is_pushed`: the base changed `app.py` incompatibly; `the PR does not merge
  cleanly into develop`; `origin/develop` unchanged.
- `test_a_moved_base_is_refused_by_the_lease`: patch `delegated.reread_pr` with a function that first pushes another
  commit to the base from a second clone and then returns the real answer; the push is refused
  (`GitHub refused the push to develop`), `origin/develop` holds the other commit only, no merge commit exists.
- `test_a_push_refused_by_a_rule_is_reported_with_its_message`: `helpers.reject_pushes(origin, "protected branch:
  only pull requests")`: the message holds that text; nothing merged.
- `test_the_reread_refuses_a_closed_a_draft_a_retargeted_and_a_moved_pr`: four patches of `reread_pr`
  (`state: CLOSED`, `isDraft: True`, `baseRefName: other`, `headRefOid: <other sha>`): each `the PR changed while it
  was checked`, no push (the post-receive marker never appears).
- `test_an_unreadable_pr_before_the_push_is_refused`: `reread_pr` raises `RelayError("boom")`: `GitHub could not be
  read: boom`, no push.
- `test_the_reread_happens_after_the_merge_commit_and_before_the_push`: record call order with mocks on
  `hookless` and `reread_pr`: merge, reread, push.
- `test_local_hooks_do_not_run`: `pre-merge-commit`, `commit-msg`, `pre-push` and `post-checkout` hooks in the work
  repo's `.git/hooks` that `exit 1`: the merge still succeeds.
- `test_head_already_in_the_base_is_refused`: the PR head is an ancestor of the base: `already holds the PR head`.
- `test_the_lease_names_the_checked_base_commit`: `hookless` is wrapped to capture the push argv; it contains
  `--force-with-lease=refs/heads/develop:<base_sha>` and the refspec `<merge>:refs/heads/develop`.

### Task 12: the bounded wait and the outcomes (R10 step 4)

Files: `relaylib/delegated.py`, `tests/test_delegated.py`.
Covers: R10 (step 4).

Approach:

```python
clock, sleep = time.monotonic, time.sleep       # read at call time so tests can replace them
WAIT_S, EVERY_S = 60, 2

class Outcome:
    def __init__(self, name, detail=None): ...  # name: merged, timeout, retargeted, head-moved, closed, read-failed

def wait_for_merge(repo, remote, number, base, pr_head):
    deadline, error = clock() + WAIT_S, None
    while True:
        try:
            info = gitops.pr_state(repo, remote, number)
            error = None
            if info["state"] == "MERGED":   return Outcome("merged")
            if info["state"] == "CLOSED":   return Outcome("closed")
            if info["state"] == "OPEN":
                if info["baseRefName"] != base:  return Outcome("retargeted", info["baseRefName"])
                if info["headRefOid"] != pr_head: return Outcome("head-moved", info["headRefOid"])
        except (RelayError, KeyError) as e:
            error = e                           # a failed read is retried until the time is up
        if clock() >= deadline:
            return Outcome("read-failed", str(error)) if error else Outcome("timeout")
        sleep(EVERY_S)

def describe(outcome, base, number, pr_head):
    """(message, exit_code) for each outcome, the spec's R10 text."""
```

`describe` returns exactly the spec's wording:

- merged: `merged into <base>`, 0.
- timeout: `merged into <base>; GitHub has not marked PR #<n> merged yet`, 0.
- retargeted: `pushed the merge to <base>; PR #<n> now targets <its base> and stays open: tell the owner`, 1.
- head-moved: `merged the checked head <sha> into <base>; PR #<n> has later commits that are not merged and stays
  open: tell the owner`, 1 (`<sha>` is `pr_head[:7]`).
- closed: `merged into <base>, but PR #<n> was closed without GitHub marking it merged: tell the owner`, 1.
- read-failed: `GitHub could not be read to confirm PR #<n>: <error>`, 1.

Tests (`tests/test_delegated.py`; `mock.patch.object(delegated, "clock", clock.monotonic)` and `sleep` with
`helpers.FakeClock`; `gitops.pr_state` patched with a list of results):

- `test_merged_on_the_first_read_does_not_sleep`: `clock.sleeps == []`.
- `test_open_for_a_few_reads_then_merged`: three `OPEN`, then `MERGED`: outcome `merged`, `sleeps == [2, 2, 2]`.
- `test_still_open_when_the_time_ends_is_a_timeout`: always `OPEN`: outcome `timeout`, total slept 60 (`now >= 60`).
- `test_closed_is_reported`, `test_a_different_base_is_retargeted`, `test_a_different_head_is_head_moved`.
- `test_a_failed_read_is_retried_and_a_later_good_read_wins`: `RelayError`, then `OPEN`, then `MERGED`: `merged`.
- `test_a_read_still_failing_at_the_end_is_read_failed`: `RelayError("gh down")` throughout: `read-failed`, detail `gh down`.
- `test_a_failure_then_a_good_open_read_that_stays_open_is_a_timeout`: not `read-failed`.
- `test_describe_texts_and_exit_codes`: the six messages exactly as above and exit codes 0, 0, 1, 1, 1, 1.

### Task 13: the orchestrator up to the push, wired into `relay merge` (R2, R9)

Files: `relaylib/owneractions.py`, `relaylib/commands.py`, `tests/test_delegated.py`.
Covers: R2, R9 (called inside the action lock after `merge_readiness`, before R10).

Approach (`owneractions.merge_delegated(repo, slug, seen, cause=None, cwd=None)`; the first half):

```python
with action_lock():
    fresh, st = _validate(repo, slug, "merge", seen)            # the owner's stale-state refusals
    try:
        remote = github_repo(repo)
        base = gitops.pr_info(repo, st.get("pr"))["baseRefName"]
        base_sha = delegated.rev(repo, f"origin/{base}")       # pinned
        info = merge_readiness(repo, st, fresh)
    except Conflict as e:
        raise Conflict(delegated.PREFIX + str(e), e.fresh)
    except RelayError as e:
        raise delegated.refuse(str(e))
    if info["baseRefName"] != base or delegated.rev(repo, f"origin/{base}") != base_sha:
        raise delegated.refuse("the PR changed while it was checked")
    if info.get("isDraft"):
        raise delegated.refuse("the PR is a draft")
    reason, facts = delegated.conditions(repo, st, fresh["commit"], remote, base, base_sha, fresh["pr_head"])
    if reason:
        raise delegated.refuse(reason)
    ... (task 14)
```

`delegated.rev(repo, ref)` is `git rev-parse --verify -q <ref>^{commit}`, a `RelayError` when missing.

`commands.cmd_merge`: the task 2 stub is replaced by
`done = owneractions.merge_delegated(repo, slug, owneractions.fingerprint(repo, slug, fetch=False), cause=cause, cwd=os.getcwd())`
(the cause is the one set by `events.set_cause`). For this task `merge_delegated` returns after the conditions
with `{"message": "checks passed", "tidy": [], "exit": 0}` only inside the tests' own patch of
`delegated.merge_and_push`; task 14 replaces that. cmd_merge prints as today, and prints `done["error"]` to
stderr as `relay: <text>` and returns 1 when `done.get("exit")` is non-zero (used by task 15).

Tests (`tests/test_delegated.py`, `DelegatedCommandTest`; setUp as `MergeCommandTest` plus the opt-in via
`base_config('[merge]\nproduction_branches = ["main"]\n')`, `ready_small_feature`, and `HORDLE_ROLE=release`
patched into `os.environ`; `delegated.merge_and_push` is patched to raise `Refused("stop")` so nothing can
merge):

- `test_each_condition_refuses_through_the_command_with_nothing_merged`: for each of R3 (no list), R4 (`github_repo`
  mocked to `owner/hordle`), R5 (the list holds `develop`), R6 (an overridden GO), R7 (`ci = none`), R8 (the PR
  touches `AGENTS.md`): exit 1, stderr `relay: not delegated (D1): <reason>`, no `pr merge` call in the gh log,
  `merge_and_push` not called, `origin/develop` unchanged.
- `test_readiness_failures_are_prefixed`: a stale GO (`freshness.check` patched), a running review
  (`st["confirming"]`), a failing CI (`FAKE_GH_RUNS` with a failure), a closed PR, a changed PR head (the `Conflict`):
  each refuses `not delegated (D1): ...` with the owner's wording after the prefix.
- `test_a_draft_is_refused`: `self.pr(isDraft=True)`: `not delegated (D1): the PR is a draft`.
- `test_conditions_run_after_readiness_and_before_the_merge`: record call order with mocks:
  `merge_readiness`, `conditions`, `merge_and_push`.
- `test_a_base_that_moves_while_checking_is_refused`: patch `merge_readiness` to push a commit to the base and fetch:
  `not delegated (D1): the PR changed while it was checked`.
- `test_a_busy_action_lock_is_refused`: as the owner's test.

### Task 14: the merged outcome end to end (R10 merged, R11, R12, R13, R14)

Files: `relaylib/owneractions.py`, `relaylib/delegated.py`, `tests/test_delegated.py`.
Covers: R10 (merge, record, branch delete), R11, R12, R13, R14 (delegated note).

Approach (second half of `merge_delegated`):

1. `delegated.merge_and_push(...)` raises `Refused` before the push (nothing merged; propagate). From the next
   line on, nothing may raise: wrap the rest in `try/except Exception` that turns an unexpected error into a
   warning and still returns, so the merge that stands is always recorded.
2. The comment (R11), at once, from a temp folder as the relayed comment is posted: factor the existing
   `gh pr comment ... -R <remote> --body` block out of `merge` into `_comment(remote, number, body)` returning a
   `why` string or `None`; `merge` uses it too (its behavior and warning text unchanged). Body:
   `Merged by relay under D1 (delegated), run by the hordle release role. Checked: <base>, not production; <mode>
   tests green; GO from <reviewer>; no protected path.` A failure is a warning `PR #<n> is merged, but the comment
   recording the delegated merge could not be posted: <why>`.
3. `outcome = delegated.wait_for_merge(repo, remote, number, base, pr_head)`; `message, code = describe(...)`.
4. For `merged`: `delegated.delete_branch(repo, branch, pr_head)`: `git ls-remote --heads origin refs/heads/<branch>`
   (empty: nothing to do), then `network_git(repo, "push", "origin",
   f"--force-with-lease=refs/heads/{branch}:{pr_head}", f":refs/heads/{branch}")`; a refusal is a warning
   `kept remote branch <branch>: <stderr>`. Then `claimed, warning = merged.mark_merged(repo, number)`;
   `keep = tidy.find_keep(repo, st.get("owner"), branch)`; `tidied = tidy.run(repo, branch, base, pr_head, cwd,
   keep=keep)`; `done["tidy"] = tidied.lines`.
5. The event: if `claimed or warning`, `events.queue(kind, name, slug, "build", number,
   events.merged_text(name, slug, number, base, tidied.left, tidied=True, delegated=True), cause,
   owner=events.owner_of(st), note_extra=<R14 line>, fields={"delegated": True, "outcome": "merged"})` with
   `kind = "released" if base == "main" else "merged"`. When it is not claimed (the resolver saw the merge first),
   queue the same with `log_only=True`, so exactly one hub log line with `delegated: true` is added and no note.
6. The R14 line when `keep`: `Remove the worktree: git worktree remove <path>`; when `keep["main"]`: `Switch this
   checkout to <base>, pull, and delete the local branch <branch>`. Empty otherwise.
7. Return `{"message": f"Merged PR #{n} into {base}, delegated under D1", "tidy": [...], "warning": ..., "exit": 0}`.

Tests (`tests/test_delegated.py`, full command; `delegated.sleep` and `clock` replaced by `FakeClock`;
`FAKE_GH_AFTER_PUSH` set to `[{"state": "MERGED"}]` with `helpers.mark_pushes` and `FAKE_GH_PUSHED`):

- `test_a_delegated_merge_that_meets_every_condition_merges_and_records`: exit 0; `origin/develop` has the merge commit
  (`Delegated-merge: D1`); no `pr merge` in the gh log; one `pr comment` with the R11 text (base `develop`, `pr tests
  green`, `GO from codex`); `merged.recorded` true; the remote branch is gone; the local worktree state per tidy;
  exactly one hub event, `event == "merged"`, `delegated is True`, `outcome == "merged"`, text contains
  `delegated under D1`.
- `test_the_remote_branch_delete_uses_a_lease_on_the_checked_head`: a commit pushed to the feature branch after the
  merge push (from a hook on the post-receive marker, or by patching `wait_for_merge`): the delete is refused, the
  branch stays, the warning says `kept remote branch`, the merge still stands and exit is 0.
- `test_a_branch_already_gone_is_silent`: delete the remote branch before the wait: no warning.
- `test_release_repo_merges_with_a_local_pass`: `[build] ci = "release"`, `test_command = "true"`; patch
  `owneractions._require_local_pass`; base `develop` merges; the comment says `local tests green`.
- `test_the_comment_failing_is_a_warning`: `FAKE_GH_API` makes `pr comment` fail: exit 0, stderr warning, event still
  queued, merge stands.
- `test_the_hub_log_failing_is_a_warning`: `hub.log` patched to return a warning: exit 0.
- `test_another_resolver_that_saw_the_merge_first_adds_one_log_line`: `merged.mark_merged` pre-claimed: no plain event;
  exactly one hub log line with `delegated: true`; no note to any session.
- `test_a_live_feature_session_keeps_its_worktree`: a linked worktree on `fix/tiny` whose folder name is the state's
  `owner.worktree`, `helpers.live_session("claude", "s1", ...)`: the worktree exists after, the tidy line is
  `kept worktree ...`, the owner's notes log holds one queued note with prefix `relay event:` whose text ends with
  `Remove the worktree: git worktree remove <path>`; with the cause set to a different session so the note is sent.
- `test_a_live_session_in_the_main_checkout_keeps_its_branch`: the main checkout on `fix/tiny`: still on it; the local
  branch exists; the note ends `Switch this checkout to develop, pull, and delete the local branch fix/tiny`.
- `test_an_ended_feature_session_is_tidied_and_noted`: `state="ended"`: the worktree is removed as today; the note is
  queued for the session (no inbox) without a `Remove` line.
- `test_the_caller_session_is_not_told_its_own_event`: the cause equals the feature's owner session: no note; and
  `test_a_hub_session_that_owns_the_feature_gets_one_note`.
- `test_the_cwd_line_uses_the_real_working_folder_with_repo`: `--repo` from another folder, tidy line unchanged.
- `test_the_event_is_delivered_after_the_result_is_printed`: as the existing ordering test.

### Task 15: the other outcomes (R10 outcomes, R12, R14)

Files: `relaylib/owneractions.py`, `tests/test_delegated.py`.
Covers: R10 (timeout, read-failed, retargeted, head-moved, closed), R12, R14.

Approach: in `merge_delegated`, for every outcome other than `merged`: no branch delete, no `mark_merged`, no tidy.
`announced = merged.announce(repo, number)`; if newly announced, queue one event with
`events.merged_text`-free text: `events.delegated_text(name, slug, number, message, left)` where `left` is
`tidy.leftovers(repo, branch)` for `timeout` (the leftovers the feature's session or the owner removes) and empty
for the exit-1 outcomes. The event fields are `{"delegated": True, "outcome": outcome.name}` and it carries
`owner=events.owner_of(st)` so the R14 note is sent (decision: all five). For exit 1, `notify.send(config.load(),
f"relay: {name} merge needs you", message)` (inside `try/except`, never raises). Return `{"message": ...,
"tidy": [], "warning": <text for timeout>, "exit": code, "error": message if code else None}`; `cmd_merge`
prints `error` to stderr and returns 1, and prints the timeout text as `relay: warning: ...` with exit 0.

Tests (`tests/test_delegated.py`, through `self.relay("merge", "7")` with `FAKE_GH_AFTER_PUSH` sequences, the
fake clock, the push marker hook):

- `test_open_for_a_few_reads_then_merged_is_a_normal_merge`: `[OPEN, OPEN, OPEN, MERGED]`: exit 0, branch deleted,
  tidied, `merged.recorded` true, `clock.sleeps == [2, 2, 2]`, no warning.
- `test_still_open_after_60_seconds_exits_0_with_a_warning`: `[OPEN]` repeating: exit 0; stderr
  `warning: merged into develop; GitHub has not marked PR #7 merged yet`; the remote branch, the local branch and the
  worktree remain; no tidy lines; `merged.json` has no key for the PR (the feature still shows ready to merge, `merged.recorded`
  false); one event with `outcome == "timeout"` whose text ends with `Not tidied: remote branch fix/tiny; ...`; one note to
  the feature's session; no desktop notification (`notify.log` empty).
- `test_a_later_status_adds_no_second_event_after_a_timeout`: after the timeout test, set the sequence to `MERGED` and run
  `merged.resolve`: returns `merged`, and the hub log still has exactly one `merged` event for PR 7.
- `test_retargeted_after_the_push`: `[{"baseRefName": "other"}]`: exit 1; stderr has `pushed the merge to develop; PR #7 now
  targets other and stays open: tell the owner`; `notify.log` has a line; no branch delete, no tidy; `outcome ==
  "retargeted"`; the event text holds the message; the owner session got the note; `merged.json` empty; the push
  reached only `develop` (`origin/other` does not exist).
- `test_head_moved_after_the_push`: `[{"headRefOid": "<other>"}]`: exit 1, message with the 7-character sha, notification,
  no delete, no tidy, `outcome == "head-moved"`.
- `test_closed_after_the_push`: `[{"state": "CLOSED"}]`: exit 1, `merged into develop, but PR #7 was closed without
  GitHub marking it merged: tell the owner`, notification, no tidy, no branch delete, note sent, `merged.json` empty.
- `test_a_read_failing_to_the_end`: `[{"__rc": 1, "__err": "gh: down"}]`: exit 1, `GitHub could not be read to confirm PR
  #7: gh: down`, notification, no delete, no tidy.
- `test_a_single_failed_read_does_not_alert`: `[{"__rc": 1}, {"state": "MERGED"}]`: exit 0, no notification.
- `test_the_comment_and_the_event_are_written_in_every_outcome`: for each of the five, one `pr comment`, one event with `delegated`
  true and the outcome name.
- `test_a_push_refused_by_a_rule_merges_nothing`: `helpers.reject_pushes`: exit 1, `not delegated (D1): GitHub refused the
  push to develop: ...`, no comment, no event, no marker, the worktree list unchanged.
- `test_a_spoofed_role_gets_no_shortcut`: `HORDLE_ROLE=release` in a repo that fails a condition refuses exactly as the real
  role does (one case, e.g. R4), and a spoof on a repo that passes every condition merges and the comment says `run by the
  hordle release role` (the accepted limit, decision 2).

### Task 16: the owner's and the relayed merge are unchanged

Files: `tests/test_merge_cmd.py`, `tests/test_owneractions.py`.
Covers: the "owner's and relayed merges unchanged" test line in the spec's Tests section; R1 (the two unchanged callers).

Tests (no production change; if one fails, the production change in tasks 4 to 6 is wrong):

- `test_owner_merge_still_uses_gh_pr_merge_with_match_head_and_delete_branch`: the gh log line is `pr merge 7 -R
  owner/project --merge --match-head-commit <head> --delete-branch`.
- `test_owner_merge_ignores_a_repo_without_an_opt_in`: a repo with no `[merge]` merges as before; a control-plane repo
  name merges as before for the owner's terminal and for `--relayed`.
- `test_owner_merge_tidies_a_live_session_worktree`: with `live_session` for the owner and a worktree: the worktree is
  removed, as before (decision 5).
- `test_relayed_comment_text_is_unchanged`: the relayed comment still reads `Merged by the owner, relayed by claude session
  s1 in its conversation with the owner`.
- The existing `tests/test_merge_cmd.py`, `tests/test_owneractions.py`, `tests/test_tidy.py` and `tests/test_merged.py`
  pass without edits except where a test counted notes that the owner session now gets (task 4); each such edit is
  named in the commit.

### Task 17: docs and help (R16)

Files: `docs/reference.md`, `README.md`, `relaylib/commands.py`.
Covers: R16.

Approach:

- `docs/reference.md`: a section `### Delegated merges (D1)` under `## Merging`, after the owner-merge text. Plain
  English, no dashes. It says: who may (`HORDLE_ROLE=release`, no `--relayed`; every other role and every other
  agent still needs the owner); the opt-in key `[merge] production_branches` in `docs/relay/config.toml` on the PR's
  base branch (read from origin's base, no second switch, the owner's global config cannot set it); each condition
  with its refusal text (R3 to R8, copied from the spec); how the merge lands (relay pushes its own merge commit to
  the checked base under a lease; the owner's own merge is unchanged; a repo whose non-production base accepts only
  pull requests cannot use D1); the record (the PR comment, the event field `delegated`, the hub log line, the
  `outcome` values); the 60-second wait and each outcome with its exit code; R13 (the live session keeps its worktree
  and removes it when it next runs); R14 (the note to the feature's session, for every merge); `--repo`; the
  accepted limit (a role name is a record, not the gate; the conditions are the gate).
- `README.md`: one line after the `relay merge <pr>` list: `In a repo that opts in, hordle's release role can merge a
  reviewed, tested PR without the owner (see "Delegated merges (D1)" in docs/reference.md).`
- The `merge` subparser help becomes: `merge a feature's PR once it is ready, then tidy this machine; agents need
  --relayed; the hordle release role may merge an opted-in repo's PR without the owner (D1)`.

Tests (`tests/test_owner_cli.py` or `tests/test_merge_cmd.py`):

- `test_merge_help_names_the_release_role`: `build_parser()` help for `merge` contains `release role may merge an opted-in
  repo's PR without the owner`.
- `test_docs_have_the_delegated_section_and_no_dashes`: `docs/reference.md` contains `### Delegated merges (D1)` and each
  refusal fragment from R3 to R8; `README.md` names it; neither new text, nor `docs/relay/delegated-merge/*.md`, holds an
  em-dash or en-dash character (check only the new section and plan/acceptance files by regex on the section).

### Task 18: acceptance ledger (R17)

Files: new `docs/relay/delegated-merge/acceptance.md`.
Covers: R17.

Approach: a short list in plain English, each item a checkbox that waits on the owner or on a later feature:

1. hordle: the `release` role and the change to its hook (today it refuses `relay merge` in every role run, hordle
   foundation spec R28). Added to hordle's `TODO.md` build order by a PR the owner merges.
2. The first repo's opt-in PR (`[merge] production_branches`), merged by the owner. With it the owner checks that the
   repo's non-production bases accept the owner's push (no pull-request-only rule) and that its production branches
   keep their protection (R10).
3. One real delegated merge seen end to end in that repo: the PR comment, the hub log line with `delegated: true`, the
   kept worktree and the note to the feature's session, the remote branch gone, and `relay status` showing it done.
4. The decision 8 cost accepted: a repo whose non-production base accepts only pull requests cannot use D1.

Tests: none (a docs file). A check in the task 17 test asserts the file exists and has no em-dash or en-dash.

## Coverage

| Requirement | Tasks |
|---|---|
| R1 Three callers | 2, 16 |
| R2 A delegated merge is a full merge | 13 |
| R3 The opt-in | 7, 10 |
| R4 Not control plane | 7, 10, 13 |
| R5 Not into production | 7, 10 |
| R6 A reviewed GO | 9, 10 |
| R7 Tests that ran | 7, 10, 14 |
| R8 No protected path | 8, 10 |
| R9 One check, one place | 10, 13 |
| R10 The merge lands on the checked base | 11, 12, 14, 15 |
| R11 On the PR | 14, 15 |
| R12 In relay's log | 4, 5, 14, 15 |
| R13 The live session keeps its worktree | 6, 14 |
| R14 Telling the feature's session | 4, 14, 15 |
| R15 `--repo <path>` | 3, 14 |
| R16 Docs | 17 |
| R17 Acceptance ledger | 18 |

Tasks 0 and 1 (baseline, helpers) support 11, 12, 14 and 15 and cover no requirement on their own.
