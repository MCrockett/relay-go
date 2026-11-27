# Plan: dispatch

Implements `docs/relay/dispatch/spec.md`. Python 3.11 standard library only. Tests are `unittest`, run with
`python3.11 -m unittest discover -s tests -t . -v`; integration tests use temp git repos with a bare `origin`
and the fake binaries in `tests/helpers.py`, and `HOME`/`RELAY_ROOT` pointed at temp folders so nothing touches
`~/.relay`. No test calls real Claude, Codex or GitHub.

Each task is written test first: add the failing tests, run them and see them fail, implement, run the module's
tests, then the whole suite, then commit (`feat: ...` or `test: ...`). Tasks are in build order.

## Task 1: the request store

New module `relaylib/dispatch.py`, the only code that reads or writes `~/.relay/dispatch/`.

- `folder()`: `~/.relay/dispatch` (from `HOME`), created with mode 0700.
- `_locked()`: an `fcntl.flock` on `folder()/.lock`, the pattern `hub._locked` uses.
- `new_id(now_ms, taken)`: `q` + base 36 of `now_ms` (reuse `hub._base36`), and `-2`, `-3` ... on a clash
  with an existing file.
- `_write(record)`: JSON to a temporary file in the folder with mode 0600, `os.replace` onto `<id>.json`,
  setting `updated_at` to now on every write.
- `read(id)`: the record, `None` when missing, `DispatchError` naming the file when unreadable. Ids are
  checked against `^q[0-9a-z]+(-[0-9]+)?$` before any path is built.
- `all_records()`: `(records, unreadable)`, skipping and naming unreadable files.
- `prune(now)`: removes `merged`, `released` and `cancelled` records whose `updated_at` is over 30 days old;
  called from `_write`.
- Record fields exactly as spec R5, plus `claim` (R18) and `started`.

Test `tests/test_dispatch.py`: ids are unique and match the pattern, a clash gets a suffix, files are 0600 and
the folder 0700, `_write` is atomic (a failing `os.replace` leaves the old file), `updated_at` moves on every
write, an unreadable file is skipped by `all_records` and named, `prune` drops only old finished records, a bad
id is refused before any path is built.

Covers: R5, R6, R22, R23.

## Task 2: checking a dispatch's inputs

In `relaylib/dispatch.py`:

- `target_repo(name)`: the real path of `<projects root>/<name>` when it is a direct child that is a git
  checkout (`gitops.repo_root` equals it); else `DispatchError("<repo> is not a repository under <root>")`.
  Names with `/`, `..` or a leading `.` are refused.
- `clean_text(text, what)`: `notes.clean`, then refuse empty text and text over 4000 characters.
- `check_slug(slug)`: `commands.SLUG` (moved to `state.SLUG` if importing commands would be circular).
- `resolve_asker(value, hub, target_path)`: `<provider>:<session_id>` must have a valid record
  (`sessions.record_path`, `sessions._valid`); a repo folder name resolves to the one live session
  (`events.session_live`) whose record `cwd` lies inside that repo's main checkout or one of its worktrees
  (`gitops.worktrees`). Refused: unknown session, none or several live (naming them), the hub itself, or a
  session inside the target repo. Returns `{provider, session_id, repo}`.
- `main_checkout(path)`: the real path of the checkout that owns `path` (`git rev-parse --git-common-dir`,
  its parent), used here and by R16.

Test `tests/test_dispatch.py`: each refusal above with its message; a repo name resolves with one live session
and is refused with zero or two; a worktree's session counts for its repo; a 4001-character request is refused
and control characters are removed.

Covers: R1, R1a, R3, R4.

## Task 3: `relay dispatch` (create, cancel, list, show)

`commands.py`: `cmd_dispatch` and its parser:
`relay dispatch [repo] [request] [--owner-said Q] [--slug S] [--for X] [--relayed] [--by P]` and the exclusive
forms `--cancel ID`, `--list [--json]`, `--show ID`.

- Who may (R2): `owner_or_relayed(args, "relay dispatch")`; with `--relayed`, `_hub_session` must be in
  `hub.registered()`, else `only a registered hub dispatches; run relay hub first`. `--list` and `--show` are
  open to anyone.
- Create: validate (Task 2), require `--owner-said` with `--relayed`, write the record with status `waiting`,
  print the id and the two start commands. Hub log line `{"command": "dispatch", "id", "repo", "result"}`
  via `hub.log` (warning on failure). With `--for`, send the asker the R7 note through `events._send`
  (queued when not open; a warning on failure). No hub event.
