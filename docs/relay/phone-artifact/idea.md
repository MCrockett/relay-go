# phone-artifact

## Problem
The relay hub (mobile-hub, PR #19) answers "what needs me?" in a remote-control chat. A chat reply is a wall of text on a phone: items scroll away, there is no way to glance at the list again without asking, and long excerpts wrap badly. The owner queued a phone page as an add-on to the hub.

## Who it is for
The owner, on a phone, between other things, already talking to the hub session.

## Shape
The hub session publishes the digest it just printed as a private claude.ai artifact that reads well on a phone: one card per numbered item (repo, what it waits on, how long, the actions it allows), with the same digest id and numbers, so "merge 3" in the chat still means the item on the page. The page is a snapshot, refreshed only when the owner asks the hub; there is no polling and no push (owner answer in mobile-hub). Acting stays in the chat, through `relay hub note` and `relay hub act`.

## What success looks like
The owner says "show me" to the hub and gets one link that opens a readable page on the phone in a few seconds, matching the digest the chat just printed, with no machine paths, tokens or credentials on it.

## Owner answers (2026-10-11)
- One link, updated in place: the hub republishes the same artifact on each refresh.
- Read-only: the page shows the digest; the owner still types actions in the hub chat.

## Open for the spec
- Who renders the page: relay itself (`relay hub --html` writes a stdlib-built file the session publishes) or the session writing it from `relay hub --json`. relay rendering keeps it tested and the same every time.
- What is left out or redacted for a page that lives on claude.ai: local paths, excerpts that quote paths, session ids.
