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
  type ImageListQuery,
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

export function useImages(filters: ImageListQuery = {}) {
  return useQuery({
    queryKey: imageKeys.list(filters),
    queryFn: () => listImages(filters),
    refetchInterval: (query) => (hasScanInFlight(query.state.data?.images) ? LIST_POLL_MS : false),
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
    refetchInterval: (query) => (isScanTerminal(query.state.data?.scan.status) ? false : SCAN_POLL_MS),
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