- `--cancel`: the R2 rule, then under the lock: refuse when not waiting, else status `cancelled`. Hub log
  line. (Claims do not exist yet; Task 5 adds the claim rules to cancel.)
- `--list [--json]`: waiting and started records, oldest first, with any unreadable-file warning on stderr.
- `--show`: the whole record as text (request, owner's words, who asked, asker, status, claim, started).
  Reconcile comes in Task 6.

Test `tests/test_dispatch_cmd.py` (subprocess-free, calling `commands.main` with patched env as
`tests/test_note_cmd.py` does): a registered hub dispatches and the file holds every field; an unregistered
agent, `--relayed` from the owner's terminal and a missing `--relayed` are refused; `--owner-said` is required
when relayed; the asker gets one queued note; the hub log line is written; cancel of a waiting request works
and of a started one is refused with its status; list order and JSON; show prints everything. Failure paths:
an unwritable dispatch folder exits 1 with the error and leaves no file; a failed hub log write is a warning and
the request stands; a failed note to the asker is a warning and the request stands.

Covers: R1, R2, R3, R7, R8, R13.

## Task 4: finding a request's feature on any branch

Files: `relaylib/dispatch.py`, `relaylib/gitops.py`.

`relaylib/dispatch.py`: `features_with(root, id)` returns `[(slug, ref, where)]` where `where` is `origin` or
`local`. It fetches (`gitops.fetch`), then reads `docs/relay/*/state.md` with `gitops.ls_files` and
`gitops.show` on every `refs/remotes/origin/*` branch and every `refs/heads/*` branch (a new
`gitops.local_branches(root)` using `for-each-ref`), checked out or not, and the working copy of each worktree
(uncommitted state counts as local). Unlike `ownership.collect`, a copy whose `branch` field names another
branch is kept: a feature merged into develop after its own branch was deleted may be the only evidence left.
Results are deduplicated by slug, an origin copy winning over a local one (with its own branch preferred over
an inherited or merged copy for `ref`). A fetch failure raises `DispatchError` with git's message.

Test `tests/test_dispatch.py` with temp repos: found on origin; found only on a local branch that is not checked
out; found in a worktree; found only on origin's develop after the feature merged and its branch was deleted;
a feature on its own branch and on a stacked branch is reported once, with its own branch; nothing found;
fetch failure.

Covers: R18 step 1.

## Task 5: claim and publish

`relaylib/dispatch.py`, every function under the lock, reading the record fresh:

- `claim(id, me, slug, branch, now)`: record must be `waiting`; an existing claim must be this session's, or
  not publishing and (session not live or older than 30 minutes); writes
  `claim: {provider, session_id, slug, branch, at, publishing: false}`. Refusals as spec R18 step 2.
- `enter_publishing(id, me)`: record still `waiting` and claim still `me`, sets `publishing: true`; else
  `DispatchError` (`was cancelled`, `was taken over by ...`).
- `mark_started(id, feature)`: the one started transition, used by every path (Task 6's reconcile before a
  start, `published`, `reconcile_dead`). Under the lock it sets status `started`, fills `started`, removes the
  claim, and reports whether the status changed. Only when it changed does it then (outside the lock) send the
  R19 note to each requester (`requesters(record)`: the hub when `requested_by` is a session, and the asker,
  through `events._send`) and write the hub log line `{"command": "event", "event": "started", ...}`. So each
  requester hears once, whichever path found the feature. Failed notes or log lines are returned as warnings.
- `published(id, me, feature)`: `mark_started` after a successful push.
- `push_failed(id, me)`: `publishing: false`, claim kept.
- `drop_claim(id, me)`: removes this session's claim (failure before the commit).
- `reconcile_dead(id, now)`: for a publishing claim whose session is not live: `features_with` on the
  recorded repo; on origin, `mark_started` from it and return it; not found, clear `publishing`; fetch fails,
  leave it and raise with the error. A live publisher is left alone.

Test `tests/test_dispatch.py` with `FakeClock` and `helpers.live_session`: a second live session is refused; a
gone session's claim and a 31-minute-old claim can be taken over; a publishing claim cannot be taken over or
cancelled at any age; `enter_publishing` fails after a cancel and after a takeover; `push_failed` keeps the
claim; `reconcile_dead` marks started when origin has the feature, clears publishing when not, and leaves a
live publisher; `mark_started` called twice (by `reconcile_dead` and then by a start, and by `--show` then
`--cancel`) sends each requester exactly one note and one log line; a failed note is a warning.

Cancel gains the claim rules here (`commands.py` `--cancel`): refuse a live claim; for a publishing claim, call
`reconcile_dead` first and refuse if it marked the request started (`was started as <slug> in <repo>`) or the
publisher is live; a claim that may be taken over is removed by the cancel. Tests: each of these cancel cases.

Covers: R8, R18 steps 2 to 5, R19.

## Task 6: `relay new --request` and `relay take --request`

`commands.py`:

- `relay new` gains `--request ID` (refused with `--idea`/`--idea-file`). `relay take` gains `--request ID`,
  `--slug`, `--small`, `--type`; with `--request` it builds the arguments and calls `cmd_new`'s body (a new
  `start_feature(args, request=...)` that `cmd_new` also uses), slug from `--slug` or the record, refused per
  R15 without one. `relay take` without `--request` is unchanged.
