/**
 * Client-visible image types shared across the image routes and the C-3
 * dashboard (container-images spec §9.1). Declared once here so the server
 * route and the client fetch layer both import the same alias instead of
 * each declaring their own copy.
 */
import type { ImagePolicy } from "./policy";

/** The policy document without the scanner-only `scan_limits` (pipeline
 * resource caps a person never needs to see). */
export type PublicImagePolicy = Omit<ImagePolicy, "scan_limits">;
