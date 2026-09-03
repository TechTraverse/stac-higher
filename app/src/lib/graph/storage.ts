/**
 * Load the flow graph's edges from the platform tables (M5-D).
 *
 * Server-only (it imports the pg client). Kept apart from `edges.ts` so the
 * pure graph logic stays unit-testable without a database — and so M5-E's
 * `/api/monitoring/graph` can reuse the exact same loader rather than writing
 * a second, subtly different query.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import {
  collectionNode,
  connectionNode,
  processNode,
  type GraphEdge,
  type GraphNodeType,
} from "./edges";

interface AssociationEdgeRow {
  id: string;
  collection_id: string;
  connection_id: string;
  direction: "ingest" | "deliver";
}

interface ProcessEdgeRow {
  id: string;
  process_id: string;
  collection_id: string;
}

interface ExtractorEdgeRow {
  id: string;
  collection_id: string;
  process_id: string;
}

/**
 * Every live edge in the platform graph.
 *
 * Only ENABLED, non-deleted rows are edges: a disabled source carries no
 * items, so treating it as an edge would refuse a wiring that cannot actually
 * loop. Re-enabling one is itself a checked write (the same reason the spec
 * lists re-enable as a hook point).
 */
export async function loadGraphEdges(): Promise<GraphEdge[]> {
  await runMigrations();

  const [associations, sources, outputs, extractors] = await Promise.all([
    query<AssociationEdgeRow>(
      `SELECT cc.id, cc.collection_id, cc.connection_id, cc.direction
         FROM stac_higher.collection_connections cc
         JOIN stac_higher.connections c ON c.id = cc.connection_id
        WHERE cc.enabled AND c.enabled
          AND cc.deleted_at IS NULL AND c.deleted_at IS NULL`,
    ),
    query<ProcessEdgeRow>(
      `SELECT s.id, s.process_id, s.collection_id
         FROM stac_higher.process_sources s
         JOIN stac_higher.processes p ON p.id = s.process_id
        WHERE s.enabled AND p.enabled AND p.deleted_at IS NULL`,
    ),
    query<ProcessEdgeRow>(
      `SELECT o.id, o.process_id, o.collection_id
         FROM stac_higher.process_outputs o
         JOIN stac_higher.processes p ON p.id = o.process_id
        WHERE p.enabled AND p.deleted_at IS NULL`,
    ),
    query<ExtractorEdgeRow>(
      `SELECT cc.id, cc.collection_id,
              cc.config->'metadata'->'extractor'->>'process_id' AS process_id
         FROM stac_higher.collection_connections cc
         JOIN stac_higher.connections c ON c.id = cc.connection_id
         JOIN stac_higher.processes p
           ON p.id::text = cc.config->'metadata'->'extractor'->>'process_id'
        WHERE cc.direction = 'ingest' AND cc.enabled AND c.enabled
          AND cc.deleted_at IS NULL AND c.deleted_at IS NULL
          AND p.enabled AND p.deleted_at IS NULL
          AND cc.config->'metadata'->>'strategy' = 'extractor'`,
    ),
  ]);

  const edges: GraphEdge[] = [];
  for (const row of associations.rows) {
    // Direction is the arrow: ingest brings items INTO a collection, delivery
    // sends them OUT to a connection.
    edges.push(
      row.direction === "ingest"
        ? {
            from: connectionNode(row.connection_id),
            to: collectionNode(row.collection_id),
            kind: "ingest",
            id: row.id,
          }
        : {
            from: collectionNode(row.collection_id),
            to: connectionNode(row.connection_id),
            kind: "deliver",
            id: row.id,
          },
    );
  }
  for (const row of sources.rows) {
    edges.push({
      from: collectionNode(row.collection_id),
      to: processNode(row.process_id),
      kind: "process_source",
      id: row.id,
    });
  }
  for (const row of outputs.rows) {
    edges.push({
      from: processNode(row.process_id),
      to: collectionNode(row.collection_id),
      kind: "process_output",
      id: row.id,
    });
  }
  for (const row of extractors.rows) {
    // GOES spec §15: drawn from the extractor PROCESS to the collection it
    // fixes items for, so the process appears in the picture and the
    // collection's lineage. Same association id as its `ingest` twin.
    edges.push({
      from: processNode(row.process_id),
      to: collectionNode(row.collection_id),
      kind: "extractor",
      id: row.id,
    });
  }
  return edges;
}

