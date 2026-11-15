---
{
  "authors": {
    "build": "claude",
    "idea": "claude",
    "plan": "claude",
    "spec": "claude"
  },
  "branch": "perf/dashboard-scan",
  "extra_rounds": {},
  "feature": "dashboard-scan",
  "history": {
    "plan": [
      {
        "blocking_ids": [
          "R1-1",
          "R1-2",
          "R1-3"
        ],
        "head": "a1c7dddf2ba04a5452cdae1f98ed786efa757921",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      }
    ],
    "spec": [
      {
        "blocking_ids": [
          "R1-1",
          "R1-2"
        ],
        "head": "bb8bf12754593a945602f7cd433cb55a744e0fb5",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      }
    ]
  },
  "owner": {
    "provider": "claude",
    "session": "64dd3837-ab64-43a0-8d14-c7aa66c038c3",
    "since": "2026-11-13T06:40:14-05:00",
    "worktree": "dashboard-scan"
  },
  "owner_actions": [
    {
      "action": "owner joined idea",
      "at": "2026-11-12T17:10:22-05:00"
    },
    {
      "action": "owner joined build",
      "at": "2026-11-15T18:08:54-05:00"
    }
  ],
  "pr": 34,
  "refresh": false,
  "repo": "relay-go",
  "retried": false,
  "review_notes": {
    "spec": "fallback: claude:claude-fable-5-2 because codex:gpt-6-astra out of usage: 5-hour limit used up until Thu 20:18, same provider as the author"
  },
  "reviewed": {
    "build": null,
    "plan": {
      "inputs": {
        "plan": "dde5534c520f504f67eca7b9e4d798e0f461421340ca791a3f304020bd854852",
        "spec": "a3a832fb418e3aebe04a5165012169c624b95c34e18eaec5a8c08c218e2885a7"
      }
    },
    "spec": {
      "inputs": {
        "spec": "a3a832fb418e3aebe04a5165012169c624b95c34e18eaec5a8c08c218e2885a7"
      }
    }
  },
  "rounds": {
    "build": 1,
    "plan": 2,
    "spec": 2
  },
  "skipped": [],
  "small": false,
  "stage": "build",
  "status": "in-review",
  "updated": "2026-11-15T18:08:54-05:00",
  "verdicts": {
    "plan": "GO",
    "spec": "GO"
  }
}
---

# Relay state

Written by `relay`. Do not edit by hand.
