/**
 * A fixture graph shaped like the platform's own two standing pipelines, for
 * unit tests and the `PipelineDag` Storybook story (spec §6).
 *
 * It is the smallest graph that exercises every feature the two views have to
 * get right:
 *   - the GOES loop's **extractor**, which points INTO `goes-abi-mcmipc`
 *     rather than deriving from it (GOES spec §15);
 *   - a **chained** product — `demo-downscaled` is both an output and the next
 *     process's source (spec §1.2), the case a column layout flattens;
 *   - two pipelines that share no node, so a lineage row must not merge them;
 *   - a **delivery** tail, so `deliver` edges are laid out too.
 *
 * Node ids follow the platform's `coll:` / `proc:` / `conn:` convention
 * (`app/src/lib/graph/edges.ts`). Real connection and process ids are UUIDs;
 * readable ones are used here so a failing assertion names what broke.
 */
import type { Graph, GraphNode, GraphNodeType } from "./types";

function node(
  id: string,
  type: GraphNodeType,
  label: string,
  meta: Record<string, unknown> = {},
): GraphNode {
  return { id, type, label, group_id: "earth-observation", meta };
}

export const FIXTURE_GRAPH: Graph = {
  nodes: [
    // --- GOES: NODD → COG → deliver, with a metadata extractor ---
    node("conn:goes-nodd", "connection", "NOAA NODD goes19", {
      protocol: "s3",
      status: "ok",
    }),
    node("proc:goes-abi-metadata", "process", "goes-abi-metadata", {
      enabled: true,
      deployed: true,
    }),
    node("coll:goes-abi-mcmipc", "collection", "goes-abi-mcmipc"),
    node("proc:goes-geocolor", "process", "goes-geocolor", {
      enabled: true,
      deployed: true,
    }),
    node("coll:goes-geocolor", "collection", "goes-geocolor"),
    node("conn:goes-geocolor-dest", "connection", "geocolor-dest", {
      protocol: "s3",
      status: "ok",
    }),

    // --- demo: a three-hop chain through two derived products ---
    node("conn:demo-store", "connection", "demo-store", {
      protocol: "s3",
      status: "ok",
    }),
    node("coll:demo-scenes", "collection", "demo-scenes"),
    node("proc:demo-downscale", "process", "demo-downscale", {
      enabled: true,
      deployed: true,
    }),
    node("coll:demo-downscaled", "collection", "demo-downscaled"),
    node("proc:demo-thumbnails", "process", "demo-thumbnails", {
      enabled: true,
      deployed: true,
    }),
    node("coll:demo-thumbnails", "collection", "demo-thumbnails"),
  ],
  edges: [
    {
      from: "conn:goes-nodd",
      to: "coll:goes-abi-mcmipc",
      kind: "ingest",
      id: "assoc-goes-ingest",
    },
    {
      from: "proc:goes-abi-metadata",
      to: "coll:goes-abi-mcmipc",
      kind: "extractor",
      // Same association row as its `ingest` twin (storage.ts).
      id: "assoc-goes-ingest",
    },
    {
      from: "coll:goes-abi-mcmipc",
      to: "proc:goes-geocolor",
      kind: "process_source",
      id: "src-geocolor",
    },
    {
      from: "proc:goes-geocolor",
      to: "coll:goes-geocolor",
      kind: "process_output",
      id: "out-geocolor",
    },
    {
      from: "coll:goes-geocolor",
      to: "conn:goes-geocolor-dest",
      kind: "deliver",
      id: "assoc-geocolor-deliver",
    },

    {
      from: "conn:demo-store",
      to: "coll:demo-scenes",
      kind: "ingest",
      id: "assoc-demo-ingest",
    },
    {
      from: "coll:demo-scenes",
      to: "proc:demo-downscale",
      kind: "process_source",
      id: "src-downscale",
    },
    {
      from: "proc:demo-downscale",
      to: "coll:demo-downscaled",
      kind: "process_output",
      id: "out-downscale",
    },
    {
      from: "coll:demo-downscaled",
      to: "proc:demo-thumbnails",
      kind: "process_source",
      id: "src-thumbnails",
    },
    {
      from: "proc:demo-thumbnails",
      to: "coll:demo-thumbnails",
      kind: "process_output",
      id: "out-thumbnails",
    },
  ],
};
