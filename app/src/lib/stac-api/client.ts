import { $builtInCatalog, $catalogs } from "@/stores/catalogStore";
import type { StacCatalog } from "@/stores/catalogStore";
import { StacApiError } from "./types";

interface FetchOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  endpointUrl?: string;
}

function getCatalogForUrl(url: string): StacCatalog | undefined {
  const normalized = url.replace(/\/+$/, "");
  return $catalogs.get().find((c) => normalized.startsWith(c.url.replace(/\/+$/, "")));
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * STAC's item search is a READ that speaks POST, so method alone cannot tell a
 * mutation from a query. Every other POST/PUT/PATCH/DELETE on a STAC API is a
 * Transaction-extension write.
 */
function isReadShapedPost(method: string, path: string): boolean {
  return (
    method === "POST" && (path === "/search" || path.endsWith("/search"))
  );
}

export async function stacFetch<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { method = "GET", body, signal, endpointUrl } = options;
  // Resolve the request's catalog ONCE; base URL, proxy routing, and the BFF
  // branch all derive from it. With no explicit endpoint the request is a
  // platform request, so it falls back to the BUILT-IN catalog — never to a
  // browse selection (UI-10): an implicit inherit is how a product write could
  // silently land in someone else's catalog, bypassing the ADR 0008 BFF.
  const catalog = endpointUrl
    ? getCatalogForUrl(endpointUrl)
    : ($builtInCatalog.get() ?? undefined);
  if (!endpointUrl && !catalog) {
    throw new StacApiError("No built-in STAC catalog configured", 0);
  }
  const upperMethod = method.toUpperCase();
  const isWrite =
    WRITE_METHODS.has(upperMethod) && !isReadShapedPost(upperMethod, path);

  // UI-10 / I-89: the app writes to the built-in catalog and nowhere else.
  // Every external catalog is browsed read-only, and a write aimed at one
  // would leave the browser without passing the ADR 0008 BFF — no RBAC check,
  // no audit row, no server-injected token. The five write-bearing components
  // all read `$builtInCatalog`, so nothing reaches this today; the guard is
  // here so the rule survives the next caller who passes an explicit
  // `endpointUrl` without knowing about it. Refuse loudly rather than send it.
  if (isWrite && catalog?.builtIn !== true) {
    throw new StacApiError(
      `Refusing to ${upperMethod} ${path}: writes are only allowed against the built-in catalog. External catalogs are read-only (UI-10).`,
      0,
    );
  }

  const baseUrl = (endpointUrl ?? catalog!.url).replace(/\/+$/, "");
  const targetUrl = `${baseUrl}${path}`;

  const headers: Record<string, string> = {
    Accept: "application/json",
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let fetchUrl: string;
  if (isWrite) {
    // ADR 0008: built-in-catalog writes go through the app BFF route
    // (`/api/catalog/*`), which injects the session access token server-side —
    // the browser never holds a bearer token, and the write is RBAC-gated and
    // audited. Unconditional (dev pass-through included) so the dev and
    // auth-enforced paths exercise the same code. Reads — including the
    // read-shaped POST /search — keep their direct//api/proxy paths.
    // The guard above has already established the catalog is the built-in one.
    fetchUrl = `/api/catalog${path}`;
  } else if (catalog?.proxy === true) {
    fetchUrl = "/api/proxy";
    headers["X-Proxy-Target"] = targetUrl;
    headers["X-Proxy-Endpoint"] = catalog.url;
  } else {
    fetchUrl = targetUrl;
  }

  const response = await fetch(fetchUrl, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!response.ok) {
    let detail: string | undefined;
    try {
      const errorBody = await response.json();
      detail = errorBody.detail ?? errorBody.message ?? JSON.stringify(errorBody);
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    throw new StacApiError(
      `STAC API error: ${response.status} ${response.statusText}`,
      response.status,
      detail,
    );
  }

  if (response.status === 204) return undefined as T;
  return response.json();
}
