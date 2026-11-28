# hub-start-session: spec

## Why

A dispatched request waits until the owner opens a Claude session in the target repo at the Mac. Every other step works from the phone through the hub. This feature lets the hub start that session, and replace a tired one, on the owner's typed go, without anyone at the Mac.

## Decisions

- D1. Launch mechanism: Claude Code's own background sessions, `claude --bg`. relay runs, with no shell and an argument list:
  `claude --bg --remote-control <name> --session-id <uuid> "<prompt>"`
  with the working folder set to the new worktree. Reasons: `--bg` needs no terminal window, no tmux (not installed on this Mac) and no AppleScript. The session keeps running after relay exits. `claude agents`, `claude attach` and `claude stop` already manage it. `--remote-control` puts it in the owner's Remote Control list. Terminal or Ghostty windows were rejected because they need a logged-in desktop and AppleScript permissions, and tmux was rejected because it is not installed.
- D2. Session id: relay makes a fresh UUID (`uuid.uuid4()`) and passes it with `--session-id`, so it knows the id before launch. It does not parse `claude --bg` output for the id. Once the session's first hook fires, the existing session records (`relaylib/sessions.py`) pick it up as for any session.
- D3. Permissions: relay passes no `--permission-mode`, no `--dangerously-skip-permissions` and no `--allow-dangerously-skip-permissions`. The session uses the owner's normal settings. A permission prompt in the new session shows in the hub like any other session's (state `permission`), and the owner answers it from the Remote Control list.
- D4. Worktree: relay makes the worktree itself, at `<main checkout>/.claude/worktrees/<slug>`, detached at `origin/<base>`. It does not use `claude --worktree`, because relay needs the path and branch to match its own layout and tidy rules. The session then runs `relay take --request <id>`. That creates `feat/<slug>` inside the worktree, because `relay new` already works on the folder it is run from.
- D4a. Base: `<base>` is the base `relay take` would pick in that repo. Request qmx0hasm1 (dispatched the same day) replaces the "develop if it exists" rule with one shared base choice: the repo's relay config, then `origin/HEAD`. This feature calls that same function, so the worktree and the branch `relay take` makes always agree. qmx0hasm1 merges first. This feature's plan starts after it and uses its function by name.
- D8. Clear releases: the owner typing "clear" means a fresh session takes the work over. So `relay hub clear` runs the owner release (`owneractions.run_override(..., "release")`, recorded as relayed) on every feature the old session owns in that folder's repo, before the launch. Without it, `relay take` in the fresh session would refuse, because ownership does not look at whether a session is live. The release only works on a feature whose branch is on origin, so clear first checks every such feature and refuses, changing nothing, if any branch is not published, naming the feature. The old session is already closed (D6), so no stop note is needed.
- D9. Grace: a started session that has not shown up within 10 minutes is treated as not started. A cold start with plugin sync and a first hook takes one or two minutes on this Mac, so 10 minutes leaves room. Until then the owner can still reach the session through Remote Control. At exactly 10 minutes or later, with no live session record, the start no longer blocks.
- D5. Commands. Both need `--relayed`, as `relay hub act` does, and are logged to the hub log with the result:
  - `relay dispatch --start <id> --relayed` starts a session for a waiting request.
  - `relay hub clear <digest ref> --relayed` replaces the session behind a hub digest item of kind `session`.
  From the owner's own terminal (no hub session), `--relayed` is not needed, as for `relay dispatch --cancel`.
- D6. Retire, not kill, and close first: "clear" starts a fresh session in the old session's folder and records the old one as retired. relay never stops, signals or types into the old process. The owner keeps one session per repo by hand, and relay cannot stop a live session from editing files. So clear refuses while the old session is live (`events.session_live`). The owner closes it from the Remote Control list first, then says clear again. Two sessions never share a folder.
- D7. Name: the Remote Control name is `<repo> <slug>` for a start, and `<repo> <old label> (fresh)` for a clear, so the owner can tell sessions apart on the phone.

## Non-goals

- Starting sessions for Codex. Claude only. Codex has no background plus Remote Control mode.
- Starting sessions on another machine. The request's repo must be on this machine, as dispatch already requires.
- Stopping, killing or closing any session.
- Starting more than one session per request.
- Ownership moves on clear only through the owner release in D8. There is no new ownership rule.

