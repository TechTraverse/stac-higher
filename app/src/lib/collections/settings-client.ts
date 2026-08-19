/**
 * Client fetchers + hooks for /api/collections/[id]/settings (M2-E).
 * The server shape (`CollectionSettings`) is imported type-only.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { collectionSettingsKeys } from "@/lib/query/keys";
import type { CollectionSettings } from "./settings";
import type { CollectionSettingsUpdatePayload } from "./settings-schemas";

export type { CollectionSettings };

export class SettingsApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "SettingsApiError";
    this.status = status;
  }
}

async function settingsFetch<T>(
  collectionId: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(
    `/api/collections/${encodeURIComponent(collectionId)}/settings`,
    {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      (typeof body.error === "string" && body.error) ||
      `Request failed: ${res.status}`;
    throw new SettingsApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

export function useCollectionSettings(collectionId: string) {
  return useQuery({
    queryKey: collectionSettingsKeys.detail(collectionId),
    queryFn: () => settingsFetch<CollectionSettings>(collectionId),
  });
}

export function useUpdateCollectionSettings(collectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CollectionSettingsUpdatePayload) =>
      settingsFetch<CollectionSettings>(collectionId, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () =>
      qc.invalidateQueries({
        queryKey: collectionSettingsKeys.detail(collectionId),
      }),
  });
}
