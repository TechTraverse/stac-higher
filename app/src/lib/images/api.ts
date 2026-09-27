/**
 * Client functions for `/api/images` and the image policy (C-3,
 * container-images spec §9). Same-origin JSON. Errors surface the server's
 * `{error, code}` as an `ImageApiError` carrying `.status` and `.code` (the
 * processes/connections `api.ts` contract).
 *
 * Nothing here touches a registry or a scanner. Adding an image writes a
 * request row and returns ids to poll (ADR 0004); the pipeline makes the
 * scan real (C-2).
 */
import type { ImageAdd, ImageException } from "./schemas";
import type { ImageScanKind, ImageStatus } from "./status";
import type { Image, ImageScan, ImageUser, PublicImagePolicy } from "./types";

export class ImageApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ImageApiError";
    this.status = status;
    this.code = code;
  }
}

async function apiFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      (typeof body.error === "string" && body.error) || `Request failed: ${res.status}`;
    const code = typeof body.code === "string" ? body.code : undefined;
    throw new ImageApiError(message, res.status, code);
  }
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;

export interface ImageListQuery {
  status?: ImageStatus;
  q?: string;
  in_use?: boolean;
}

export interface ImageList {
  images: Image[];
  /** null when the deployment's policy cannot be read (staleness unknown). */
  scan_window_days: number | null;
}

export function imageListSearch(filters: ImageListQuery): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  if (filters.in_use !== undefined) params.set("in_use", String(filters.in_use));
  const search = params.toString();
  return search ? `?${search}` : "";
}

export async function listImages(filters: ImageListQuery = {}): Promise<ImageList> {
  return apiFetch<ImageList>(`/api/images${imageListSearch(filters)}`);
}

export interface ImageDetail {
  image: Image;
  scans: ImageScan[];
  /** Processes in the caller's groups whose current revision uses the image. */
  in_use_by: ImageUser[];
  /** Processes in other groups using it (named only to their own members). */
  in_use_elsewhere: number;
}

export async function getImage(id: string): Promise<ImageDetail> {
  return apiFetch<ImageDetail>(`/api/images/${enc(id)}`);
}

export interface AddedImage {
  id: string;
  image_id: string;
  scan_id: string;
  deduplicated: boolean;
  reference: string;
  tag: string;
}

export async function addImage(input: ImageAdd): Promise<AddedImage> {
  // A blank tag is "no tag", not an empty string on the wire: the server
  // folds it to undefined too, but the client shouldn't send "" at all.
  const body: ImageAdd = input.tag ? input : { ...input, tag: undefined };
  return apiFetch<AddedImage>("/api/images", { method: "POST", body: JSON.stringify(body) });
}

export interface ImageScanPoll {
  scan: ImageScan;
  /** The image the scan belongs to NOW (authoritative after the drain's dedup). */
  image: Image | null;
}

export async function getImageScan(imageId: string, scanId: string): Promise<ImageScanPoll> {
  return apiFetch<ImageScanPoll>(`/api/images/${enc(imageId)}/scans/${enc(scanId)}`);
}

export interface RescanRequested {
  image_id: string;
  scan_id: string;
  kind: ImageScanKind;
}

export async function requestRescan(id: string): Promise<RescanRequested> {
  return apiFetch<RescanRequested>(`/api/images/${enc(id)}/rescan`, { method: "POST" });
}

export async function getImagePolicy(): Promise<PublicImagePolicy> {
  return apiFetch<PublicImagePolicy>("/api/processes/image-policy");
}

/** Admin (spec §4.4): grant an expiring exception, or replace the one an
 * approved image carries. The route re-checks the role and the policy's
 * `exception_max_days`. */
export async function grantImageException(
  id: string,
  body: ImageException,
): Promise<{ image: Image }> {
  return apiFetch<{ image: Image }>(`/api/images/${enc(id)}/exception`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Admin (spec §4.3): any status -> `revoked`, terminal. */
export async function revokeImage(id: string): Promise<{ image: Image }> {
  return apiFetch<{ image: Image }>(`/api/images/${enc(id)}/revoke`, { method: "POST" });
}