## Requirements

### Start

- R1. `relay dispatch --start <id> [--slug <slug>]` refuses unless the request exists on this machine and its status is `waiting`. The slug is `--slug`, or else the request's suggested slug, checked with `dispatch.check_slug`. With neither, it refuses with: `request <id> has no slug; run relay dispatch --start <id> --slug <slug> --relayed`.
- R2. Under the dispatch lock (`dispatch.mutate`), it refuses while a live session claims the request (`dispatch.claim_blocks`), or while the request has a `started` record that is not stale. A `started` record is stale when its session has no live session record (`events.session_live`) and its `at` is 10 minutes old or more (D9). The check and the write of the new `started` record (R7) happen in one mutate, so two starts cannot both pass.
- R3. It refuses when `claude` is not on PATH, naming the problem. The request stays `waiting`.
- R4. It fetches origin and resolves the base as in D4a. It refuses, changing nothing, when the fetch fails, when the base cannot be resolved, or when `origin/<base>` does not exist. On a first start it makes the worktree as in D4, and it refuses, changing nothing, if that path already exists or the branch `feat/<slug>` already exists locally or on origin.
- R4a. Re-start after a stale start: when the stale `started` record names a worktree that still exists, relay never deletes it or resets it. If that worktree already holds the feature (`docs/relay/<slug>/state.md` exists there), the earlier session got as far as `relay take`. The re-start then refuses, changing nothing, with: `<slug> was already started in <worktree> by session <id>; its work is kept there. Finish it at the Mac, or push its branch and run relay override release`. Otherwise relay reuses the worktree as it is, skips the branch check in R4, and launches there. If the recorded worktree is gone, the start goes on as a first start.
- R5. It launches the session as in D1 and D3. The argument list must not contain any bypass flag. A test checks this on the exact argument list.
- R6. The prompt tells the session, in plain English, to run `relay dispatch --show <id>`, then `relay take --request <id> --slug <slug>` with the slug chosen in R1, then continue with the relay skills through spec, plan and build, and that the owner merges. The slug is also stored in the `started` record (R7).
- R7. Before launch, in the same mutate as R2, it records on the request: `started = {provider: "claude", session_id, slug, worktree, name, at, relayed_by, launch: "pending"}`. After launch, `launch` becomes `ok`, `unconfirmed` (R9a) or is removed with the whole record (R9).
- R8. It prints the session id, the worktree path and the Remote Control name. The hub passes them to the owner.
- R9. Failed launch: `claude --bg` exits non-zero, or `claude` cannot be run at all (OSError before the process starts). relay removes the worktree only if it made that worktree in this run and the worktree is still clean and detached at the base commit, then clears `started`, and refuses with the first line of `claude`'s error output. A reused worktree (R4a) is never removed. The request stays `waiting`.
- R9a. Unconfirmed launch: `claude --bg` does not exit within 60 seconds. relay leaves the process running, keeps the worktree and sets `launch: "unconfirmed"`. It deletes nothing. It reports to the hub that the session may be starting, names the session id and the worktree, and says a later start is allowed once the record is stale (R2). A session record for that id appearing later is the confirmation. No extra step is needed, because R2 reads it.
- R10. Notice and event routing for the request does not change. Every session in the repo keeps getting the waiting-request notice until the request is claimed, so the owner can still take it by hand. Request progress events go to the requesters as today. The started session gets feature events once it owns the feature, as any owner does.

### Clear

