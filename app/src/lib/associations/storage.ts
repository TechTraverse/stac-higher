/**
 * collection_connections persistence (ROADMAP §5, §6.1 — Phase 4 ingest slice).
 *
 * The app owns this DDL and writes ingest associations here; the pipeline reads
 * them (and writes the separate ingest_files ledger). Reads LEFT JOIN the
 * parent connection so the API can surface the connection's name/protocol/
 * health and — critically — its `group_id`, which is the access boundary
 * (`lib/associations/access.ts`).
 *
 * `flow_stats` is pipeline-written telemetry; this module never writes it, so a
 * user edit never clobbers it. `updated_at` is maintained app-side (like
 * connections) so pipeline flow-stat writes don't masquerade as user edits.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import type { ConnectionProtocol } from "@/lib/connections/schemas";
import type {
  AssociationDirection,
  DeliveryConfig,
  Expectation,
  IngestConfig,
} from "./schemas";

/**
 * Columns shared by every association read. Joined connection fields carry a
 * `connection_` prefix; `connection_group_id` is selected for access checks and
 * is NOT part of the client-facing shape (stripped in `toApiAssociation`).
 */
const ASSOCIATION_COLUMNS = `
  cc.id, cc.collection_id, cc.connection_id, cc.direction,
  cc.enabled, cc.config, cc.expectation, cc.flow_stats,
  cc.created_by, cc.created_at, cc.updated_at,
  c.name AS connection_name,
  c.protocol AS connection_protocol,
  c.status AS connection_status,
  c.group_id AS connection_group_id
`;

interface AssociationRow {
  id: string;
  collection_id: string;
  connection_id: string;
  direction: AssociationDirection;
  enabled: boolean;
  config: Record<string, unknown>;
  expectation: Record<string, unknown> | null;
  flow_stats: Record<string, unknown>;
  created_by: string;
  created_at: Date | string;
  updated_at: Date | string;
  connection_name: string | null;
  connection_protocol: ConnectionProtocol | null;
  connection_status: "unverified" | "ok" | "error" | null;
  connection_group_id: string | null;
}

export interface ApiAssociation {
  id: string;
  collection_id: string;
  connection_id: string;
  direction: AssociationDirection;
  enabled: boolean;
  config: Record<string, unknown>;
  expectation: Record<string, unknown> | null;
  /** Pipeline-written telemetry (files/bytes/last_activity_at/latency). */
  flow_stats: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at: string;
  /** Denormalized from the parent connection for display. */
  connection: {
    name: string | null;
    protocol: ConnectionProtocol | null;
    status: "unverified" | "ok" | "error" | null;
  };
}

/**
 * Access-bearing view: the client shape plus the owning connection's group_id.
 * Returned by the loader in `access.ts`; routes strip it via `toApiAssociation`
 * before responding so group ids never leak in the API surface.
 */
export interface AssociationWithGroup extends ApiAssociation {
  connectionGroupId: string | null;
}

function iso(value: Date | string): string {
  return value instanceof Date ? value.toISOString() : String(value);
}

function toAssociationWithGroup(row: AssociationRow): AssociationWithGroup {
  return {
    id: row.id,
    collection_id: row.collection_id,
    connection_id: row.connection_id,
    direction: row.direction,
    enabled: row.enabled,
    config: row.config,
    expectation: row.expectation,
    flow_stats: row.flow_stats,
    created_by: row.created_by,
    created_at: iso(row.created_at),
    updated_at: iso(row.updated_at),
    connection: {
      name: row.connection_name,
      protocol: row.connection_protocol,
      status: row.connection_status,
    },
    connectionGroupId: row.connection_group_id,
  };
}

/** Drop the access-only field to get the client-facing shape. */
export function toApiAssociation(row: AssociationWithGroup): ApiAssociation {
  const { connectionGroupId: _drop, ...api } = row;
  return api;
}

export async function listAssociations(
  collectionId: string,
): Promise<AssociationWithGroup[]> {
  await runMigrations();
  const result = await query<AssociationRow>(
    `SELECT ${ASSOCIATION_COLUMNS}
       FROM stac_higher.collection_connections cc
       LEFT JOIN stac_higher.connections c ON c.id = cc.connection_id
      WHERE cc.collection_id = $1
      ORDER BY cc.created_at DESC`,
    [collectionId],
  );
  return result.rows.map(toAssociationWithGroup);
}

export async function getAssociation(
  id: string,
): Promise<AssociationWithGroup | null> {
  await runMigrations();
  const result = await query<AssociationRow>(
    `SELECT ${ASSOCIATION_COLUMNS}
       FROM stac_higher.collection_connections cc
       LEFT JOIN stac_higher.connections c ON c.id = cc.connection_id
      WHERE cc.id = $1`,
    [id],
  );
  return result.rows[0] ? toAssociationWithGroup(result.rows[0]) : null;
}

export interface CreateAssociationInput {
  collectionId: string;
  connectionId: string;
  direction: AssociationDirection;
  enabled: boolean;
  config: IngestConfig | DeliveryConfig;
  expectation: Expectation | null;
  createdBy: string;
}

/** Raised when the (collection, connection, direction) pair already exists. */
export class DuplicateAssociationError extends Error {
  constructor() {
    super("This connection is already associated with the collection");
    this.name = "DuplicateAssociationError";
  }
}

function isUniqueViolation(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { code?: unknown }).code === "23505"
  );
}

export async function createAssociation(
  input: CreateAssociationInput,
): Promise<AssociationWithGroup> {
  await runMigrations();
  let inserted;
  try {
    inserted = await query<{ id: string }>(
      `INSERT INTO stac_higher.collection_connections
         (collection_id, connection_id, direction, enabled, config, expectation, created_by)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       RETURNING id`,
      [
        input.collectionId,
        input.connectionId,
        input.direction,
        input.enabled,
        JSON.stringify(input.config),
        input.expectation === null ? null : JSON.stringify(input.expectation),
        input.createdBy,
      ],
    );
  } catch (err) {
    if (isUniqueViolation(err)) throw new DuplicateAssociationError();
    throw err;
  }
  // Re-read through the join so the response carries the connection fields.
  const created = await getAssociation(inserted.rows[0].id);
  if (!created) throw new Error("Association vanished immediately after insert");
  return created;
}

export interface UpdateAssociationInput {
  enabled?: boolean;
  config?: IngestConfig;
  expectation?: Expectation | null;
}

export async function updateAssociation(
  id: string,
  patch: UpdateAssociationInput,
): Promise<AssociationWithGroup | null> {
  await runMigrations();
  const sets: string[] = [];
  const params: unknown[] = [];
  const set = (fragment: string, value: unknown) => {
    params.push(value);
    sets.push(fragment.replace("?", `$${params.length}`));
  };

  if (patch.enabled !== undefined) set("enabled = ?", patch.enabled);
  if (patch.config !== undefined) set("config = ?", JSON.stringify(patch.config));
  if (patch.expectation !== undefined) {
    set(
      "expectation = ?",
      patch.expectation === null ? null : JSON.stringify(patch.expectation),
    );
  }
  if (sets.length === 0) return getAssociation(id);
  sets.push("updated_at = now()");

  params.push(id);
  const result = await query<{ id: string }>(
    `UPDATE stac_higher.collection_connections
        SET ${sets.join(", ")}
      WHERE id = $${params.length}
      RETURNING id`,
    params,
  );
  if (!result.rows[0]) return null;
  return getAssociation(id);
}

export async function deleteAssociation(id: string): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.collection_connections WHERE id = $1`,
    [id],
  );
  return (result.rowCount ?? 0) > 0;
}
