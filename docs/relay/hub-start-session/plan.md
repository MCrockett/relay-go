# hub-start-session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The hub can start a Claude session for a waiting dispatch request (`relay dispatch --start`) and replace a closed session with a fresh one (`relay hub clear`), only on the owner's typed words.

**Architecture:** Three new modules. `relaylib/launch.py` builds and runs the `claude --bg` command and makes or removes the worktree. `relaylib/retired.py` is the store of retired sessions in `<relay home>/sessions/retired.json`. `relaylib/hubstart.py` runs the two flows (start and clear) on top of `dispatch`, `basebranch`, `ownership`, `owneractions` and the two new modules. `relaylib/commands.py` only parses, checks who is asking, logs to the hub log and prints. The retired store is then read by notices, `hub note`, the digest, `resolve_asker` and prune.

**Tech Stack:** Python 3.11 standard library, `unittest`, temp git repos with a bare origin and fake binaries from `tests/helpers.py`.

**Spec:** `docs/relay/hub-start-session/spec.md`

## Global Constraints

- Python 3.11 standard library only. One module per responsibility in `relaylib/`.
- Plain English in prompts, skills and docs; no em-dashes.
- Tests never call real Claude or Codex. `claude` is always a fake from `tests/helpers.fake_bin` on a test PATH.
- The launch argument list never holds `--permission-mode`, `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions` (D3, R5).
- No shell: `subprocess.Popen(list, cwd=...)`, never `shell=True` (D1).
- Grace for a start or a replacement: 10 minutes, `GRACE_S = 600` (D9). Launch wait: 60 seconds, `LAUNCH_WAIT_S = 60` (R9a). Retired retention: 7 days (R19).
- Both commands need `--relayed` from a hub session (registered hub, as `_dispatch_who` checks) and not from the owner's terminal (D5). Every run is logged with `_hub_logged`.
- The base branch comes from `basebranch.choose(root)[0]`, the same function `relay take` uses (D4a). The branch already has it: origin/develop (PR #47) was merged into `feat/hub-start-session` at the plan stage.

## Review Focus

1. A `started` launch record on a still-waiting request must not confuse code that already reads `record["started"]` (`_dispatch_show_text`, `_not_waiting`). `mark_started` overwrites it when the session takes the request, which is right. Task 4 pins `--show` on a waiting request with a launch record, and Task 4 pins that `relay take --request` (`dispatch.claim`) still works while that record is there.
2. The launcher's output must not block a process left running after 60 seconds. Output goes to files, not pipes (Task 2 test `test_unconfirmed_leaves_process_running`).
3. A clear whose old session holds a feature that exists only in a local worktree (not on origin) must refuse before any release (Task 6 `test_clear_refuses_unpublished_feature`).
4. A retry of clear after a failed launch must not release the old session's features twice or refuse because the entry is `retired` (Task 7 `test_clear_retry_after_failed_launch`).
5. A digest saved before the session closed must still resolve for clear after it closed (Task 6 `test_clear_uses_saved_digest_after_close`).

---

### Task 1: Retired sessions store

**Files:**
- Create: `relaylib/retired.py`
- Test: `tests/test_retired.py`

**Interfaces:**
- Produces:
  - `path() -> str`: `<relay home>/sessions/retired.json`.
  - `locked()`: context manager, `fcntl.flock` on `<relay home>/sessions/retired.lock`, same pattern as `dispatch._locked`.
  - `load() -> dict`: `{"<provider>:<session_id>": entry}`; a missing or unreadable file is `{}`. Call under `locked()` when writing.
  - `save(entries: dict) -> None`: atomic write (temp file plus `os.replace`), mode 0600.
  - `entry(provider, session_id) -> dict | None`: read without the lock.
  - `is_stale(entry: dict, now: float) -> bool`: the replacement (`entry["provider"]`, `entry["replaced_by"]`) has no `events.session_live` record and `now - entry["at"] >= GRACE_S`.
  - `is_retired(provider, session_id) -> bool`: an entry with `state == "retired"`. Never raises (any error is False).
  - `prune(now: float) -> int`: under the lock, drop `retired` entries with `now - retired_at >= 7 * 86400` whose old session is not live, and `clearing` entries that are stale and with `now - at >= 7 * 86400`. Returns how many went.
  - `GRACE_S = 600`, `KEEP_S = 7 * 86400`.
- Entry shape: `{provider, session_id, state: "clearing"|"retired", replaced_by, replaced_by_name, at, relayed_by}`, plus `retired_at` once retired and `launch: "ok"|"failed"|"unconfirmed"` plus `launch_error` after a launch. `replaced_by_name` is the new session's Remote Control name, kept for the digest (R17).

- [ ] **Step 1: Write the failing tests** in `tests/test_retired.py`, with `RELAY_HOME` set to a temp folder as other tests do:
  - `test_missing_file_is_empty`: `load() == {}`, `is_retired("claude", "a") is False`.
  - `test_save_and_entry_round_trip`: save one retired entry; `entry("claude","a")["replaced_by"] == "b"`; file mode is `0o600`.
  - `test_stale_needs_both_quiet_and_ten_minutes`: entry `at = now - 599`, no live record: not stale. `at = now - 600`: stale. `at = now - 600` with `helpers.live_session("claude", "b")`: not stale.
  - `test_clearing_is_not_retired`: a `clearing` entry gives `is_retired` False (R15).
  - `test_prune_keeps_live_old_session`: a retired entry 8 days old whose old session `a` is live stays; the same entry with `a` not live goes; a retired entry 6 days old stays (R19).
  - `test_prune_clearing`: a `clearing` entry 8 days old and stale goes; one 8 days old whose replacement is live stays.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_retired -v`. Expected: FAIL, no module `relaylib.retired`.
- [ ] **Step 3: Implement** `relaylib/retired.py` with the interfaces above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: retired sessions store`.

Covers: R11a (store and staleness), R15 (clearing is not retired), R19.

### Task 2: Launcher and worktree helpers

**Files:**
- Create: `relaylib/launch.py`
- Test: `tests/test_launch.py`

**Interfaces:**
- Produces:
  - `BYPASS = ("--permission-mode", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")`
  - `LAUNCH_WAIT_S = 60`
  - `argv(name: str, session_id: str, prompt: str) -> list[str]`: exactly `["claude", "--bg", "--remote-control", name, "--session-id", session_id, prompt]`.
  - `run(args: list[str], cwd: str, session_id: str) -> tuple[str, str | None]`: `("ok", None)` on exit 0; `("failed", reason)` on a non-zero exit or `OSError`; `("unconfirmed", None)` when the process has not exited after `LAUNCH_WAIT_S`. stdout and stderr go to `<relay home>/launch/<session_id>.out` and `.err` (opened files, not pipes), and the process is left running on a timeout. `reason` is the first non-empty line of stderr, else of stdout, else `claude --bg exited with code <n>`; for an `OSError` it is `cannot run claude: <error>` (R9).
  - `start_prompt(request_id: str, slug: str) -> str` and `clear_prompt(old_session_id: str, slugs: list[str]) -> str` (exact text below).
  - `worktree_path(main: str, slug: str) -> str`: `<main>/.claude/worktrees/<slug>`.
  - `make_worktree(main: str, path: str, base: str) -> str`: `git worktree add --detach <path> origin/<base>`; returns the commit it is at.
  - `remove_if_pristine(main: str, path: str, commit: str) -> bool`: removes with `git worktree remove` only when `git status --porcelain` is empty, `git symbolic-ref -q HEAD` fails (detached) and `HEAD == commit`. True when removed.
- Prompt text (exact):
  - start: `This session was started by relay for dispatch request <id>, on the owner's word through the relay hub. Run relay dispatch --show <id>, then relay take --request <id> --slug <slug>. Then continue with the relay skills through spec, plan and build. The owner merges.`
  - clear: `This session was started by relay to replace session <old id>, which the owner retired through the relay hub. Run relay status and read any handoff, then relay take each feature that session held: <slug list, comma separated, or "none">. Then continue with the relay skills. The owner merges.`

- [ ] **Step 1: Write the failing tests** in `tests/test_launch.py`. Put a fake `claude` on PATH with `helpers.fake_bin`; it appends its argv and cwd as JSON to `FAKE_LOG`, then behaves on env: `FAKE_EXIT=<n>` exits n, `FAKE_ERR=<text>` writes it to stderr first, `FAKE_SLEEP=<s>` sleeps first.
  - `test_argv_has_no_bypass_flag`: `args = argv("relay-go x", "u", "p")`; `args == ["claude","--bg","--remote-control","relay-go x","--session-id","u","p"]`; `not set(args) & set(BYPASS)` (R5).
  - `test_run_ok_in_cwd`: `run(argv(...), wt, "u") == ("ok", None)`; the log shows that argv and `cwd == wt`.
  - `test_run_failed_first_line`: `FAKE_EXIT=3`, `FAKE_ERR="not logged in\nmore"`: `("failed", "not logged in")`.
  - `test_run_failed_no_output`: `FAKE_EXIT=3`: `("failed", "claude --bg exited with code 3")`.
  - `test_run_oserror`: PATH without claude: result is `failed` and the reason starts with `cannot run claude:`.
  - `test_unconfirmed_leaves_process_running`: patch `LAUNCH_WAIT_S` to 1, `FAKE_SLEEP=5`: `("unconfirmed", None)` within 3 seconds; the child's pid is still running (wait for it in cleanup).
  - `test_prompts`: `start_prompt("q1","s")` contains `relay dispatch --show q1` and `relay take --request q1 --slug s`; `clear_prompt("old", ["a","b"])` contains `old` and `a, b`; neither contains an em-dash.
  - `test_remove_if_pristine`: with `helpers.make_repo`: a made worktree is removed (True); one with an untracked file is kept (False); one at another commit is kept.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_launch -v`. Expected: FAIL, no module.
- [ ] **Step 3: Implement** `relaylib/launch.py`. `run` uses `subprocess.Popen(args, cwd=cwd, stdin=DEVNULL, stdout=<file>, stderr=<file>, start_new_session=True)` then `wait(timeout=LAUNCH_WAIT_S)`; on `TimeoutExpired` return unconfirmed without killing.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: claude --bg launcher and worktree helpers`.

Covers: D1, D3, R5, R6, R9 (reasons, pristine removal), R9a (no kill, no pipes), R12 (clear prompt).

### Task 3: Start a session for a waiting request

**Files:**
- Create: `relaylib/hubstart.py`
- Modify: `relaylib/dispatch.py` (add `launch_stale(started, now) -> bool`)
- Test: `tests/test_hubstart.py`

**Interfaces:**
- Consumes: Task 2 (`launch.*`), `dispatch.read/mutate/check_slug/claim_blocks/main_checkout`, `basebranch.choose`, `gitops.fetch/local_branches/remote_ref_exists`, `events.session_live`.
- Produces:
  - `dispatch.launch_stale(started: dict, now: float) -> bool`: True when `started` has no live session record (`events.session_live("claude", started["session_id"])` is None) and `now - started["at"] >= retired.GRACE_S`. A `started` without a `launch` key (the existing take record) is never treated as a launch.
  - `hubstart.start(request_id: str, slug: str | None, relayed_by: str | None, now: float | None = None) -> dict` returning `{"session_id", "worktree", "name", "launch": "ok"|"unconfirmed", "reused": bool}`; raises `RelayError` for every refusal and for a failed launch.
- Order inside `start` (each refusal changes nothing unless noted):
  1. R1: read the record (`no request <id> on this machine`), status `waiting` (`dispatch._not_waiting`), slug from `slug or record["slug"]` through `check_slug`, else the exact R1 message.
  2. R3: `shutil.which("claude")` is None: `claude is not on PATH; start the session at the Mac`.
  3. R4: `gitops.fetch(root)` on `record["repo"]["path"]`, `base = basebranch.choose(root)[0]`, `main = dispatch.main_checkout(root)`, `path = launch.worktree_path(main, slug)`.
  4. Read the old `started` (a dict with a `launch` key, else None). If old and its worktree exists: R4a. `docs/relay/<slug>/state.md` there: refuse with the exact R4a text. Else reuse (skip the path and branch checks). Otherwise (first start, or the old worktree is gone): refuse if `path` exists, if `feat/<slug>` is in `gitops.local_branches(main)`, or if `origin/feat/<slug>` exists.
  5. R2 and R7 in one `dispatch.mutate`: still `waiting`; `claim_blocks(record.get("claim"), now)` refuses (`request <id> is being started by <provider> session <id>`); a `started` with `launch` that is not `launch_stale` refuses (`request <id> already has a session starting: <name> (<session id>); try again after <HH:MM>`); the `started` must equal the one read in step 4, else refuse `request <id> changed; run it again`. Then write `started = {provider: "claude", session_id: str(uuid.uuid4()), slug, worktree: path, name: f"{repo} {slug}", at: now, relayed_by, launch: "pending"}`.
  6. Make the worktree when not reused. If that fails: clear `started` (mutate, only when its `session_id` is ours) and refuse with the git error.
  7. `launch.run(launch.argv(name, sid, launch.start_prompt(id, slug)), path, sid)`.
  8. `ok` or `unconfirmed`: mutate `started.launch` to that value (only when `session_id` is still ours). `failed`: `remove_if_pristine` when not reused, clear `started`, refuse with the reason.

- [ ] **Step 1: Write the failing tests** in `tests/test_hubstart.py`. Setup: `helpers.make_repo` with a bare origin and a `develop` branch, the repo under a temp projects root, a dispatch record made with `dispatch.create`, a fake `claude` as in Task 2 on PATH. Each test checks the record afterwards with `dispatch.read`.
  - `test_start_ok`: returns `launch == "ok"`; the worktree exists at `<main>/.claude/worktrees/<slug>`, detached at `origin/develop`; the fake saw `cwd` there and `--remote-control "<repo> <slug>"`; record stays `waiting` with `started.launch == "ok"` and `started.relayed_by` set (R4, R7, R8).
  - `test_refuses_without_slug`: request with no slug and no `--slug`: exact R1 message.
  - `test_refuses_not_waiting`: a cancelled request refuses; nothing launched.
  - `test_refuses_without_claude`: PATH without claude: refusal names PATH; no worktree; no `started` (R3).
  - `test_refuses_existing_path_or_branch`: three cases (path exists, local `feat/<slug>`, `origin/feat/<slug>`): refusal, the fake never ran, no `started` (R4).
  - `test_refuses_fetch_failure`: origin removed: refusal, nothing changed (R4).
  - `test_refuses_live_claim`: `helpers.live_session` plus a fresh claim on the record: refusal (R2).
  - `test_refuses_fresh_started`: a `started` with `launch: "pending"` 5 minutes old: refusal; 10 minutes old with no live record: goes ahead (R2, D9).
  - `test_failed_launch_cleans_up`: `FAKE_EXIT=1 FAKE_ERR=boom`: refusal text `boom`; worktree gone; no `started`; still `waiting` (R9).
  - `test_failed_launch_keeps_dirty_worktree`: the fake writes a file into its cwd then exits 1: the worktree stays (R9).
  - `test_unconfirmed`: `LAUNCH_WAIT_S` patched to 1, `FAKE_SLEEP=5`: returns `unconfirmed`; worktree kept; `started.launch == "unconfirmed"` (R9a).
  - `test_restart_reuses_worktree`: a stale `started` naming an existing worktree without the feature: launches in it, `reused` True, no branch check; a failed launch then leaves it in place (R4a, R9).
  - `test_restart_refuses_when_feature_started`: that worktree holds `docs/relay/<slug>/state.md`: exact R4a refusal; nothing changed.
  - `test_restart_after_worktree_gone`: stale `started` whose worktree was removed: a first start again.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_hubstart -v`. Expected: FAIL, no module.
- [ ] **Step 3: Implement** `dispatch.launch_stale` and `hubstart.start` in the order above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: start a Claude session for a waiting request`.

Covers: D2, D4, D4a, D7 (start name), D9, R1, R2, R3, R4, R4a, R6, R7, R9, R9a.

### Task 4: `relay dispatch --start` command

**Files:**
- Modify: `relaylib/commands.py` (`cmd_dispatch`, parser near the `dp` arguments, `_dispatch_show_text`)
- Test: `tests/test_dispatch_cmd.py`

**Interfaces:**
- Consumes: `hubstart.start` (Task 3), `_dispatch_who(args)`, `_hub_logged(entry, result)`.
- Produces: `--start ID` on `relay dispatch`. It joins `--cancel`, `--show`, `--list` in the "cannot be used together" check, and takes `--slug` but not a repo, a request, `--owner-said` or `--for`.

- [ ] **Step 1: Write the failing tests** in `tests/test_dispatch_cmd.py`, in the file's existing style (run `relay` as a subprocess with the fake `claude` on PATH):
  - `test_start_prints_session_worktree_and_name`: output holds the session id, the worktree path and `"<repo> <slug>"` (R8). The hub log's last line is `{"command": "dispatch", "action": "start", "id": ..., "result": "started"}`.
  - `test_start_unconfirmed_message`: prints that the session may be starting, its id and worktree, and that a later start is allowed after 10 minutes with no session (R9a).
  - `test_start_from_agent_needs_relayed_and_hub`: in an agent env without `--relayed`: refused; with `--relayed` from a session that is not a registered hub: `only a registered hub dispatches; run relay hub first` (D5).
  - `test_start_refusal_logged`: a refusal writes `result: "refused: ..."` to the hub log.
  - `test_show_waiting_with_launch`: `--show` on a waiting request with a launch record prints `Session starting as <slug> in <worktree>: <name> (<session id>), <launch>, since <time>` and not `Started as`.
  - `test_take_after_start`: with a launch record on the request, `relay take --request <id> --slug <slug>` in the worktree succeeds and the record becomes `started` with the take's own `started` (Review Focus 1).
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_dispatch_cmd -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `_dispatch_start(args)`: entry `{"command": "dispatch", "action": "start", "id": args.start, "repo": None}`, `relayed_by, _ = _dispatch_who(args)`, call `hubstart.start`, log `started` or `unconfirmed` or `refused: <reason>`, print as the tests say. In `_dispatch_show_text`, a `started` dict with a `launch` key on a waiting record prints the "Session starting" line.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: relay dispatch --start`.

Covers: D5, R8, R9a (report), R10 (take still works; notices unchanged, see Task 8).

### Task 5: Release a session's features (shared by clear)

**Files:**
- Modify: `relaylib/hubstart.py` (add `held_features` and `release_all`)
- Test: `tests/test_hubstart.py`

**Interfaces:**
- Consumes: `ownership.collect(root)`, `owneractions.published(repo, slug)`, `owneractions.fingerprint(repo, slug)`, `owneractions.run_override(repo, slug, "release", seen, relayed_by=..., via="hub")`, `state.HOLDING_STATUSES`.
- Produces:
  - `held_features(root: str, session_id: str) -> list[str]`: sorted slugs of claims (worktree or origin) owned by `session_id`, with a holding status and not done.
  - `release_all(root: str, session_id: str, relayed_by: str | None) -> list[str]`: first, for every held slug, `owneractions.published(root, slug)` must succeed, else refuse `<slug> is not published on origin; push its branch first` before any release (D8). Then release each in order; a failure refuses with `could not release <slug>: <error>; released so far: <list or none>` (R12). Returns the released slugs.

- [ ] **Step 1: Write the failing tests**:
  - `test_release_all_releases_every_held_feature`: two published features owned by session `old`: both now have no owner on origin and an owner action `release` relayed by the given text.
  - `test_release_all_refuses_unpublished_first`: one published and one only in a local worktree: refusal names the local one; the published one is still owned (D8).
  - `test_release_all_partial_failure`: the second release fails (use `helpers.reject_pushes` after the first): refusal names it and says the first was released.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_hubstart -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** both functions.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: release every feature a retired session holds`.

Covers: D8, R12 (release part).

### Task 6: Clear a closed session

**Files:**
- Modify: `relaylib/hubstart.py` (add `clear`)
- Test: `tests/test_hubstart.py`

**Interfaces:**
- Consumes: Tasks 1, 2 and 5, `hub.registered()`, `events.session_live`, `sessions.record_path`, `sessions._valid`, `dispatch.main_checkout`.
- Produces: `clear(item: dict, relayed_by: str | None, now: float | None = None) -> dict` returning `{"session_id", "name", "old": <old id>, "launch": "ok"|"unconfirmed", "released": [slugs]}`; raises `RelayError` for every refusal and a failed launch. `item` is a digest item from `hub.resolve`.
- Order:
  1. R11 checks, refusing in this order: item kind not `session` (`item <n> is not a session`); provider not `claude` (`clear starts Claude sessions only`); no valid session record, or its `cwd` missing or not a folder (`<label>'s folder is gone`); `(provider, id)` in `hub.registered()` (`<label> is a hub session`); `events.session_live` not None (exact R11 text: `close <label> in the Remote Control list first, then clear again`).
  2. R16: an existing entry with `state == "retired"` whose replacement is live or not stale: refuse `<label> was retired; replaced by <replaced_by_name>, which is still starting or running`. A stale retired entry goes to Task 7's retry path.
  3. R11a: under `retired.locked()`, an entry that is not stale refuses `<label> is already being cleared`; else write the `clearing` entry with a new `replaced_by` uuid and `replaced_by_name = f"{repo} {label} (fresh)"` (D7).
  4. `root = dispatch.main_checkout(cwd)`, `gitops.fetch(root)`, `released = release_all(root, old_id, relayed_by)`. A refusal here leaves the entry `clearing`, which goes stale on its own (R11a).
  5. Under the lock: `state = "retired"`, `retired_at = now`.
  6. Launch with `launch.argv(name, new_id, launch.clear_prompt(old_id, released))` in `cwd`.
  7. R13: under the lock, `launch = "ok"|"unconfirmed"|"failed"` and `launch_error` on failure. A failure then refuses with the reason; releases stand.

- [ ] **Step 1: Write the failing tests** (a saved digest built with `hub.save` holding one session item; fake `claude` on PATH):
  - `test_clear_ok`: old session record with `cwd` in the repo and not live, holding one published feature: the feature is released; the fake ran in that `cwd` with `--remote-control "<repo> <label> (fresh)"`; the entry is `retired` with `launch: "ok"`; result names the new id (R12, R13, R14).
  - `test_clear_refuses_live`: exact R11 text; nothing released; no entry.
  - `test_clear_refuses_hub_codex_gone_folder`: three refusals, nothing changed (R11).
  - `test_clear_uses_saved_digest_after_close`: the item was saved while the session was live, then the session record shows ended: clear goes ahead (R11, Review Focus 5).
  - `test_clear_refuses_unpublished_feature`: refusal names it; no feature released; entry stays `clearing` (D8, Review Focus 3).
  - `test_second_clear_refused`: a `clearing` entry 2 minutes old: `<label> is already being cleared` (R11a).
  - `test_stale_clearing_replaced`: a `clearing` entry 11 minutes old with no live replacement: clear goes ahead (R11a).
  - `test_clear_failed_launch`: `FAKE_EXIT=1`: refusal with the reason; entry `retired`, `launch: "failed"`; feature stays released (R13).
  - `test_clear_refuses_while_replacement_fresh`: a retired entry 3 minutes old: R16 refusal naming the replacement.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_hubstart -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `clear` in the order above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: clear a closed session into a fresh one`.

Covers: D6, D7 (clear name), D8, R11, R11a, R12, R13 (first launch), R14, R16 (clear part).

### Task 7: Clear retry after a stale replacement

**Files:**
- Modify: `relaylib/hubstart.py` (`clear`, retry path)
- Test: `tests/test_hubstart.py`

**Interfaces:**
- Consumes: Task 6.
- Produces: in `clear`, when the old session's entry is `retired` and stale (R16 step 2 passed):
  1. Under the lock, keep `state: "retired"` and set a new `replaced_by`, `replaced_by_name`, `at = now`, and drop `launch` (the reservation, so a second retry within 10 minutes refuses with `<label> is already being cleared`). The old session is never set back to `clearing`, so it never gets notices again.
  2. `gitops.fetch(root)`; `held = held_features(root, stale_id)` for the stale replacement's id. When not empty: `release_all(root, stale_id, relayed_by)` and write a retired entry for the stale replacement `{provider: "claude", session_id: stale_id, state: "retired", replaced_by: new_id, replaced_by_name, at, retired_at, relayed_by}`.
  3. Launch as in Task 6 steps 6 and 7. The prompt lists the old session's released slugs from the first clear (kept in the entry as `released`) plus any from step 2.
- Add `released: [slugs]` to the entry in Task 6 step 5 so the retry can name them.

- [ ] **Step 1: Write the failing tests**:
  - `test_clear_retry_after_failed_launch`: first clear fails to launch; move the clock 11 minutes; second clear launches; the old feature is not released twice (one `release` owner action); entry `launch: "ok"` with the new `replaced_by` (R13, Review Focus 4).
  - `test_clear_retry_releases_stale_replacement`: after the first clear, the stale replacement id takes the feature (set its owner on origin to that id); retry releases it and writes a retired entry for that id (R13).
  - `test_clear_retry_refuses_unpublished_on_replacement`: the stale replacement holds a local-only feature: refusal names it; nothing launched.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_hubstart -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the retry path.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: clear retries after a replacement that never started`.

Covers: R13 (retry), R16 (relaunch only).

### Task 8: `relay hub clear` command and what retired means

**Files:**
- Modify: `relaylib/commands.py` (parser `hsub`, `cmd_hub`, new `cmd_hub_clear`, `cmd_hub_note`), `relaylib/sessions.py` (`capture` notices, `prune`), `relaylib/dispatch.py` (`resolve_asker`), `relaylib/hub.py` (`_session_item`, `_request_item`, `_item_lines`)
- Test: `tests/test_hub.py`, `tests/test_notify.py`, `tests/test_dispatch.py`, `tests/test_sessions.py`

**Interfaces:**
- Consumes: Tasks 1 and 6.
- Produces:
  - `relay hub clear <ref> [--relayed] [--by]`. `cmd_hub_clear`: entry `{"command": "clear", "ref", "target", "action": None, "relayed_by"}`; who asks as `_dispatch_who(args)` (D5: owner's terminal without `--relayed`, a registered hub with it); `item = hub.resolve(args.ref, me)`; `hubstart.clear(item, relayed_by)`; print `relay: started <name> (<new id>); <label> (<old id>) is retired` plus the released slugs (R14); log `cleared`, `unconfirmed` or `refused: ...`.
  - `cmd_hub_note`: after `note_target`, `retired.is_retired(provider, sid)` refuses `<label> was retired; replaced by <replaced_by_name>` (R16).
  - `sessions.capture`: skip both `merged_features` and `waiting_requests` notices when `retired.is_retired(provider, session_id)` (R15).
  - `sessions.prune`: also call `retired.prune(now)` (R19).
  - `dispatch.resolve_asker`: the repo search skips `retired.is_retired` sessions (R18).
  - `hub._session_item`: when the session is retired, `retired = entry["replaced_by_name"]` and `answer_here = False`; `_item_lines` prints `<label> · retired, replaced by <name>` with no other lines (R17). Other session items get `actions: ["clear"]` when the provider is `claude`.
  - `hub._request_item`: `actions: ["start", "cancel"]`; `_item_lines` prints `start: relay dispatch --start <id> --relayed` in place of the `cd ... && claude` line, and keeps the cancel line.

- [ ] **Step 1: Write the failing tests**:
  - `test_hub.py::test_clear_command_logs_and_prints`: a saved digest with a closed Claude session: `relay hub clear <ref> --relayed` from a registered hub prints the new name and `is retired`; the hub log line has `command: "clear"`, `result: "cleared"`.
  - `test_hub.py::test_clear_needs_relayed_from_agent`: an agent env without `--relayed` refuses.
  - `test_hub.py::test_note_refuses_retired`: refusal names the replacement (R16).
  - `test_hub.py::test_digest_shows_retired`: the item reads `retired, replaced by <name>`, no actions, no answer line (R17).
  - `test_hub.py::test_request_item_offers_start`: actions `["start", "cancel"]` and the start line.
  - `test_notify.py::test_retired_session_gets_no_notices`: a retired session's `UserPromptSubmit` gets no request or merge notice; the same session with a `clearing` entry still does (R15). `test_waiting_notice_with_launch_record`: a waiting request with a `started` launch record still notices every session in the repo (R10).
  - `test_dispatch.py::test_resolve_asker_skips_retired`: a repo with one live retired session and one live other: resolves to the other (R18).
  - `test_sessions.py::test_prune_prunes_retired`: `sessions.prune` with an 8-day-old retired entry for a session not live removes it (R19).
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_hub tests.test_notify tests.test_dispatch tests.test_sessions -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the changes above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: relay hub clear, and retired sessions get no notices`.

Covers: D5, R11 (resolve), R14, R15, R16, R17, R18, R19.

### Task 9: Docs and the hub skill

**Files:**
- Modify: `docs/reference.md` (section "Dispatching work to another repo" and "The hub: relay from your phone"), `skills/relay-hub/SKILL.md`
- Test: `tests/test_public.py`, add `test_reference_documents_start_and_clear`.

- [ ] **Step 1: Write the failing test** `test_reference_documents_start_and_clear`: `docs/reference.md` contains `relay dispatch --start`, `relay hub clear`, `claude --bg --remote-control` and `retired`; `skills/relay-hub/SKILL.md` contains `relay dispatch --start` and `relay hub clear`; neither file contains an em-dash.
- [ ] **Step 2: Run** `python3.11 -m unittest tests.test_public -v`. Expected: FAIL.
- [ ] **Step 3: Write** in `docs/reference.md`: both commands, who may run them, the launch command line (D1, D3), the worktree and name (D4, D7), the 10-minute grace and 60-second wait, close-first and the release on clear (D6, D8), and what retired means (R15 to R19). In the skill, replace step 17's "relay never starts one" with: offer "start" for a waiting request and "clear" for a closed Claude session, run `relay dispatch --start <id> --relayed` or `relay hub clear <ref> --relayed` only on words the owner typed in this chat, tell the owner to close a live session in the Remote Control list first, and pass on the session name and id relay prints (R21).
- [ ] **Step 4: Run** `python3.11 -m unittest tests.test_public -v`. Expected: PASS.
- [ ] **Step 5: Run the whole suite** `python3.11 -m unittest discover -s tests -t . -v`. Expected: all pass.
- [ ] **Step 6: Commit** `docs: hub start and clear`.

Covers: R20, R21.

### Acceptance

A1 and A2 need the real Mac and phone after merge. They stay in `docs/relay/hub-start-session/acceptance.md` with status open; the build is reviewed on code and tests (owner rule 2026-10-01).

## Coverage

| Req | Task | Req | Task |
| --- | --- | --- | --- |
| R1 | 3 | R11 | 6, 8 |
| R2 | 3 | R11a | 1, 6 |
| R3 | 3 | R12 | 2, 5, 6 |
| R4 | 3 | R13 | 6, 7 |
| R4a | 3 | R14 | 6, 8 |
| R5 | 2 | R15 | 1, 8 |
| R6 | 2, 3 | R16 | 6, 7, 8 |
| R7 | 3 | R17 | 8 |
| R8 | 4 | R18 | 8 |
| R9 | 2, 3 | R19 | 1, 8 |
| R9a | 2, 3, 4 | R20 | 9 |
| R10 | 4, 8 | R21 | 9 |
