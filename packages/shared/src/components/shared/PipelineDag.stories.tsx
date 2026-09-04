import type { Meta, StoryObj } from "@storybook/react-vite";
import { FIXTURE_GRAPH } from "@shared/lib/graph/fixtures";
import { capLineage, lineage } from "@shared/lib/graph/lineage";
import type { GraphNode } from "@shared/lib/graph/types";
import { PipelineDag } from "./PipelineDag";

/**
 * The fixture is the GOES loop (with its metadata extractor) plus a chained
 * demo pipeline — the two shapes the old five-column view could not tell
 * apart (spec §1).
 */
const meta: Meta<typeof PipelineDag> = {
  component: PipelineDag,
  title: "Shared/PipelineDag",
  args: {
    graph: FIXTURE_GRAPH,
    decorate: (node: GraphNode) => ({
      health:
        node.type === "process"
          ? ("unknown" as const)
          : node.id === "coll:demo-thumbnails"
            ? ("error" as const)
            : ("ok" as const),
      href: "#",
      detail: node.id,
    }),
  },
  argTypes: {
    decorate: { table: { disable: true } },
    highlight: { table: { disable: true } },
  },
};

export default meta;
type Story = StoryObj<typeof PipelineDag>;

/** The whole platform at once — P-4's view. */
export const WholeGraph: Story = {
  args: { size: "full" },
};

/**
 * One product's lineage, the row the Pipelines list draws. The extractor sits
 * in the source connection's column with a dashed arrow INTO the product: it
 * fixes up the items being ingested, it does not derive from them.
 */
export const GoesLineage: Story = {
  args: {
    graph: lineage(FIXTURE_GRAPH, "coll:goes-geocolor"),
    focus: "coll:goes-geocolor",
    size: "compact",
  },
};

/** A chained product: an output that is also the next process's source. */
export const ChainedProduct: Story = {
  args: {
    graph: lineage(FIXTURE_GRAPH, "coll:demo-downscaled"),
    focus: "coll:demo-downscaled",
    size: "compact",
  },
};

/** Clicking a node in the Graph view fades everything outside its lineage. */
export const Highlighted: Story = {
  args: {
    size: "full",
    highlight: new Set(
      lineage(FIXTURE_GRAPH, "coll:goes-geocolor").nodes.map((n) => n.id),
    ),
    focus: "coll:goes-geocolor",
  },
};

/**
 * A back edge — deliver → re-ingest through the same connection. The M5-D
 * write gate deliberately does not refuse those, so the layout has to survive
 * them: the loop is dropped from ranking and drawn dashed in the warning
 * colour.
 */
export const WithALoop: Story = {
  args: {
    size: "full",
    graph: {
      nodes: FIXTURE_GRAPH.nodes,
      edges: [
        ...FIXTURE_GRAPH.edges,
        {
          from: "conn:goes-geocolor-dest",
          to: "coll:goes-abi-mcmipc",
          kind: "ingest" as const,
          id: "assoc-loop",
        },
      ],
    },
  },
};

/** A wide fan-out, capped so one row cannot swallow the list (spec §8). */
export const CappedRow: Story = {
  args: {
    size: "compact",
    focus: "coll:fan",
    graph: capLineage(
      lineage(
        {
          nodes: [
            {
              id: "coll:fan",
              type: "collection",
              label: "fan",
              group_id: null,
              meta: {},
            },
            ...Array.from({ length: 12 }, (_, i) => ({
              id: `proc:p${i}`,
              type: "process" as const,
              label: `consumer-${i}`,
              group_id: null,
              meta: {},
            })),
          ],
          edges: Array.from({ length: 12 }, (_, i) => ({
            from: "coll:fan",
            to: `proc:p${i}`,
            kind: "process_source" as const,
            id: `s${i}`,
          })),
        },
        "coll:fan",
      ),
      4,
    ),
  },
};
