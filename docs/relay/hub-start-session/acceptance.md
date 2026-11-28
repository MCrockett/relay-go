# hub-start-session: acceptance ledger

Items that depend on owner actions after merge. They do not block the build review.

| id | check | trigger | status |
|---|---|---|---|
| A1 | Start a real waiting request from the hub on the phone. The session shows in the Remote Control list within 3 minutes, in `.claude/worktrees/<slug>`, and runs `relay take`. | the next dispatched request after merge, with the owner's "start it" | pending |
| A2 | Clear a real session from the hub. The fresh session appears and the old one shows as retired in the digest. | the owner's first "clear" after merge | pending |
