/**
 * The image reference + digest grammar (C-1, container-images spec §3/§4.1),
 * pinned by `tests/contract-fixtures/image-reference.json` against
 * `pipeline/images/reference.py`.
 *
 * A STORED reference is normalized. It is lowercase, carries an explicit
 * registry host (a dotted name or `localhost`, optional port), has at least
 * one path component, and has no tag and no digest, because the tag is
 * resolved once and the digest is its own field. Normalizing what a person
 * types is C-3's job. This module only says what may be stored.
 */
import { z } from "zod";

const LABEL = "[a-z0-9](?:[a-z0-9-]*[a-z0-9])?";
const HOST = `(?:localhost|${LABEL}(?:\\.${LABEL})+)(?::[0-9]{1,5})?`;
const COMPONENT = "[a-z0-9]+(?:(?:\\.|_|__|-+)[a-z0-9]+)*";

export const IMAGE_REFERENCE_RE = new RegExp(`^${HOST}(?:/${COMPONENT})+$`);
export const IMAGE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
export const IMAGE_REFERENCE_MAX_LENGTH = 255;

export function isImageReference(value: string): boolean {
  return value.length <= IMAGE_REFERENCE_MAX_LENGTH && IMAGE_REFERENCE_RE.test(value);
}

export function isImageDigest(value: string): boolean {
  return IMAGE_DIGEST_RE.test(value);
}

/** The registry host of a normalized reference (its first component). */
export function registryHost(reference: string): string {
  return reference.split("/", 1)[0];
}

export const imageReferenceSchema = z
  .string()
  .refine(
    isImageReference,
    "image.reference must be a normalized repository (registry/namespace/name: lowercase, no tag, no digest)",
  );

export const imageDigestSchema = z
  .string()
  .refine(isImageDigest, "image.digest must be a sha256 manifest digest (sha256:<64 lowercase hex>)");

/** The immutable snapshot a revision carries of a `container_images` row
 * (spec §3). A later rescan, revocation or deletion never rewrites it. */
export const imageSnapshotSchema = z
  .object({
    id: z.string().uuid("image.id must be a container image id"),
    reference: imageReferenceSchema,
    digest: imageDigestSchema,
  })
  .strict();

export type ImageSnapshot = z.infer<typeof imageSnapshotSchema>;
