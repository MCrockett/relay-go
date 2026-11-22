---
{
  "authors": {
    "build": "claude"
  },
  "branch": "fix/usage-read-once",
  "extra_rounds": {},
  "feature": "usage-read-once",
  "history": {
    "build": [
      {
        "blocking_ids": [
          "R1-1",
          "R1-2",
          "R1-3",
          "R1-4"
        ],
        "head": "5a4d8c4c8fcebae5fb6e344b52b370d5a8ae02d2",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      },
      {
        "blocking_ids": [
          "R2-1"
        ],
        "head": "609661d3f24d95659017bf27c4b8a7e1230ee829",
        "prior": {
          "R1-1": "resolved",
          "R1-2": "resolved",
          "R1-3": "resolved",
          "R1-4": "resolved"
        },
        "round": 2,
        "verdict": "NO-GO"
      }
    ]
  },
  "owner": {
    "provider": "claude",
    "session": "64dd3837-ab64-43a0-8d14-c7aa66c038c3",
    "since": "2026-11-21T19:37:42-05:00",
    "worktree": "usage-read-once"
  },
  "owner_actions": [
    {
      "action": "owner joined idea",
      "at": "2026-11-21T19:37:42-05:00"
    },
    {
      "action": "owner joined build",
      "at": "2026-11-21T19:54:40-05:00"
    }
  ],
  "pr": 40,
  "refresh": false,
  "repo": "relay-go",
  "retried": false,
  "review_notes": {},
  "reviewed": {
    "build": {
      "base_ref": "origin/develop",
      "base_sha": "8196431fce0883a4f775ca80d757d836c09974a1",
      "head": "0f6292a848ee04f0650681401a83f97454ab739b",
      "inputs": {
        "idea": "12bc192b7be8fa7d4835c44e1cb2910a1fd0ef5f62b872c0c7f583bfe914a42d"
      },
      "merge": "bfc7a45e0c0c3309819ac726d0c51c8fade8a6040fcb43542ab17054a55c3e20"
    },
    "plan": null,
    "spec": null
  },
  "rounds": {
    "build": 3
  },
  "skipped": [
    "spec",
    "plan"
  ],
  "small": true,
  "stage": "build",
  "status": "ready-to-merge",
  "updated": "2026-11-21T23:18:43-05:00",
  "verdicts": {
    "build": "GO"
  }
}
---

# Relay state

Written by `relay`. Do not edit by hand.
