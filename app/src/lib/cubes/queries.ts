/**
 * Client fetchers + hooks for the virtual cube sink (Z-8b): GET/PUT/PATCH
 * /api/collections/[id]/cube-sink and GET /api/associations/reference-sources.
 * Server modules are imported type-only (`storage.ts` touches the DB).
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import type { z } from "zod";
import { cubeSinkKeys } from "@/lib/query/keys";
import type { cubeSinkPutSchema } from "./schemas";
import type { CubeSourceRefusalCode } from "./status";
import type {
  ApiCubeSink,
  CubeLedgerSummary,
  ReferenceSourceCollection,
} from "./storage";

export interface CubeSinkView {
  sink: ApiCubeSink;
  ledger: CubeLedgerSummary;
}

export interface ReferenceSourceOption extends ReferenceSourceCollection {
  refusal: CubeSourceRefusalCode | null;
}

export type CubeSinkPutBody = z.input<typeof cubeSinkPutSchema>;

export interface CubeSinkIssue {
  path: (string | number)[];
  message: string;
}

export class CubeSinkApiError extends Error {
  status: number;
  code?: string;
  details?: CubeSinkIssue[];
  constructor(
    message: string,
    status: number,
    code?: string,
    details?: CubeSinkIssue[],
  ) {
    super(message);
    this.name = "CubeSinkApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function cubeFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    throw new CubeSinkApiError(
      (typeof body.error === "string" && body.error) ||
        `Request failed: ${res.status}`,
      res.status,
      typeof body.code === "string" ? body.code : undefined,
      Array.isArray(body.details) ? (body.details as CubeSinkIssue[]) : undefined,
    );
  }
  return res.json() as Promise<T>;
}

const sinkUrl = (collectionId: string) =>
  `/api/collections/${encodeURIComponent(collectionId)}/cube-sink`;

/** A 404 is "no sink yet": the card is mounted only for a caller who manages
 * the collection, so there is nothing else a 404 can mean. */
export function useCubeSink(
  collectionId: string,
): UseQueryResult<CubeSinkView | null> {
  return useQuery({
    queryKey: cubeSinkKeys.detail(collectionId),
    queryFn: async () => {
      try {
        return await cubeFetch<CubeSinkView>(sinkUrl(collectionId));
      } catch (err) {
        if (err instanceof CubeSinkApiError && err.status === 404) return null;
        throw err;
      }
    },
    // Faster while waiting for the first append (the card's lock and the
    // write refusal depend on it).
    refetchInterval: (query) =>
      query.state.data && !query.state.data.sink.last_snapshot_id
        ? 15_000
        : 60_000,
  });
}

export function useReferenceSources(
  enabled: boolean,
): UseQueryResult<ReferenceSourceOption[]> {
  return useQuery({
    queryKey: cubeSinkKeys.referenceSources(),
    enabled,
    queryFn: async () =>
      (
        await cubeFetch<{ collections: ReferenceSourceOption[] }>(
          "/api/associations/reference-sources",
        )
      ).collections,
  });
}

export function usePutCubeSink(
  collectionId: string,
): UseMutationResult<{ sink: ApiCubeSink }, CubeSinkApiError, CubeSinkPutBody> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CubeSinkPutBody) =>
      cubeFetch<{ sink: ApiCubeSink }>(sinkUrl(collectionId), {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    // Settled, not success: a 409 must reload the view.
    onSettled: () =>
      qc.invalidateQueries({ queryKey: cubeSinkKeys.detail(collectionId) }),
  });
}

export function usePatchCubeSink(
  collectionId: string,
): UseMutationResult<
  { sink: ApiCubeSink },
  CubeSinkApiError,
  { enabled: boolean }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { enabled: boolean }) =>
      cubeFetch<{ sink: ApiCubeSink }>(sinkUrl(collectionId), {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSettled: () =>
      qc.invalidateQueries({ queryKey: cubeSinkKeys.detail(collectionId) }),
  });
}