- In `start_feature`, before `ownership.check_can_write`: read the record (R16 refusals: missing, cancelled,
  started, other repo by `main_checkout(root)` against the recorded path), then `features_with` (Task 4):
  on origin, `mark_started` (which sends the R19 notes only if the record was not already started) and
  refuse; local only, refuse with the push-or-delete message. Then, when the record holds a publishing claim
  whose session is gone, `reconcile_dead` (so a start attempt alone clears a dead publisher's claim after a
  successful fetch finds nothing). Then `claim`. Any refusal of `relay new`'s own checks after this point calls
  `drop_claim` (a `try/except RelayError` around the checks and the commit).
- The idea text (R14) from a pure `idea_text(record, slug, now)`; `state.md` gets `request` (R17) and the
  owner action via `record_owner_action(st, "owner asked for this through relay dispatch", relayed_by)`, with
  `relayed_by` from the record (`None` for the owner).
- Before `ciskip.push`: `enter_publishing`; on refusal, `git switch` back to the start branch and raise with the
  local branch and `git branch -D <branch>`. After the push: `published`; a failed push calls `push_failed`
  and re-raises. A failed `published` write is a warning (the next start or show reconciles it). The R19
  notes come from `mark_started` (Task 5); other hubs get nothing.
- `--show` (Task 3) calls `reconcile_dead` for a publishing claim, or `features_with` when a claimed or waiting
  record is shown from inside its repo, and prints the stored record with a warning when either fails.

Test `tests/test_dispatch_cmd.py` (temp repos and bare origin): `relay new x --request q..` writes the R14 idea
(with and without an asker, owner-dispatched wording, `--small`), state has `request` and the owner action,
the record is `started` and both requesters got one note; `relay take --request` uses the dispatch slug and
refuses without one; R16 refusals change nothing (branch unchanged, no claim); a feature already on origin
marks the record started and refuses; a local-only branch refuses with the push hint; an open-PR refusal
drops the claim; a cancel between commit and push (patched hook) stops before the push and names the branch;
a rejected push (`helpers.reject_pushes`) leaves the claim with `publishing: false`; `--show` reconciles a dead
publisher and warns when fetch fails; a dead publisher whose local branch was deleted is cleared by a plain
retry of `relay take --request` with no show or cancel first, and the retry starts the feature; a failure to
write `started` after a successful push is a warning, and the next `relay new --request` finds the feature on
origin, marks it started and refuses, with the requesters told once.

Covers: R14, R15, R16, R17, R18 (all steps), R19, R13.

## Task 7: telling the target repo's sessions

- `relaylib/notices.py`: `waiting_requests(session_id, cwd)` returns `[(key, line)]` with key
  `dispatch:<id>` for each waiting record whose path equals `dispatch.main_checkout(cwd)`, line as spec R12.
  Any exception returns `[]`.
- `relaylib/sessions.py` `capture`: alongside `merged_features`, compute the request notices; they share the
  `notified` list (keys are distinct), and `notices.render` gets them as extra lines before the notes.

Test `tests/test_sessions.py` and `tests/test_notices.py` (new): a session in the repo and one in its
worktree are told once and not again; a session in another repo is not told; a started request is not
announced; a broken dispatch folder leaves the hook output unchanged and exit 0.

Covers: R12.

## Task 8: hub, hub page and status

Files: `relaylib/hub.py`, `relaylib/hubpage.py`, `relaylib/status.py`, `relaylib/commands.py` (`cmd_status`
passes the records).

