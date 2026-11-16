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
    "build": [
      {
        "blocking_ids": [
          "R1-1"
        ],
        "head": "81fe9cfb4d933e2d242cc5c2c9256294c4202df4",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      },
      {
        "blocking_ids": [
          "R1-1"
        ],
        "head": "dda446f3fbd12135e65742c262db4f79458d34bf",
        "prior": {
          "R1-1": "partial"
        },
        "round": 2,
        "verdict": "NO-GO"
      },
      {
        "blocking_ids": [
          "R1-1"
        ],
        "head": "825ad29306867cc587b3944d57d00c3d72b2a354",
        "prior": {
          "R1-1": "partial"
        },
        "round": 3,
        "verdict": "NO-GO"
      }
    ],
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
    },
    {
      "action": "owner joined plan",
      "at": "2026-11-15T18:28:21-05:00"
    }
  ],
  "pr": 34,
  "refresh": false,
  "repo": "relay-go",
  "retried": false,
  "review_notes": {},
  "reviewed": {
    "build": {
      "base_ref": "origin/develop",
      "base_sha": "9e560e52e65a33bc9736a4e7d6c461d8690c11cb",
      "head": "40a1460c3b974f2fa1ef436244ecb519aef65a0f",
      "inputs": {
        "plan": "df4b9e3c82ad04506dd00b484d271a9d8a2fc39278f7ea5c1a573c1f638d5b52",
        "spec": "c47b0eaef8ce3a2c2021d8bc2bc688a6c518b7b1cd05b74ae7f0be914c06d062"
      },
      "merge": "eb3ba2a3bb364b205b065e104d288dd4fc2963876468ba8b4ab0e58121717ab6"
    },
    "plan": {
      "inputs": {
        "plan": "df4b9e3c82ad04506dd00b484d271a9d8a2fc39278f7ea5c1a573c1f638d5b52",
        "spec": "c47b0eaef8ce3a2c2021d8bc2bc688a6c518b7b1cd05b74ae7f0be914c06d062"
      }
    },
    "spec": {
      "inputs": {
        "spec": "c47b0eaef8ce3a2c2021d8bc2bc688a6c518b7b1cd05b74ae7f0be914c06d062"
      }
    }
  },
  "rounds": {
    "build": 4,
    "plan": 3,
    "spec": 2
  },
  "skipped": [],
  "small": false,
  "stage": "build",
  "status": "ready-to-merge",
  "updated": "2026-11-15T19:10:42-05:00",
  "verdicts": {
    "build": "GO",
    "plan": "GO",
    "spec": "GO"
  }
}
---

# Relay state

Written by `relay`. Do not edit by hand.
