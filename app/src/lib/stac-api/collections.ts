import { stacFetch } from "./client";
import type {
  StacCollection,
  StacCollectionsResponse,
} from "./types";

export function getCollections(endpointUrl?: string) {
  return stacFetch<StacCollectionsResponse>("/collections", { endpointUrl });
}

export function getCollection(collectionId: string, endpointUrl?: string) {
  return stacFetch<StacCollection>(`/collections/${encodeURIComponent(collectionId)}`, {
    endpointUrl,
  });
}

export function createCollection(data: StacCollection, endpointUrl?: string) {
  return stacFetch<StacCollection>("/collections", {
    method: "POST",
    body: data,
    endpointUrl,
  });
}

export function updateCollection(
  collectionId: string,
  data: StacCollection,
  endpointUrl?: string,
) {
  return stacFetch<StacCollection>(
    `/collections/${encodeURIComponent(collectionId)}`,
    { method: "PUT", body: data, endpointUrl },
  );
}

export function deleteCollection(collectionId: string, endpointUrl?: string) {
  return stacFetch<void>(`/collections/${encodeURIComponent(collectionId)}`, {
    method: "DELETE",
    endpointUrl,
  });
}
