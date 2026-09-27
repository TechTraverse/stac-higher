/**
 * Client-visible image types shared across the image routes and the C-3
 * dashboard (container-images spec §9.1). Declared once here so the server
 * route and the client fetch layer both import the same alias instead of
 * each declaring their own copy.
 *
 * The `Image`/`ImageScan`/`ImageUser`/`ImageRegistryConnection` re-exports
 * are type-only, so nothing from `storage.ts` (the pg client) reaches the
 * browser bundle: the connections/processes `types.ts` pattern. A registry
 * connection appears only as `{id, name, group_id, deleted}`, never with its
 * credentials.
 */
import type { ImagePolicy } from "./policy";

export type {
  ApiImage as Image,
  ApiImageScan as ImageScan,
  ApiImageUser as ImageUser,
  ApiImageRegistryConnection as ImageRegistryConnection,
} from "./storage";

/** The policy document without the scanner-only `scan_limits` (pipeline
 * resource caps a person never needs to see). */
export type PublicImagePolicy = Omit<ImagePolicy, "scan_limits">;
