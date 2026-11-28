# data-paths-freshness

Requested through relay dispatch (qmwzsp3b4) by claude hub session b98b7951-453c-40f9-81ce-6e74b537d43e, 2026-11-27, for claude session a16be7ff-3d08-4908-8b0c-bdde1907ab41 in UpstreamPartners.

## Request

Stale GOs from hub data commits. Asked by the UpstreamPartners fact-command build session, which reports the owner asked for it there on 2026-11-27. relaylib/freshness.py merge_digest hashes the whole merged tree, so every data commit the UpstreamPartners hub pushes to main (hub notes, asks, KPI snapshot, mkt save, soon scripts/fact) makes a waiting build GO stale. Change: a repo may declare data paths in its relay config; merge_digest leaves them out unless the PR's own diff (merge-base to head) touches one; a PR that touches a data path keeps the whole tree in the digest. UpstreamPartners data paths (set by that repo's own PR, not this one): portfolio/releases/status.yaml, portfolio/revenue.yaml, portfolio/hub-notes.csv, portfolio/asks.csv, kpi/snapshots.csv, kpi/marketing.csv, kpi/social.csv, kpi/goals.yaml, marketing/packs/. Tests: a GO stays fresh after a base commit to a declared path; goes stale when the PR touches that path or base changes an undeclared path. The spec should also say how data paths interact with D1 (the config file is a protected path) and that code paths can never be declared data. Owner merges.

## Owner's words

> Dispatched tasks like this should go through relay automatically
