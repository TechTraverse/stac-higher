/**
 * Normalizing what a person TYPES into C-1's stored reference grammar
 * (C-3, container-images spec §9.1: `POST /api/images` "normalizes the
 * reference"). The stored form is `lib/images/reference.ts`'s. This module
 * only produces it.
 *
 * Rules (Docker's, the ones people already know):
 *   - The tag follows the last `:` AFTER the last `/`. An earlier `:` is a
 *     registry port (`localhost:5000/team/img`).
 *   - The first component is a registry host when it has a `.` or `:` or is
 *     `localhost`. Otherwise the host is Docker Hub, and a single-component
 *     Hub name lives under `library/` (`python` -> `docker.io/library/python`).
 *   - Host and repository are lowercased. Tags are case-sensitive and kept
 *     as typed.
 *   - A digest (`@sha256:…`) is refused: the scanner resolves a TAG and pins
 *     the digest it scanned (spec §6.3). Adding by digest is not in v1.
 */
import { isImageReference } from "./reference";

/** An OCI tag: 1–128 characters, first one a word character. */
export const IMAGE_TAG_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
export const DEFAULT_IMAGE_TAG = "latest";

const DOCKER_HUB_ALIASES = new Set([
  "docker.io",
  "index.docker.io",
  "registry-1.docker.io",
  "registry.hub.docker.com",
]);

/** A registry host as the platform stores it: lowercase, Docker Hub's
 * aliases folded to `docker.io`. Used for references AND for a `registry`
 * connection's `config.host`, so the two compare equal. */
export function canonicalRegistryHost(host: string): string {
  const lower = host.trim().toLowerCase();
  return DOCKER_HUB_ALIASES.has(lower) ? "docker.io" : lower;
}

export type NormalizedImage =
  | { ok: true; reference: string; tag: string }
  | { ok: false; error: string };

export function normalizeImageInput(
  typed: string,
  explicitTag?: string | null,
): NormalizedImage {
  const raw = typed.trim();
  if (raw.length === 0) {
    return { ok: false, error: "Enter an image reference, e.g. ghcr.io/org/tool:1.2" };
  }
  if (/\s/.test(raw)) return { ok: false, error: "An image reference has no spaces" };
  if (raw.includes("://")) {
    return {
      ok: false,
      error: "Leave out the scheme: ghcr.io/org/tool, not https://ghcr.io/org/tool",
    };
  }
  if (raw.includes("@")) {
    return {
      ok: false,
      error:
        "Add an image by tag, not by digest: the scan resolves the tag and pins the digest it scanned",
    };
  }

  const lastSlash = raw.lastIndexOf("/");
  const lastColon = raw.lastIndexOf(":");
  let name = raw;
  let typedTag: string | null = null;
  if (lastColon > lastSlash) {
    name = raw.slice(0, lastColon);
    typedTag = raw.slice(lastColon + 1);
  }
  const wanted = explicitTag?.trim() ? explicitTag.trim() : null;
  if (typedTag !== null && wanted !== null && typedTag !== wanted) {
    return {
      ok: false,
      error: `The reference names tag "${typedTag}" but the tag field says "${wanted}"; give one`,
    };
  }
  const tag = typedTag ?? wanted ?? DEFAULT_IMAGE_TAG;
  if (!IMAGE_TAG_RE.test(tag)) {
    return {
      ok: false,
      error: `"${tag}" is not a valid tag (letters, digits, _ . - and at most 128 characters)`,
    };
  }

  const parts = name.split("/");
  const first = parts[0];
  const hasHost =
    parts.length > 1 &&
    (first.includes(".") || first.includes(":") || first.toLowerCase() === "localhost");
  const host = hasHost ? canonicalRegistryHost(first) : "docker.io";
  let path = (hasHost ? parts.slice(1) : parts).map((part) => part.toLowerCase());
  if (host === "docker.io" && path.length === 1) path = ["library", ...path];
  const reference = [host, ...path].join("/");
  if (!isImageReference(reference)) {
    return {
      ok: false,
      error: `"${raw}" is not an image reference this platform can store (read as ${reference})`,
    };
  }
  return { ok: true, reference, tag };
}
