---
{
  "reason": "ceiling reached: round 6 of 6",
  "stage": "plan"
}
---

# plan review stopped

Reason: ceiling reached: round 6 of 6

## Findings by round

| id | history |
|---|---|
| R1-1 | R1: raised, R2: unresolved, R3: partial, R4: partial, R5: resolved |
| R1-2 | R1: raised, R2: unresolved, R3: resolved |
| R1-3 | R1: raised, R2: unresolved, R3: resolved |
| R1-4 | R1: raised, R2: unresolved, R3: resolved |
| R3-1 | R3: raised, R4: resolved |
| R5-1 | R5: raised, R6: resolved |
| R6-1 | R6: raised |

## What changed between the last two rounds

~~~
docs/relay/mobile-hub/plan.md | 8 ++++++--
 1 file changed, 6 insertions(+), 2 deletions(-)
~~~

## Your options

Run these in your own terminal, or tell the agent which one you choose: it runs the same command with
`--relayed`, which records that it passed on your decision.

- `relay override go`: accept the stage as it is.
- Narrow or split the stage: edit the stage file, then `relay override reset-rounds` and let the author resubmit.
- Change the reviewer: `relay roles set review.<author> "provider:model, ..."` (add `--until 23:00` for a
  temporary change), then `relay override extra-round`.
- `relay override extra-round`: allow exactly one more round.
