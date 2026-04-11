import { $activeEndpoint } from "@/stores/endpointStore";
import { StacApiError } from "./types";

interface FetchOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  endpointUrl?: string;
}

function getBaseUrl(overrideUrl?: string): string {
  if (overrideUrl) return overrideUrl.replace(/\/+$/, "");
  const endpoint = $activeEndpoint.get();
  if (!endpoint) throw new StacApiError("No active STAC endpoint configured", 0);
  return endpoint.url.replace(/\/+$/, "");
}

export async function stacFetch<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { method = "GET", body, signal, endpointUrl } = options;
  const baseUrl = getBaseUrl(endpointUrl);
  const url = `${baseUrl}${path}`;

  const headers: Record<string, string> = {
    Accept: "application/json",
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
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
