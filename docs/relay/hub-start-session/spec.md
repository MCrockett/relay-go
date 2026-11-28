# hub-start-session: spec

## Why

A dispatched request waits until the owner opens a Claude session in the target repo at the Mac. Every other step works from the phone through the hub. This feature lets the hub start that session, and replace a tired one, on the owner's typed go, without anyone at the Mac.

## Decisions

- D1. Launch mechanism: Claude Code's own background sessions, `claude --bg`. relay runs, with no shell and an argument list:
  `claude --bg --remote-control <name> --session-id <uuid> "<prompt>"`
  with the working folder set to the new worktree. Reasons: `--bg` needs no terminal window, no tmux (not installed on this Mac) and no AppleScript. The session keeps running after relay exits. `claude agents`, `claude attach` and `claude stop` already manage it. `--remote-control` puts it in the owner's Remote Control list. Terminal or Ghostty windows were rejected because they need a logged-in desktop and AppleScript permissions, and tmux was rejected because it is not installed.
- D2. Session id: relay makes a fresh UUID (`uuid.uuid4()`) and passes it with `--session-id`, so it knows the id before launch. It does not parse `claude --bg` output for the id. Once the session's first hook fires, the existing session records (`relaylib/sessions.py`) pick it up as for any session.
- D3. Permissions: relay passes no `--permission-mode`, no `--dangerously-skip-permissions` and no `--allow-dangerously-skip-permissions`. The session uses the owner's normal settings. A permission prompt in the new session shows in the hub like any other session's (state `permission`), and the owner answers it from the Remote Control list.
- D4. Worktree: relay makes the worktree itself, at `<main checkout>/.claude/worktrees/<slug>`, detached at `origin/<base>` (base `develop`, as `relay new` uses). It does not use `claude --worktree`, because relay needs the path and branch to match its own layout and tidy rules. The session then runs `relay take --request <id>`. That creates `feat/<slug>` inside the worktree, because `relay new` already works on the folder it is run from.
- D5. Commands. Both need `--relayed`, as `relay hub act` does, and are logged to the hub log with the result:
  - `relay dispatch --start <id> --relayed` starts a session for a waiting request.
  - `relay hub clear <digest ref> --relayed` replaces the session behind a hub digest item of kind `session`.
  From the owner's own terminal (no hub session), `--relayed` is not needed, as for `relay dispatch --cancel`.
- D6. Retire, not kill: "clear" starts a fresh session in the old session's folder and records the old one as retired. relay never stops, signals or types into the old process. The owner closes it.
- D7. Name: the Remote Control name is `<repo> <slug>` for a start, and `<repo> <old label> (fresh)` for a clear, so the owner can tell sessions apart on the phone.

## Non-goals

- Starting sessions for Codex. Claude only. Codex has no background plus Remote Control mode.
- Starting sessions on another machine. The request's repo must be on this machine, as dispatch already requires.
- Stopping, killing or closing any session.
- Starting more than one session per request.
- Moving a feature's ownership on clear. The fresh session takes features over with the normal `relay take`, which the prompt tells it to run.

## Requirements

### Start

- R1. `relay dispatch --start <id>` refuses unless the request exists on this machine and its status is `waiting`.
- R2. It refuses while a live session already claims the request (`dispatch.claim_blocks`), and while a session that relay started for this request is still live. That stops two sessions for one request.
- R3. It refuses when `claude` is not on PATH, naming the problem. The request stays `waiting`.
- R4. It makes the worktree as in D4. It refuses, changing nothing, if that path already exists, if the branch `feat/<slug>` already exists locally or on origin, or if the main checkout is not a git repo. The slug is the request's suggested slug, checked with `dispatch.check_slug`.
- R5. It launches the session as in D1 and D3. The argument list must not contain any bypass flag. A test checks this on the exact argument list.
- R6. The prompt tells the session, in plain English, to run `relay dispatch --show <id>`, then `relay take --request <id>`, then continue with the relay skills through spec, plan and build, and that the owner merges.
- R7. Before launch, it records on the request: `started = {provider: "claude", session_id, worktree, name, at, relayed_by}`. A start that fails after this point (R9) removes the record again.
- R8. It prints the session id, the worktree path and the Remote Control name. The hub passes them to the owner.
- R9. A launch failure (non-zero exit, timeout after 60 seconds, or OSError) removes the worktree it made, clears `started` on the request, and refuses with the first line of `claude`'s error output. The request stays `waiting`.
- R10. The request's notices and events treat the started session as the expected taker. When it runs `relay take --request`, the existing claim works unchanged.

### Clear

- R11. `relay hub clear <ref>` resolves the digest item as `relay hub note` does. It refuses unless the item is a session with provider `claude`, the session's record has a folder (`cwd`) that still exists, and the session is not a registered hub session.
- R12. It launches a fresh session in the old session's folder as in D1 and D3, with a prompt that names the old session id and tells the fresh session to run `relay status`, then take over the features that session held with `relay take`, reading any handoff first.
- R13. Only after a successful launch does it record the old session as retired: `{provider, session_id, retired_at, replaced_by, relayed_by}` in `<relay home>/sessions/retired.json`, written under a lock. If the launch fails, nothing is retired and the hub gets the reason, as in R9.
- R14. It prints the new session id and name, and says that the owner should close the old session.

### What retired means

- R15. A retired session gets no dispatch request notices (`notices.waiting_requests`) and no merge notices.
- R16. `relay hub note` and `relay hub clear` refuse a retired session, naming its replacement.
- R17. The hub digest shows a retired session as `retired, replaced by <name>`, and offers no actions for it.
- R18. `relay dispatch` asker resolution (`dispatch.resolve_asker`) skips retired sessions when it looks for a live session in a repo.
- R19. Retired entries older than 7 days are pruned with the session records (`sessions.prune`).

### Docs and hub

- R20. `docs/reference.md` documents both commands, the retire rules and the launch command line.
- R21. The relay-hub skill tells the hub when to offer "start" for a waiting request and "clear" for a session, and to run either only on words the owner typed in the hub chat.

## Failure paths

- `claude` is missing, logged out or fails: R3 and R9. The request stays waiting, the worktree is removed, and the hub is told why.
- `--bg` accepts the launch but the session dies at once: relay cannot see it at launch. The hub's session list shows no live session for the started id, and R2 then lets the owner start again.
- The new session hits a permission prompt or an untrusted-folder prompt: it shows in the Remote Control list and in the hub as `permission`. The owner answers it. relay does not try to pre-trust folders.
- The worktree path or branch already exists: R4 refuses before anything changes.
- Two starts race: the request is mutated under the dispatch lock (`dispatch.mutate`), and the second start sees `started` with a live or not-yet-seen session and refuses. A started session not seen within 10 minutes of `at` no longer blocks (R2).
- Clear on a session whose folder is gone: R11 refuses.

## Acceptance (after merge, on this Mac)

- A1. From the hub on the phone, start a real waiting request. The session shows in the Remote Control list within a minute, in `.claude/worktrees/<slug>`, and runs `relay take`. Recorded in `docs/relay/hub-start-session/acceptance.md`.
- A2. Clear a real session from the hub. The fresh session appears, and the old one shows as retired in the digest.

## Open questions
