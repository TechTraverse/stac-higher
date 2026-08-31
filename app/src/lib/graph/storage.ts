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

  const [associations, sources, outputs] = await Promise.all([
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
  return edges;
}
