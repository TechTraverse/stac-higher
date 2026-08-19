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
  getAssociationDeleteImpact,
  getBackfill,
  listAssociations,
  listDeliveries,
  redeliverDelivery,
  requestBackfill,
  updateAssociation,
} from "./api";
import type {
  AssociationCreatePayload,
  AssociationUpdatePayload,
} from "./schemas";

export function useAssociations(collectionId: string) {
  return useQuery({
    queryKey: associationKeys.list(collectionId),
    queryFn: () => listAssociations(collectionId),
    enabled: !!collectionId,
  });
}

/** Pre-flight deletion impact for the confirm dialog (ADR 0009). */
export function useAssociationDeleteImpact(
  collectionId: string,
  id: string | null,
) {
  return useQuery({
    queryKey: associationKeys.deleteImpact(collectionId, id ?? "none"),
    queryFn: () => getAssociationDeleteImpact(collectionId, id as string),
    enabled: !!id,
  });
}

export function useCreateAssociation(collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: AssociationCreatePayload) =>
      createAssociation(collectionId, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: associationKeys.list(collectionId) });
    },
  });
}

export function useUpdateAssociation(collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: AssociationUpdatePayload }) =>
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

// ---------------------------------------------------------------------------
// Delivery status + actions (Slice D)
// ---------------------------------------------------------------------------

/** Delivery status for one deliver association. The pipeline writes rows
 * asynchronously, so keep the panel fresh with a slow poll while mounted. */
export function useDeliveries(collectionId: string, associationId: string) {
  return useQuery({
    queryKey: associationKeys.deliveries(collectionId, associationId),
    queryFn: () => listDeliveries(collectionId, associationId),
    refetchInterval: 15_000,
  });
}

export function useRedeliver(collectionId: string, associationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (deliveryId: string) =>
      redeliverDelivery(collectionId, associationId, deliveryId),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: associationKeys.deliveries(collectionId, associationId),
      });
    },
  });
}

export function useRequestBackfill(collectionId: string) {
  return useMutation({
    mutationFn: (associationId: string) =>
      requestBackfill(collectionId, associationId),
  });
}

/** Poll a requested backfill until it reaches a terminal status. */
export function useBackfill(
  collectionId: string,
  associationId: string,
  backfillId: string | null,
) {
  return useQuery({
    queryKey: associationKeys.backfill(
      collectionId,
      associationId,
      backfillId ?? "none",
    ),
    queryFn: () => getBackfill(collectionId, associationId, backfillId as string),
    enabled: !!backfillId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "completed" || status === "failed" ? false : 3_000;
    },
  });
}
