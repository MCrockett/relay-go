# mobile-hub: plan

Builds the spec in `spec.md` (spec GO, codex round 4). Tests first in every task. Gate: `python3.11 -m unittest discover -s tests -t . -v`. Tests use temporary RELAY_HOME, HOME, CLAUDE_CONFIG_DIR and CODEX_HOME, the fake gh from `tests/helpers.py`, and never call real Claude or Codex.

## Two clarifications from the spec GO notes

- **Actions offered = the dashboard's filtered list.** A feature item lists exactly `row["actions"]` from the snapshot, which `snapshot.feature()` has already filtered: merge only when merge readiness passes, review only for an open PR, nothing while a review job runs. `relay hub act` refuses any action not in the saved item's list, then the execution checks in `owneractions` and `reviewjobs` run as usual. Wherever the spec says "what `owneractions.applicable` offers", this filtered list is what is meant.
- **An override during a running review.** D7's sentence that an override is "refused by the fingerprint check" while a review runs is too strong. An override on that feature can pass its fingerprint check while the reviewer works. The review then fails to publish, because its lease check sees the branch moved, and it reports that. The hub does not add a lock of its own for this; the existing lease check protects the result. The hub skill tells the hub to say so if a review it started reports that the branch moved.

## Shapes used below

New module `relaylib/hub.py` (one responsibility: the hub's digest, its storage and its session registry). The CLI handlers live in `relaylib/commands.py` with the other commands, and call into `hub.py`.

Digest item (saved and `--json`):

```
{"n": 1, "kind": "feature", "repo": "bottomsup", "repo_path": "...", "slug": "x", "stage": "build",
 "status": "ready-to-merge", "asks": [{"kind": "merge", "text": "Merge PR #12"}], "wait_since": 1760100000.0,
 "pr": 12, "pr_state": "OPEN", "ci": "green", "actions": ["merge"], "session": {"provider": "claude",
 "session_id": "...", "label": "bottomsup", "state": "waiting"} | null, "answer_here": false, "seen": {...}}
{"n": 2, "kind": "session", "provider": "codex", "session_id": "...", "label": "proteindiary", "state": "permission",
 "pending_tools": ["Bash"], "wait_since": ..., "excerpt": "...", "answer_here": true}
```

`answer_here` is true when the wait is a permission prompt or a question tool: a session item in state `permission`, or a feature item whose session ask has kind `approve` (spec D8). `repo_path` and `seen` are saved but never printed in text mode. The saved digest file also holds `id`, `created_at`, `session` (`{"provider", "session_id"}` or null) and `source`.

## Tasks

### Task 1: cross-process owner action lock

Files: `relaylib/owneractions.py`, `relaylib/commands.py` (`cmd_override`), `relaylib/reviewjobs.py`, `tests/test_owneractions.py`, `tests/test_commands.py`, `tests/test_reviewjobs.py`.

- `action_lock()` keeps the existing `threading.Lock` (threads in the dashboard) and also takes `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `<relay_home>/owner-action.lock`, opened per call. Either failing raises `Conflict("busy: another owner action is running")`. The lock file is created with mode 0o600.
- `action_lock(wait=0)` gains a wait in seconds. With `wait > 0` it retries both locks (thread lock with `acquire(timeout=...)`, then `LOCK_NB` flock polled every 0.1 seconds) until the deadline before refusing.
- Review publication in `reviewjobs` (`reviewjobs.py:273`, which today takes `owneractions.ACTION_LOCK.acquire(timeout=60)` directly) uses `owneractions.action_lock(wait=60)` instead, so a review's push is under the cross-process lock too. Its refusal message stays "another owner action kept the lock; nothing was published. Request again." Dashboard shutdown (`reviewjobs.py:330`) keeps taking the in-process `ACTION_LOCK` only: it coordinates the dashboard's own review threads, which run in that process.
- `relay override` (`cmd_override`, non-review path) wraps its state change and save in `owneractions.action_lock()`, so it also refuses while the dashboard or a hub acts.
- Test: a child process (`subprocess` running `python3.11 -c` that imports `relaylib.owneractions`, enters `action_lock()`, prints "held" and sleeps until stdin closes) holds the lock; the parent's `action_lock()` raises `Conflict` with "busy"; after the child exits, the parent acquires it. A second test shows `relay override go` from an owner terminal is refused with "busy" while the child holds it. A third runs a review job (fake reviewer) to its publish step while the child holds the file lock, with the wait patched to 0.3 seconds: the job publishes nothing and reports "another owner action kept the lock", and the branch on origin is unchanged; with the child gone, the same publish goes through. A fourth shows `action_lock(wait=1)` succeeds when the child releases after 0.3 seconds.

Covers: R13.

### Task 2: hub session registry, left out of other sessions

Files: `relaylib/hub.py` (new), `relaylib/othersessions.py`, `tests/test_hub.py` (new), `tests/test_othersessions.py`.

- `hub.register(provider, session_id, records)`: under `fcntl.flock` on `<relay_home>/hub/lock`, read `hub/sessions.json` (a list of `{"provider", "session_id", "at"}`), drop entries whose session record (from `records`, keyed by provider and id) is missing or has state `ended`, add this session if absent, and write with a temp file and `os.replace`. Returns a warning string or None: an unreadable file is treated as empty and rewritten; a failed write returns "could not record this hub session: <error>", and nothing raises.
- `hub.registered()`: the set of `(provider, session_id)` in the file; an unreadable or missing file gives the empty set.
- `othersessions.listed` drops candidates whose `(provider, session_id)` is in `hub.registered()`. `relay status` and `snapshot.build` both call `listed`, so both leave the hub out.
- Tests: register adds, is idempotent, drops ended and missing sessions; two threads registering two sessions at once both end up in the file; an unreadable file leaves nothing out; an unwritable folder (chmod 0o500) gives the warning; `listed` leaves out a registered waiting session and keeps an unregistered one.

Covers: R14.

### Task 3: snapshot source

Files: `relaylib/hub.py`, `tests/test_hub.py`.

- `hub.load(timeout=5)` returns `(data, source)`, where source is `{"from": "dashboard", "age_seconds": a}` or `{"from": "build", "seconds": t}`.
  - Dashboard path: read `~/.relay/ui.json` through `server._discovery_path()`, check the pid is alive, and `GET /api/snapshot` with the token. The request runs in a daemon thread and the caller waits with `thread.join(timeout)`, so 5 seconds bounds the whole request (connect, headers and body), not each socket operation. If the thread has not finished by then, its result is ignored. The response is used only when it is 200, `loading` is false and `data` is not null.
  - Any failure: `snapshot.build()` timed with `time.monotonic()`.
  - The token is never returned or printed.
- Tests:
  - A fake dashboard (a `http.server` thread in the test, serving a canned snapshot and checking the token header), with `ui.json` written in the temp RELAY_HOME. `hub.load` returns its data with `from: dashboard`. No git or gh process runs: `subprocess.run`, `gitops.run` and `snapshot.build` are patched to fail the test if called.
  - A dashboard answering HTTP 500, one answering 403 (wrong token), one answering `loading: true`, one that delays its headers past the deadline, one that sends headers at once and then trickles the body a byte at a time past the deadline (deadline passed as 0.3 in the test; each fallback returns within 1 second), and a stale `ui.json` with a dead pid each fall back to `build` (patched to return canned data).
  - The token string does not appear in `repr(hub.load(...))`.

Covers: R6.

### Task 4: digest items and order

Files: `relaylib/hub.py`, `tests/test_hub.py`.

- `hub.items(data)` builds the item list from `data["rows"]` and `data["other_sessions"]`:
  - A feature row becomes an item when `waiting_on_owner` is true, or its status is `waiting-owner` or `ready-to-merge`, or a flag starts with "stale" or "fallback GO". Each item copies:
    - repo, slug, stage, status;
    - asks (kind and text);
    - `wait_since`;
    - pr number, PR state and ci;
    - `actions`;
    - `seen`, `repo_path`;
    - the session named by its `answer` or `approve` ask (`provider:session_id`), with label from the row's repo and state `waiting` or `permission`.
  - A row with `error` becomes an error item: repo plus the error text, and no actions.
  - Each `other_sessions` entry becomes a session item.
  - `answer_here` as in the shapes above.
- Order: `wait_since` ascending; items without it last, ordered by repo then slug for features and label then session id for sessions.
- Registered hub sessions (`hub.registered()`, Task 2) are left out here too, so a dashboard snapshot cached before this hub registered does not list it.
- `hub.counts(data, items)`: features listed in `rows` that are not items and not done, and `running` sessions (from `data["running"]`) that are not items.
- Tests: canned snapshot dicts covering:
  - every inclusion rule, the error item and the order with missing wait times;
  - an `approve` ask setting `answer_here` on a feature item, and a `permission` session item;
  - a waiting session whose pending tool is `AskUserQuestion` (a question tool, reached through a permission request) is `answer_here`;
  - a registered hub session present in `other_sessions` of a cached snapshot is left out;
  - `actions` copied unchanged from the filtered row (a ready-to-merge row whose actions lack `merge` gives an item without it);
  - counts.

Covers: R1, R2, R3.

### Task 5: digest storage and references

Files: `relaylib/hub.py`, `tests/test_hub.py`.

- `hub.new_id(now_ms)`: `base36(now_ms) + 4 chars from secrets.choice(digits + lowercase)`.
- `hub.save(items, source, session, now)` writes the digest dict to a temp file in `<relay_home>/hub/digests/`, then `os.link(temp, final)`. On `FileExistsError` it draws a new id, up to 5 tries; then it unlinks the temp file. It returns `(id, None)`, or `(None, warning)` on any OSError. After a save it prunes files whose `created_at` is older than 24 hours, keeping the newest 20 by `created_at`.
- `hub.resolve(ref, session, now)` parses `<id>.<n>`. It loads the digest file, requires the same session (provider and id, or both null) and `now - created_at <= 86400`, and returns the item. Every miss raises `RelayError("unknown reference <ref>: run relay hub for a fresh digest")`: bad format, no file, unreadable JSON, another session, too old, or no item n.
- Tests:
  - Two processes saving at the same patched millisecond with patched `secrets.choice` returning the same characters first: both saves succeed with different ids, and neither file is overwritten.
  - **Wipe test:** save digest A (item 1 = feature x) in session S; `shutil.rmtree` the hub folder; save digest B (item 1 = feature y) in session S. Resolving `A.1` is refused, and `B.1` gives y.
  - Malformed references (`abc`, `abc.`, `.1`, `abc.x`, `abc.0`), a digest file holding invalid JSON, and an item number past the end are each refused as unknown.
  - Another session's reference is refused; one 24 hours plus one second old is refused.
  - Pruning keeps the newest 20 when all are old, and removes old ones beyond that.
  - A hub folder that cannot be written gives `(None, warning)`.

Covers: R7, R7a.

### Task 6: `relay hub` command, text and JSON

Files: `relaylib/commands.py` (parser and `cmd_hub`), `relaylib/hub.py` (`render_text`, `render_json`), `tests/test_hub.py`, `tests/test_commands.py`.

- `relay hub [--json] [--by ...]`:
  1. `identity.detect` decides the session. In an agent session it registers the session (Task 2); from the owner's terminal there is no registration, and the session is null.
  2. Then `hub.load`, `items`, `counts`, `save`, and print.
- **Text output:**
  - Header: `relay hub · digest <id> · dashboard data 12s old`, or `· built in 34s`. When the save failed, the id is replaced by "not saved: actions need a new digest".
  - Items: `<n>. <repo>/<slug> · <stage> <status> · waiting 2h`, then indented lines for:
    - the ask text;
    - `PR #12 · CI green`;
    - `actions: merge, review`;
    - `session: <label> (<provider>) · <state>` when the item has a session (R2);
    - `open this session to answer` when `answer_here` is set.
  - Session items: `<n>. <label> · <provider> session · <state> · waiting 40m`, then:
    - the pending tools for a permission wait;
    - the excerpt, cut to 160 characters;
    - the answer-here line.
  - Lines are wrapped at 78 columns with `textwrap`.
  - Closing line: `Nothing else needs you: 3 features in progress, 2 sessions working.`
  - With no items, the header is followed by `Nothing needs you right now.` and then the closing line.
  - Item numbers are printed only when the digest was saved.
  - Warnings (registration, save) go to stderr.
- **`--json`:** one object `{"digest": id|null, "source": {...}, "items": [...], "counts": {...}, "warnings": [...]}` on stdout, with `seen` and `repo_path` left out of the printed items. Nothing else goes to stdout.
- **Tests (fixture snapshot through a patched `hub.load`):**
  - the text matches an expected block, including the feature item's session line;
  - the empty case;
  - the unsaved-digest header with no numbers;
  - `--json` parses and carries the counts and warnings, including when the hub folder is unwritable;
  - a run with `CLAUDE_CODE_SESSION_ID` set registers the session, and one with the owner env does not;
  - the dashboard token from a fake `ui.json` appears in neither output.

Covers: R1, R2, R3, R4, R5, R6.

### Task 7: notes carry their own prefix

Files: `relaylib/notes.py`, `tests/test_notes.py`.

- `notes.send(..., prefix=None)` stores `"prefix"` on the note only when it is given.
- `line()` and the hook path (`list_for`'s delivery text at `notes.py:244`) use `n.get("prefix") or PREFIX`. `_note` accepts an optional string `prefix`.
- Existing notes without the field behave exactly as before.
- Tests: a note with a prefix posts it to a fake inbox socket and delivers it through the hook path; a note without one still shows the dashboard prefix.

Covers: R8 (prefix).

### Task 8: `relay hub note`

Files: `relaylib/commands.py`, `relaylib/hub.py` (`note_target`, `log`), `tests/test_commands.py`, `tests/test_hub.py`.

- `relay hub note <ref> <text> --relayed [--by]`:
  1. If `--relayed` is missing, refuse with "relay hub note passes on the owner's words: run it with --relayed from the hub session" (`owner_or_relayed` alone would let the owner's terminal through). Then `owner_or_relayed(args, "relay hub note")`, which refuses `--relayed` outside an agent session.
  2. `hub.resolve(ref, my session, now)`.
  3. Session item: target its provider and id. Feature item: target its `session`, or refuse "item <n> has no session to send a note to". Refuse with "open this session to answer" when `answer_here` is set.
  4. Two checks, both current:
     - **Still listed (D5), computed now.** Never from `hub.load` or any dashboard cache. `hub.listed_now(provider, sid)` reads `sessions.read_records()` at the moment of sending and applies the same rules the snapshot uses (the first two are local reads; the feature path below fetches):
       - `othersessions.listed(records, set(), root, config.load(), now)`, with an empty claimed set so feature sessions count too;
       - `leftoff.running(records, {}, root, sessions.alive(records), now, leftoff.codex_turns(...))`.
       - for a feature item, the feature's own session, checked against published state: `owneractions.fingerprint(item.repo_path, slug)` with its default `fetch=True`, which fetches origin first, so an ownership change or a deleted branch pushed from another checkout is seen. Its owner must still be the target, and the feature must not be done. A fetch or lookup failure refuses the note ("could not confirm the feature still names that session: <error>"). This path makes a git fetch and, for a feature with a PR, a gh call, as `fingerprint` does. This path has no age limit, like the feature's ask; `othersessions.listed` drops waits older than `other_sessions_hours`, but a feature's own session stays deliverable as long as the feature claims it.
       The target must be in one of them. Otherwise refuse with "that session is no longer listed; run relay hub again". This is the eligibility rule `snapshot.send_note` applies (`_listed`), extended to feature sessions and evaluated fresh.
     - **Its state now.** A dashboard snapshot can be up to 30 seconds old, so the permission and running checks also read the target's own session record from `sessions.record_path(provider, sid)`, validated with `sessions._valid`:
       - no record, or one that cannot be read: refuse with "that session is no longer known; run relay hub again";
       - state `permission`: refuse with the answer-here message;
       - state `waiting` or `working`: post to the record's inbox; a working session gets the note as it does from the dashboard;
       - state `ended`, or a session `sessions.alive` reports not running: queue with no inbox.
  5. Send with `notes.send(provider, sid, text, inbox, prefix=f"Note from the owner, relayed by {relayed_by} from the relay hub:")`. `notes.send` applies session-notify's text checks (not empty, at most 2,000 characters).
  6. Print "posted to <label>", "queued for <label>'s next prompt", or the not-recorded warning.
  7. Every attempt that gets past argument parsing is logged, refusals and failures included. The handler wraps steps 1 to 6 in `try`/`finally` and logs `result` as `posted`, `queued`, `posted, not recorded` or `refused: <message>`.
- `hub.log(entry)` appends one JSON line to `<relay_home>/hub/log.jsonl` (mode 0o600): `at`, `command`, `ref`, `target`, `action`, `relayed_by`, `result`. It returns a warning on OSError, which is printed to stderr.
- Tests:
  - refused without `--relayed` from an agent env, and from the owner env (the terminal case `owner_or_relayed` alone would allow);
  - refused with `--relayed` and the owner env;
  - live checks after a cached snapshot: the digest saw the session waiting, then its record changes to `permission` (refused, answer-here), to `working` while it is still in `running` (posted to its inbox), is deleted (refused), or becomes `ended` (queued);
  - a feature item's session waiting for 25 hours, still the feature's owner, is posted to; an unclaimed session waiting equally long is refused; after the feature's owner changes through a `relay take` commit pushed from a separate clone, without updating the hub checkout's refs beforehand, the old session is refused; after the feature branch is deleted on origin the same way, it is refused; with fetch failing (origin made unreachable), it is refused with the could-not-confirm message;
  - a session whose record is still valid and waiting but has aged out (its `since` past `other_sessions_hours`, and not running) is refused as no longer listed, even while a fake dashboard's cached snapshot still lists it in `other_sessions` (`hub.load` patched to return that cached data, to prove it is not consulted);
  - empty text and 2,001 characters are refused with session-notify's messages;
  - posted but not recorded: `os.replace` in `notes` patched to fail after a successful post prints the not-recorded warning (the matching `notes.send` cases are already covered in `tests/test_notes.py`; these tests check the hub command reports them);
  - a refusal writes a log line with `refused: ...`;
  - unknown ref;
  - a feature item with no session;
  - an answer-here item;
  - a session no longer listed;
  - posted to a fake inbox socket, with the hub prefix in the posted line;
  - queued when not running;
  - a log line written;
  - the log folder unwritable gives a warning, and the note is still sent.

Covers: R8, R11, R12.

### Task 9: `relay hub act`

Files: `relaylib/commands.py`, `relaylib/owneractions.py`, `tests/test_commands.py`, `tests/test_owneractions.py`.

- `owneractions.run_override(repo, slug, action, seen, relayed_by=None)` passes `relayed_by` to `apply_override`, which records it through `record_owner_action`. It posts no PR comment: spec D11 allows only the merge comment to be published.
- `owneractions.merge(repo, slug, seen, relayed_by=None)`, after a successful `gh pr merge`, when `relayed_by` is set:
  - posts the comment "Merged by the owner, relayed by <relayed_by> from the relay hub" with `gitops.pr_comment` from a temp folder with `-R`, as the merge does;
  - on failure, the message says the PR is merged and the comment could not be posted.
- `relay hub act <ref> <action> --relayed [--reviewer] [--by]`:
  1. If `--relayed` is missing, refuse as in Task 8 step 1; then `owner_or_relayed`, then resolve.
  2. Refuse a session item ("item <n> is a session; use relay hub note"), and an action not in the item's `actions`.
  3. Then:
     - `go`, `extra-round`, `reset-rounds`, `release`: `run_override(item.repo_path, slug, action, item.seen, relayed_by)`;
     - `merge`: `merge(..., relayed_by)`;
     - `review`, `review-spec`, `review-plan`: `reviewjobs.prepare(repo_path, slug, item.seen, args.reviewer, relayed_by, stage=build|spec|plan)` and `job.run()`.
  4. Print the message and exit non-zero on a refusal or failure. As in Task 8, the handler logs every attempt in a `finally`: refusals, failures and successes, including a merge that succeeded when the comment failed.
- `repo_path` must still be a checkout under `projects_root()` (`snapshot.allowed_repo`).
- Tests:
  - A temp repo with a feature at `waiting-owner`, a bare origin and fake gh, plus a saved digest made from its real fingerprint:
    - `extra-round` is recorded with `relayed_by` in `owner_actions` and pushed;
    - after another commit moves the feature, the same reference is refused with "changed since you looked";
    - an action not listed is refused;
    - a session item is refused.
  - `merge` on a ready-to-merge fixture (fake gh reporting mergeable and green): fake gh logs `pr merge` and then a `pr comment` with the hub text; with the comment call made to fail, the output says merged and that the comment failed.
  - `review` with the fake reviewer binary runs and records `relayed_by`. `review-spec` and `review-plan` items call `reviewjobs.prepare` with stage `spec` and `plan` (prepare patched to record its arguments).
  - An override records `relayed_by` and the fake gh logs no `pr comment`.
  - With gh unavailable (fake gh failing `pr view`), `merge` is refused by merge readiness and nothing is merged.
  - Refusals without `--relayed` from an agent env and from the owner env, and `--relayed` from the owner env.
  - A log line per attempt, including a refusal. With the log folder unwritable, a successful merge still reports success, plus a warning that the log line failed.

Covers: R9, R10, R11, R12.

### Task 10: skill, install and README

Files: `skills/relay-hub/SKILL.md` (new), `README.md`, `tests/test_skills.py` if it exists, otherwise `tests/test_hub.py`.

- `SKILL.md` frontmatter: `name: relay-hub`; the description says to use it when the owner wants to see, from a phone or anywhere, what needs them across relay projects, and act on it.
- The body states spec D10's rules, plus:
  - start with `relay hub`;
  - use `relay hub --json` to read references;
  - show items in short form with their numbers;
  - turn the owner's "merge 2" or "tell 3 to go ahead" into `relay hub act <id>.2 merge --relayed` or `relay hub note <id>.3 "<owner's words>" --relayed`;
  - run reviews in the background;
  - on "changed since you looked" or "unknown reference", run `relay hub` again and ask;
  - report "open this session to answer" items as such;
  - if a review reports the branch moved, say so (plan clarification 2).
- `install.sh` already links every `skills/*/` with a SKILL.md, so no change is needed. A test checks the new folder has SKILL.md with `name: relay-hub` and that the text has no em-dash.
- README: a section "### The hub: relay from your phone" covering:
  - start a Claude session in any folder;
  - run `/relay-hub`;
  - turn on remote control;
  - ask "what needs me?";
  - what it can and cannot do (no permission prompts; actions only on your words);
  - that it lists nothing as waiting for itself.

Covers: R15, R16.

### Task 11: full gate

Run the whole suite and fix anything it finds. Check every requirement R1 to R16 (and R7a) against a test or a file named above. Record the result in `docs/relay/mobile-hub/build-notes.md` with `relay commit`.

Covers: R17.

## Coverage

| Requirement | Tasks |
|---|---|
| R1 | 4, 6 |
| R2 | 4, 6 |
| R3 | 4, 6 |
| R4 | 6 |
| R5 | 6 |
| R6 | 3, 6 |
| R7, R7a | 5 |
| R8 | 7, 8 |
| R9 | 9 |
| R10 | 9 |
| R11 | 8, 9 |
| R12 | 8, 9 |
| R13 | 1 |
| R14 | 2 |
| R15 | 10 |
| R16 | 10 |
| R17 | 1 to 11 |
