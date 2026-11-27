---
{
  "authors": {
    "idea": "claude",
    "plan": "claude",
    "spec": "claude"
  },
  "branch": "feat/dispatch",
  "extra_rounds": {},
  "feature": "dispatch",
  "history": {
    "plan": [
      {
        "blocking_ids": [
          "R1-1",
          "R1-2",
          "R1-3"
        ],
        "head": "519edda3877ae6d1d2a6c4cfb54e8f2bdd0d86e7",
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
        "head": "0fca48741a46a3c441bc3ea3390a67bf1c1b1d2f",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      },
      {
        "blocking_ids": [
          "R1-1",
          "R1-2"
        ],
        "head": "7584660c12c92d59c7927be2518205bc3e96852d",
        "prior": {
          "R1-1": "partial",
          "R1-2": "partial"
        },
        "round": 2,
        "verdict": "NO-GO"
      }
    ]
  },
  "owner": {
    "provider": "claude",
    "session": "533a6442-fe90-46df-9213-a7a0e9b4b92f",
    "since": "2026-11-27T08:07:37-05:00",
    "worktree": "relay-go"
  },
  "owner_actions": [
    {
      "action": "owner joined idea",
      "at": "2026-11-27T08:07:37-05:00"
    },
    {
      "action": "owner joined spec",
      "at": "2026-11-27T08:14:37-05:00"
    }
  ],
  "pr": null,
  "refresh": false,
  "repo": "relay-go",
  "retried": false,
  "review_notes": {},
  "reviewed": {
    "build": null,
    "plan": null,
    "spec": {
      "inputs": {
        "spec": "7079384d3ba4dbb209cd891633c21c4c9d8218c4864ed1f7d12503595c36b172"
      }
    }
  },
  "rounds": {
    "plan": 1,
    "spec": 3
  },
  "skipped": [],
  "small": false,
  "stage": "plan",
  "status": "changes-requested",
  "updated": "2026-11-27T08:21:22-05:00",
  "verdicts": {
    "plan": "NO-GO",
    "spec": "GO"
  }
}
---

# Relay state

Written by `relay`. Do not edit by hand.
