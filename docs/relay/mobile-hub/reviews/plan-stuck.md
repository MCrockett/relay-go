---
{
  "reason": "no progress (4 -> 4 blocking), unresolved after a fix attempt: R1-1, R1-2, R1-3, R1-4",
  "stage": "plan"
}
---

# plan review stopped

Reason: no progress (4 -> 4 blocking), unresolved after a fix attempt: R1-1, R1-2, R1-3, R1-4

## Findings by round

| id | history |
|---|---|
| R1-1 | R1: raised, R2: unresolved |
| R1-2 | R1: raised, R2: unresolved |
| R1-3 | R1: raised, R2: unresolved |
| R1-4 | R1: raised, R2: unresolved |

## What changed between the last two rounds

~~~
(no changes)
~~~

## Your options

Run these in your own terminal, or tell the agent which one you choose: it runs the same command with
`--relayed`, which records that it passed on your decision.

- `relay override go`: accept the stage as it is.
- Narrow or split the stage: edit the stage file, then `relay override reset-rounds` and let the author resubmit.
- Change the reviewer: `relay roles set review.<author> "provider:model, ..."` (add `--until 23:00` for a
  temporary change), then `relay override extra-round`.
- `relay override extra-round`: allow exactly one more round.
