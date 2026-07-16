/**
 * Connections persistence (ROADMAP §5 CONNECTIONS + Phase 2 contract).
 *
 * Hard invariants:
 *   - The `credentials` bytea column is NEVER selected. Every read maps it to
 *     `credentials IS NOT NULL AS credentials_set` — plaintext never exists
 *     app-side and ciphertext never leaves the DB through this module.
 *   - `host_key` is selected only to derive a fingerprint; the raw key text
 *     is not part of the API shape (`toApiConnection`).
 *   - `updated_at` is maintained here (app-side), deliberately NOT by a DB
 *     trigger: the pipeline's health sweep UPDATEs status columns every few
 *     minutes and must not bump "when a user last edited this".
 */
import { createHash } from "node:crypto";
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import type { ConnectionProtocol } from "./schemas";
import { isSshFamily } from "./schemas";

/** Column list shared by every connection read. NO credentials column. */
const CONNECTION_COLUMNS = `
  id, name, description, protocol, config,
  (credentials IS NOT NULL) AS credentials_set,
  host_key, host_key_pinned_at,
  group_id, created_by, created_at, updated_at,
  enabled, status, last_checked_at, last_error
`;

interface ConnectionRow {
  id: string;
  name: string;
  description: string;
  protocol: ConnectionProtocol;
  config: Record<string, unknown>;
  credentials_set: boolean;
  host_key: string | null;
  host_key_pinned_at: Date | string | null;
  group_id: string;
  created_by: string;
  created_at: Date | string;
  updated_at: Date | string;
  enabled: boolean;
  status: "unverified" | "ok" | "error";
  last_checked_at: Date | string | null;
  last_error: string | null;
}

export interface ApiConnection {
  id: string;
  name: string;
  description: string;
  protocol: ConnectionProtocol;
  config: Record<string, unknown>;
  /** Write-only credentials: the API exposes presence only. */
  credentials_set: boolean;
  /** TOFU pin metadata (§5.2) — fingerprint, never the raw key. */
  host_key: { fingerprint: string; pinned_at: string | null } | null;
  group_id: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  enabled: boolean;
  status: "unverified" | "ok" | "error";
  last_checked_at: string | null;
  last_error: string | null;
}

export interface ApiConnectionCheck {
  id: string;
  connection_id: string;
  requested_by: string;
  requested_at: string;
  status: "pending" | "running" | "done" | "failed";
  /** Pipeline-written {ok, message, latency_ms?}; host_key stripped. */
  result: Record<string, unknown> | null;
  finished_at: string | null;
}

function iso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : String(value);
}

/**
 * OpenSSH-style SHA256 fingerprint of a stored host key. The stored text is
 * expected as `<type> <base64-blob> [comment]`; falls back to hashing the
 * whole string when it does not split, so a fingerprint is always derivable.
 */
export function hostKeyFingerprint(hostKey: string): string {
  const parts = hostKey.trim().split(/\s+/);
  const blob = parts.length >= 2 ? parts[1] : parts[0];
  // Buffer.from(..., "base64") never throws (it decodes best-effort); the empty
  // result is the only case needing the raw-string fallback.
  let material = Buffer.from(blob, "base64");
  if (material.length === 0) material = Buffer.from(blob, "utf8");
  const digest = createHash("sha256").update(material).digest("base64");
  // OpenSSH prints SHA256 fingerprints without base64 padding.
  return `SHA256:${digest.replace(/=+$/, "")}`;
}

function toApiConnection(row: ConnectionRow): ApiConnection {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    protocol: row.protocol,
    config: row.config,
    credentials_set: row.credentials_set,
    host_key: row.host_key
      ? {
          fingerprint: hostKeyFingerprint(row.host_key),
          pinned_at: iso(row.host_key_pinned_at),
        }
      : null,
    group_id: row.group_id,
    created_by: row.created_by,
    created_at: iso(row.created_at) as string,
    updated_at: iso(row.updated_at) as string,
    enabled: row.enabled,
    status: row.status,
    last_checked_at: iso(row.last_checked_at),
    last_error: row.last_error,
  };
}

/**
 * List connections. `groups: null` = admin (all rows); otherwise rows whose
 * group_id is in the caller's groups (empty array matches nothing).
 */
export async function listConnections(
  groups: string[] | null,
): Promise<ApiConnection[]> {
  await runMigrations();
  const where = groups === null ? "" : "WHERE group_id = ANY($1::text[])";
  const params = groups === null ? [] : [groups];
  const result = await query<ConnectionRow>(
    `SELECT ${CONNECTION_COLUMNS}
       FROM stac_higher.connections
       ${where}
       ORDER BY created_at DESC`,
    params,
  );
  return result.rows.map(toApiConnection);
}

export async function getConnection(
  id: string,
): Promise<ApiConnection | null> {
  await runMigrations();
  const result = await query<ConnectionRow>(
    `SELECT ${CONNECTION_COLUMNS}
       FROM stac_higher.connections
       WHERE id = $1`,
    [id],
  );
  return result.rows[0] ? toApiConnection(result.rows[0]) : null;
}

export interface CreateConnectionInput {
  name: string;
  description: string;
  protocol: ConnectionProtocol;
  config: Record<string, unknown>;
  /** Sealed envelope from the encryption provider — never plaintext. */
  encryptedCredentials: Buffer;
  groupId: string;
  createdBy: string;
  enabled: boolean;
}

export async function createConnection(
  input: CreateConnectionInput,
): Promise<ApiConnection> {
  await runMigrations();
  const result = await query<ConnectionRow>(
    `INSERT INTO stac_higher.connections
       (name, description, protocol, config, credentials, group_id, created_by, enabled)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
     RETURNING ${CONNECTION_COLUMNS}`,
    [
      input.name,
      input.description,
      input.protocol,
      JSON.stringify(input.config),
      input.encryptedCredentials,
      input.groupId,
      input.createdBy,
      input.enabled,
    ],
  );
  return toApiConnection(result.rows[0]);
}

