# models-tab: spec

## Why

The reviewer table (who reviews whose work, in what order, at what effort) lives in `~/.relay/config.toml` and changes only through `relay roles set` in a terminal. The owner wants to see and change it from the dashboard, and often for a limited time ("every review through Fable until 11pm, Codex as backup"), which today needs a hand-made script to switch back. See idea.md.

## Decisions

- D1. **The Usage tab becomes Models.** A Reviewers panel goes at the top; the usage meters and the sortable reviewer activity stay below it, unchanged. The old `#view=usage` link still opens the tab.
- D2. **Timed changes live in relay, not in the dashboard.** A timed table is stored in `~/.relay/review-until.json` (under `RELAY_HOME`, mode 0600), next to the owner config rather than inside `config.toml`: relay reads TOML but only edits it line by line, and a timed entry must be replaced atomically and expire without anything running. `~/.relay/unavailable.json` already works this way for "out of usage until". `config.load` applies an entry only while its end time is in the future; an expired entry is ignored and removed at the next write.
- D3. **A timed table wins over every other table** for its author, including a project's own `docs/relay/config.toml`: it is the owner's explicit decision for a limited time. The owner's permanent table is untouched underneath and applies again when the time ends.
- D4. **Only the review tables can be timed** (`review.claude`, `review.codex`, `review.owner`). Writer roles and `reviewer.<provider>` defaults stay permanent-only.
- D5. **Changing the reviewer table is the owner's decision.** `relay roles set` becomes owner-only like `relay rule` and the overrides: inside an agent session it refuses unless passed `--relayed`, which records that the agent is passing on the owner's words. The dashboard is the owner by definition (token, loopback, confirmation).
- D6. **The dashboard edits the owner's tables only.** Projects that keep their own `[review.prefer]` are listed in the panel as overriding it, with a pointer to their `docs/relay/config.toml`; the dashboard does not edit project files.
- D7. **Writes are atomic and locked.** `set_role` and the timed file both write through a temporary file and rename, under one lock in relay home, because the dashboard and the CLI can now write at the same time.
- D8. **Edits are bound to what the owner saw**, like every other dashboard action: a save carries a revision of the owner config and the timed file, and is refused with fresh values if either changed since the page loaded.

## Requirements

### Timed tables (CLI and config)
- R1. `relay roles set review.<author> "<entries>" --until <when>` writes a timed table for that author to `review-until.json` instead of `config.toml`. `<when>` is `HH:MM` (the next time it occurs in `[limits] timezone`: today if still ahead, otherwise tomorrow) or an ISO 8601 date-time with a time zone. It must be in the future and at most 7 days away; otherwise the command refuses and changes nothing.
- R2. Each timed entry stores the author, the entries (validated like `set_role` does today), the end time as epoch seconds, when it was set, and who set it (`owner`, or the agent for `--relayed`).
- R3. `config.load` applies unexpired timed entries after the owner file and after the repo overlay (D3). An unreadable or malformed timed file is ignored for selection, never fatal, and reported by `relay roles` and the panel.
- R4. `relay roles set review.<author> "<entries>"` without `--until` writes `config.toml` as today and also ends any timed entry for that author, saying so in its output.
- R5. `relay roles end review.<author>` (or `relay roles end all`) ends timed entries now.
- R6. `relay roles` shows, for an author with a timed table, the timed list marked `temporary until <ddd HH:MM>` in the configured time zone, followed by the permanent list it returns to.
- R7. `relay roles set` and `relay roles end` follow D5: owner terminal, or an agent with `--relayed`. Showing the table stays open to everyone.
- R8. `set_role` writes atomically under the relay-home lock (D7); its output and the file format are otherwise unchanged.

### Snapshot and server
- R9. The snapshot gains a top-level `reviewers` object from the owner config with the timed file applied: for each author, the effective entries (`id`, `provider`, `model`, `effort`, `available`, `reason`), the timed end time if any, and the permanent entries; the writer roles; the effort defaults; a `known` list of `provider:model` values from the owner config and the review ledger; the projects whose own config sets `[review.prefer]` (D6); and `revision`, a hash of the owner config bytes and the timed file bytes.
- R10. `POST /api/roles` takes `{author, entries, until, seen}` (`until` absent, or `HH:MM` / ISO as in R1) and applies it through the same code as R1 and R4. `POST /api/roles/end` takes `{author, seen}` (author or `all`) and applies R5. Both check the token and Host like other actions, refuse with 409 and the fresh `reviewers` object when `seen` differs from the current revision (D8), refuse invalid input with 400 and a plain message, and trigger a snapshot refresh.
- R11. Entries from the dashboard are validated like the CLI: known provider, non-empty model, effort empty or one of `low`, `medium`, `high`, `xhigh`, no duplicates, one to six entries.

### Models tab
- R12. The nav tab reads Models; `#view=models` and the old `#view=usage` both open it.
- R13. The Reviewers panel shows one row per author: the reviewers in order with effort and availability, the `temporary until` mark with an End now button when timed, and the permanent list beneath a timed one. Writer roles appear read-only below, labeled as informational.
- R14. Edit on a row opens a dialog: an ordered list where each reviewer can be moved up or down or removed, an effort select (default, low, medium, high, xhigh), Add reviewer from the `known` list or typed `provider:model`, and Until (no end, or a time). Unavailable models are marked with the reason, but can still be chosen.
- R15. Save shows the existing confirmation dialog with the old and new lists and the end time, then posts R10. The result appears in the header message; a 409 shows "Changed since you looked" with the fresh table, like other actions.
- R16. Projects listed under D6 appear in the panel with a note that their own config overrides the owner table for that project.
- R17. The panel works with the keyboard: every control is a button, select or input with a label, and focus returns to the row's Edit button after the dialog closes.

### Docs and tests
- R18. README: the Models tab, `--until`, `relay roles end`, and owner-only `relay roles set`.
- R19. Tests cover time parsing (today, tomorrow, ISO, past, beyond 7 days, time zone), load precedence including a repo overlay, expiry, a malformed timed file, R4 ending a timed entry, R5, owner-only refusal and `--relayed`, atomic writes, the snapshot `reviewers` object, both endpoints (200, 400, 409), and page strings. List and order handling in the editor is a small pure function tested in Node, like the sort rules.

## Failure paths

- F1. The owner config or timed file changes between page load and save: 409 with fresh values; nothing is written.
- F2. Invalid entry, past or too-distant end time, or an empty list: 400 with the reason; nothing is written.
- F3. The timed file is malformed or unreadable: selection uses the permanent tables; `relay roles` and the panel say the timed file was ignored and why. The next write replaces it.
- F4. The lock is busy for more than a second: the write is refused with "busy, try again"; nothing is written.
- F5. A review already running keeps the reviewer it started with; a change applies from the next review.
- F6. The machine was asleep or off when a timed table ended: it simply stops applying; nothing has to run at the end time.
- F7. An agent session runs `relay roles set` or `relay roles end` without `--relayed`: refused with the owner-only message; nothing is written.

## Non-goals

- Editing a project's own `docs/relay/config.toml` from the dashboard.
- Timed changes for writer roles, `reviewer.<provider>` defaults, limits or other settings.
- Notifications when a timed table ends.
- Launching writers from the writer roles.

## Open questions
