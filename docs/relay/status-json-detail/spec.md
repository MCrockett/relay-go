# status-json-detail: spec

## Why

hordle's pm view (pm-fixes P-A2) reads `relay status --json`. It wants two things the output does not give
today: the agent's whole last message when a session waits on the owner (the row has only the 160-character
`excerpt.text`), and which owner actions a feature had and when (the row has only the `owner_actions`
count). The owner also saw `--json` take 2m24s from the UpstreamPartners checkout.

## What the slowness is

Profiled on 2026-11-27 from `~/StudioProjects/UpstreamPartners`: one run took 58s, and 57s of it was 7,254 git
subprocesses. 4,391 were `git show` reads of origin state files (35s, in `_read_remote_features` through
`state.parse_at`), 1,990 were `git remote get-url origin` (14s), and `merged.resolve` added 9s. The dashboard
does not pay this, because its builds run inside a `scancache.Store` with `gitops.read_memo()`, which batch
blob reads by object id and keep repo facts for the build. `relay status` runs with no store.

Running the same scan inside a fresh store and memo, with no prefetch, took 11s and gave identical rows (473
rows, compared as sorted JSON). Adding the dashboard's per-repo `scancache.prefetch` of origin heads first took
9.6s, so status does not prefetch: the extra code buys about a second. The 2m24s the owner saw is the same
cost on a busier machine.

## Decisions

1. **One fresh store per `relay status` run.** `cmd_status` runs its scan inside `gitops.read_memo()` and
   `scancache.use(scancache.Store())`, the same way a dashboard build does. The store is new each run and is
   dropped at the end, so nothing is kept between runs and status stays read-only and live. Owner actions
   still read with no store (scancache D6), because only `cmd_status` changes. It does not call
   `scancache.prefetch`; blobs are read when first needed and cached by id for the run.
   A store also brings dashboard-scan F2: when git cannot list or read a repo's relay files, that repo's rows
   say so ("cannot read origin branches" flag, `scan_error`) instead of quietly reading less. relay status
   takes that behavior as it is, the same as the dashboard shows (F3).
2. **The new fields are for `relay status --json`.** The scan adds them to its rows. The dashboard
   rebuilds `excerpt` with only `source` and `text` (`ui/snapshot.py`), so its rows do not carry
   `excerpt.full`; its detail view already has the agent's text. `snapshot.py` does not change. The text
   output of `relay status`, the hub items and the phone page do not show the new fields.
3. **`excerpt.full` is the agent's text cut at `agentask.FULL` (4,000 characters)** by `agentask.full()`,
   with its line breaks kept, unlike `excerpt.text`, which is flattened and clipped to 160. This replaces
   waiting-visibility R6's rule that `relay status --json` never includes the full text, as the idea asks.
   The text is still read from the transcript each run and never stored (waiting-visibility D3 holds).
4. **`owner_action_items` matches the count.** The count adds one for a merge relay saw on GitHub but did not
   record; the list adds one item for it too, with `at` null, because finding the merge time would cost a gh
   call per merged row. The list's length equals the count on every row whose entries are well formed (F2).

## Non-goals

- No change to the `relay status` text output, `relay status --json` row order, or any existing field.
- No cache kept between `relay status` runs, and no change to the dashboard's own store.
- No speedup for `tools/deploy_check.py` or other `status.scan` callers.

## Requirements

- **R1.** When a row has an `excerpt`, it carries `excerpt.full`: `agentask.full()` of the same text
  `excerpt.text` came from. When `excerpt` is null it stays null.
- **R2.** Every feature row (local checkout, origin-only, and the unreadable-state row) carries
  `owner_action_items`: a list of `{"action": <str>, "at": <str or null>}`, one per entry of the state's
  `owner_actions` in recorded order, taking only `action` and `at`. Other entry fields (`relayed_by`, `via`)
  are left out. An `at` that is missing or not a string becomes null.
- **R3.** A row whose PR relay found merged on GitHub ends its list with
  `{"action": "PR #<n> merged on GitHub", "at": null}`, so `len(owner_action_items) == owner_actions` on
  every row whose entries are well formed (F2). R2's unreadable row has `[]` and 0.
- **R4.** `owner_actions`, `excerpt.text`, `excerpt.source` and every other existing field keep their names,
  types and meanings.
- **R5.** The text output of `relay status` and the hub's items do not change, on any run where git reads
  every repo's relay files without error (F3 covers a failed read).
- **R6.** `cmd_status` (text and `--json`) runs its scan inside `gitops.read_memo()` and a new
  `scancache.Store()` made for that run only.
- **R7.** With R6, the rows `relay status` builds are the same as without it, for the same repos and git
  state, on a run with no read failure. A test checks that on a temp project tree with a local feature, an
  origin-only feature, a merged one, and two origin branches that carry the same state blob (a stacked copy).
- **R8.** With R6, the scan reads origin state files with no `git show` calls, and with at most one
  `git cat-file` per distinct state blob, however many branches carry that blob. A test counts the calls on
  the R7 tree.
- **R9.** `docs/reference.md` gets a short list of the `relay status --json` row fields next to its mention
  of `--json`, including `excerpt.full` and `owner_action_items`.
- **R10.** After the build GO, the session that built this tells the hub session the final names and shapes
  of `excerpt.full` and `owner_action_items`, by peer message, because hordle's pm reads them. If a name
  changes during review, the message gives the new one.

## Failure paths

- **F1.** A transcript that cannot be read leaves `excerpt` null, as today. No `full` without `text`.
- **F2.** An `owner_actions` entry that is not a dict, or has no `action`, is skipped in the list. The count
  stays as the state says, so in that case only the count can be larger than the list. This is a state
  that relay never writes.
- **F3.** If git cannot list a repo's relay tree or read a blob during the run, scancache marks the repo
  failed for the run (dashboard-scan F2). Its rows then carry the "cannot read origin branches" flag and
  `scan_error`, a repo left with no rows shows as a `?` row waiting on the owner, and origin-only features
  whose state could not be read are missing from that run. That matches the dashboard. Nothing is kept, so
  the next run reads again.

## Open questions