export interface UpdateConnectionInput {
  name?: string;
  description?: string;
  groupId?: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
  /** When present, REPLACES the stored envelope wholesale (contract). */
  encryptedCredentials?: Buffer;
  /** SSH-family host/port changed → un-pin so the next test re-pins. */
  clearHostKey?: boolean;
}

export async function updateConnection(
  id: string,
  patch: UpdateConnectionInput,
): Promise<ApiConnection | null> {
  await runMigrations();
  const sets: string[] = [];
  const params: unknown[] = [];
  const set = (fragment: string, value: unknown) => {
    params.push(value);
    sets.push(fragment.replace("?", `$${params.length}`));
  };

  if (patch.name !== undefined) set("name = ?", patch.name);
  if (patch.description !== undefined) set("description = ?", patch.description);
  if (patch.groupId !== undefined) set("group_id = ?", patch.groupId);
  if (patch.enabled !== undefined) set("enabled = ?", patch.enabled);
  if (patch.config !== undefined) set("config = ?", JSON.stringify(patch.config));
  if (patch.encryptedCredentials !== undefined) {
    set("credentials = ?", patch.encryptedCredentials);
  }
  if (patch.config !== undefined || patch.encryptedCredentials !== undefined) {
    // Endpoint or secret changed: previous health verdict no longer applies.
    sets.push("status = 'unverified'", "last_error = NULL");
  }
  if (patch.clearHostKey) {
    sets.push("host_key = NULL", "host_key_pinned_at = NULL");
  }
  if (sets.length === 0) return getConnection(id);
  sets.push("updated_at = now()");

  params.push(id);
  const result = await query<ConnectionRow>(
    `UPDATE stac_higher.connections
        SET ${sets.join(", ")}
      WHERE id = $${params.length}
      RETURNING ${CONNECTION_COLUMNS}`,
    params,
  );
  return result.rows[0] ? toApiConnection(result.rows[0]) : null;
}

export async function deleteConnection(id: string): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.connections WHERE id = $1`,
    [id],
  );
  return (result.rowCount ?? 0) > 0;
}

/**
 * Clear the TOFU pin (§5.2 "re-verify"): the next successful SSH-family test
 * re-captures and re-pins the host key. Also drops the connection back to
 * `unverified` so a stale `ok` badge cannot outlive its pin.
 */
export async function resetHostKey(id: string): Promise<ApiConnection | null> {
  await runMigrations();
  const result = await query<ConnectionRow>(
    `UPDATE stac_higher.connections
        SET host_key = NULL,
            host_key_pinned_at = NULL,
            status = 'unverified',
            last_error = NULL,
            updated_at = now()
      WHERE id = $1
      RETURNING ${CONNECTION_COLUMNS}`,
    [id],
  );
  return result.rows[0] ? toApiConnection(result.rows[0]) : null;
}

// ---------------------------------------------------------------------------
// connection_checks — the app half of the app→pipeline bridge (ADR 0004).
// The app only INSERTs pending rows and polls them; the pipeline's drain job
// claims pending rows (FOR UPDATE SKIP LOCKED), runs adapter.test, and writes
// status/result/finished_at plus the parent connection's health fields.
// ---------------------------------------------------------------------------

interface CheckRow {
  id: string;
  connection_id: string;
  requested_by: string;
  requested_at: Date | string;
  status: "pending" | "running" | "done" | "failed";
  result: Record<string, unknown> | null;
  finished_at: Date | string | null;
}

function toApiCheck(row: CheckRow): ApiConnectionCheck {
  let result = row.result;
  if (result && typeof result === "object" && "host_key" in result) {
    // The raw host key is bridge-internal (pipeline → connection row); the
    // API surfaces pins as fingerprints on the connection resource instead.
    const { host_key: _hostKey, ...rest } = result;
    result = rest;
  }
  return {
    id: row.id,
    connection_id: row.connection_id,
    requested_by: row.requested_by,
    requested_at: iso(row.requested_at) as string,
    status: row.status,
    result,
    finished_at: iso(row.finished_at),
  };
}

export async function insertConnectionCheck(
  connectionId: string,
  requestedBy: string,
): Promise<ApiConnectionCheck> {
  await runMigrations();
  const result = await query<CheckRow>(
    `INSERT INTO stac_higher.connection_checks (connection_id, requested_by)
     VALUES ($1, $2)
     RETURNING id, connection_id, requested_by, requested_at, status, result, finished_at`,
    [connectionId, requestedBy],
  );
  return toApiCheck(result.rows[0]);
}

export async function getConnectionCheck(
  connectionId: string,
  checkId: string,
): Promise<ApiConnectionCheck | null> {
  await runMigrations();
  const result = await query<CheckRow>(
    `SELECT id, connection_id, requested_by, requested_at, status, result, finished_at
       FROM stac_higher.connection_checks
      WHERE id = $1 AND connection_id = $2`,
    [checkId, connectionId],
  );
  return result.rows[0] ? toApiCheck(result.rows[0]) : null;
}

/**
 * True when an SSH-family config change (host/port) should clear the pinned
 * host key: the pin authenticates a specific endpoint, not the row.
 */
export function shouldClearHostKey(
  protocol: ConnectionProtocol,
  oldConfig: Record<string, unknown>,
  newConfig: Record<string, unknown> | undefined,
): boolean {
  if (!newConfig || !isSshFamily(protocol)) return false;
  return (
    oldConfig.host !== newConfig.host || oldConfig.port !== newConfig.port
  );
}
