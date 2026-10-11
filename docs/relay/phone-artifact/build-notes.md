# phone-artifact: build notes

## Requirements to tests

| Requirement | Where |
|---|---|
| R1 | `tests/test_hub.py` OutputTest.test_page_matches_the_digest, test_no_page_without_the_flag |
| R2 | `tests/test_hubpage.py` PageFileTest; `tests/test_hub.py` DigestStoreTest.test_pruning_takes_pages_with_their_digests |
| R3 | `tests/test_hubpage.py` LinkTest; OutputTest.test_page_reports_the_link; `tests/test_commands.py` test_hub_page_url_records_for_a_hub_session_only |
| R4 | RenderTest.test_items_in_digest_order_with_their_fields, test_nothing_waiting_and_a_failed_scan |
| R5 | RenderTest.test_an_unsaved_digest_has_no_numbers; OutputTest.test_an_unsaved_digest_still_gets_a_page |
| R6 | ShellTest.test_the_age_script_runs (node), test_the_artifact_rules |
| R7 | RenderTest.test_nothing_private_reaches_the_page; RedactTest.test_known_ids_and_the_token |
| R8 | RedactTest |
| R9 | RenderTest.test_item_text_is_escaped |
| R10 | ShellTest.test_the_artifact_rules, test_limits, test_the_byte_budget_drops_cards_from_the_end |
| R11 | SkillTest.test_the_skill_has_the_page_rules |
| R12 | README "The hub: relay from your phone" |
| R13 | Live check below; phone check in acceptance.md |
| F5 | ItemsTest.test_malformed_values_do_not_abort_the_digest; OutputTest.test_a_malformed_snapshot_prints_in_every_mode, test_a_malformed_snapshot_gets_a_page; RenderTest.test_values_of_the_wrong_type_are_left_out |

## Where the build differs from the plan's wording

- **No document skeleton.** The Artifact tool's page contract says the publisher wraps a published page in its own doctype, head (charset and a viewport meta with `viewport-fit=cover`) and body. So the page starts at its `<title>` and `<style>`, has no doctype, `html`, `head`, `body` or `meta` tags, and keeps `data-made` and `data-expires` on its own `<main id="hub">`. The dark blocks also set `color-scheme: dark`, as the contract asks. Spec D6 and R10 and plan Task 4 were updated to match.
- **Malformed snapshot tests** use the digest with `hub.load` patched to return the malformed data, as the other OutputTest cases do, instead of a fake dashboard server: the dashboard path hands the same dict to `items()`.
- **Session cards** leave out the state "waiting" when a wait time is shown, as the text digest does.

## Live check (2026-10-11)

From this Claude hub session, with the branch's code:
- `relay hub --json --page` wrote a page for a 10-item digest; a scan for workstation paths, UUIDs and email addresses in it found none.
- The page was published with the Artifact tool as a private artifact; `relay hub page-url` recorded it.
- A second `relay hub --json --page` reported that link and the first digest as `published_digest`; publishing its page with that link updated the same artifact (version 2), and `page-url` recorded the second digest.
- Opened in Chrome: the dark set applied, the age line read "made 0 min ago", the expiry banner stayed hidden, and cards wrapped in a single column. The browser window would not narrow below desktop width, so the 360px check moves to acceptance.md.
