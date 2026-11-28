# config-path-case: plan

Spec: `docs/relay/config-path-case/spec.md`. Test command: `python3.11 -m unittest discover -s tests -t . -v`. Each task is test first: write the failing test, see it fail, make it pass, run the full suite.

## Task 1: `gitops.show_relay`

Files: `relaylib/gitops.py`, `tests/test_gitops.py`.

- Add `show_relay(root, ref, rel)`:
  1. `text = show(root, ref, f"{RELAY_DIR}/{rel}")`; return it when not None (fast path, uses the scan cache as `show` does today).
  2. Else get the folder spelling with `_relay_folder(root, ref)`: non-recursive `git ls-tree --name-only <ref>` at the root, pick the first name whose lower() is `docs`; then `git ls-tree --name-only <ref> <Docs>/`, pick the first entry whose last part lower() is `relay`. Any git error or no match returns None. Inside a build, wrap with `scancache.per_build(("relay_folder", root, commit), ...)` keyed by `scancache.commit_of(root, ref)` (None commit returns None).
  3. If the folder is None or equals `docs/relay`, return None. Else return `show(root, ref, f"{folder}/{rel}")`.
- Also return the path used, through a sibling `relay_path_at(root, ref, rel)` that returns the spelled path or None, so callers can build labels (D4). `show_relay` calls it and keeps the plain text return.
- Import `RELAY_DIR` locally from `state` to avoid an import cycle if one appears.
- Tests: lowercase hit; `Docs/relay/config.toml` hit; absent file in a lowercase repo returns None; repo with no docs folder returns None; bad ref returns None without raising; inside `scancache` build (use the same build entry point `tests/test_scancache.py` uses) a `Docs` repo is read and a second read of another file at the same ref does not call ls-tree again (count via `buildstats` or a patched `git`).

Covers: R1.

## Task 2: freshness data_paths

Files: `relaylib/freshness.py`, `tests/test_freshness.py`.

- `data_paths` reads `gitops.show_relay(root, base_commit, "config.toml")`; TOML error label uses the found path.
- Test: temp repo whose base commit tracks `Docs/relay/config.toml` with `[freshness] data_paths = ["data/"]`; `data_paths` returns `(["data/"], None)`.

Covers: R2.

## Task 3: delegated production_branches

Files: `relaylib/delegated.py`, `tests/test_delegated.py`.

- `production_branches` uses `relay_path_at` then `show`; label `origin/<base>:<found path>`; missing keeps the "has not opted in" message.
- Test: base commit tracks `Docs/relay/config.toml` with the existing `OPT_IN` text; result is `(["main"], None)`.

Covers: R3.

## Task 4: cimode overlay

Files: `relaylib/cimode.py`, `tests/test_cimode.py`.

- The `origin/<base>` overlay uses `relay_path_at` and `show`; label `origin/<base>:<found path>`.
- Test: origin base tracks `Docs/relay/config.toml` with `[build] ci = "none"` while the worktree has no repo config; settings show ci none. Also an invalid TOML there names `Docs/relay/config.toml` in the error.

Covers: R4.

## Task 5: deploycheck needs_command

Files: `relaylib/deploycheck.py`, `tests/test_deploycheck.py`.

- Read via `gitops.show_relay(repo, f"origin/{b}", "config.toml")`.
- Test: a repo under a temp root whose origin/develop tracks `Docs/relay/config.toml` with `[build] ci = "none"` is not listed.

Covers: R5.

## Task 6: localtests state reads

Files: `relaylib/localtests.py`, `tests/test_localtests.py`.

- `_feature_done` and `_origin_state` read `gitops.show_relay(repo, f"origin/{branch}", f"{slug}/state.md")`.
- Test: origin feature branch tracks `Docs/relay/<slug>/state.md` with a live (not merged) state; `_origin_state` returns it and `_feature_done` is False.

Covers: R6.

## Task 7: sweep and full suite

Files: any found.

- `grep -rn "gitops.show(" relaylib/` and check every hit whose path comes from `RELAY_DIR`, `CONFIG` or a literal `docs/relay` without going through `ls_files`; move any left to `show_relay`. Record the grep result in the PR body.
- Run the full suite; all green.

Covers: R7, R8.
