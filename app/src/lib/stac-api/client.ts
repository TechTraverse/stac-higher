import { $activeCatalog, $catalogs } from "@/stores/catalogStore";
import type { StacCatalog } from "@/stores/catalogStore";
import { StacApiError } from "./types";

interface FetchOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  endpointUrl?: string;
}

function getBaseUrl(overrideUrl?: string): string {
  if (overrideUrl) return overrideUrl.replace(/\/+$/, "");
  const catalog = $activeCatalog.get();
  if (!catalog) throw new StacApiError("No active STAC catalog configured", 0);
  return catalog.url.replace(/\/+$/, "");
}

function getCatalogForUrl(url: string): StacCatalog | undefined {
  const normalized = url.replace(/\/+$/, "");
  return $catalogs.get().find((c) => normalized.startsWith(c.url.replace(/\/+$/, "")));
}

function resolveCatalog(endpointUrl?: string): StacCatalog | undefined {
  if (endpointUrl) return getCatalogForUrl(endpointUrl);
  return $activeCatalog.get() ?? undefined;
}

function shouldProxy(endpointUrl?: string): { proxy: boolean; endpointBase: string } {
  if (endpointUrl) {
    const cat = getCatalogForUrl(endpointUrl);
    return { proxy: cat?.proxy === true, endpointBase: cat?.url ?? endpointUrl };
  }
  const cat = $activeCatalog.get();
  return { proxy: cat?.proxy === true, endpointBase: cat?.url ?? "" };
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * ADR 0008: built-in-catalog writes go through the app BFF route
 * (`/api/catalog/*`), which injects the session access token server-side —
 * the browser never holds a bearer token, and the write is RBAC-gated and
 * audited. Unconditional (dev pass-through included) so the dev and
 * auth-enforced paths exercise the same code. Reads and external catalogs
 * keep their existing direct//api/proxy paths.
 */
function isBuiltInWrite(method: string, endpointUrl?: string): boolean {
  return (
    WRITE_METHODS.has(method.toUpperCase()) &&
    resolveCatalog(endpointUrl)?.builtIn === true
  );
}

export async function stacFetch<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { method = "GET", body, signal, endpointUrl } = options;
  const baseUrl = getBaseUrl(endpointUrl);
  const targetUrl = `${baseUrl}${path}`;
  const { proxy, endpointBase } = shouldProxy(endpointUrl);

  const headers: Record<string, string> = {
    Accept: "application/json",
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let fetchUrl: string;
  if (isBuiltInWrite(method, endpointUrl)) {
    fetchUrl = `/api/catalog${path}`;
  } else if (proxy) {
    fetchUrl = "/api/proxy";
    headers["X-Proxy-Target"] = targetUrl;
    headers["X-Proxy-Endpoint"] = endpointBase;
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
