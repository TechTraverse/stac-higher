/**
 * TanStack Query hooks for ingest associations. Keyed per collection so a
 * collection's Data-flow tab refetches independently; mutations invalidate the
 * collection's association list.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { associationKeys } from "@/lib/query/keys";
import {
  createAssociation,
  deleteAssociation,
  listAssociations,
  updateAssociation,
} from "./api";
import type {
  AssociationCreateInput,
  AssociationUpdateInput,
} from "./schemas";

export function useAssociations(collectionId: string) {
  return useQuery({
    queryKey: associationKeys.list(collectionId),
    queryFn: () => listAssociations(collectionId),
    enabled: !!collectionId,
  });
}

export function useCreateAssociation(collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: AssociationCreateInput) =>
      createAssociation(collectionId, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: associationKeys.list(collectionId) });
    },
  });
}

export function useUpdateAssociation(collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: AssociationUpdateInput }) =>
      updateAssociation(collectionId, id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: associationKeys.list(collectionId) });
    },
  });
}

export function useDeleteAssociation(collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAssociation(collectionId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: associationKeys.list(collectionId) });
    },
  });
}