// ---------------------------------------------------------------------------
// The full graph for /api/monitoring/graph (M5-E)
// ---------------------------------------------------------------------------

export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  /** Owning group, or null for an unowned collection (ADR 0003: public). */
  group_id: string | null;
  /** Type-specific badges the UI shows: connection status, archived, … */
  meta: Record<string, unknown>;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface ConnectionNodeRow {
  id: string;
  name: string;
  protocol: string;
  status: string;
  group_id: string;
}

interface ProcessNodeRow {
  id: string;
  name: string;
  group_id: string;
  enabled: boolean;
  current_revision: string | null;
}

interface CollectionNodeRow {
  collection_id: string;
  group_id: string | null;
  archived: boolean;
  serving_enabled: boolean;
}

/**
 * The member+-scoped graph.
 *
 * `groups: null` = admin (everything). Otherwise a node is visible when the
 * caller owns it, and a collection with NO settings row is visible to all —
 * the ADR 0003 unowned-is-public rule, the same one `/api/monitoring/flows`
 * applies.
 *
 * Edges are filtered to those whose BOTH endpoints survived: a half-edge
 * would draw an arrow to a node the caller cannot see, leaking the existence
 * of another group's wiring through the picture.
 */
export async function loadGraph(groups: string[] | null): Promise<Graph> {
  await runMigrations();

  const [connections, processes, collections, edges] = await Promise.all([
    query<ConnectionNodeRow>(
      `SELECT id, name, protocol, status, group_id
         FROM stac_higher.connections
        WHERE deleted_at IS NULL`,
    ),
    query<ProcessNodeRow>(
      `SELECT id, name, group_id, enabled, current_revision
         FROM stac_higher.processes
        WHERE deleted_at IS NULL`,
    ),
    // Collections are named by the edges that touch them: pgstac owns the
    // collection list, and a graph of every collection in the catalog would
    // be noise rather than a pipeline view.
    query<CollectionNodeRow>(
      `SELECT DISTINCT c.collection_id,
              s.group_id, coalesce(s.archived, false) AS archived,
              coalesce(s.serving_enabled, false) AS serving_enabled
         FROM (
           SELECT collection_id FROM stac_higher.collection_connections
            WHERE deleted_at IS NULL
           UNION
           SELECT collection_id FROM stac_higher.process_sources
           UNION
           SELECT collection_id FROM stac_higher.process_outputs
         ) c
         LEFT JOIN stac_higher.collection_settings s
                ON s.collection_id = c.collection_id`,
    ),
    loadGraphEdges(),
  ]);

  const visible = (groupId: string | null): boolean =>
    groups === null || groupId === null || groups.includes(groupId);

  const nodes: GraphNode[] = [];
  for (const row of connections.rows) {
    if (!visible(row.group_id)) continue;
    nodes.push({
      id: connectionNode(row.id),
      type: "connection",
      label: row.name,
      group_id: row.group_id,
      meta: { protocol: row.protocol, status: row.status },
    });
  }
  for (const row of processes.rows) {
    if (!visible(row.group_id)) continue;
    nodes.push({
      id: processNode(row.id),
      type: "process",
      label: row.name,
      group_id: row.group_id,
      meta: { enabled: row.enabled, deployed: row.current_revision !== null },
    });
  }
  for (const row of collections.rows) {
    if (!visible(row.group_id)) continue;
    nodes.push({
      id: collectionNode(row.collection_id),
      type: "collection",
      label: row.collection_id,
      group_id: row.group_id,
      meta: { archived: row.archived, serving_enabled: row.serving_enabled },
    });
  }

  const ids = new Set(nodes.map((node) => node.id));
  return {
    nodes,
    edges: edges.filter((edge) => ids.has(edge.from) && ids.has(edge.to)),
  };
}
