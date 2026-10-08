# relay-go

**Nothing merges without a GO.**

One pipeline for every project: idea, spec, plan, build. Each stage after idea is reviewed by the other provider (Claude reviews Codex's work and Codex reviews Claude's) before it moves on. The owner merges and releases; agents never do. You are the owner; your Claude Code and Codex sessions are the authors and reviewers. Every review ends in a GO or a NO-GO. A stage moves on only with a GO from the other provider, or with your decision when the reviews stall. The command is `relay`.

## Requirements

- Python 3.11 or later (standard library only; `bin/relay` runs `python3.11`).
- git, and the GitHub CLI `gh`, logged in. Each project needs a GitHub remote named `origin`. Features start from `develop` by default (`relay new --base` picks another branch).
- Claude Code (`claude`) and Codex (`codex`), both installed and logged in. relay runs them headless for reviews and never stores credentials; it uses your existing logins.
- macOS for notifications and the SwiftBar menu. Everything else, the dashboard included, is plain Python and git. The test suite runs on Linux in CI.
- Or Docker, with all of the above in one image: see Running in Docker.

## Limits

- Built for one owner on one machine. Ownership, handoffs and usage history live in `~/.relay/` and in each project's `docs/relay/`; there is no shared server.
- Reviews spend your Claude and Codex plan usage. `relay cost` and the dashboard show how much.
- A reviewer's verdict is advice. relay stops the loop and shows you the findings; it does not prove the code correct. You still read the PR before you merge.
- relay skips a reviewer whose weekly limit Codex's local logs show as used up, or one whose last review failed on a usage limit. Claude's limits appear in the dashboard once its status line is set up (see the end of this file), but relay learns Claude is out only when a review fails.

## Setup (once per machine)

    ./install.sh      # links the relay skills into Claude and Codex, and `relay` into ~/.local/bin

Then add this line to a repo's AGENTS.md to opt it in:

    Follow <your relay checkout>/README.md.

## Existing PRs

A PR opened before relay can join it: on the PR's branch, `relay adopt <slug> [--plan <existing plan>] [--spec <existing spec>]`, then `relay submit`. It starts at the build stage and the other provider reviews the whole PR.

## The loop

    relay new <slug>              start: branch, docs/relay/<slug>/, you own the repo
    relay new <slug> --small --type fix --idea "..."   small change: straight to build
    relay submit                  hand in the current stage; the other provider reviews it
    relay status                  every feature in your projects folder; * = waiting on you, with what
                                  it needs, how long it has waited, and what the agent last said
    relay review                  re-run a review after an error or a stale GO

Reviews continue while they make progress, up to 4 rounds. "Partly fixed" counts as progress. A stage stops and waits on you (with a `reviews/<stage>-stuck.md` summary) only when blocking findings rise, when the count stays the same and a finding the author already tried to fix is marked unresolved, or at round 4. A review you request (Request review, Re-review spec or plan) is independent: its NO-GO goes back to the author for at least one more round, even at round 4, and is not compared with earlier reviewers' findings.

## What reaches you

- A macOS notification when a feature needs you: ready to merge, a review stopped, a review failed, or a handoff was pushed. Turn it off with `[notify] enabled = false` in `~/.relay/config.toml`.
- `relay status`: the `*` rows are your inbox, newest wait first (`--json` keeps its oldest-first order). Below the features, "Other sessions waiting on you" lists Claude and Codex sessions on this machine that are waiting on you for work outside any open feature (for example after its PR merged), with how long each has waited and the start of its last message.
- `relay left`: where you left off, for coming back after a break, a closed project or a restart. Per project (a repository and its worktrees count as one), your newest 3 sessions on this machine in any state (waiting on you, needs approval, stopped, was working, ended), how long ago each was last active, the feature it holds, the start of its last message, and the command to resume it from its own folder, such as `cd ~/app && claude --resume <id>`. `relay left --all` shows every session, and `relay left --recent` shows them as one list, newest first across projects. A "Running now" block comes first: your sessions in the middle of a task. relay's hooks record the Claude or Codex process each session runs in, and a session counts as running while that process is alive and the session has sent a hook event in the last 2 hours. Sessions without a recorded process (older ones, or when the check fails) count as running only if they sent an event in the last 10 minutes. A session mid-task whose process is gone shows as "stopped"; one that went quiet without a known process shows as "was working". Codex sessions from an editor share one long-lived Codex process, so for them the 2-hour limit does most of the work. It reads only this machine: no fetch, no GitHub.
- One open PR per repo: `relay new` refuses while another feature's PR is unmerged, unless you ask the agent for a stack (`--stack`, recorded as your decision).

