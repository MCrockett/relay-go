# status-json-detail: plan

All code is in `relaylib/status.py`; tests in `tests/test_status.py`. Run the suite with
`python3.11 -m unittest discover -s tests -t . -v` at the end.

## Task 1: `owner_action_items` on every row

- `relaylib/status.py`: a helper `_action_items(st, was_merged)` returns
  `[{"action": a["action"], "at": a["at"] if isinstance(a.get("at"), str) else None}]` for each entry of
  `st.get("owner_actions") or []` that is a dict with a string `action`, in order, then
  `{"action": f"PR #{st['pr']} merged on GitHub", "at": None}` when `was_merged`. `_row` (local) and
  `_remote_row` (origin-only) call it with the same `was_merged` that adds one to `owner_actions` today
  (status.py around lines 144 and 213), so list and count cannot drift. The unreadable-state row (around
  line 252) gets `[]`.
- Expected side effect, stated per spec D2 note: every `status.scan` caller (dashboard snapshot rows, menubar,
  `tools/deploy_check.py`) also carries this small list; none shows it.
- Tests:
  - a local feature with two recorded actions (one with `relayed_by` and `via`) lists both as `{action, at}`
    only, in order, and `len == owner_actions`;
  - a feature whose PR the fake gh reports merged ends with `PR #<n> merged on GitHub`, `at` null, under
    `relay status --all --json`;
  - an entry with no `at` and one with a non-string `at` give null; a non-dict entry and one with no `action`
    are skipped (F2);
  - an origin-only feature carries the list;
  - the unreadable row has `[]` and 0.
- Covers: R2, R3, R4, F2.

## Task 2: `excerpt.full`

- `relaylib/status.py` `with_asks`: when it builds `excerpt`, add `"full": agentask.full(words["text"])` next to
  `source` and `text`. `excerpt` stays null otherwise. `render` and the hub keep reading `text` only.
- Update `test_a_waiting_session_is_waiting_on_the_owner_with_its_words`: its last assertion (full text never
  in `--json`) is replaced, because the spec replaces waiting-visibility R6. It now checks
  `data[0]["excerpt"]["full"] == agentask.full(question)` with line breaks kept, `text` still 161
  characters, and the text output still shows only the excerpt line.
- Tests:
  - a message over 4,000 characters gives a `full` of exactly `agentask.FULL` characters;
  - a multi-line message keeps its newlines in `full` and is flattened in `text`;
  - with no transcript, `excerpt` is null (F1);
  - `relay hub` items for the same row show the same excerpt as before (R5).
- Covers: R1, R4, R5, F1.

## Task 3: one store per `relay status` run

- `relaylib/status.py` `cmd_status`: wrap `scan_with_claims(root)` in
  `with gitops.read_memo(), scancache.use(scancache.Store()):`. No `scancache.prefetch`. `other_sessions` and
  `waiting_requests` stay outside, as they read no git.
- Tests, on one temp tree: a local feature, an origin-only feature on a pushed branch, a merged feature (fake
  gh answers merged, so `merged.resolve` gives the same answer in both passes), and two origin branches that
  carry the same `state.md` blob (a stacked copy).
  - R7: `status.scan_with_claims` rows with no store equal the rows inside a fresh store and memo, compared as
    sorted JSON.
  - R8: patch `gitops.run` to record argv while `relay status --json` runs; no recorded call is
    `git show` of a `state.md`, and the `git cat-file` calls ask for each distinct state blob at most once
    (count cat-file calls, not parses).
  - R6: `relay status` and `relay status --json` both run with a store active (patch `status.scan_with_claims`
    to record `scancache.active()` is not None), and none is active after the run returns.
  - F3: make `gitops.cat_files` raise `RelayError` under `relay status --json`; that repo's rows carry the
    "cannot read origin branches" flag and `scan_error`. A second run with the patch removed reads normally
    (nothing kept).
- Covers: R5, R6, R7, R8, F3.

## Task 4: docs and acceptance

- `docs/reference.md`: after the `relay status` line (around line 116), a short list of the `--json` row
  fields: `repo`, `feature`, `stage`, `status`, `round`, `owner`, `verdict`, `pr`, `flags`, `asks`,
  `wait_since`, `waiting_on_owner`, `excerpt` (`source`, `text`, and `full`, the first 4,000 characters of the
  agent's last words), `owner_actions` and `owner_action_items` (`{action, at}`, `at` null for a merge seen on
  GitHub).
- `docs/relay/status-json-detail/acceptance.md`: one open item for R10, triggered by the build GO: the
  building session tells the hub the final field names and shapes by peer message. The build gate does not
  wait on it.
- Covers: R9, R10.
