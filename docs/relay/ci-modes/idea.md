# CI modes per project

## Problem

relay's build gate assumes GitHub CI. The only escape is `[build] require_ci = false`, which the owner set globally on 2026-10-23 (portfolio CI policy: tests run locally, GitHub CI only on releases). With it off, nothing checks that tests pass before a merge. Many projects never need GitHub CI on PRs, so "no GitHub CI" should be a normal, supported mode, not an exception that drops the test gate.

## Who it is for

The owner, across every relay repo: fewer CI minutes, with a real test gate on every merge.

## The idea

Replace `require_ci` with a per-project `ci` mode, set globally or in a repo's `docs/relay/config.toml`:

- `pr`: GitHub CI on the PR must be green (today's behavior with require_ci on).
- `local`: relay runs the project's own test command at `relay submit`, on the PR head, and records pass or fail with the commit in state.md. The build gate and `relay merge` use that result instead of GitHub.
- `release`: like `local` for PRs into develop; PRs into main still need green GitHub CI.

Also: warn when a `local` project's GitHub workflow still runs on PRs to develop (wasted minutes). `relay status` and the dashboard say where tests ran, for example "tests: local, green at 54d1991".

## Success

- A `local` project cannot merge with failing tests or tests that ran on a different commit.
- A `pr` project behaves exactly as it does today with require_ci on.
- Existing configs with `require_ci` keep working until changed.
