# hub-start-session

Requested through relay dispatch (qmx0h7k1c) by claude hub session b98b7951-453c-40f9-81ce-6e74b537d43e, 2026-11-28.

## Request

Let the hub start (and retire) sessions on the owner's go. Today every dispatched request waits for the owner to open a session at the Mac; that is the one step not reachable from the phone. (1) Start: a hub-run command (e.g. relay hub act <id> start --relayed or relay session start <request> --relayed) that, only on words the owner typed, opens a new Claude session in the target repo (in a worktree, never the main checkout) with Remote Control on, told to relay take the request. It runs under the owner's normal permission settings, never a bypass mode, and shows in the Remote Control list. (2) Clear: a session cannot /clear another and relay should not kill processes, so 'clear' means start a fresh session and mark the old one retired so dispatch stops routing to it; the owner closes the old one. The spec picks the launch mechanism (Terminal/tmux/other) and how relay learns the new session id. Start with relay-idea; owner merges.

## Owner's words

> yes, dispatch it to relay-go

## Problem

A dispatched request sits in `relay dispatch` until the owner opens a Claude session in the target repo at the Mac. Every other step (dispatch, review, merge) already works from the phone through the hub. Starting the session is the one gap.

## Who it is for

The owner, driving relay from the phone through the hub session over Remote Control.

## What success looks like

- The owner tells the hub "start it" (or similar) and, within a minute, a new Claude session appears in their Remote Control list, working in a worktree of the target repo, already running `relay take` on the request.
- The new session runs under the owner's normal permission settings. relay never passes a bypass mode and never starts a session without the owner's typed words.
- "Clear" for a session means relay starts a fresh one and marks the old one retired, so dispatch stops routing to it. relay never kills or types into the old one; the owner closes it.
- If the launch fails, the hub gets a plain reason, and the request stays waiting as it is today.