- `relaylib/hub.py` `items`: add `_request_item(record)` for each waiting record (`kind: "request"`, repo,
  path, id, `wait_since: created_at`, first line, `actions: ["cancel"]`), sorted with the rest by `_order`
  (extend its name key for requests). `_item_lines` renders R9's text and the start command. `counts` ignores
  requests. Unreadable records become a warning in `digest`'s warnings. `resolve` keeps working (items are
  saved in the digest as now).
- `relaylib/hubpage.py` `_card`: a request card with the same text, no path (as `redact` already ensures).
- `relaylib/status.py` `render`: a "Requests waiting for a session" block after the other sessions, one line
  per waiting request (`* <repo> · request <id> · waiting <age>` and the first line indented), and the count
  added to the `waiting on you (*)` total. `cmd_status` passes the records in.

Test `tests/test_hub.py`, `tests/test_hubpage.py`, `tests/test_status.py`: a waiting request is numbered among
features by its time; started and cancelled requests are not shown; the page card has no path; the status
block and total; an unreadable record gives a warning, not a failure.

Covers: R9, R10, R11, R23.

## Task 9: requesters hear of the merge

Files: `relaylib/events.py`, `relaylib/merged.py`, `relaylib/owneractions.py`, `relaylib/dispatch.py`
(`mark_landed`).

- `relaylib/events.py`: `event()` gains `requesters` (list of `(provider, session_id)`) and `request_id`.
  In `deliver`, for `merged` and `released`: the addition ` This was your request <id>.` goes into the note of
  each registered hub that is a requester; each requester that is not a registered hub, is not the cause, and
  is not the feature's owner gets its own note with the addition; a requester that is the owner gets the
  owner note with the addition, once. A requester that is the cause is skipped. With an owner-dispatched
  request (no requesters), every hub's note gets the addition.
- `events.requesters_of(st)`: from `st["request"]` (`requested_by` when a session, `asked_by`).
- Every merge or release queue site passes `requesters` and `request_id`: `merged._queue_merge_event`,
  `owneractions.py` lines 323, 381 and 402 (the delegated record event too).
- At each of those sites, `dispatch.mark_landed(id, kind)` sets the record to `merged` or `released` when
  the event is queued, whether or not a note is then sent; a missing or unreadable record is ignored (R21).

Test `tests/test_events.py`: a hub requester gets one note with the addition; a non-hub asker gets its own
queued note; an asker that is also the feature owner gets one; the cause is not told; an owner-dispatched
request adds the line for every hub; a feature without `request` delivers exactly as before; a `released`
event behaves like `merged`; with `owner_only` the hub notes are skipped but a non-hub requester still gets
its note; with `log_only` no note goes to anyone, requesters included, and the record is still marked.
`tests/test_merged.py` and `tests/test_owneractions.py`: a found merge and an owner merge pass the requesters
and mark the record; a missing record still sends the notes; a delegated merge (the `owneractions.py`
line 402 record event and the final event) passes the requesters once, not twice; a merge whose requester is
the cause marks the record `merged` though no note is sent.

Covers: R20, R21.

## Task 10: skills and docs

- `skills/relay-hub/SKILL.md`: hand work to another repo with `relay dispatch <repo> "<request>"
  --owner-said "<the owner's words>" [--for <session or repo>] [--slug <slug>] --relayed`, not a peer
  message; `--for` the session that asked when the work came from one; give the owner the id;
  `relay dispatch --show <id>` for where it stands; a cancel only on the owner's word.
- `skills/relay-idea/SKILL.md`: a session told of a request, or asked to take one, runs `relay dispatch
  --show <id>` and then `relay take --request <id>` or `relay new <slug> --request <id>`, and continues as
  for any idea; a session that needs work in another repo asks the hub to dispatch it.
- `docs/reference.md`: "Dispatching work to another repo" under the hub section (commands, who may, where
  requests live and that they are per machine, claim and recovery in plain words, notices, merge notes).
- `README.md`: `relay dispatch` in the command list.
- Plain English, no em-dashes. `tests/test_public.py` (if it checks docs or skills) must stay green.

Test: `grep` for em-dashes in the changed files returns nothing; the full suite passes.

Covers: R24, R25, R26.

## Task 11: whole-suite check

Run `python3.11 -m unittest discover -s tests -t . -v` and confirm it passes; run `relay dispatch --help`,
`relay new --help` and `relay take --help` from the worktree to check the wiring. No commands against the
live `~/.relay`.

Covers: R1 to R26 (verification).
