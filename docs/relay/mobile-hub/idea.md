# mobile-hub

## Problem
The owner is often away from the desk and wants to keep an eye on their projects from a phone. relay's dashboard (`relay ui`) only works on the machine that runs it, so away from the desk there is no way to see which sessions wait on the owner, which PRs are ready to merge, or whether CI is stuck.

## Who it is for
The owner, on a phone, between other things. Reading first; acting (merge, answer a session) is a later step.

## Shapes the owner named
- A claude.ai artifact that works on mobile and shows relay's state: waiting sessions, features by stage, PRs, CI.
- A "hub" Claude session the owner reaches through remote control (which they already use daily) that answers "what needs me?" by running relay's own reads on demand.

## What success looks like
From a phone, in under a minute, the owner can see the same "waiting on you" list the dashboard shows, with enough context to decide what to do next, without exposing credentials or machine details off the workstation.

## Open for the spec
How an artifact gets live data when the dashboard is local only, versus a hub session that reads on demand; whether the two combine (the hub session publishes or refreshes the artifact).
