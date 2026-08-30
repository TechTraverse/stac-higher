/**
 * Synchronous pre-validation for brokered push writes (Phase 7 spec §4.3).
 *
 * Tier 0 of the §6.3 failure semantics: grammar, session-binding, collection,
 * and gross-shape failures become 4xxs on the BFF route BEFORE anything
 * reaches pgstac — nothing created, nothing destroyed, no ledger churn. The
 * async machinery (finalize, §6) handles only what is unknowable at POST time
 * (missing bytes, checksum mismatch, deep stac-pydantic validation).
 *
 * Deliberately NOT full STAC validation: the light Zod shape below checks
 * type/id/geometry/properties presence plus the required top-level
 * `collection`; stac-pydantic at finalize remains the single authoritative
 * validator (§6.2) — duplicating it in TypeScript would fork the gate.
 *
 * Session-rule rejections reuse the pinned `PUSH_REJECTION_REASONS` strings
 * (uploads/schemas.ts) as `{ error, code }` codes, so a client sees the same
 * machine-readable vocabulary synchronously that the ledger would show it
 * asynchronously.
 */
import { z } from "zod";
import {
  isStagedHref,
  parseStagedHref,
  StorageKeyError,
} from "@/lib/storage/keys";
import {
  TERMINAL_STAGED_UPLOAD_STATUSES,
  type PushRejectionReason,
} from "@/lib/uploads/schemas";
import { getStagedUpload } from "@/lib/uploads/storage";

/** A synchronous rejection: the route turns this into `jsonResponse`. */
export interface PushRejection {
  ok: false;
  status: number;
  error: string;
  /** Present for §4.2 session-rule failures — a pinned rejection reason. */
  code?: PushRejectionReason;
}

export interface PushAdmission {
  ok: true;
  /** The single upload session every staged href in the body references. */
  uploadId: string;
}

export type PreValidationResult = PushAdmission | PushRejection;

function reject(
  status: number,
  error: string,
  code?: PushRejectionReason,
): PushRejection {
  return code === undefined
    ? { ok: false, status, error }
    : { ok: false, status, error, code };
}

/**
 * Collect every `staging://`-prefixed string value in a JSON document,
 * recursively. The legitimate location is item asset hrefs, but detection is
 * deliberately whole-document (the R3 "merge would involve" test and the R2
 * never-snapshot guard both want the conservative answer, and a staged href
 * smuggled anywhere else should fail pre-validation loudly rather than land
 * in the catalog as junk).
 */
export function collectStagedHrefs(value: unknown): string[] {
  const found: string[] = [];
  const walk = (node: unknown): void => {
    if (isStagedHref(node)) {
      found.push(node);
      return;
    }
    if (Array.isArray(node)) {
      for (const entry of node) walk(entry);
      return;
    }
    if (node !== null && typeof node === "object") {
      for (const entry of Object.values(node)) walk(entry);
    }
  };
  walk(value);
  return found;
}

/** Whether a document involves staged hrefs at all (R2/R3 detection). */
export function hasStagedHrefs(value: unknown): boolean {
  return collectStagedHrefs(value).length > 0;
}

/**
 * The light structural check (§4.3): presence, not STAC semantics. `geometry`
 * must be present but may be null (a valid STAC item shape); unknown keys
 * pass through untouched — the route forwards the ORIGINAL bytes.
 */
const lightItemSchema = z.object({
  type: z.literal("Feature"),
  id: z.string().min(1),
  collection: z.string().min(1),
  geometry: z.looseObject({}).nullable(),
  properties: z.looseObject({}),
});

export interface StagedWriteTarget {
  /** Upper-cased HTTP method of the transaction. */
  method: string;
  /** From `matchCatalogTransaction` — item vs collection transaction. */
  resourceType: "catalog_collection" | "catalog_item";
  /** The collection id from the path. */
  pathCollection: string | null;
  /** The item id from the path (PUT/PATCH/DELETE item shapes); null on POST. */
  pathItemId: string | null;
}

/**
 * Pre-validate a request body containing `staging://` hrefs against the §4.2
 * admission rules. Call ONLY when `hasStagedHrefs(doc)` is true — a body with
 * no staged hrefs needs none of this and must not pay the DB lookup.
 *
 * Order: shape of the target (staged hrefs are item-write-only, PUT/POST
 * only — R3), href grammar, single-session rule, light item shape (incl. the
 * required top-level `collection`, matched against the path), then the ledger
 * lookup (exists / not terminal / minted for this collection / not bound to
 * another item).
 */
export async function preValidateStagedWrite(
  doc: unknown,
  target: StagedWriteTarget,
): Promise<PreValidationResult> {
  // R3: PATCH merge semantics would make both the snapshot and the finalize
  // target ambiguous — staged-asset updates require PUT.
  if (target.method === "PATCH") {
    return reject(
      400,
      "Staged-asset updates are not supported via PATCH on the brokered path — send a full item PUT instead",
    );
  }
  if (target.resourceType !== "catalog_item" || target.pathCollection === null) {
    return reject(
      400,
      "staging:// hrefs are only valid in item writes (POST /collections/{id}/items or PUT .../items/{id})",
    );
  }

  const hrefs = collectStagedHrefs(doc);
  const uploadIds = new Set<string>();
  for (const href of hrefs) {
    try {
      uploadIds.add(parseStagedHref(href).uploadId);
    } catch (err) {
      if (err instanceof StorageKeyError) {
        return reject(400, `Invalid staged href ${JSON.stringify(href)}: ${err.message}`);
      }
      throw err;
    }
  }
  if (uploadIds.size > 1) {
    return reject(
      400,
      "Item references more than one upload session — strictly one session per item (§4.2)",
      "multi_session",
    );
  }
  const uploadId = [...uploadIds][0];

  const parsed = lightItemSchema.safeParse(doc);
  if (!parsed.success) {
    return reject(
      400,
      `Item failed the structural pre-check (type/id/collection/geometry/properties): ${parsed.error.issues
        .map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`)
        .join("; ")}`,
    );
  }
  if (parsed.data.collection !== target.pathCollection) {
    return reject(
      400,
      `Item's top-level collection ("${parsed.data.collection}") must match the path collection ("${target.pathCollection}")`,
    );
  }
  // The item id this write lands on: the path id for PUT, the body id for POST.
  const itemId = target.pathItemId ?? parsed.data.id;

  const session = await getStagedUpload(uploadId);
  if (!session) {
    return reject(
      400,
      `Staged hrefs reference unknown upload session ${uploadId}`,
      "unknown_session",
    );
  }
  if (
    (TERMINAL_STAGED_UPLOAD_STATUSES as readonly string[]).includes(
      session.status,
    )
  ) {
    return reject(
      409,
      `Upload session ${uploadId} is already ${session.status} — mint a new session and re-push`,
      "session_terminal",
    );
  }
  if (session.collectionId !== target.pathCollection) {
    return reject(
      403,
      `Upload session ${uploadId} was minted for collection "${session.collectionId}", not "${target.pathCollection}"`,
      "wrong_collection",
    );
  }
  if (session.itemId !== null && session.itemId !== itemId) {
    return reject(
      409,
      `Upload session ${uploadId} is already bound to item "${session.itemId}"`,
      "bound_to_other_item",
    );
  }

  return { ok: true, uploadId };
}