## Owner decisions

    relay override go             accept a stuck stage
    relay override extra-round    allow one more round
    relay override reset-rounds   after narrowing or splitting the stage
    relay override release        free a feature whose agent is gone, so another can `relay take`
    relay override review [--reviewer provider:model]   a fresh build review from the reviewer you pick
    relay override review --stage spec|plan             review an approved spec or plan again
    relay hooks install | status  add relay's session hooks to Claude Code and Codex (owner), or check them

Two ways to make one:

- Run the command in your own terminal. Inside an agent session it refuses, so this form proves it was you.
- Tell the agent in the session ("give it an extra round"). It runs the same command with `--relayed`, which
  records the decision as relayed by that agent in state.md, the commit message and, for a build, a PR comment.
  The skills forbid relaying anything you did not say in that conversation; you see every relayed decision
  when you merge.

## Handoffs

At about 60% context, or when switching provider or machine: `relay handoff`, fill it in, `relay handoff --commit`. A handoff covers every feature that session holds in the repo (ownership is per repo). The next session runs `relay take`, which takes over all of them, including stacked PRs on other branches.

## Reviewer preferences

`[review.prefer]` in `~/.relay/config.toml` lists, for each stage author (claude, codex, owner), the reviewers in order of preference. relay uses the first one that is available. A provider is skipped while its weekly limit is used up (read from Codex's own logs) or after a review failed on a usage limit (remembered until the reset); the next entry reviews in the same `relay submit`. Fallback and same-provider reviews are recorded in the review file, the commit and `relay status`. Reviewers run at `effort` (medium) each round, at `final_effort` (high) in the last round before a stage comes to you, and at `release_effort` (high) for a PR to main; an entry like `codex:gpt-6-astra@low` pins its own effort. A same-provider review on purpose (`--same-provider`) is your call: an agent passes it only when you ask, with `--relayed`, and it is recorded as your decision. `relay roles` shows the table and who is out right now; `relay roles set review.claude "codex:gpt-6-astra@high, claude:claude-fable-5-1"` changes it for every repo.

Changing a table is your decision: `relay roles set` and `relay roles end` run in your own terminal, and an agent passes them on only when you ask, with `--relayed` (logged with its session). A list may not name the same model twice, and an effort is one lowercase word (`low`, `high`, `xhigh`). Add `--until 23:00` (or an ISO date-time with a zone, at most 7 days away) to make a change temporary: relay keeps it in `~/.relay/review-until.json` and returns to your normal table by itself when the time passes, even if nothing is running then. A temporary table wins over a project's own `docs/relay/config.toml`, and setting the normal table leaves it in place. `relay roles end review.claude` (or `all`) ends it early. Every change is logged in `~/.relay/roles-log.jsonl`, and `relay roles` shows the latest one per author.

## Settings

    relay roles                              show who does what
    relay roles set build claude:claude-sonnet-5
    relay roles set reviewer.codex gpt-6-astra@medium
    relay roles set review.claude "claude:claude-fable-5-1, codex:gpt-6-astra" --until 23:00
    relay roles end review.claude            back to the normal table now (or: all)
    relay rule "Prefer Sonnet for plan reviews under 200 lines"
    relay cost --since 7d                    tokens used by relay-launched reviews and by writing sessions

Your own files live in `~/.relay/` (`RELAY_HOME` moves it), never in this repo:

- `config.toml`: roles, limits, reviewer preferences, and `[projects] root`, the folder relay scans for repos (default `~/Projects`; `RELAY_ROOT` overrides it). Start from `config.example.toml`. A project can override settings in its own `docs/relay/config.toml`.
- `RULES.md`: your global judgment rules, included in every review. Add one with `relay rule --global "..."`.

A project's own rules live in its `docs/relay/RULES.md`, committed with the project and applied after the global ones. Add one from inside the project with `relay rule "..."`. Rules are the owner's decisions: an agent may add one only with `--relayed`, which records that it is passing on your words. `relay rules` prints the rules in effect for the current project. `RULES.example.md` shows the format.

Your own skills, such as a release runbook for your apps, stay outside this repo: everything in `skills/` is
installed for every relay user. Keep them in a folder of your own, link them into `~/.claude/skills` (and
`~/.codex/skills`) the way `install.sh` links relay's, and tie them to the work through rules. A project rule like
`relay rule "Releases follow the ios-release skill"` reaches the author through `relay rules` and the reviewer
through every review prompt; the release review checks that a release follows the runbook its rules name. Use
`relay rule --global` for a skill that applies to every project.

Repo files: `prompts/` (review prompts), `skills/` (agent skills), `config.example.toml`. Run tests: `python3.11 -m unittest discover -s tests -t . -v`.

## Running in Docker

The image holds Python 3.11, git, gh, Claude Code, Codex and relay, with the skills linked. Your projects folder
is mounted at `/projects`; logins, git identity and `~/.relay` live in a named volume, so they survive rebuilds.

    cp compose.example.yaml compose.yaml
    export RELAY_PROJECTS=~/Projects RELAY_UID=$(id -u) RELAY_GID=$(id -g)
    docker compose up -d --build           # starts the dashboard
    docker compose logs relay              # prints the dashboard link
    docker compose exec relay bash         # a shell in the container

Once, in that shell: `gh auth login`, `claude` (then `/login`), `codex login --device-auth`, and
`git config --global user.name "..."` and `user.email`. Then run your Claude Code and Codex sessions in the
container too, so relay sees them, and run `relay hooks install` there for session health.

The compose file publishes the dashboard on your machine's loopback only (`127.0.0.1:8765`). Keep the same port
number on both sides: the dashboard checks that the browser's address matches its own port. There are no desktop
notifications or menu bar in the container; the dashboard and `relay status` show what waits on you. A git
worktree whose main repo is outside the mounted folder cannot be read in the container and is skipped.

## Local dashboard and menu bar

Run `relay ui` in your own terminal to open the dashboard, or `relay ui --background` to keep it running
after the terminal closes. Use `--port 8765` to choose the first of eleven ports it tries. A second launch
opens the running dashboard. Agent sessions cannot start it.

The inbox lists features waiting on you, newest wait first. Each card says in plain words what is needed
("Decide: plan review stopped. <reason>", "Review failed: <why>", "Merge PR #6", "Take the handoff",
"Answer the claude session", "Approve Bash in the codex session", "Check the codex session: no activity 2h"),
how long it has waited, and the buttons for that ask: Override GO, Extra round, Reset rounds, Request review or
Merge PR. When a session is waiting on you, the card shows the start of what the agent last said (Claude's
summary while you were away, or the agent's last message), and the feature's details show all of it. relay reads
that text live from the CLI's own log on this machine and never stores it. `relay status` shows the same asks.
A failed review's reason is saved in state.md without paths, usernames, emails or tokens, since state.md is
published with the branch.

The inbox also shows other sessions waiting on you: Claude and Codex sessions on this machine that no open
feature accounts for, such as one asking for a key after its feature merged. A session is listed when relay's
hooks say it is waiting on you or for a tool approval (past `[ui] health_grace_minutes`), its wait started within
`[ui] other_sessions_hours` (default 24, up to 168), and, unless it waits for an approval, it has said something.
A session already shown under one of its features is not listed again. These cards only show what is waiting:
you answer in the session's terminal. Clicking one shows the full last message and how to resume the session if
its terminal is gone (such as `cd ~/app && claude --resume <id>`, or `codex resume <id>`). Both settings are read from
`~/.relay/config.toml` only, since many sessions are outside any repository. Nothing about these sessions is
stored or published.

"Running now" at the top of the page lists your sessions in the middle of a task, as `relay left` does, with how
long each has been running; click one for its last message. Below the inbox, "Where you left off" shows the same as
`relay left`: per project, your newest 3 sessions in any state, with the feature each holds. Its menu switches
between "By project" and "Most recent" (one list, newest first); your browser remembers the choice. Clicking one shows its full last message and the resume command; the cards
have no buttons. Only sessions you started yourself are shown, not automated runs such as relay's own reviews
(`claude -p`, `codex exec`), and only sessions that said something or wait for a tool approval. relay's hooks keep
session records for 7 days, so older sessions and ones started before the hooks were installed are not shown.

Open a feature to see its stage history, reviews, owner decisions,
session, handoff and CI. Reviews open even when their branch is not checked out. The All features table
can include completed work. Models shows Codex and Claude weekly limits, daily carry-forward budgets, and the
review runs relay launched, by project and model, for seven or thirty days. Writing sessions shows the Claude Code
and Codex sessions that held a relay feature, by feature and stage and by model, read from each CLI's own logs on
this machine (`~/.claude/projects`, `~/.codex/sessions`). relay reads only timestamps, model names and token
counts, never the conversation, and only for sessions that held a feature. Work a session does outside relay
while it holds a feature counts toward that feature; work you write by hand is not counted; turns outside any
hold show as unattributed, and a log relay cannot read shows as unreadable rather than zero. The log formats are
not published contracts, so a CLI update can change them. Daily budgets need a sample from before today's midnight in the current week; until then they say "no
baseline yet". Below them are the reviewer tables: reorder, add and remove reviewers, set their effort, or make
a change temporary, each after a confirmation. Writer roles are shown for information.

The server listens only on `127.0.0.1` unless you pass `--host 0.0.0.0`, which only a container needs (see Running in Docker). A random token protects the page and every API request, and requests
must use a local Host header. The private discovery file `~/.relay/ui.json` has mode 0600. The token is
removed from the browser address after loading. The cache rebuilds every thirty seconds; Refresh requests
an immediate rebuild. A failed rebuild keeps the last good snapshot and reports its age and error.

Every action asks for confirmation and is bound to the published commit, stage, status and session you
saw. A changed revision is refused with fresh details. Overrides and release use temporary detached
worktrees and a fast-forward push, leaving the session's checkout and branch untouched. Sessions pick
up published owner actions before submit, review, handoff, take and override. Merge also requires a fresh GO,
confirmed fallback reviews and green CI, and passes the displayed PR head to GitHub's merge guard.

Request review, in a feature's detail, runs a fresh build review for a PR that is ready to merge or whose
review errored. It defaults to the reviewer a fallback GO is waiting for, or the author's first choice, and
you can pick any reviewer from the preference table. It runs in the background in a temporary worktree and
publishes only if the branch has not moved; a NO-GO sends the build back to the author like any review, and
a failed request changes nothing. `relay override review` does the same from your terminal.

Re-review spec and Re-review plan, in the same place, ask for an approved spec or plan to be reviewed again,
for example by Codex once it is back after a same-provider GO. The default reviewer is the first choice for
that stage's author. A spec re-review carries on to the plan, without new rounds, and the feature returns to
where it was; a NO-GO sends that stage back to the author. `relay override review --stage spec|plan` does the
same from your terminal. relay never suggests a re-review: a same-provider GO is a valid GO.

Session health shows, for each feature a session holds, whether it needs permission, is waiting on you,
has gone quiet, or is active. Run `relay hooks install` once in your terminal: it adds relay's hooks to
~/.claude/settings.json and ~/.codex/hooks.json, keeping everything else and backing up each file it
changes. Codex runs new hooks only after you trust them with /hooks inside Codex. `relay hooks status`
shows what is configured and when the last event arrived. Without hooks, health comes from file and
commit activity alone. The same hooks tell a session, once, when a PR it owns was merged: the
notice appears in the agent's next turn, as soon as the dashboard or `relay status` has seen the merge. `[ui] health_grace_minutes` (default 1) and `quiet_minutes` (default 30) tune it.

For SwiftBar, choose its plugin folder and run `./install.sh`. The installer links `menubar/relay.30s.py`.
The menu shows how many features need you, links to the dashboard or GitHub, and can open the UI.
The installer prints setup instructions when SwiftBar has no `PluginDirectory` setting.

To capture Claude usage, approve adding this after `input=$(cat);` in the existing Claude status-line
command, without changing its display code:

```bash
printf '%s' "$input" | ~/.local/bin/relay usage-snapshot claude >/dev/null 2>&1 &
```

The command is silent, ignores malformed input and saves the weekly and five-hour limits independently
of the usage history append. It waits at most 100 ms for the history lock. Codex samples retain their
original log timestamps. History is kept locally in `~/.relay/usage.jsonl` and pruned after 35 days.
Background server errors are in `~/.relay/ui.log`; stop the process listed in `~/.relay/ui.json` to stop it.
