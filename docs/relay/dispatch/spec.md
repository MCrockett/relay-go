# Spec: dispatch

## Why

The hub hands work to other repos with peer messages today, and the receiving session has to remember to run
`relay new`. Until it does, relay does not know the work exists: the hub cannot show it, nobody is told when
it merges, and a session that forgets leaves the work outside relay. On 2026-11-27 this happened twice
(thinkling PR #19, clip-hook, and upstream-studio PR #9, clip-hook-fix). The owner's words that day:
"Dispatched tasks like this should go through relay automatically."

This feature adds `relay dispatch`: the hub records a request for another repo, the request waits on the hub
until a session in that repo turns it into a feature, the request text becomes the feature's idea, and the
session that asked is told when the work is picked up and when it merges.

## Decisions

1. **Requests live on this machine** (owner, 2026-11-27, asked during this spec), in `~/.relay/dispatch/`,
   like notes and the hub log. The hub never commits or pushes to the target repo, so it never becomes that
   repo's writer and never meets its branch rules or CI. The request moves into git only when the target
   session starts the feature and it becomes `idea.md` and a `request` record in `state.md`.
2. **Only a registered hub session dispatches, and only with `--relayed`.** A dispatch passes on the owner's
   words, like `relay hub note`. The owner may also run it from their own terminal without `--relayed`. Any
   other agent session is refused.
3. **The owner's words travel separately from the request.** The request is the hub's statement of the work,
   which may add context the owner did not type (file names, PR numbers). `--owner-said "<quote>"` is
   required for a relayed dispatch and holds what the owner typed. Both go into the idea, labelled, so the
   target session and the reviewers can tell them apart.
4. **Picking up a request always starts a feature.** There is no "taken but not started" state that relay
   would have to track. `relay new <slug> --request <id>` and `relay take --request <id>` both run the normal
   `relay new` (with all its checks: writer, one open PR per repo, slug) and use the request as the idea.
   `relay take --request` uses the slug the dispatch suggested; `relay new` names its own.
5. **The requesting session is the hub that ran the dispatch.** It is told when the request is picked up and
   when the feature merges or is released. Dispatching on behalf of a non-hub session is not in this feature.
6. **The target repo's sessions learn of a waiting request through relay's hooks,** the same channel that
   carries merge notices and owner notes, once per session. relay never starts or resumes a session to
   deliver it, as with notes.
7. **The dashboard is not changed in this feature.** Waiting requests show on `relay hub`, its phone page and
   `relay status`. A dashboard panel can follow later.

## Non-goals

- Requests across machines. A request is seen only on the Mac where it was dispatched.
- Dispatch from non-hub agent sessions, or on behalf of another session.
- Starting, resuming or choosing a session in the target repo.
- Changing how existing hub events (go, waiting, merged, released) reach hub sessions.
- Auto-expiry of waiting requests.
- A dashboard view of requests.

## Requirements

### Dispatching

- **R1.** `relay dispatch <repo> "<request>" --owner-said "<quote>" [--slug <slug>] --relayed` records one
  request and prints its id and the commands the target session runs. `<repo>` is a folder directly under the
  projects root (as `relay owner` takes it) that is a git checkout; anything else is refused with `<repo> is
  not a repository under <root>` and nothing is recorded.
- **R2.** Who may run it: a registered hub session with `--relayed`, or the owner's own terminal without it.
  A relayed dispatch from a session that is not a registered hub is refused with `only a registered hub
  dispatches; run relay hub first`. `--relayed` from the owner's terminal, and a missing `--relayed` from an
  agent session, are refused as other relayed commands refuse them.
- **R3.** `--owner-said` is required with `--relayed` and optional from the owner's terminal (where the
  request itself is the owner's words). The request and the quote are each cleaned as notes are cleaned
  (control characters removed) and each limited to 4000 characters; empty text, or text over the limit, is
  refused.
- **R4.** `--slug` is optional and must match the slug rule `relay new` uses. It is a suggestion; it is not
  checked against the target repo at dispatch time.
- **R5.** The record is one JSON file per request in `~/.relay/dispatch/` (folder mode 0700, files 0600),
  written under a lock with a temporary file and rename. It holds: id, repo (folder name and real path),
  request, owner_said, slug, requested_by (`{provider, session_id}`, or `owner`), relayed_by, created_at,
  status (`waiting`, `started`, `merged`, `released`, `cancelled`), and, once started, `started`
  (`{provider, session_id, slug, branch, at}`).
- **R6.** Ids are short, unique on this machine and safe in a command line: `q` followed by base 36 of the
  creation time in milliseconds, with a suffix on collision.
- **R7.** A dispatch writes a hub log line (`command: dispatch`, id, repo, result). It is not sent to other
  hub sessions as an event.
- **R8.** `relay dispatch --cancel <id>` marks a waiting request `cancelled`, with the same who-may rule as R2.
  A request that is not waiting is refused with its status. `relay dispatch --list [--json]` lists the
  requests on this machine that are waiting or started, oldest first.

### Showing a waiting request

- **R9.** `relay hub` lists each waiting request as an item of a new kind, `request`, numbered with the other
  items and ordered by creation time among them: `<repo>: request <id>, waiting for a session · <age>`, then
  the first line of the request. The hub's actions on it are a cancel (`relay dispatch --cancel <id>
  --relayed`) and, in text, the command to start a session there (`cd <path> && claude`, then "take request
  <id>"). The hub page shows the same item as a card, with the path left out as the page already does.
- **R10.** `relay status` shows a "Requests waiting for a session" block after "Other sessions waiting on
  you", one line per waiting request with repo, id, age and the first line, and the count joins the total of
  items waiting.
- **R11.** A started, merged, released or cancelled request leaves the hub and the status block.

### Telling the target repo's sessions

- **R12.** At the hook events that carry merge notices today, a session whose folder is inside the target
  repo (its main checkout or one of its worktrees) is told once about each waiting request for that repo:
  `relay: request <id> from the hub is waiting in <repo>: <first line>. Read it with relay dispatch --show
  <id>, then start it with relay take --request <id> or relay new <slug> --request <id>.` It is recorded in
  the session's `notified` list, so it is not repeated. A failure to read requests never disturbs the hook.
- **R13.** `relay dispatch --show <id>` prints the whole request, the owner's words, who asked and when. Any
  session may run it.

### Starting the feature

- **R14.** `relay new <slug> --request <id>` starts a feature exactly as `relay new` does today, with every
  check it makes, and writes `idea.md` from the request:

      # <slug>

      Requested through relay dispatch (<id>) by <provider> hub session <session_id>, <date>.

      ## Request

      <request>

      ## Owner's words

      > <owner_said>

  The "Owner's words" section is left out when the record has none. `--request` cannot be combined with
  `--idea` or `--idea-file`. `--small` and `--type` work as today; with `--small`, the request text is the
  idea.
- **R15.** `relay take --request <id> [--slug <slug>] [--small] [--type <type>]` does the same as R14, with the
  slug from `--slug` or else from the dispatch. With neither it is refused with `request <id> has no slug;
  run relay new <slug> --request <id>`. `relay take` without `--request` is unchanged.
- **R16.** The request must be waiting and this checkout must belong to its repo: the real path of this
  checkout's main worktree (for a linked worktree, the checkout that owns it) equals the path the dispatch
  recorded. Folder and origin names are not compared, since they can differ. Otherwise the command refuses before it switches
  branch or writes anything: `request <id> is for <repo>`, `request <id> was started as <slug> in <repo>`,
  `request <id> was cancelled`, `no request <id> on this machine`.
- **R17.** The feature's `state.md` gets `request: {id, requested_by, relayed_by, at}`, and an owner action
  `owner asked for this through relay dispatch`, recorded as relayed by the hub when it was relayed, so the
  owner's involvement is counted as with `--with-owner`.
- **R18.** Two sessions cannot both start one request. The command claims the request under the dispatch
  lock before it starts the feature (status stays `waiting`, with a `claim` naming the session and time). A
  claim held by another session that is still live and less than 30 minutes old refuses the second with
  `request <id> is being started by <provider> session <session_id>`. A claim by a session that is gone, or
  older than 30 minutes, can be taken over. When the feature is published the record becomes `started`;
  when `relay new` fails, the claim is removed and the request stays waiting.
- **R19.** Once the feature is published, the requesting session is sent a `relay event:` note: `relay:
  request <id> was started in <repo> as <slug> by <provider> session <session_id>.` It is queued for the
  next prompt when the session is not open, as event notes are. The hub log gets a line with event
  `started`. Other hub sessions are not sent it.

### Telling the requester when the work lands

- **R20.** When a feature with a `request` record merges or is released (the existing `merged` and
  `released` events, from any resolver: the owner's merge, a relayed merge, a delegated merge, or a merge
  found on GitHub), the requesting session is told, once: the event text followed by ` This was your request
  <id>.` If the requester is a registered hub that would get the event anyway, that addition goes into the
  note it already gets, so it gets one note, not two. If it is not registered or not open, the note is
  queued as event notes are. The requester is never told an event it caused.
- **R21.** The dispatch record moves to `merged` or `released` when that event is delivered. A record that
  is missing (pruned, or another machine) does not stop the note: the requester comes from `state.md`.

### Upkeep

- **R22.** Records that are merged, released or cancelled are pruned 30 days after their last change, when
  relay writes a dispatch record. Waiting and started records are kept.
- **R23.** An unreadable record file is skipped by every reader and named once in a warning by `relay
  dispatch --list` and `relay hub`; it never blocks another request.

### Skills and docs

- **R24.** The relay-hub skill tells the hub to hand work to another repo with `relay dispatch ... --relayed`
  instead of a peer message, with the owner's words in `--owner-said`, and to give the owner the request id.
- **R25.** The relay-idea skill tells a session that was told about a request (R12), or asked by the owner to
  take one, to start it with `relay take --request <id>` or `relay new <slug> --request <id>`, and then
  continue as for any idea.
- **R26.** `docs/reference.md` gets a "Dispatching work to another repo" section under the hub, covering
  R1 to R23, and README's command list names `relay dispatch`.

## Failure paths

| What fails | What happens |
|---|---|
| Target folder is not a repo under the projects root | Refused (R1); nothing recorded |
| Caller is not a registered hub, or `--relayed` misused | Refused (R2); nothing recorded |
| Request or owner's words empty or too long | Refused (R3) |
| `~/.relay/dispatch` cannot be written | `relay dispatch` exits 1 with the error; nothing is half written (temporary file and rename) |
| Hub log line cannot be written | A warning; the request stands |
| Hook cannot read requests | No notice; the hook carries on (R12) |
| `relay new --request` hits one open PR per repo, another writer, or a slug clash | The usual `relay new` refusal; the claim is removed and the request stays waiting (R18) |
| Two sessions start the same request | The second is refused while the first's claim is live (R18) |
| A session dies mid-start | Its claim expires after 30 minutes or when the session is gone (R18) |
| The started note cannot be sent | A warning; the feature stands |
| The merge note to the requester cannot be sent | A warning, as for other event notes; the merge stands |
| The dispatch record is missing at merge time | The note still goes, from `state.md` (R21) |
| A record file is unreadable | Skipped and named in a warning (R23) |

## Open questions
