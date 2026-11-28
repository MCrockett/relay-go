# data-paths-freshness: plan

Code in `relaylib/freshness.py`; tests in `tests/test_freshness.py`; docs in `docs/reference.md`. The spec's
decisions are cited as D1 to D7 in the order written; "delegated merges" means delegated-merge's D1.

Choices this plan makes where the spec review left notes:
- Never-data names match ignoring case, as `relaylib/delegated.py` does (`agents.md`, `Claude.md`,
  `.GitHub/` are never data). Entries themselves match case-sensitively (D7).
- Entries are also invalid (D6) when they start with `./` or `/`, contain `\`, `//`, or surrounding
  spaces. So every entry either matches as written or makes the list unused with a reason.
- `merge_digest` reads `ls-tree -r -z`, so paths with spaces or non-ASCII characters match entries.
  The digest text changes form, which makes every existing build GO stale once. To avoid that, the
  hashed lines stay in today's form (`git ls-tree` without `-z` output), and the `-z` listing is used
  only to decide which paths to leave out: one extra `ls-tree -z` call, only when a valid list applies.
- R4 is computed over the PR diff's own paths (`git diff --name-only --no-renames -z <merge-base> <head>`),
  each tested with R3's rule on the path alone. A PR that deletes a data file, or renames one out of
  a declared folder, touches data.
- `base_ref` is resolved to one commit at the start of `snapshot` and `check`, and the config, the
  merge-base and the merge-tree all read that commit, so a fetch in between cannot mix two bases.
- Order in `check` for build: inputs, code changed, PR base changed, **data paths changed**, merge
  result changed. The invalid-list suffix (R6) is added to whichever stale reason comes after the
  inputs check.
- Why the GO records `data_paths` (D5): `docs/relay/config.toml` is under `docs/relay/`, which the
  digest leaves out, so without the record a list change on the base would go unseen.
- A stacked PR's base is another feature branch, so its list comes from that branch. That is safe: the
  GO goes stale on "PR base changed" when the lower PR merges.
- Cost: with no list declared, `check` and `snapshot` make one extra call (`git show` of the config at
  the base commit). The merge-base and the PR diff run only when a valid list applies.

## Task 1: reading and validating the list

- `freshness.data_paths(root, base_commit)` returns `(entries, why)`. It reads `docs/relay/config.toml`
  with `gitops.show` at the commit, parses with `cimode._read_toml`, and takes `[freshness] data_paths`.
  No file or no key gives `([], None)`. Any D6 problem, or the extra forms above, gives `([], "<why>")`.
- `freshness.is_data(path, entries)`: true when the path equals a file entry or is under a folder entry,
  ends in a data extension, and is not never-data (case-insensitive: the protected names as basenames,
  `.github/`, `docs/relay/`).
- Tests: valid file and folder entries; each invalid form gives a reason (TOML error, not a list, a
  non-string, absolute, `..`, empty, `*`, `?`, `[`, a `.py` file entry, `./x.csv`, `a//b.csv`,
  `a\\b.csv`, ` x.csv`); `AGENTS.md`, `sub/claude.md`, `.github/x.yml` and `docs/relay/x.md` listed as
  data are not data; `kpi/Snapshots.csv` does not match `kpi/snapshots.csv`; a `.py` under a folder
  entry is not data.
- Covers: R1, R2, D3, D6, D7.

## Task 2: the digest, the snapshot and the check

- `merge_digest(root, base_ref, head, prefix, entries=())`: as today, plus it leaves out lines whose path
  `is_data`, using the `-z` listing to name paths as described above.
- `freshness.applied(root, base_commit, head)` returns `(entries, why)`: `data_paths`, then `[]` when the
  PR diff from the merge-base touches data (R4) or no merge-base is found (F2).
- `snapshot`: resolve `base_ref` once, then record `data_paths` (the applied entries) next to `merge`, and
  compute `merge` with them.
- `check`: resolve the base once, compute the applied entries; if they differ from the recorded
  `data_paths` (missing reads as `[]`), stale with `data paths changed since the GO`; then the merge
  digest with them, as today. R6's suffix when the list was invalid.
- Tests (R7), on the existing fixture with a second clone pushing to the base:
  - base changes a declared data file only: fresh;
  - base adds a new `.csv` under a declared folder: fresh;
  - base changes an undeclared path: stale, merge result changed;
  - base changes a `.py` under a declared folder: stale;
  - the PR edits a declared file, then the base changes another declared file: stale;
  - the PR deletes a declared file: R4 applies (recorded `data_paths` is `[]`);
  - the PR renames a file out of a declared folder: R4 applies;
  - a list on the PR branch only: no effect (recorded `[]`, base data commit makes it stale);
  - GO with no list, then the base adds one: stale, `data paths changed since the GO`;
  - an invalid list on the base: a base data commit makes the GO stale, and the reason ends with
    `data paths on origin/develop not used: ...`; a code change on the PR with an invalid list gives
    `code changed since the GO; data paths on ...`;
  - no merge-base (patch `gitops.git` for `merge-base` to fail): whole tree, recorded `[]`;
  - a GO recorded before this feature (no `data_paths` key) with no list stays fresh;
  - a declared path with a space in its name is left out.
- Covers: R3, R4, R5, R6, R7, F1, F2, F3.

## Task 3: docs

- `docs/reference.md`, in the freshness or config section: `[freshness] data_paths` with an example, the
  data extensions, the never-data paths, the PR-touches rule, the base-branch rule, that
  `docs/relay/config.toml` is protected for delegated merges, and a plain warning that some data-type
  files act like code (`package.json`, `requirements.txt`, `docker-compose.yml`, `SKILL.md`, prompt
  files), so list only files that are data.
- Covers: R8.
