# data-paths-freshness: spec

## Why

A build GO stays fresh only while the merge result of the PR and its base is the same as when it was
reviewed (`freshness.merge_digest`, which hashes the whole merged tree except `docs/relay/`). The
UpstreamPartners hub pushes data commits to its base many times a day: hub notes, asks, the KPI
snapshot, marketing saves, and soon `scripts/fact` output. Each one changes the merge result, so every
waiting build GO there goes stale and needs `relay review` again, though no code changed. Asked for
through relay dispatch (qmwzsp3b4) by the UpstreamPartners fact-command build session, which reports the
owner asked for it there on 2026-11-27.

## Decisions

1. **A repo declares its data paths in its own relay config, on its base branch.** The key is
   `[freshness] data_paths`, a list of strings, in `docs/relay/config.toml`. relay reads it from the
   PR's base ref (the `base_ref` the GO was taken against, for example `origin/develop`), never from the
   PR branch, so a PR cannot widen its own exclusions. The owner's global `~/.relay/config.toml` cannot
   set it: what counts as data is the repo's business.
2. **Interaction with delegated merges (D1).** `docs/relay/config.toml` is a protected path in
   `relaylib/delegated.py` (delegated-merge R8), so a PR that adds or changes data paths is never merged
   by the `release` role; the owner merges it. Since D1's freshness check is the same `freshness.check`,
   a delegated merge sees data paths the same way the owner's merge does, and only after the owner has
   merged the list onto the base.
3. **Code can never be declared data.** An entry is either one file or a folder ending in `/`. Only
   files whose names end in a data extension are ever left out: `.csv`, `.tsv`, `.json`, `.jsonl`,
   `.yaml`, `.yml`, `.md`, `.txt`. A file entry with another extension makes the list invalid. Under a
   folder entry, files with other extensions stay in the digest. These are never data, whatever the
   list says: `AGENTS.md`, `CLAUDE.md` and `WORKFLOW.md` anywhere (delegated-merge's protected names),
   anything under `.github/`, and anything under `docs/relay/` (already left out as bookkeeping).
4. **A PR that touches a data path keeps the whole tree.** If the PR's own diff (merge-base of base
   and head, to head, with renames shown as delete plus add) touches any path the list would leave
   out, nothing is left out for that PR, so a change to data the PR itself makes stays bound to the
   review.
5. **The GO records which data paths it used,** in the build snapshot as `data_paths` (the list as
   read, or `[]` when none applied). `check` computes the current list the same way; if it differs
   from the recorded one, the GO is stale with the reason `data paths changed since the GO`. A GO taken
   before this feature has no `data_paths` and reads as `[]`, so it behaves exactly as today until its
   repo declares a list; the first check after that reports it stale once.
6. **A bad list fails closed.** A TOML error, a key that is not a list of strings, an absolute path, a
   `..` part, an empty entry, a glob character (`*`, `?`, `[`), or a file entry without a data
   extension makes the whole list unused: the digest covers the whole tree, as today. The stale reason
   then names the problem (`data paths on <base> not used: <why>`), so the owner can see why the list
   did not help. A bad list never makes a GO fresher.
7. **Paths match exactly as git names them,** case-sensitive, from the repository root.

## Non-goals

- Setting UpstreamPartners' own list. That repo's own PR does it, with the paths the request names:
  `portfolio/releases/status.yaml`, `portfolio/revenue.yaml`, `portfolio/hub-notes.csv`,
  `portfolio/asks.csv`, `kpi/snapshots.csv`, `kpi/marketing.csv`, `kpi/social.csv`, `kpi/goals.yaml`,
  `marketing/packs/`.
- Changing the "code changed since the GO" check, which compares the PR head with the reviewed head.
  Data commits land on the base, not the PR.
- Leaving data paths out of the local test-run key or CI decisions.

## Requirements

- **R1.** `freshness` reads `[freshness] data_paths` from `docs/relay/config.toml` at the base ref, and
  only there. A missing file or key means no data paths.
- **R2.** The list is validated as in D3 and D6. An invalid list gives no data paths and a reason.
- **R3.** `merge_digest` leaves out, besides `docs/relay/`, each merged-tree path that a valid entry
  covers (equal to a file entry, or under a folder entry) and that ends in a data extension and is not a
  never-data path (D3).
- **R4.** If the PR's diff from the merge-base of base and head to head touches any path R3 would
  leave out (either side of a rename), the digest leaves out only `docs/relay/`.
- **R5.** `snapshot` records `data_paths`: the valid list it applied, or `[]` when there was none, it
  was invalid, or R4 applied.
- **R6.** `check` computes the applied list the same way and returns stale with
  `data paths changed since the GO` when it differs from the recorded one (a missing record is `[]`).
  When the list is invalid and the GO is stale for any reason, the reason adds
  `; data paths on <base> not used: <why>`.
- **R7.** Tests, in `tests/test_freshness.py`, on a temp repo with a bare origin:
  - a GO stays fresh after a base commit that changes only a declared data file;
  - a GO stays fresh after a base commit that adds a new data file under a declared folder;
  - a GO goes stale when the base changes an undeclared path;
  - a GO goes stale when the base changes a non-data file under a declared folder (for example a `.py`);
  - a PR that touches a declared data path: its GO goes stale after a base commit to any declared path;
  - a list read from the PR branch alone (not on the base) has no effect;
  - a GO taken with no list, then the base adds a list: stale once, `data paths changed since the GO`;
  - each invalid form in D6 gives a whole-tree digest and the reason;
  - `AGENTS.md` and a file under `.github/` listed as data stay in the digest.
- **R8.** `docs/reference.md` documents `[freshness] data_paths`, the data extensions, the never-data
  paths, the PR-touches rule, and that the list is read from the base branch and is a protected file
  for delegated merges.

## Failure paths

- **F1.** The base ref cannot be read: `merge_digest` already returns None and the GO is stale; no
  change.
- **F2.** The merge-base cannot be found for R4: treat the PR as touching data (whole tree), so the
  answer can only be staler.
- **F3.** The config file is present but cannot be read by git: no data paths, as F1's whole-tree path.

## Open questions
