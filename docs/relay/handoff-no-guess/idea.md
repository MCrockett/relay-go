# handoff-no-guess

Requested through relay dispatch (qmx0id8ov) by claude hub session b98b7951-453c-40f9-81ce-6e74b537d43e, 2026-11-28.

## Request

Drop the guessed-context handoff rule. relay-idea, relay-spec, relay-plan and relay-build SKILL.md each say 'Around 60% of your context window: relay handoff ...'. Sessions cannot see their context use, so they guess; Opus sessions hand off at a claimed 15% with little more than the spec or plan loaded (one guessed 60% when it was 19%). Replace with: hand off only after the session has auto-compacted, when the system's low-context warning appears, or at a natural stage boundary the owner asks for; never on a guessed percentage. Keep the handoff mechanics (relay handoff, --commit, stop all work in the repo) unchanged. Update both copies (relay-go/skills and the installed ~/.claude/skills via the normal install path) and any docs/reference.md mention. Small change (--small). One open PR per repo: queue behind take-base-branch (qmx0hasm1). Owner merges.

## Owner's words

> Go for 1
