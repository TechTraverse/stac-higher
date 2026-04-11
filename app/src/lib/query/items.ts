import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { stacKeys } from "./keys";
import {
  getItems,
  getItem,
  createItem,
  updateItem,
  patchItem,
  deleteItem,
  type GetItemsParams,
} from "@/lib/stac-api/items";
import type { StacItem } from "@/lib/stac-api/types";

export function useItems(
  endpointUrl: string,
  collectionId: string,
  params?: GetItemsParams,
) {
  return useQuery({
    queryKey: [...stacKeys.items(endpointUrl, collectionId), params],
    queryFn: () => getItems(collectionId, params, endpointUrl),
    enabled: !!endpointUrl && !!collectionId,
  });
}

export function useItem(
  endpointUrl: string,
  collectionId: string,
  itemId: string,
) {
  return useQuery({
    queryKey: stacKeys.item(endpointUrl, collectionId, itemId),
    queryFn: () => getItem(collectionId, itemId, endpointUrl),
    enabled: !!endpointUrl && !!collectionId && !!itemId,
  });
}

export function useCreateItem(endpointUrl: string, collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: StacItem) => createItem(collectionId, data, endpointUrl),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: stacKeys.items(endpointUrl, collectionId),
      });
    },
  });
}

export function useUpdateItem(endpointUrl: string, collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, data }: { itemId: string; data: StacItem }) =>
      updateItem(collectionId, itemId, data, endpointUrl),
    onSuccess: (_, { itemId }) => {
      qc.invalidateQueries({
        queryKey: stacKeys.items(endpointUrl, collectionId),
      });
      qc.invalidateQueries({
        queryKey: stacKeys.item(endpointUrl, collectionId, itemId),
      });
    },
  });
}

export function usePatchItem(endpointUrl: string, collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      itemId,
      patch,
    }: {
      itemId: string;
      patch: Partial<StacItem>;
    }) => patchItem(collectionId, itemId, patch, endpointUrl),
    onSuccess: (_, { itemId }) => {
      qc.invalidateQueries({
        queryKey: stacKeys.items(endpointUrl, collectionId),
      });
      qc.invalidateQueries({
        queryKey: stacKeys.item(endpointUrl, collectionId, itemId),
      });
    },
  });
}

export function useDeleteItem(endpointUrl: string, collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) =>
      deleteItem(collectionId, itemId, endpointUrl),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: stacKeys.items(endpointUrl, collectionId),
      });
    },
  });
}
