# hub-note

Problem: a local app in another repo, run by the owner, needs to send free-text requests to the owner's open relay hub session and show which hub sessions are open. relay registers hub sessions in the hub folder (sessions.json, D9, `hub.registered()`, there can be several) and delivers notes with `notes.send`. Today `relay hub note` only takes a digest item ref, and nothing lists open hub sessions on the command line.

Who: the owner, through that local app (a plain process, not an agent).

Want: the smallest owner-facing command.
- `relay note --hub --list --json` prints a JSON list of registered hub sessions with provider, session_id, last_active (epoch seconds or null) and open (true when the session is running and its inbox socket is present, judged the way relay already judges it for notes). Exit 0 even when empty.
- `relay note --hub --session <session_id> "<text>"` sends the text to that registered hub session through `notes.send`. Exit 0 when posted to the inbox socket. Non-zero with a plain message when the session is not registered, not open, or the post fails; queued-but-not-posted counts as failure for this command. Without `--session`: send when exactly one hub session is open, otherwise refuse with a message naming the open sessions. Never posts to a session that is not a registered hub.
- Text is cleaned by `notes.clean`; the note is recorded in the notes log like any other.

Success: the app can call these two commands; tests cover each case with temp dirs; nothing else in relay changes behaviour.

Non-goals: no new delivery channel, no network, no UI change.

Owner approval: given in chat on 2026-11-01.