- R11. `relay hub clear <ref>` resolves the digest item as `relay hub note` does. It refuses unless the item is a session with provider `claude`, the session's record has a folder (`cwd`) that still exists, the session is not a registered hub session, and the session is not live. When it is live, the refusal says: `close <label> in the Remote Control list first, then clear again`.
- R11a. Reservation: under the lock of `<relay home>/sessions/retired.json`, relay refuses if the old session already has an entry that is not stale, and otherwise writes `{provider, session_id, state: "clearing", replaced_by, at, relayed_by}`. An entry is stale when its replacement has no live session record and its `at` is 10 minutes old or more (D9). A stale entry, including a `clearing` entry left by a relay run that died, is replaced. A second clear of the same session within that time sees the entry and refuses, so only one replacement can start.
- R12. Order: reserve (R11a), check that every feature the old session holds is published (D8), release them, set the entry to `state: "retired"` with `retired_at`, then launch a fresh session in the old session's folder as in D1 and D3. The prompt names the old session id and tells the fresh session to run `relay status`, read any handoff, and `relay take` each feature the old session held. A release that fails partway refuses before the launch and names the feature. Features already released stay released, and the owner can see that in `relay status`.
- R13. After the launch: on success the entry keeps `state: "retired"` and records the launch as `ok`. On a failed launch (R9 rules) the entry stays retired and records the failure, and relay refuses with the reason. On an unconfirmed launch (R9a rules) it records `unconfirmed`. In both of those cases the releases stand. The owner retries with the same `relay hub clear <ref>` once the replacement is stale (D9). The retry looks at current ownership first. relay knows the stale replacement's id from the entry (`replaced_by`), because relay started it. If that replacement already owns any feature in the repo, the retry releases those features under the D8 rules (published check first, refusing and changing nothing if a branch is not on origin), and records the stale replacement as retired with its own entry. Then it launches again. The stale replacement does not need to be a digest item. The ownership is already settled before any launch, so a replacement that starts late can still run `relay take`.
- R14. It prints the new session id and name, and that the old session is retired.

### What retired means

- R15. A session whose entry has `state: "retired"` gets no dispatch request notices (`notices.waiting_requests`) and no merge notices. An entry in `clearing` state changes nothing. The `launch` field (`ok`, `failed`, `unconfirmed`) never affects notices.
- R16. `relay hub note` refuses a retired session, naming its replacement. `relay hub clear` refuses a retired session while its replacement is live or not yet stale. After that it only relaunches (R13).
- R17. The hub digest shows a retired session as `retired, replaced by <name>`, and offers no actions for it.
- R18. `relay dispatch` asker resolution (`dispatch.resolve_asker`) skips retired sessions when it looks for a live session in a repo.
- R19. A retired entry is pruned (in `sessions.prune`) only when its `retired_at` is 7 days old or more and the old session has no live session record. While the old session is live, the entry stays, so it never starts getting notices again. Seven days matches the session record retention already in `sessions.prune`. A `clearing` entry is pruned once it is stale (R11a) and older than 7 days.

### Docs and hub

- R20. `docs/reference.md` documents both commands, the retire rules and the launch command line.
- R21. The relay-hub skill tells the hub when to offer "start" for a waiting request and "clear" for a session, and to run either only on words the owner typed in the hub chat.

## Failure paths

- `claude` is missing, logged out or fails: R3 and R9. The request stays waiting, and the hub is told why. The worktree is removed only if this run made it and it is still clean at the base commit.
- `--bg` accepts the launch but the session dies at once: relay cannot see it at launch. After the grace in D9, R2 lets the owner start again. R4a reuses the worktree, or refuses with recovery steps if the feature was already started there.
- The launcher hangs: R9a. Nothing is deleted.
- The new session hits a permission prompt or an untrusted-folder prompt: it shows in the Remote Control list and in the hub as `permission`. The owner answers it. relay does not try to pre-trust folders.
- The worktree path or branch already exists: R4 refuses before anything changes.
- Two starts race: R2 checks and writes in one locked mutate, so the second refuses.
- Two clears race: R11a reserves under a lock, so the second refuses.
- The owner asks to clear a session that is still open: R11 refuses and says to close it first, so two sessions never edit one folder.
- Clear on a session whose folder is gone: R11 refuses.

## Acceptance (after merge, on this Mac)

- A1. From the hub on the phone, start a real waiting request. The session shows in the Remote Control list within 3 minutes, in `.claude/worktrees/<slug>`, and runs `relay take`. This relaxes the idea's "within a minute" to 3 minutes, a scope adjustment because a cold start takes one or two minutes (D9). Recorded in `docs/relay/hub-start-session/acceptance.md`.
- A2. Clear a real session from the hub. The fresh session appears, and the old one shows as retired in the digest.

## Open questions
