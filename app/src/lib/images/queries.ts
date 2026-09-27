/**
 * TanStack Query hooks for the image registry (C-3). The app never learns a
 * scan result directly (ADR 0004), so both the scan poll and the list POLL
 * while something is in flight, and stop when nothing is.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { imageKeys } from "@/lib/query/keys";
import {
  addImage,
  getImage,
  getImagePolicy,
  getImageScan,
  listImages,
  requestRescan,
  type ImageList,
  type ImageListQuery,
  type ImageScanPoll,
} from "./api";
import type { ImageAdd } from "./schemas";
import type { Image, ImageScan } from "./types";

const SCAN_POLL_MS = 2_000;
const LIST_POLL_MS = 10_000;

export function isScanTerminal(status: ImageScan["status"] | undefined): boolean {
  return status === "done" || status === "failed";
}

export function hasScanInFlight(images: readonly Image[] | undefined): boolean {
  return (images ?? []).some((image) => image.status === "pending" || image.status === "scanning");
}

/** The bit of `Query.state` a `refetchInterval` callback needs, factored out
 * so the polling decision is a plain function `useImages`/`useImageScan` can
 * both use and this file's tests can call directly. */
interface PollState<TData> {
  status: string;
  data: TData | undefined;
}

/** Stop polling on a persistent error (query.state.status === "error"): the
 * global `retry` already bounded retries within one fetch, but a failed
 * fetch leaves `data` at its last (possibly `undefined`, possibly stale)
 * value, and `hasScanInFlight`/`isScanTerminal` alone would poll it forever. */
export function listRefetchInterval(state: PollState<ImageList>): number | false {
  if (state.status === "error") return false;
  return hasScanInFlight(state.data?.images) ? LIST_POLL_MS : false;
}

export function scanRefetchInterval(state: PollState<ImageScanPoll>): number | false {
  if (state.status === "error") return false;
  return isScanTerminal(state.data?.scan.status) ? false : SCAN_POLL_MS;
}

/**
 * `options.retry` lets a caller opt out of the default retry (C-3's home
 * overview: a member without a session gets a 401 from the platform-wide
 * images read, and retrying that is pointless — see `DashboardPage.tsx`).
 * Omitted entirely, the global default (`query/client.ts`) applies.
 */
export function useImages(filters: ImageListQuery = {}, options: { retry?: boolean } = {}) {
  return useQuery({
    queryKey: imageKeys.list(filters),
    queryFn: () => listImages(filters),
    refetchInterval: (query) => listRefetchInterval(query.state),
    ...options,
  });
}

export function useImage(id: string | null) {
  return useQuery({
    queryKey: imageKeys.detail(id ?? "none"),
    queryFn: () => getImage(id as string),
    enabled: !!id,
  });
}

/** Poll one scan by the ids the 202 handed out, until it is done or failed. */
export function useImageScan(imageId: string | null, scanId: string | null) {
  return useQuery({
    queryKey: imageKeys.scan(imageId ?? "none", scanId ?? "none"),
    queryFn: () => getImageScan(imageId as string, scanId as string),
    enabled: !!imageId && !!scanId,
    refetchInterval: (query) => scanRefetchInterval(query.state),
  });
}

export function useImagePolicy() {
  return useQuery({
    queryKey: imageKeys.policy(),
    queryFn: getImagePolicy,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

function useImageMutation<TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => qc.invalidateQueries({ queryKey: imageKeys.all() }),
  });
}

export function useAddImage() {
  return useImageMutation((input: ImageAdd) => addImage(input));
}

export function useRescanImage() {
  return useImageMutation((id: string) => requestRescan(id));
}
