---
{
  "reason": "blocking findings rose (1 -> 2)",
  "stage": "spec"
}
---

# spec review stopped

Reason: blocking findings rose (1 -> 2)

## Findings by round

| id | history |
|---|---|
| R1-1 | R1: raised, R2: partial |
| R2-1 | R2: raised |

## What changed between the last two rounds

~~~
docs/relay/ci-on-submit/spec.md | 28 ++++++++++++++++++++--------
 1 file changed, 20 insertions(+), 8 deletions(-)
~~~

## Your options

Run these in your own terminal, or tell the agent which one you choose: it runs the same command with
`--relayed`, which records that it passed on your decision.

- `relay override go`: accept the stage as it is.
- Narrow or split the stage: edit the stage file, then `relay override reset-rounds` and let the author resubmit.
- Change the reviewer: `relay roles set review.<author> "provider:model, ..."` (add `--until 23:00` for a
  temporary change), then `relay override extra-round`.
- `relay override extra-round`: allow exactly one more round.
