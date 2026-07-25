/**
 * /api/catalog/[...path] — the BFF for built-in-catalog browser writes
 * (ADR 0008, resolves ISSUES I-50).
 *
 * In auth-enforced mode every catalog transaction at the proxy needs a bearer
 * token, but the browser can't present one: the access token is sealed inside
 * the httpOnly session cookie by design. This route forwards the mutation to
 * the built-in catalog and injects the caller's session access token
 * server-side. The proxy stays the enforcement point — the app only adds the
 * token. In dev-bypass mode there is no token and none is attached; the
 * pass-through proxy accepts the write, so dev and enforced posture exercise
 * the same code path (ADR 0008 decision 5).
 *
 * Scope is deliberately narrow (writes only, transaction endpoints only, the
 * built-in catalog only — the target URL is server-configured, never
 * client-supplied), so this cannot be used as a generic authenticated proxy.
 * Reads keep their existing direct path; external catalogs keep /api/proxy.
 *
 * RBAC + audit: the middleware guard gates these paths (operator+) and writes
 * one audit_log row per mutation — closing the catalog-plane audit gap.
 */
import type { APIRoute } from "astro";
import { getAuthConfig } from "@/lib/auth/config";
import { readSession } from "@/lib/auth/session";
import {
  DEFAULT_MAX_BYTES,
  SafeFetchError,
  errorToResponse,
  safeFetch,
} from "@/lib/http/safe-fetch";

const FORWARDED_RESPONSE_HEADERS = ["content-type", "etag", "last-modified"];

/** Transaction-endpoint shapes the BFF forwards, by method (ADR 0008 scope:
 * writes only). Anything else — /search, arbitrary proxy paths — is a 404. */
const TRANSACTION_PATHS: Record<string, RegExp[]> = {
  POST: [/^collections$/, /^collections\/[^/]+\/items$/],
  PUT: [/^collections\/[^/]+$/, /^collections\/[^/]+\/items\/[^/]+$/],
  PATCH: [/^collections\/[^/]+$/, /^collections\/[^/]+\/items\/[^/]+$/],
  DELETE: [/^collections\/[^/]+$/, /^collections\/[^/]+\/items\/[^/]+$/],
};

export function builtinCatalogUrl(env: Record<string, string | undefined> = process.env): string {
  return (
    env.BUILTIN_CATALOG_URL?.trim() ||
    env.PUBLIC_BUILTIN_CATALOG_URL?.trim() ||
    "http://localhost:8081"
  ).replace(/\/+$/, "");
}

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const handler: APIRoute = async ({ params, request, cookies }) => {
  const path = (params.path ?? "").replace(/\/+$/, "");
  const shapes = TRANSACTION_PATHS[request.method.toUpperCase()];
  if (!shapes || !shapes.some((re) => re.test(path))) {
    return jsonError("Not a built-in-catalog transaction endpoint", 404);
  }

  // Token injection (ADR 0008): oidc sessions carry the access token in the
  // sealed cookie — middleware has already refreshed it if it was near
  // expiry. Bypass mode has no token; the write goes out bare and the
  // pass-through proxy accepts it. (The guard has already 401'd anonymous
  // oidc callers before this handler runs.)
  const headers: Record<string, string> = { accept: "application/json" };
  const cfg = getAuthConfig();
  if (cfg.mode === "oidc") {
    const session = cfg.sessionSecret
      ? await readSession(cookies, cfg.sessionSecret)
      : null;
    if (!session) {
      return jsonError("Authentication required for catalog writes", 401);
    }
    headers.authorization = `Bearer ${session.accessToken}`;
  }

  const contentType = request.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;

  let body: ArrayBuffer | undefined;
  if (request.method.toUpperCase() !== "DELETE") {
    body = await request.arrayBuffer();
    if (body.byteLength > DEFAULT_MAX_BYTES) {
      return jsonError(`Request body exceeds ${DEFAULT_MAX_BYTES} bytes`, 413);
    }
  }

  let result;
  try {
    result = await safeFetch(`${builtinCatalogUrl()}/${path}`, {
      method: request.method,
      headers,
      body,
    });
  } catch (err) {
    if (err instanceof SafeFetchError) return errorToResponse(err);
    const msg = err instanceof Error ? err.message : "Unknown error";
    return jsonError(`Catalog request failed: ${msg}`, 502);
  }

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = result.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(result.body, {
    status: result.status,
    headers: responseHeaders,
  });
};

export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
