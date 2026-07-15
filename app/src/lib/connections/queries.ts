/**
 * TanStack Query hooks for connections. Mutations invalidate by the
 * `connectionKeys` prefix so lists and details refresh together (e.g. a
 * host-key reset flips both `status` and the fingerprint on the row).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { connectionKeys } from "@/lib/query/keys";
import {
  createConnection,
  deleteConnection,
  getConnection,
  listConnections,
  resetHostKey,
  updateConnection,
} from "./api";
import type {
  ConnectionCreateInput,
  ConnectionUpdateInput,
} from "./schemas";

export function useConnections() {
  return useQuery({
    queryKey: connectionKeys.list(),
    queryFn: listConnections,
  });
}

export function useConnection(id: string) {
  return useQuery({
    queryKey: connectionKeys.detail(id),
    queryFn: () => getConnection(id),
    enabled: !!id,
  });
}

export function useCreateConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ConnectionCreateInput) => createConnection(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: connectionKeys.all() });
    },
  });
}

export function useUpdateConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: ConnectionUpdateInput }) =>
      updateConnection(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: connectionKeys.all() });
    },
  });
}

export function useDeleteConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConnection(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: connectionKeys.all() });
    },
  });
}

export function useResetHostKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => resetHostKey(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: connectionKeys.all() });
    },
  });
}
