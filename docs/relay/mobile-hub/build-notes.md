# mobile-hub: build notes

## Requirements to tests

| Requirement | Where |
|---|---|
| R1, R2, R3 | `tests/test_hub.py` ItemsTest, OutputTest.test_text |
| R4 | OutputTest.test_nothing_waiting |
| R5 | OutputTest.test_json_parses_with_counts_and_warnings |
| R6 | LoadTest (dashboard used with no git or gh process; 500, 403, loading, slow headers, trickled body, dead pid fall back within 1 second; token never printed) |
| R7, R7a | DigestStoreTest (same millisecond and random characters, concurrent saves, wipe test, unknown references, pruning, unwritable folder) |
| R8 | NoteTest; `tests/test_notes.py` test_a_note_can_carry_its_own_prefix |
| R9 | `tests/test_owneractions.py` HubActTest; `tests/test_commands.py` test_a_hub_review_runs_and_records_who_relayed_it |
| R10 | HubActTest.test_a_hub_merge_comments_after_merging, test_a_failed_comment_does_not_hide_the_merge_nor_does_a_failed_log |
| R11 | NoteTest and HubActTest log checks, including refusals and an unwritable log |
| R12 | NoteTest.test_relayed_is_required_and_only_from_an_agent, HubActTest.test_refusals |
| R13 | `tests/test_owneractions.py` lock tests across processes; `tests/test_commands.py` override and review-publish tests with another process holding the lock |
| R14 | RegistryTest; `tests/test_othersessions.py` test_a_registered_hub_session_is_not_listed; `tests/test_commands.py` test_hub_registers_an_agent_session_and_not_the_owner |
| R15 | SkillTest; `skills/relay-hub/SKILL.md` |
| R16 | README "The hub: relay from your phone" |

## Where the build differs from the plan's wording

- **A note to an ended session.** The plan's Task 8 test list says a session whose record becomes `ended` is queued. Under spec D5, a note goes only to a session that is still listed. An ended session is in neither other sessions nor running, so a session item that has ended is refused as no longer listed. An ended session that a feature still claims as its owner is still deliverable, and its note is queued. The test checks both cases.
- **Queue or post.** A target counts as not running, so its note is queued, when the dashboard would show it ended or stopped (`leftoff._shown_state`), rather than by the plan's "`sessions.alive` reports not running". This matches session-notify and avoids queueing for a waiting session that has no recorded process.
- **Running sessions.** The listing check uses `leftoff.is_running`, the rule `leftoff.running` applies, without reading every session's last words.

## Gate

Full suite: 746 tests, OK (2026-10-10).

## Live check (2026-10-10)

`relay hub` from a terminal environment (no agent markers, so nothing was registered) read the running dashboard's data, made no git or gh call, and printed 8 items in 0.1 seconds. The idlekeeper feature waiting on AskUserQuestion was marked "open this session to answer".
