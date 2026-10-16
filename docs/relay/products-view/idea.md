# Products view

## Owner decisions (2026-10-16)

- "I think what I want is relay-go to be able to show a product by product pipeline of where we're
  at, recent analysis of how it's going, next steps, and a coordination layer across all.
  Highlighting where I'm absolutely needed."
- "When I'm at my Mac, the UI is great, but when I'm remote I'd like to just use remote control in
  a session here." At the Mac the owner uses `relay ui`. Remote, they use an UpstreamPartners
  session running `/revenue-day`, which reads the same products file. This feature is the Mac view
  and the hub phone page; it does not replace the session.
- "Maybe a separate questions vs. actions queue for me. Tag actions with which service/portal I
  need to do it in, vs approval only as well."

## Input

UpstreamPartners writes `kpi/products.json` (schema 1). The contract is in
`UpstreamPartners/kpi/README.md`, "Products file", and spec `Docs/relay/path-to-money/spec.md`
R20 to R25 (PR #40). For each product it gives:
- tier: focus, next, decide, longer or parked;
- find, use and pay states with dates;
- analysis lines with the facts they cite;
- owner steps, each a question, approval or action with the portal it happens in, flagged
  `needed`;
- agent work in flight.

The file also has a `needed` list and `flags`.

## What relay-go does

1. Reads the products file from a path set in `config.toml` (`[products] file`). With no path set,
   nothing changes.
2. `relay ui` gets a Products view. Each product shows its pipeline, its analysis lines, its owner
   steps and its agent work, with its relay sessions and features grouped under it by repo
   (`repos`).
3. The coordination layer is "Absolutely needed". It merges the file's `needed` steps with relay
   items waiting on the owner for `focus` and `next` products, in three queues:
   - Questions;
   - Approvals;
   - Actions, grouped by portal.

   The hub phone page leads with the same three queues.
4. A file whose `generated_at` is more than 36 hours old shows as stale. An unknown `schema`
   shows as unreadable, never as a guess.

## Not in this feature

- Writing to the products file or `revenue.yaml`. UpstreamPartners owns them.
- Product logic: tiers, ordering and analysis come from the file as given.
