# Pipeline graph — per-product lineage lines + a full graph view — design

**Date:** 2026-09-04
**Status:** **draft for lead review.** Written from the lead's 2026-09-04
feedback (`FEEDBACK.md` items 1–3, screenshot
`assets/2026-09-04-pipeline-graph-before.png`) and three answers given in
the same session (§2). The decisions in §9 were taken by the agent without
a lead answer. The P queue in `TODO.md` is copied from §10 and must not
start past P-1 before this spec is approved (P-1 is a bounded bug fix and
may start now).
**Scope source:** M5-E (`/api/monitoring/graph`, `lib/graph/*`), M5-F
(`/graph` page, `LineagePanel`), UI-7 (`LineageStrip`), ADR 0017 (lineage
language, theme tokens), GOES spec §6.6 / §15 (the display-only
`extractor` edge).

## 1. Problem

The `/graph` page renders the pipeline as five columns (source
connections → source products → processes → derived products →
destinations) with ONE arrow between adjacent columns. The component's
own design note says why: five columns cannot align arbitrary N:M wiring
without drawing edges that are wrong, so the exact wiring lives in the
Flows list underneath as text. That was the right call for M5-F's scope;
with the GOES loop standing it is no longer enough:

1. **The extractor is invisible.** `goes-abi-metadata` sits in the
   Processes column next to `goes-geocolor` with no indication that it
   fixes up items INTO `goes-abi-mcmipc` rather than deriving from it.
2. **Pipelines are indistinguishable.** `demo-scenes → demo-downscale →
   demo-thumbnails` and `goes-nodd → goes-abi-mcmipc → goes-geocolor →
   goes-geocolor-dest` share the same columns; nothing says which product
   feeds which process. Products also CHAIN — a derived product is the
   source of the next process — and a column layout flattens that.
3. **"Not wired" lists ghosts.** Eight `e2e-goes-*` collections that no
   longer exist in pgstac appear as orphans. Cause (verified 2026-09-04):
   `loadGraph`'s collection-node query unions
   `process_sources.collection_id` and `process_outputs.collection_id`
   without excluding soft-deleted processes, while `loadGraphEdges` does
   exclude them — so a soft-deleted process's collections become nodes
   with degree 0. (`NOAA NODD goes19` in the same list is real: a live
   connection whose only association was deleted that day.)

## 2. Lead answers (2026-09-04)

- **Both views**: one line per pipeline AND a full graph. "Sometimes a
  derived product of one process is the source product of another. A full
  graph view would be extremely helpful, but it might not be the best way
  to find the specific product you're interested in, which is where the
  one line per pipeline view helps."
- **A line is one product's full lineage**: a searchable list, one row per
  collection, showing its complete upstream chain back to the source
  connection and its downstream chain to destinations. A chained product
  appears in its own row and inside its neighbours' rows.
- **Layout is computed in-house** (layered layout, SVG edges) — no
  dependency. `elkjs`/`dagre` and React Flow were offered and declined.

## 3. Goals / non-goals

Goals:
- Draw the REAL edges — every kind the graph API returns (`ingest`,
  `deliver`, `process_source`, `process_output`, `extractor`) — between
  the specific nodes they join, in both views.
- Make the extractor's role legible: it points AT a product, not out of
  one.
- Let an operator find a product by name and read its whole chain in one
  row.
- Keep the graph API and the group scoping unchanged: this is a rendering
  change over data the platform already serves.
- Stop rendering ghost nodes.

Non-goals:
- Interactive editing (drag to rewire). Wiring is edited where it is
  owned: the Data flow tab, the process page.
- Force-directed / physics layouts.
- Per-item lineage (which items came from which run). Runs stay on the
  process page.
- Replacing `LineageStrip`'s `mini`/`medium` sizes, which other surfaces
  use as a glyph.

## 4. Data model — nothing new on the wire

`GET /api/monitoring/graph` already returns typed nodes and every edge.
Both views are pure client-side derivations of that payload, which keeps
the M5-F invariant that the picture and the write gate (`lib/graph/edges`)
cannot disagree.

Two pure functions, new, in the SHARED package
(`packages/shared/src/lib/graph/`), unit-tested without React:

```ts
// The subgraph an operator means by "this product's pipeline".
lineage(graph, nodeId): { nodes: GraphNode[]; edges: GraphEdge[]; focus: string }
// upstream = transitive closure over incoming edges; downstream over
// outgoing. An `extractor` edge is followed UPSTREAM only (the extractor
// is part of what produces the product) and never downstream.

// Coordinates for an SVG rendering of any graph the API can return.
layeredLayout(graph, opts): { nodes: PlacedNode[]; edges: PlacedEdge[]; width; height }
```

