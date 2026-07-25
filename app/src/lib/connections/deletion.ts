/**
 * Connection deletion per ADR 0009 (soft-delete, retained history,
 * warn-and-proceed) — the app half of ISSUES I-51.
 *
 * DELETE is a soft delete: `deleted_at` is set (the row leaves every listing
 * and API read), the credential envelope and host-key pin are scrubbed
 * immediately (record-keeping never justifies retaining secrets), and the
 * connection's associations soft-delete with it. History rows (`ingest_files`,
 * `delivery_log`, `connection_checks`) are passive records and survive.
 *
 * Reference-backed items are REMOVED with their connection (ADR 0009 §3):
 * their bytes live at the source, so once the managed relationship ends they
 * are dead links waiting to happen. Removal deletes the items from pgstac
 * (the outbox captures `delete` events, which drain without propagating —
 * §6.4) and stamps their ledger rows `reference_removed_at` so the asset
 * route's reference resolution stops offering them; the rows themselves are
 * retained as provenance.
 *
 * `connectionDeleteImpact` is the counted blast radius shown by the
 * warn-and-proceed dialog and returned by the DELETE response.
 */
import { getClient, query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";

/** Reference-backed items that will be removed, counted per collection. */
export interface ReferenceImpact {
  collection_id: string;
  items: number;
}

export interface ConnectionDeleteImpact {
  /** Live associations that will stop (soft-deleted with the connection). */
  associations: { ingest: number; deliver: number };
  /** Items removed from the catalog on delete (ADR 0009 §3). */
  reference_items: ReferenceImpact[];
  /** Provenance rows RETAINED (never deleted) — shown so "kept" is explicit. */
  history: {
    ingest_files: number;
    delivery_log: number;
    connection_checks: number;
  };
}

/** Filter shared by the impact count and the removal pass. */
const REFERENCE_ROWS_WHERE = `
  FROM stac_higher.ingest_files f
  JOIN stac_higher.collection_connections cc ON cc.id = f.association_id
 WHERE cc.connection_id = $1
   AND f.item_id IS NOT NULL
   AND f.source_href IS NOT NULL
   AND f.reference_removed_at IS NULL
`;

export async function connectionDeleteImpact(
  connectionId: string,
): Promise<ConnectionDeleteImpact> {
  await runMigrations();
  const [assocs, reference, history] = await Promise.all([
    query<{ direction: "ingest" | "deliver"; count: string }>(
      `SELECT direction, count(*)::text AS count
         FROM stac_higher.collection_connections
        WHERE connection_id = $1 AND deleted_at IS NULL
        GROUP BY direction`,
      [connectionId],
    ),
    query<{ collection_id: string; items: string }>(
      `SELECT cc.collection_id, count(DISTINCT f.item_id)::text AS items
        ${REFERENCE_ROWS_WHERE}
        GROUP BY cc.collection_id
        ORDER BY cc.collection_id`,
      [connectionId],
    ),
    query<{ ingest_files: string; delivery_log: string; connection_checks: string }>(
      `SELECT
         (SELECT count(*) FROM stac_higher.ingest_files f
            JOIN stac_higher.collection_connections cc ON cc.id = f.association_id
           WHERE cc.connection_id = $1)::text AS ingest_files,
         (SELECT count(*) FROM stac_higher.delivery_log d
            JOIN stac_higher.collection_connections cc ON cc.id = d.association_id
           WHERE cc.connection_id = $1)::text AS delivery_log,
         (SELECT count(*) FROM stac_higher.connection_checks
           WHERE connection_id = $1)::text AS connection_checks`,
      [connectionId],
    ),
  ]);

  const associations = { ingest: 0, deliver: 0 };
  for (const row of assocs.rows) {
    associations[row.direction] = Number(row.count);
  }
  const historyRow = history.rows[0];
  return {
    associations,
    reference_items: reference.rows.map((r) => ({
      collection_id: r.collection_id,
      items: Number(r.items),
    })),
    history: {
      ingest_files: Number(historyRow?.ingest_files ?? 0),
      delivery_log: Number(historyRow?.delivery_log ?? 0),
      connection_checks: Number(historyRow?.connection_checks ?? 0),
    },
  };
}

/**
 * Remove the connection's reference-backed items from pgstac. Per-item and
 * failure-tolerant: an item already gone (or a pgstac-less dev DB) must not
 * block the deletion the operator confirmed. Runs BEFORE the soft-delete
 * transaction so a crash mid-way leaves the connection live and the operation
 * retryable — never marked-but-still-served items.
 */
async function removeReferenceItems(connectionId: string): Promise<void> {
  const rows = await query<{ collection_id: string; item_id: string }>(
    `SELECT DISTINCT cc.collection_id, f.item_id ${REFERENCE_ROWS_WHERE}`,
    [connectionId],
  );
  for (const row of rows.rows) {
    try {
      await query(`SELECT pgstac.delete_item($1, $2)`, [
        row.item_id,
        row.collection_id,
      ]);
    } catch (err) {
      console.warn(
        `[deletion] pgstac.delete_item failed for ${row.collection_id}/${row.item_id}:`,
        err instanceof Error ? err.message : err,
      );
    }
  }
}

/**
 * Soft-delete a connection (ADR 0009): remove its reference-backed items,
 * then — in one transaction — mark their ledger rows, soft-delete the
 * connection's associations, and soft-delete + scrub the connection itself.
 * Returns false when no live connection matched.
 */
export async function softDeleteConnection(
  connectionId: string,
): Promise<boolean> {
  await runMigrations();
  await removeReferenceItems(connectionId);

  const client = await getClient();
  try {
    await client.query("BEGIN");
    await client.query(
      `UPDATE stac_higher.ingest_files f
          SET reference_removed_at = now()
         FROM stac_higher.collection_connections cc
        WHERE cc.id = f.association_id
          AND cc.connection_id = $1
          AND f.item_id IS NOT NULL
          AND f.source_href IS NOT NULL
          AND f.reference_removed_at IS NULL`,
      [connectionId],
    );
    await client.query(
      `UPDATE stac_higher.collection_connections
          SET deleted_at = now(), updated_at = now()
        WHERE connection_id = $1 AND deleted_at IS NULL`,
      [connectionId],
    );
    const result = await client.query(
      `UPDATE stac_higher.connections
          SET deleted_at = now(),
              credentials = NULL,
              host_key = NULL,
              host_key_pinned_at = NULL,
              enabled = false,
              updated_at = now()
        WHERE id = $1 AND deleted_at IS NULL`,
      [connectionId],
    );
    await client.query("COMMIT");
    return (result.rowCount ?? 0) > 0;
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}
