---
{
  "authors": {
    "build": "claude",
    "idea": "claude",
    "plan": "claude",
    "spec": "claude"
  },
  "branch": "feat/ci-on-submit",
  "extra_rounds": {
    "spec": 1
  },
  "feature": "ci-on-submit",
  "history": {
    "plan": [
      {
        "blocking_ids": [
          "R1-1"
        ],
        "head": "fb92fb4b9e1ee148d2de624b6e282285214f2ef8",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      }
    ],
    "spec": [
      {
        "blocking_ids": [
          "R1-1"
        ],
        "head": "a7fd9142c1987c2ea4069ee04d3fb9258f2b8960",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      },
      {
        "blocking_ids": [
          "R1-1",
          "R2-1"
        ],
        "head": "8c28e7a5ad109cb8b9de6f9aaa54454023353c19",
        "prior": {
          "R1-1": "partial"
        },
        "round": 2,
        "verdict": "NO-GO"
      }
    ]
  },
  "owner": {
    "provider": "claude",
    "session": "958dee3a-031e-4b49-be5f-09efa15292b1",
    "since": "2026-10-10T07:26:02-04:00",
    "worktree": "relay-go-dev"
  },
  "owner_actions": [
    {
      "action": "owner joined idea",
      "at": "2026-10-10T07:26:02-04:00"
    },
    {
      "action": "owner joined spec",
      "at": "2026-10-10T07:28:49-04:00"
    },
    {
      "action": "override extra-round (spec)",
      "at": "2026-10-10T07:52:00-04:00",
      "relayed_by": "claude session 958dee3a-031e-4b49-be5f-09efa15292b1"
    }
  ],
  "pr": 18,
  "refresh": false,
  "repo": "relay-go",
  "retried": false,
  "review_notes": {},
  "reviewed": {
    "build": {
      "base_ref": "origin/develop",
      "base_sha": "b993faaefc9a87bf9a7c423d32eae2bfbcfc7dde",
      "head": "302131c399cfc2e437f7dc2f073701908733e9e6",
      "inputs": {
        "plan": "1a19aa351b778506dd25a7de9ddb461339a6a87013cc786449bbda15ac0d66b2",
        "spec": "9659d4b080090f98681fff74767d03053d46a066894f98fc49c704fbcf087e32"
      },
      "merge": "34a81d801b90d326258275b2f2e117dc579ead26b7ac5f855167ee8dc9033b12"
    },
    "plan": {
      "inputs": {
        "plan": "1a19aa351b778506dd25a7de9ddb461339a6a87013cc786449bbda15ac0d66b2",
        "spec": "9659d4b080090f98681fff74767d03053d46a066894f98fc49c704fbcf087e32"
      }
    },
    "spec": {
      "inputs": {
        "spec": "9659d4b080090f98681fff74767d03053d46a066894f98fc49c704fbcf087e32"
      }
    }
  },
  "rounds": {
    "build": 1,
    "plan": 2,
    "spec": 3
  },
  "skipped": [],
  "small": false,
  "stage": "build",
  "status": "ready-to-merge",
  "updated": "2026-10-10T11:59:44-04:00",
  "verdicts": {
    "build": "GO",
    "plan": "GO",
    "spec": "GO"
  }
}
---

# Relay state

Written by `relay`. Do not edit by hand.
