/**
 * Notification channel CRUD (M2-C, M2 spec §4, ADR 0010).
 *
 * The app owns the rows end to end (DDL in migrations 014/015 and the CRUD
 * here); the pipeline only READS channels at fan-out time and writes the
 * `notification_deliveries` ledger. Group ownership is stored (unlike alerts,
 * whose group is derived) because a channel belongs to a group by
 * construction.
 *
 * The webhook signing secret is WRITE-ONLY, like connection credentials: API
 * responses carry `has_secret`, never the value. `updateChannelConfig`
 * replaces the config wholesale, so dropping the secret is "PUT without it".
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { iso } from "@/lib/associations/storage";

export type ChannelKind = "in_app" | "webhook";

interface ChannelRow {
  id: string;
  group_id: string;
  kind: ChannelKind;
  config: Record<string, unknown>;
  created_by: string;
  created_at: Date | string;
  updated_at: Date | string;
}

/** Redacted config shape: webhook → url + has_secret, in_app → {}. */
export interface ApiChannelConfig {
  url?: string;
  has_secret?: boolean;
}

export interface ApiChannel {
  id: string;
  group_id: string;
  kind: ChannelKind;
  config: ApiChannelConfig;
  created_by: string;
  created_at: string;
  updated_at: string;
}

function redactConfig(
  kind: ChannelKind,
  config: Record<string, unknown>,
): ApiChannelConfig {
  if (kind !== "webhook") return {};
  return {
    url: typeof config.url === "string" ? config.url : undefined,
    has_secret: typeof config.secret === "string" && config.secret.length > 0,
  };
}

function toApiChannel(row: ChannelRow): ApiChannel {
  return {
    id: row.id,
    group_id: row.group_id,
    kind: row.kind,
    config: redactConfig(row.kind, row.config ?? {}),
    created_by: row.created_by,
    created_at: iso(row.created_at) ?? "",
    updated_at: iso(row.updated_at) ?? "",
  };
}

const CHANNEL_COLUMNS = "id, group_id, kind, config, created_by, created_at, updated_at";

/** Channels visible to the caller: `groups = null` for admin (all). */
export async function listChannels(
  groups: string[] | null,
): Promise<ApiChannel[]> {
  await runMigrations();
  const params: unknown[] = [];
  let where = "";
  if (groups !== null) {
    params.push(groups);
    where = `WHERE group_id = ANY($1::text[])`;
  }
  const result = await query<ChannelRow>(
    `SELECT ${CHANNEL_COLUMNS} FROM stac_higher.notification_channels
      ${where} ORDER BY created_at DESC`,
    params,
  );
  return result.rows.map(toApiChannel);
}

export async function getChannel(id: string): Promise<ApiChannel | null> {
  await runMigrations();
  const result = await query<ChannelRow>(
    `SELECT ${CHANNEL_COLUMNS} FROM stac_higher.notification_channels WHERE id = $1`,
    [id],
  );
  return result.rows[0] ? toApiChannel(result.rows[0]) : null;
}

export async function createChannel(input: {
  groupId: string;
  kind: ChannelKind;
  config: Record<string, unknown>;
  createdBy: string;
}): Promise<ApiChannel> {
  await runMigrations();
  const result = await query<ChannelRow>(
    `INSERT INTO stac_higher.notification_channels
       (group_id, kind, config, created_by)
     VALUES ($1, $2, $3::jsonb, $4)
     RETURNING ${CHANNEL_COLUMNS}`,
    [input.groupId, input.kind, JSON.stringify(input.config), input.createdBy],
  );
  return toApiChannel(result.rows[0]);
}

/** Wholesale config replacement (kind/group immutable). Null when missing. */
export async function updateChannelConfig(
  id: string,
  config: Record<string, unknown>,
): Promise<ApiChannel | null> {
  await runMigrations();
  const result = await query<ChannelRow>(
    `UPDATE stac_higher.notification_channels
        SET config = $2::jsonb, updated_at = now()
      WHERE id = $1
      RETURNING ${CHANNEL_COLUMNS}`,
    [id, JSON.stringify(config)],
  );
  return result.rows[0] ? toApiChannel(result.rows[0]) : null;
}

/** Hard delete — pending notification_deliveries and channel-anchored alerts
 * cascade with the row (a deleted channel has nothing left to notify or
 * complain about). Returns false when the row was already gone. */
export async function deleteChannel(id: string): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.notification_channels WHERE id = $1`,
    [id],
  );
  return (result.rowCount ?? 0) > 0;
}
