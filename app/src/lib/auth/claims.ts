/**
 * Claims-mapping layer (ROADMAP §5.5) — the IdP compatibility contract.
 *
 * A per-deployment config maps arbitrary claim paths in the IdP's token
 * payload to the canonical `{ sub, email, name, groups, roles }` model.
 * IdPs are NOT required to emit our token shape; this module is the only
 * place raw claims are interpreted. The app trusts only the mapped output.
 *
 * Config sources (first match wins):
 *   1. `AUTH_CLAIMS_MAPPING`      — inline JSON in the env
 *   2. `AUTH_CLAIMS_MAPPING_FILE` — path to a JSON file
 *   3. built-in Keycloak default  — `groups` claim + `realm_access.roles`
 */
import { readFileSync } from "node:fs";
import { z } from "zod";
import {
  isCanonicalRole,
  type CanonicalIdentity,
  type CanonicalRole,
} from "./types";

/** A claim path is dot-separated (`realm_access.roles`). Path segments may
 * contain any character except `.` — Cognito's `cognito:groups` works as-is.
 * A field may list several paths; the first one that resolves wins. */
export type ClaimPath = string | string[];

export interface ClaimsMappingConfig {
  /** Path(s) to the stable subject id. Default `"sub"`. */
  sub?: ClaimPath;
  /** Path(s) to the email. Default `"email"`. */
  email?: ClaimPath;
  /** Path(s) to the display name. Default `["name","preferred_username","email"]`. */
  name?: ClaimPath;
  /** Path(s) to the group list (array of strings, or a single string). */
  groups?: ClaimPath;
  /** Path(s) to the raw role list. */
  roles?: ClaimPath;
  /** Optional raw-group → canonical-group rename (e.g. Entra GUIDs → names).
   * Unmapped groups pass through unchanged. */
  groupMap?: Record<string, string>;
  /** Optional raw-role → canonical-role rename (e.g. `"Operators"` →
   * `"operator"`). After mapping, anything outside member/operator/admin is
   * dropped. */
  roleMap?: Record<string, string>;
}

/** Keycloak default: matches infra/keycloak/realm-stac-higher.json (groups
 * protocol mapper on the `stac-higher-app` client + realm roles). */
export const DEFAULT_CLAIMS_MAPPING: ClaimsMappingConfig = {
  sub: "sub",
  email: "email",
  name: ["name", "preferred_username", "email"],
  groups: "groups",
  roles: "realm_access.roles",
};

const claimPathSchema = z.union([z.string(), z.array(z.string())]);

const claimsMappingSchema = z.object({
  sub: claimPathSchema.optional(),
  email: claimPathSchema.optional(),
  name: claimPathSchema.optional(),
  groups: claimPathSchema.optional(),
  roles: claimPathSchema.optional(),
  groupMap: z.record(z.string(), z.string()).optional(),
  roleMap: z.record(z.string(), z.string()).optional(),
});

export class ClaimsMappingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClaimsMappingError";
  }
}

/** Resolve a dot-separated path against a claims payload. */
export function getClaimAtPath(payload: unknown, path: string): unknown {
  let current: unknown = payload;
  for (const segment of path.split(".")) {
    if (current === null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

function firstAtPaths(payload: unknown, paths: ClaimPath | undefined): unknown {
  if (paths === undefined) return undefined;
  const list = Array.isArray(paths) ? paths : [paths];
  for (const path of list) {
    const value = getClaimAtPath(payload, path);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function asStringList(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string");
}

/** Keycloak's group-membership mapper can emit full paths (`/earth-observation`). */
function normalizeGroup(raw: string): string {
  return raw.startsWith("/") ? raw.slice(1) : raw;
}

/**
 * Map a raw token payload to the canonical identity. Throws
 * `ClaimsMappingError` when no subject can be resolved — callers must treat
 * that as an unauthenticated request, never fall back to raw claims.
 */
export function mapClaims(
  payload: Record<string, unknown>,
  config: ClaimsMappingConfig = DEFAULT_CLAIMS_MAPPING,
): CanonicalIdentity {
  const merged: ClaimsMappingConfig = { ...DEFAULT_CLAIMS_MAPPING, ...config };

  const sub = firstAtPaths(payload, merged.sub);
  if (typeof sub !== "string" || sub.length === 0) {
    throw new ClaimsMappingError(
      `No subject claim found at path(s) ${JSON.stringify(merged.sub)}`,
    );
  }

  const email = firstAtPaths(payload, merged.email);
  const name = firstAtPaths(payload, merged.name);

  const groupMap = merged.groupMap ?? {};
  const groups = [
    ...new Set(
      asStringList(firstAtPaths(payload, merged.groups))
        .map(normalizeGroup)
        .map((g) => groupMap[g] ?? g)
        .filter((g) => g.length > 0),
    ),
  ];

  const roleMap = merged.roleMap ?? {};
  const roles = [
    ...new Set(
      asStringList(firstAtPaths(payload, merged.roles))
        .map((r) => roleMap[r] ?? r)
        .filter(isCanonicalRole),
    ),
  ] as CanonicalRole[];

  return {
    sub,
    email: typeof email === "string" ? email : null,
    name: typeof name === "string" ? name : null,
    groups,
    roles,
  };
}

let cachedMapping: { source: string; config: ClaimsMappingConfig } | null =
  null;

/**
 * Load the deployment's claims-mapping config. Invalid config falls back to
 * the Keycloak default with a warning (auth keeps working; roles/groups may
 * be empty, which fails closed for RBAC).
 */
export function loadClaimsMapping(
  env: Record<string, string | undefined> = process.env,
): ClaimsMappingConfig {
  const inline = env.AUTH_CLAIMS_MAPPING;
  const file = env.AUTH_CLAIMS_MAPPING_FILE;
  const source = inline ? `inline:${inline}` : file ? `file:${file}` : "default";

  if (cachedMapping?.source === source) return cachedMapping.config;

  let config = DEFAULT_CLAIMS_MAPPING;
  try {
    if (inline) {
      config = claimsMappingSchema.parse(JSON.parse(inline));
    } else if (file) {
      config = claimsMappingSchema.parse(
        JSON.parse(readFileSync(file, "utf-8")),
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(
      `[auth] Invalid claims-mapping config (${inline ? "AUTH_CLAIMS_MAPPING" : "AUTH_CLAIMS_MAPPING_FILE"}): ${msg} — falling back to the Keycloak default mapping`,
    );
    config = DEFAULT_CLAIMS_MAPPING;
  }

  cachedMapping = { source, config };
  return config;
}
