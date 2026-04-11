import { stacFetch } from "./client";
import type { StacItem, StacItemCollection } from "./types";

export interface GetItemsParams {
  limit?: number;
  token?: string;
  bbox?: number[];
  datetime?: string;
}

export function getItems(
  collectionId: string,
  params?: GetItemsParams,
  endpointUrl?: string,
) {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.token) searchParams.set("token", params.token);
  if (params?.bbox) searchParams.set("bbox", params.bbox.join(","));
  if (params?.datetime) searchParams.set("datetime", params.datetime);

  const query = searchParams.toString();
  const path = `/collections/${encodeURIComponent(collectionId)}/items${query ? `?${query}` : ""}`;
  return stacFetch<StacItemCollection>(path, { endpointUrl });
}

export function getItem(
  collectionId: string,
  itemId: string,
  endpointUrl?: string,
) {
  return stacFetch<StacItem>(
    `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`,
    { endpointUrl },
  );
}

export function createItem(
  collectionId: string,
  data: StacItem,
  endpointUrl?: string,
) {
  return stacFetch<StacItem>(
    `/collections/${encodeURIComponent(collectionId)}/items`,
    { method: "POST", body: data, endpointUrl },
  );
}

export function updateItem(
  collectionId: string,
  itemId: string,
  data: StacItem,
  endpointUrl?: string,
) {
  return stacFetch<StacItem>(
    `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`,
    { method: "PUT", body: data, endpointUrl },
  );
}

export function patchItem(
  collectionId: string,
  itemId: string,
  patch: Partial<StacItem>,
  endpointUrl?: string,
) {
  return stacFetch<StacItem>(
    `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`,
    { method: "PATCH", body: patch, endpointUrl },
  );
}

export function deleteItem(
  collectionId: string,
  itemId: string,
  endpointUrl?: string,
) {
  return stacFetch<void>(
    `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`,
    { method: "DELETE", endpointUrl },
  );
}
