# hub-start-session

Requested through relay dispatch (qmx0h7k1c) by claude hub session b98b7951-453c-40f9-81ce-6e74b537d43e, 2026-11-28.

## Request

Let the hub start (and retire) sessions on the owner's go. Today every dispatched request waits for the owner to open a session at the Mac; that is the one step not reachable from the phone. (1) Start: a hub-run command (e.g. relay hub act <id> start --relayed or relay session start <request> --relayed) that, only on words the owner typed, opens a new Claude session in the target repo (in a worktree, never the main checkout) with Remote Control on, told to relay take the request. It runs under the owner's normal permission settings, never a bypass mode, and shows in the Remote Control list. (2) Clear: a session cannot /clear another and relay should not kill processes, so 'clear' means start a fresh session and mark the old one retired so dispatch stops routing to it; the owner closes the old one. The spec picks the launch mechanism (Terminal/tmux/other) and how relay learns the new session id. Start with relay-idea; owner merges.

## Owner's words

> yes, dispatch it to relay-go
