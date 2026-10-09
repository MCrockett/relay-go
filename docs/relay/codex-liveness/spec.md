# codex-liveness: spec

## Why

Running now (running-now, PR #12) counts a `working` session as running while its recorded agent process is alive and the session sent a hook event in the last 2 hours. Codex sessions started from an editor run inside one long-lived Codex app-server that hosts many sessions, so the recorded process stays alive after a session's turn ends without a hook event (a missed `Stop`, a turn that failed inside the server). Such a session stays in Running now for up to 2 hours. The owner asked (2026-10-08) for better results for Codex. See idea.md.

Research on this machine (Codex CLI 0.160, app-server 0.162):
- Codex writes every session's rollout file (`~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl`) as the turn runs. Each turn starts with an `event_msg` record of payload type `task_started` and ends with `task_complete`, or `turn_aborted` when the owner interrupts it. These three appear in every rollout relay has seen (about 3,900 turns).
- A turn whose process was killed or crashed writes no end record: the rollout's last turn marker stays `task_started` and the file stops changing. Codex writes no record while a long tool command runs, so a quiet file alone does not mean the turn died; the longest quiet gaps seen inside finished turns were under 20 seconds, but a long build or test run can be quiet for minutes.
- The app-server reports exact thread status (`idle`, `active`) over its control socket, but that needs a client speaking its JSON-RPC protocol, tied to the server version, which can differ from the installed CLI.

## Decisions

- D1. **The signal.** For a Codex record, relay reads the last turn marker from the session's rollout file: the newest record in the file's last 1 MB (the window `agentask` already reads) that is an `event_msg` whose payload type is `task_started`, `task_complete` or `turn_aborted`. `agentask.codex_turn(session_id)` returns `{"marker": "started"|"ended", "mtime": <file mtime>}` (`ended` for `task_complete` and `turn_aborted`), or `None` when the turn cannot be told: no rollout file, an unreadable file, or no marker anywhere in what was read. When the last 1 MB holds no marker, relay reads further back, 1 MB at a time, up to 16 MB from the end of the file, and takes the newest marker it finds; when there is none in those 16 MB (or the whole file, if smaller), the answer is `None`. So a rollout without markers, whatever its size or the reason (a format change, a very long turn), never counts as `started`, and D3 alone decides for it. Complete lines that are not JSON, or JSON that is not a record with a dict `payload`, are skipped, as a partial last line is; they never make the whole signal unknown. With several rollout files for one id, the newest by mtime is read. Read live, never stored.
- D2. **Running, for Codex.** (How each way a turn stops is caught: a finished or interrupted turn by its end marker, at once; a turn whose process dies by running-now's process check, at once; a turn that hangs while its process lives, which looks like a long silent tool command, after 30 quiet minutes. The owner chose the 30-minute limit for hung turns on 2026-10-08, over a 10-minute limit and over asking the app-server.) D3 of running-now still decides first. A Codex record that D3 counts as running is then checked with D1:
  - `marker` is `ended`: not running (the turn finished or was interrupted, whatever the hooks recorded);
  - `marker` is `started` and `now - mtime > 1800`: not running (the turn has been silent for over 30 minutes: its process died or the turn hung);
  - `marker` is `started` and the file changed in the last 30 minutes: running;
  - `None`: running, as today (fail safe: D3 alone decides).
  The check only removes sessions from Running now; it never adds one that D3 leaves out. Exactly 1800 seconds still counts as running. Claude records are not checked.
- D3. **Shown state.** A Codex `working` session left out of Running now by D2 is shown in "Where you left off" as `was working`, unless running-now D4 already makes it `stopped` (its process is known dead). No new word.
- D4. **Cost and one answer per run.** The rollout is read only for Codex records that running-now D3 counts as running, once per `relay left` run or snapshot build: one tail read per such session, the same read size as the last message relay already shows. `leftoff.codex_turns(records, alive, now)` reads them once and returns `{session_id: codex_turn result}`; the caller passes that one map to both `leftoff.running` and `leftoff.projects`, so a rollout written between the two cannot leave a session out of both sections or put it in both.
- D5. **Where it applies.** One function, `leftoff.is_running(record, alive, now, turns)`, gains the check (a `turns` of `None`, the default, means no Codex check), so Running now, "Where you left off" (running sessions left out, `more`), `relay left` and the dashboard all agree. A failure reading the rollout counts as `None` (D2's fail-safe).
- D6. **Local and read-only.** Only files Codex already writes on this machine are read; nothing is written, stored or sent. The app-server is not contacted.

## Requirements

- R1. `agentask.codex_turn` per D1: `started` and `ended` (both `task_complete` and `turn_aborted`), the newest marker wins, a marker 1 MB to 16 MB back from the end is found by reading further back, a file with no marker in its last 16 MB (and a large file with no markers at all) gives `None`, a line split across two 1 MB reads is read whole, a partial last line, a non-JSON line and a record without a dict `payload` are skipped, newest file of several, and `None` for a missing file, an unreadable file and a small file without markers.
- R2. `leftoff.is_running` per D2: a Codex session D3 counts as running is left out when its last marker is `ended`, left out when `started` and quiet for 1801 seconds, kept at exactly 1800 seconds, kept when `None`, and a reading error counts as `None`; a session D3 leaves out is never added; Claude records are unchanged (the rollout is not read for them).
- R3. `leftoff.codex_turns` per D4, and `relay left` and the snapshot pass its one map to both lists (a test that changes the rollout between the two calls still finds the session in exactly one section). `leftoff.running`, `leftoff.projects` and the snapshot reflect R2: a Codex session left out of Running now by D2 appears in "Where you left off" as `was working` (or `stopped` per running-now D4) and counts toward `more`.
- R4. README: the Codex check in the Running now section, in plain words (a Codex session leaves Running now as soon as its turn ends, or after 30 minutes of silence).
- R5. Tests with temporary `RELAY_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `HOME`, fake records and fake rollout files with set mtimes, never real Claude or Codex.

## Failure paths

- F1. No rollout, unreadable rollout, no marker: D3 alone decides (today's behaviour).
- F2. A rollout format change that drops the markers: same as F1, so relay never hides a session it cannot judge.
- F3. A long tool command that keeps a turn silent for over 30 minutes: the session leaves Running now and shows as `was working` until the rollout is written again (the command finishes and the turn continues), as long as running-now D3 still counts it (its last hook event within 2 hours). A hook event alone does not bring it back while the rollout stays unchanged. Accepted: a wrong "not running" for a rare long silent command is better than 2 hours of a dead session shown as running.

## Non-goals

- Talking to the Codex app-server (thread status, subscriptions). Revisit only if the rollout signal proves insufficient.
- Changing how Claude sessions are judged.
- A setting for the 30-minute limit.
- Showing a new word for a Codex session whose turn ended without a hook event.

## Open questions
