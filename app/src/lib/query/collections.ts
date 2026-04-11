import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { stacKeys } from "./keys";
import {
  getCollections,
  getCollection,
  createCollection,
  updateCollection,
  deleteCollection,
} from "@/lib/stac-api/collections";
import type { StacCollection } from "@/lib/stac-api/types";

export function useCollections(endpointUrl: string) {
  return useQuery({
    queryKey: stacKeys.collections(endpointUrl),
    queryFn: () => getCollections(endpointUrl),
    enabled: !!endpointUrl,
  });
}

export function useCollection(endpointUrl: string, collectionId: string) {
  return useQuery({
    queryKey: stacKeys.collection(endpointUrl, collectionId),
    queryFn: () => getCollection(collectionId, endpointUrl),
    enabled: !!endpointUrl && !!collectionId,
  });
}

export function useCreateCollection(endpointUrl: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: StacCollection) => createCollection(data, endpointUrl),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: stacKeys.collections(endpointUrl) });
    },
  });
}

export function useUpdateCollection(endpointUrl: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      collectionId,
      data,
    }: {
      collectionId: string;
      data: StacCollection;
    }) => updateCollection(collectionId, data, endpointUrl),
    onSuccess: (_, { collectionId }) => {
      qc.invalidateQueries({ queryKey: stacKeys.collections(endpointUrl) });
      qc.invalidateQueries({
        queryKey: stacKeys.collection(endpointUrl, collectionId),
      });
    },
  });
}

export function useDeleteCollection(endpointUrl: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (collectionId: string) =>
      deleteCollection(collectionId, endpointUrl),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: stacKeys.collections(endpointUrl) });
    },
  });
}
