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
};

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
