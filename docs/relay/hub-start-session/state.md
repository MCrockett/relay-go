---
{
  "authors": {
    "idea": "claude",
    "spec": "claude"
  },
  "branch": "feat/hub-start-session",
  "extra_rounds": {},
  "feature": "hub-start-session",
  "history": {
    "spec": [
      {
        "blocking_ids": [
          "R1-1",
          "R1-2",
          "R1-3",
          "R1-4",
          "R1-5"
        ],
        "head": "3228cff82fe0530c11090112399a40239bd38c87",
        "prior": {},
        "round": 1,
        "verdict": "NO-GO"
      },
      {
        "blocking_ids": [
          "R1-2",
          "R1-3",
          "R2-1"
        ],
        "head": "06c5e47ea7a5fc599dce012c8105e9c6eb5a9e59",
        "prior": {
          "R1-1": "resolved",
          "R1-2": "partial",
          "R1-3": "partial",
          "R1-4": "resolved",
          "R1-5": "resolved"
        },
        "round": 2,
        "verdict": "NO-GO"
      }
    ]
  },
  "owner": {
    "provider": "claude",
    "session": "533a6442-fe90-46df-9213-a7a0e9b4b92f",
    "since": "2026-11-28T09:00:36-05:00",
    "worktree": "relay-go"
  },
  "owner_actions": [
    {
      "action": "owner asked for this through relay dispatch",
      "at": "2026-11-28T09:00:36-05:00",
      "relayed_by": "claude session b98b7951-453c-40f9-81ce-6e74b537d43e"
    }
  ],
  "pr": null,
  "refresh": false,
  "repo": "relay-go",
  "request": {
    "asked_by": null,
    "at": "2026-11-28T09:00:36-05:00",
    "id": "qmx0h7k1c",
    "relayed_by": "claude session b98b7951-453c-40f9-81ce-6e74b537d43e",
    "requested_by": {
      "provider": "claude",
      "session_id": "b98b7951-453c-40f9-81ce-6e74b537d43e"
    }
  },
  "retried": false,
  "review_notes": {},
  "reviewed": {
    "build": null,
    "plan": null,
    "spec": null
  },
  "rounds": {
    "spec": 2
  },
  "skipped": [],
  "small": false,
  "stage": "spec",
  "status": "changes-requested",
  "updated": "2026-11-28T09:06:00-05:00",
  "verdicts": {
    "spec": "NO-GO"
  }
}
---

# Relay state

Written by `relay`. Do not edit by hand.
