# status-json-detail

## Problem

`relay status --json` gives tools too little to act on. A row's `excerpt` holds only the clipped one-line
text (`agentask.excerpt`), so a reader that wants the agent's whole last message has to open the session
transcript itself. `owner_actions` is only a count, so a reader cannot tell which owner actions a feature
had or when they happened. Also, `--json` took 2m24s when run from the UpstreamPartners checkout, which is
too slow for a tool that calls it on demand.

## Who it is for

Tools that read `relay status --json`, first of all hordle's pm view (pm-fixes item P-A2), which wants
to show the owner what an agent asked in full and what the owner already did on each feature.

## What success looks like

- In `--json` output only, `excerpt` also carries `full`: the agent's last words cut at `agentask.full()`.
  The text output does not change.
- In `--json` output only, each row gets `owner_action_items`: a list of `{action, at}`, next to the
  `owner_actions` count, which keeps its meaning (a merge seen on GitHub still counts as one).
- Existing field names and meanings stay as they are, so current readers keep working.
- We know why `--json` took 2m24s from the UpstreamPartners checkout and fix it if the cause is in relay.
- The hub is told the final field names, because hordle's pm reads them.
