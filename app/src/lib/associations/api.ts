/**
 * Client functions for the `/api/collections/[id]/connections` surface
 * (Phase 4). All requests are same-origin JSON. Errors surface the guard shape
 * `{error, code}` as an `AssociationApiError` with `.status`/`.code` attached.
 */
import type { Association } from "./types";
import type {
  AssociationCreateInput,
  AssociationUpdateInput,
} from "./schemas";

export class AssociationApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "AssociationApiError";
    this.status = status;
    this.code = code;
  }
}

async function associationFetch<T>(
  collectionId: string,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const base = `/api/collections/${encodeURIComponent(collectionId)}/connections`;
  const res = await fetch(`${base}${path}`, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      (typeof body.error === "string" && body.error) ||
      `Request failed: ${res.status}`;
    const code = typeof body.code === "string" ? body.code : undefined;
    throw new AssociationApiError(message, res.status, code);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function listAssociations(
  collectionId: string,
): Promise<Association[]> {
  const data = await associationFetch<{ associations: Association[] }>(
    collectionId,
    "",
  );
  return data.associations;
}

export async function createAssociation(
  collectionId: string,
  input: AssociationCreateInput,
): Promise<Association> {
  return associationFetch<Association>(collectionId, "", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateAssociation(
  collectionId: string,
  id: string,
  input: AssociationUpdateInput,
): Promise<Association> {
  return associationFetch<Association>(
    collectionId,
    `/${encodeURIComponent(id)}`,
    { method: "PUT", body: JSON.stringify(input) },
  );
}

export async function deleteAssociation(
  collectionId: string,
  id: string,
): Promise<void> {
  await associationFetch<void>(collectionId, `/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
