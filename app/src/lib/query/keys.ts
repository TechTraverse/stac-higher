import type { StacSearchBody } from "@/lib/stac-api/types";

export const authKeys = {
  all: () => ["auth"] as const,
  me: () => [...authKeys.all(), "me"] as const,
};

export const extensionKeys = {
  all: () => ["extensions"] as const,
  list: () => [...extensionKeys.all(), "list"] as const,
  detail: (id: string) => [...extensionKeys.all(), id] as const,
};

export const connectionKeys = {
  all: () => ["connections"] as const,
  list: () => [...connectionKeys.all(), "list"] as const,
  detail: (id: string) => [...connectionKeys.all(), id] as const,
  deleteImpact: (id: string) =>
    [...connectionKeys.detail(id), "delete-impact"] as const,
};

/** Ingest/delivery associations, scoped per collection (Phase 4). */
export const associationKeys = {
  all: () => ["associations"] as const,
  list: (collectionId: string) =>
    [...associationKeys.all(), collectionId] as const,
  detail: (collectionId: string, id: string) =>
    [...associationKeys.list(collectionId), id] as const,
  deleteImpact: (collectionId: string, id: string) =>
    [...associationKeys.detail(collectionId, id), "delete-impact"] as const,
  deliveries: (collectionId: string, id: string) =>
    [...associationKeys.detail(collectionId, id), "deliveries"] as const,
  backfill: (collectionId: string, id: string, backfillId: string) =>
    [...associationKeys.detail(collectionId, id), "backfill", backfillId] as const,
};

/** Collection platform settings (M2-E). */
export const collectionSettingsKeys = {
  all: () => ["collection-settings"] as const,
  detail: (collectionId: string) =>
    [...collectionSettingsKeys.all(), collectionId] as const,
};

/** Tile-server (titiler-pgstac) reads — the item raster preview (G-5). */
export const servingKeys = {
  all: () => ["serving"] as const,
  itemTileJson: (collectionId: string, itemId: string, assetKey: string) =>
    [...servingKeys.all(), "item-tilejson", collectionId, itemId, assetKey] as const,
  tipgCollections: () => [...servingKeys.all(), "tipg-collections"] as const,
};

/** Alerts + monitoring surfaces (M2-B/M2-D). */
export const alertKeys = {
  all: () => ["alerts"] as const,
  list: (state: string) => [...alertKeys.all(), "list", state] as const,
  unread: () => [...alertKeys.all(), "unread"] as const,
};

/** Cross-collection flow telemetry for /monitoring (M2-D). */
export const monitoringKeys = {
  all: () => ["monitoring"] as const,
  flows: () => [...monitoringKeys.all(), "flows"] as const,
};

/** Per-group notification channels (M2-C/M2-D). */
export const channelKeys = {
  all: () => ["channels"] as const,
  list: () => [...channelKeys.all(), "list"] as const,
};

export const extractorKeys = {
  all: () => ["extractors"] as const,
  builtin: () => [...extractorKeys.all(), "builtin"] as const,
};

export const processKeys = {
  all: () => ["processes"] as const,
  list: () => [...processKeys.all(), "list"] as const,
  detail: (id: string) => [...processKeys.all(), id] as const,
  revisions: (id: string) => [...processKeys.detail(id), "revisions"] as const,
  sources: (id: string) => [...processKeys.detail(id), "sources"] as const,
  outputs: (id: string) => [...processKeys.detail(id), "outputs"] as const,
  runs: (id: string) => [...processKeys.detail(id), "runs"] as const,
} as const;

export const graphKeys = {
  all: () => ["graph"] as const,
  graph: () => [...graphKeys.all(), "pipeline"] as const,
  history: (kind: string, id: string, days: number) =>
    [...graphKeys.all(), "history", kind, id, days] as const,
} as const;

export const stacKeys = {
  all: (endpointUrl: string) => ["stac", endpointUrl] as const,

  landing: (endpointUrl: string) =>
    [...stacKeys.all(endpointUrl), "landing"] as const,

  collections: (endpointUrl: string) =>
    [...stacKeys.all(endpointUrl), "collections"] as const,

  collection: (endpointUrl: string, id: string) =>
    [...stacKeys.collections(endpointUrl), id] as const,

  items: (endpointUrl: string, collectionId: string) =>
    [...stacKeys.collection(endpointUrl, collectionId), "items"] as const,

  item: (endpointUrl: string, collectionId: string, itemId: string) =>
    [...stacKeys.items(endpointUrl, collectionId), itemId] as const,

  search: (endpointUrl: string, params: StacSearchBody) =>
    [...stacKeys.all(endpointUrl), "search", params] as const,
};
