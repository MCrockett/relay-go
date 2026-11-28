---
{
  "reason": "ceiling reached: round 4 of 4",
  "stage": "spec"
}
---

# spec review stopped

Reason: ceiling reached: round 4 of 4

## Findings by round

| id | history |
|---|---|
| R1-1 | R1: raised, R2: resolved |
| R1-2 | R1: raised, R2: partial, R3: resolved |
| R1-3 | R1: raised, R2: partial, R3: resolved |
| R1-4 | R1: raised, R2: resolved |
| R1-5 | R1: raised, R2: resolved |
| R2-1 | R2: raised, R3: resolved |
| R3-1 | R3: raised, R4: partial |

## What changed between the last two rounds

~~~
docs/relay/hub-start-session/spec.md | 10 +++++-----
 1 file changed, 5 insertions(+), 5 deletions(-)
~~~

## Your options

Run these in your own terminal, or tell the agent which one you choose: it runs the same command with
`--relayed`, which records that it passed on your decision.

- `relay override go`: accept the stage as it is.
- Narrow or split the stage: edit the stage file, then `relay override reset-rounds` and let the author resubmit.
- Change the reviewer: `relay roles set review.<author> "provider:model, ..."` (add `--until 23:00` for a
  temporary change), then `relay override extra-round`.
- `relay override extra-round`: allow exactly one more round.
