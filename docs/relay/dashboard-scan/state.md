---
{
  "authors": {
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
    "session": "3af7cd77-bc40-41e4-bd64-68563935201e",
    "since": "2026-11-12T20:54:33-05:00",
    "worktree": "dashboard-scan"
  },
  "owner_actions": [
    {
      "action": "owner joined idea",
      "at": "2026-11-12T17:10:22-05:00"
    }
  ],
  "pr": null,
  "refresh": false,
  "repo": "relay-go",
  "retried": false,
  "review_notes": {
    "plan": "fallback: claude:claude-fable-5-2 because codex:gpt-6-astra out of usage: 5-hour limit used up until Thu 20:18, same provider as the author",
    "spec": "fallback: claude:claude-fable-5-2 because codex:gpt-6-astra out of usage: 5-hour limit used up until Thu 20:18, same provider as the author"
  },
  "reviewed": {
    "build": null,
    "plan": {
      "inputs": {
        "plan": "d6bc9da3ff4e149d2fd10862c396254e97033353cb096b41389496bc9112b19d",
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
    "plan": 2,
    "spec": 2
  },
  "skipped": [],
  "small": false,
  "stage": "build",
  "status": "drafting",
  "updated": "2026-11-12T21:29:48-05:00",
  "verdicts": {
    "plan": "GO",
    "spec": "GO"
  }
}
---

# Relay state

Written by `relay`. Do not edit by hand.
