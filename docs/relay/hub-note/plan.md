# hub-note: plan

Tests first in every task. Gate: `python3.11 -m unittest discover -s tests -t . -v`. Commits: `feat: ...`, `test: ...`, `docs: ...`.

## Task 1. `notes.send(queue=False)` and a shared socket test

Files: `relaylib/notes.py`, `tests/test_notes.py`.

- Add a helper `usable_socket(path)` to notes.py (a path that is a socket owned by the current user, by `os.lstat`), and have `post` use it instead of its inline test. Behaviour of `post` does not change.
- Add keyword `queue=True` to `send`. With `queue=False`, when the post does not happen or fails, write nothing to the log and return `(None, "not posted")`. When the post succeeds, behave as today. The default path is untouched.
- Tests: `queue=False` with no inbox, with a dead socket path and with a regular file writes no log and returns `(None, "not posted")`; with a live fake inbox it posts and logs `posted`; default `queue=True` still queues on failure (existing tests pass unchanged); `usable_socket` is false for a symlink, a regular file and a missing path, true for a socket.
- Covers: R8, R12.

## Task 2. Hub file in order and the open test

Files: `relaylib/hub.py`, `tests/test_hub.py`.

- Add `hub.entries()`: the hub file's `(provider, session_id)` pairs as a list in file order, without duplicates; empty when the file is missing or unreadable. `registered()` keeps its current body.
- Add `hub.session_status(provider, session_id, alive)`: returns `{"provider", "session_id", "last_active", "open", "inbox"}` by spec D2 and D3, reading `sessions.record_path` live. `last_active` is `record["at"]` for a record `sessions._valid` accepts, else None. `open` needs provider `claude`, a valid record, `leftoff._shown_state` not ended or stopped, an inbox path in the record and `notes.usable_socket(inbox)`.
- Tests with a temp `RELAY_HOME` and a real unix socket: open; Codex session; missing record; malformed record (no numeric `at`); ended; stopped (working, process known dead); no inbox; inbox path is a regular file; `entries()` order and missing file; `registered()` unchanged.
- Covers: R2, R3.

## Task 3. The `relay note` command

Files: `relaylib/commands.py`, `tests/test_commands.py` (a new `NoteCommandTest` class using the same harness).

- Parser: `note` with `--hub`, `--list`, `--json`, `--session`, and optional positional `text`. Argument rules from spec D1, checked before anything is read.
- `--list --json`: for each of `hub.entries()`, `hub.session_status` with one `sessions.alive(sessions.read_records())` call; print the JSON list of the four keys. `[]` for none.
- Send: resolve the target (`--session` must be in `entries()`; two providers with the same id is refused as ambiguous; without `--session`, exactly one open entry), require open, call `notes.send(provider, sid, text, inbox, prefix="Note from the owner, sent with relay note:", queue=False)`. A `None` note gives exit 1 with "not delivered; nothing is left waiting". `NOT_RECORDED` goes to stderr with exit 0. `OSError` and `ValueError` become `RelayError`.
- Tests (temp dirs, real fake inbox from `tests/test_notes.py`): list empty and missing file exit 0 with `[]`; list shows an open and a closed session; send with `--session` posts, the inbox receives the prefixed cleaned text and the log has one `posted` note; unregistered id refused though a record and inbox exist (inbox receives nothing, no log); registered but not open refused; no `--session` with one open sends, with two open refuses naming both, with none refuses; failed post (socket closed after the check, simulated by patching `notes.post` to return False) exits 1 and leaves no log file; empty and over-long text refused; each forbidden argument mix refused with nothing posted; unreadable notes folder gives a plain message and exit 1; `relay note` without `--hub` refused; concurrent `notes.take` while a failed `queue=False` send runs finds nothing (the log never held a queued note).
- Covers: R1, R2, R4, R5, R6, R7, R8, R9, R10, R11, R12.

## Task 4. README

Files: `README.md`; `tests/test_public.py` already scans for forbidden text, run it.

- Add a short section after the notes text describing `relay note --hub --list --json` and `relay note --hub [--session ID] "text"`, the open rule, and that a failed post is a failure and leaves nothing queued. No personal paths or names.
- Test: full suite green.
- Covers: R13.
