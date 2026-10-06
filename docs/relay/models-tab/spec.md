# models-tab: spec

## Why

The reviewer table (who reviews whose work, in what order, at what effort) lives in `~/.relay/config.toml` and changes only through `relay roles set` in a terminal. The owner wants to see and change it from the dashboard, and often for a limited time ("every review through Fable until 11pm, Codex as backup"), which today needs a hand-made script to switch back. See idea.md.

## Decisions

- D1. **The Usage tab becomes Models.** A Reviewers panel goes at the top; the usage meters and the sortable reviewer activity stay below it, unchanged. The old `#view=usage` link still opens the tab.
- D2. **Timed changes live in relay, not in the dashboard.** The owner asked for the end time to be kept with the owner's config; it is stored in `~/.relay/review-until.json` (under `RELAY_HOME`, mode 0600), next to `config.toml` rather than inside it: relay reads TOML but only edits it line by line, and a timed entry must be replaced atomically and expire without anything running. `~/.relay/unavailable.json` already works this way for "out of usage until". `config.load` applies an entry only while its end time is in the future; an expired entry is ignored and removed at the next write.
- D3. **A timed table wins over every other table** for its author, including a project's own `docs/relay/config.toml`: it is the owner's explicit decision for a limited time. The owner's permanent table is untouched underneath and applies again when the time ends.
- D4. **Only the review tables can be timed** (`review.claude`, `review.codex`, `review.owner`). Writer roles and `reviewer.<provider>` defaults stay permanent-only.
- D5. **Changing the reviewer table is the owner's decision.** `relay roles set` becomes owner-only like `relay rule` and the overrides: inside an agent session it refuses unless passed `--relayed`, which records that the agent is passing on the owner's words. The dashboard is the owner by definition (token, loopback, confirmation). Without this, an agent could quietly choose a weaker reviewer for its own work.
- D6. **The dashboard edits the owner's tables only.** Projects that keep their own `[review.prefer]` are listed in the panel as overriding it, with a pointer to their `docs/relay/config.toml`; the dashboard does not edit project files.
- D7. **Writes are atomic and locked.** `set_role` and the timed file both write through a temporary file and rename, under one lock in relay home, because the dashboard and the CLI can now write at the same time.
- D8. **Edits are bound to what the owner saw**, like every other dashboard action: a save carries a revision of the owner config and the timed file, and is refused with fresh values if either changed since the page loaded.
- D9. **Every change to a review table is logged**, permanent or timed, from the CLI or the dashboard, in `~/.relay/roles-log.jsonl`: when, which table, the new list, the end time if any, and who (owner, dashboard, or the agent's provider and session for `--relayed`). `relay roles` and the panel show the latest change per author, so a relayed change is always visible.
- D10. **The permanent table and a timed table are separate.** Setting the permanent table never ends a timed one, and saving a timed table never touches the permanent one; a timed table ends only at its end time or by `relay roles end` / End now. One rule for the CLI and the dashboard, so no save silently discards the other table.
- D11. **One set of validation rules for every write** (CLI and dashboard): the provider is `claude` or `codex`; the model is not empty; the effort is empty or one lowercase word (`[a-z]+`), passed to the reviewer as today; no `provider:model` appears twice in one list; a list has at least one entry. Duplicates are newly refused on the CLI. Effort values are not checked against a fixed list because each reviewer CLI owns its vocabulary (Claude takes `low` to `max`; the owner's Codex uses `xhigh`); the dashboard offers `low`, `medium`, `high`, `xhigh` and keeps any other value already in a list. Reading stays lenient: an existing config that breaks a rule still loads as today.
- D12. **A timed table lasts at most 7 days.** It is for a short change; a forgotten one would otherwise override the owner's table for weeks without notice.

## Requirements

### Timed tables (CLI and config)
- R1. `relay roles set review.<author> "<entries>" --until <when>` writes a timed table for that author to `review-until.json` instead of `config.toml`. `<when>` is `HH:MM` (the next time it occurs in the owner config's `[limits] timezone`, never a project's: today if still ahead, otherwise tomorrow) or an ISO 8601 date-time with a time zone. It must be in the future and at most 7 days away (D12). A local time that a daylight-saving change skips is refused with a message; one that occurs twice means the first. Otherwise the command refuses and changes nothing.
- R2. Each timed entry stores the author, the entries (validated by D11), the end time as epoch seconds, when it was set, and who set it (as in D9).
- R2a. Every write of a review table, permanent or timed, appends one line to `roles-log.jsonl` (D9) after the write succeeds. `relay roles` prints the latest change per author: `last changed <ddd HH:MM> by <who>`.
- R3. `config.load` applies unexpired timed entries after the owner file and after the repo overlay (D3). An unreadable or malformed timed file is ignored for selection, never fatal, and reported by `relay roles` and the panel. In a well-formed file, an author's timed entry with a bad item (unknown provider, empty model) is ignored as a whole for that author and reported the same way, so a review never fails on it.
- R4. `relay roles set review.<author> "<entries>"` without `--until` writes `config.toml` as today and leaves any timed entry in place (D10); when one exists, the output says the timed table still applies until its end time.
- R5. `relay roles end review.<author>` (or `relay roles end all`) ends timed entries now. With nothing to end it prints `no temporary table for <author>` and exits 0.
- R6. `relay roles` shows, for an author with a timed table, the timed list marked `temporary until <ddd HH:MM>` in the configured time zone, followed by the permanent list it returns to.
- R7. `relay roles set` and `relay roles end` follow D5: owner terminal, or an agent with `--relayed`. Showing the table stays open to everyone.
- R8. `set_role` writes atomically under the relay-home lock (D7); apart from D11, its output and the file format are unchanged.

### Snapshot and server
- R9. The snapshot gains a top-level `reviewers` object from the owner config with the timed file applied: for each author, the effective entries (`id`, `provider`, `model`, `effort`, `available`, `reason`), the timed end time if any, and the permanent entries; the writer roles; the effort defaults; a `known` list of `provider:model` values from the owner config and the review ledger; the projects whose own config sets `[review.prefer]` (D6); the latest change per author (D9); and `revision`, a hash of the owner config bytes and the timed file bytes, where a missing file counts as empty so a first save works.
- R10. `POST /api/roles` takes `{author, entries, until, seen}` (`until` absent, or `HH:MM` / ISO as in R1) and applies it through the same code as R1 and R4. `POST /api/roles/end` takes `{author, seen}` (author or `all`) and applies R5. The `seen` check and the write happen under the same lock (D7), so a CLI write cannot slip between them. Both check the token and Host like other actions, refuse with 409 and the fresh `reviewers` object when `seen` differs from the current revision (D8), refuse invalid input with 400 and a plain message, and trigger a snapshot refresh.
- R11. Entries from the dashboard are validated by D11, the same function the CLI uses. `POST /api/roles` carries `table`: `permanent` or `timed`; `timed` requires `until`, and `permanent` refuses one. End now on an entry that has already expired is a no-op that returns 200.

### Models tab
- R12. The nav tab reads Models; `#view=models` and the old `#view=usage` both open it.
- R13. The Reviewers panel shows one row per author: the reviewers in order with effort and availability, the `temporary until` mark with an End now button when timed, the permanent list beneath a timed one, and the latest change (D9). Once a timed table's end time passes, the page stops showing it as current at its next render, without waiting for the server. Writer roles appear read-only below, labeled as informational.
- R14. A row has Edit permanent and, when timed, Edit temporary; a row without a timed table also has Set temporary. Each opens the dialog seeded from that table (Set temporary starts from the permanent list), with Until shown and required only for a temporary table, prefilled with the current end time when editing one. The dialog is an ordered list where each reviewer can be moved up or down or removed, an effort select (default, low, medium, high, xhigh, plus any other value already in the list, per D11), and Add reviewer from the `known` list or typed `provider:model`. Unavailable models are marked with the reason, but can still be chosen. A typed model not in `known` gets a note: relay checks only its shape, and a mistyped model placed first makes reviews fail.
- R15. Save shows the existing confirmation dialog naming the table (permanent, or temporary until a time) with that table's old and new lists, then posts R10. The result appears in the header message; a 409 shows "Changed since you looked" with the fresh table, like other actions.
- R16. Projects listed under D6 appear in the panel with a note that their own config overrides the owner table for that project.
- R17. The panel works with the keyboard: every control is a button, select or input with a label, and focus returns to the row's Edit button after the dialog closes.

### Docs and tests
- R18. README: the Models tab, `--until`, `relay roles end`, and owner-only `relay roles set`.
- R19. Tests cover time parsing (today, tomorrow, ISO, past, beyond 7 days, time zone), load precedence including a repo overlay, expiry, a malformed timed file, R4 ending a timed entry, R5, owner-only refusal and `--relayed`, atomic writes, the snapshot `reviewers` object, both endpoints (200, 400, 409), and page strings. List and order handling in the editor is a small pure function tested in Node, like the sort rules. Every test that loads config runs with `RELAY_HOME` set to a temporary folder, so a live timed table on the owner's machine cannot change results.

## Failure paths

- F1. The owner config or timed file changes between page load and save: 409 with fresh values; nothing is written.
- F2. Invalid entry, past or too-distant end time, or an empty list: 400 with the reason; nothing is written.
- F3. The timed file is malformed or unreadable: selection uses the permanent tables; `relay roles` and the panel say the timed file was ignored and why. The next write replaces it.
- F4. The lock is busy for more than a second: the write is refused with "busy, try again"; nothing is written.
- F5. A review already running keeps the reviewer it started with; a change applies from the next review.
- F6. The machine was asleep or off when a timed table ended: it simply stops applying; nothing has to run at the end time.
- F7. An agent session runs `relay roles set` or `relay roles end` without `--relayed`: refused with the owner-only message; nothing is written.
- F8. A typed model that does not exist: accepted (shape only, as the CLI today); the review that tries it fails and says so; the dialog warns before saving (R14).

## Non-goals

- Editing a project's own `docs/relay/config.toml` from the dashboard.
- Timed changes for writer roles, `reviewer.<provider>` defaults, limits or other settings.
- Notifications when a timed table ends.
- Launching writers from the writer roles.

## Open questions