`layeredLayout` is a small Sugiyama: (1) rank = longest path from any
source node (a node with no incoming non-extractor edge); an extractor
process is ranked one column LEFT of the product it extracts into, i.e.
alongside the ingest connection; (2) within a rank, order by the
barycenter of neighbours in the previous rank, two sweeps; (3) x from rank,
y from order, edges as orthogonal paths with a fixed elbow. The write gate
guarantees the owned edges are acyclic, but the function is total: a
back-edge found by DFS is dropped from ranking and still drawn, dashed.
Deterministic output for a given input is a tested property.

## 5. The two views

`/graph` gets a segmented control **Pipelines | Graph** (URL `?view=`,
default `pipelines`); the Flows list stays under both as the text truth.

### 5.1 Pipelines — one row per product

A searchable list (client-side filter over label), one row per
collection node, sorted by label. Each row renders
`layeredLayout(lineage(graph, coll))` as a compact SVG strip: the focus
product outlined, its ingest connection(s) left, processes and derived
products right, destinations at the end, the extractor drawn above its
product with an arrow DOWN into it and the badge `extractor`. Every node
is a link (collection page, process page, connections page); edge kind on
hover. Rows are collapsed to one line of height for short chains; a
branching lineage grows the row.

The collection page's **`LineagePanel`** (today: one hop each way as two
lists) renders the SAME row for its collection, keeping its per-flow
30-day strips beneath. One component, two surfaces, no drift.

### 5.2 Graph — the whole platform

`layeredLayout(graph)` over the group-scoped graph, full width, with the
kind colour bar and health dot every node already carries (`KIND_COLOR_VAR`,
alert join unchanged). Clicking a node highlights `lineage(graph, node)`
(others fade) and shows an "Open" link; clicking empty space clears.
Orphans (degree 0) render in a "Not wired" row under the graph as today —
after P-1 they are real.

Both views are SVG in a horizontally scrolling container (the page body
never scrolls sideways), theme-aware through the existing tokens, and
degrade to the Flows list when the graph is empty.

## 6. Component placement

- `packages/shared/src/lib/graph/{lineage,layout}.ts` + tests — pure.
- `packages/shared/src/components/shared/PipelineDag.tsx` — the SVG
  renderer over `layeredLayout` output (nodes as the existing `NodeChip`
  visuals, edges as paths). Storybook story with the GOES + demo fixture.
- `app/src/components/monitoring/PipelineGraph.tsx` — the view switch,
  search, and both views; `LineageStrip size="full"` is no longer used
  here (the `mini`/`medium` sizes stay for the collection cards).
- `app/src/components/collections/LineagePanel.tsx` — swaps its two lists
  for a `PipelineDag` of the collection's lineage.

## 7. Testing

- Unit (shared): `lineage` follows extractor edges upstream only;
  `layeredLayout` puts a chain in strictly increasing ranks, an extractor
  left of its product, is deterministic, and survives a synthetic cycle.
- Component: the graph page renders one `<path>` per edge and one row per
  collection; search filters rows; the e2e-ghost fixture yields no
  orphan.
- API/storage (P-1): `loadGraph` excludes collections referenced only by
  soft-deleted processes — a test with one live and one deleted process.
- e2e: `/graph` shows the GOES chain with an `extractor` badge on the
  Pipelines view and ≥ 4 edges on the Graph view against the seeded demo.

## 8. Risks

- **Layout quality on tangled graphs.** Barycenter ordering with two
  sweeps is good for tens of nodes, not thousands; the platform's graphs
  are tens. If a group ever wires hundreds of processes, revisit the
  declined `elkjs` (§2) — the renderer takes placed nodes, so the layout
  function is the only swap.
- **Row height in the Pipelines view** for a product with many
  downstreams. Cap the row at N downstream branches with "+k more" and
  let the Graph view carry the rest.

## 9. Agent-taken decisions (confirm or overturn)

1. Default view is Pipelines, not Graph — "find the product" is the
   stated common case.
2. `LineagePanel` on the collection page is REPLACED by the lineage row
   (with its strips kept), rather than gaining a third widget.
3. An extractor is ranked beside the ingest connection (left of its
   product) and drawn pointing INTO the product. The alternative — a
   badge on the product, no node — loses the process's health dot and
   link.
4. Orphans stay a separate row rather than floating in the SVG.
5. No new API. If the client-side closure ever proves slow it moves
   server-side behind the same shape.

## 10. Slices (copied to `TODO.md` as the P queue)

- **P-1 · Ghost nodes.** `loadGraph`'s collection union joins
  `processes` and requires `deleted_at IS NULL` for the `process_sources`
  and `process_outputs` branches (edges already do); storage test; log
  I-104 as closed. Bounded — may start before approval.
- **P-2 · Lineage + layout (shared, pure).** `lineage`, `layeredLayout`,
  tests, a fixture graph (GOES + demo). Depends on nothing.
- **P-3 · Pipelines view.** `PipelineDag`, the row list with search, the
  view switch, `LineagePanel` swap, Storybook story, e2e. Depends on P-2.
- **P-4 · Graph view.** Full-graph rendering, click-to-highlight, orphan
  row, e2e. Depends on P-2; independent of P-3.
