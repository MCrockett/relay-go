# config-path-case: spec

## Why

Some relay readers fetch a file under `docs/relay/` straight from git with `git show <ref>:docs/relay/...`. Git paths are case-sensitive, and UpstreamPartners tracks the folder as `Docs/relay/`. For that repo the read misses and the setting silently reads as absent:

- `freshness.data_paths` returns `[]`, so a declared `[freshness] data_paths` list does nothing (this blocks request qmwzwfbxs).
- `delegated.production_branches` reads as "not opted in" (fails safe, but the opt-in cannot work).
- `cimode` skips the git overlay of `[build]` from `origin/<base>` (the disk read still works).
- `deploycheck.needs_command` sees no repo config and reports the repo as needing a test_command.
- `localtests._feature_done` and `localtests._origin_state` read `docs/relay/<slug>/state.md` from the feature branch; a miss makes a live feature look done and its GO look absent.

Disk readers already use `state.relay_dir()`, and the tree-listing readers (`ls_files`, holds, ownership, dispatch, owneractions) already match case-insensitively. Only these direct `git show` reads with a hardcoded spelling are broken.

## Decisions

- D1. One shared helper in `gitops`: `show_relay(root, ref, rel)` returns the text of `docs/relay/<rel>` at ref, spelled however that ref tracks the `docs` and `relay` folders, or None when it is not there. `rel` is relay's own naming (`config.toml`, `<slug>/state.md`) and is used as given. Reason: one place to get it right, and every caller keeps its current None-means-absent handling.
- D2. Fast path first: try the lowercase spelling with the existing `gitops.show` (which already answers from the scan cache inside a snapshot build). Only on a miss, look up the folder spelling with non-recursive `git ls-tree --name-only` at the repo root and then inside the found `docs` folder, matching each name case-insensitively. If both found names are already lowercase, the file is simply absent: return None without a second show. Reason: repos with lowercase `docs/relay` pay nothing extra on a hit and at most two small git calls on a miss; no recursive tree listing.
- D3. Inside a snapshot build the folder spelling per (root, commit of ref) is computed once with `scancache.per_build`, so a build that reads several files from one ref looks it up once.
- D4. Labels in error messages (for example a TOML error) name the path as found, so the owner sees the real file.

## Non-goals

- No change to disk readers, tree-listing readers, or the lowercase spelling relay writes for new repos.
- No case-insensitive matching of `rel` itself (`config.toml`, slug folders): relay creates those names.
- No change to `status.py`'s handoff check, which builds its path from the on-disk spelling.

## Requirements

- R1. `gitops.show_relay(root, ref, rel)` as in D1 to D3. A ref that does not resolve, or a tree with no `docs`/`relay` folder in any case, returns None and never raises.
- R2. `freshness.data_paths` reads the base commit's config through `show_relay`.
- R3. `delegated.production_branches` reads the pinned base commit's config through `show_relay`; its TOML error label names `origin/<base>:<path as found>`.
- R4. `cimode` settings read the `origin/<base>` overlay through `show_relay`, label as in D4.
- R5. `deploycheck.needs_command` reads `origin/develop` then `origin/main` config through `show_relay`.
- R6. `localtests._feature_done` and `localtests._origin_state` read the feature's state.md through `show_relay`.
- R7. After the change, `grep` finds no `gitops.show(` call in `relaylib/` whose path is built from `RELAY_DIR` or a literal `docs/relay`; any other hit found during the build moves to `show_relay` too.
- R8. Tests, in temp repos that track `Docs/relay/config.toml` (and `Docs/relay/<slug>/state.md` for R6): one per reader R2 to R6 showing the setting is read; `show_relay` unit tests for lowercase hit, `Docs` hit, absent file in a lowercase repo, absent folder, a bad ref, and inside a snapshot build. Existing tests stay green.

## Failure paths

- Git error during the ls-tree lookup: treated as absent (None), matching today's `gitops.show` behaviour.
- Two folders differing only by case at one ref (`docs/` and `Docs/`): lowercase wins through the fast path; on a miss, the first case-insensitive match from ls-tree order is used. This is a broken repo and not otherwise handled.

## Open questions
