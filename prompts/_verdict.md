## How to answer

You are reviewing work written by a different AI provider. You are read-only: do not modify, create or delete any file. This is review round $round.

Blocking means it would cause wrong behavior, data loss, an endless loop, a security problem, or it defeats the owner's stated goal. Style preferences are notes, not blockers. Decisions the spec records as already made by the owner are not findings.

From round 2 on:
- Earlier blocking findings, by id: $prior_ids. Mark every one of them under `prior` as resolved, partial or unresolved.
- A finding that is still open keeps its original id and stays under `blocking`. Do not renumber it or reword it into a new id. If you now consider an earlier finding nonblocking, mark it resolved and mention it under `notes`.
- A new blocking finding is allowed only if the revision introduced it. Tag it `[introduced-by-revision]`. Anything else new goes under `notes`.

End your final message with exactly one fenced block in this shape, and nothing after it. Write GO or NO-GO, not both. Leave a section empty rather than writing placeholder lines.

```text
verdict: GO
prior:
  - id: R1-2 status: resolved
blocking:
  - id: R2-1 path/to/file.py:42 - what is wrong and why it blocks [introduced-by-revision]
notes:
  - nonblocking observation
```
