import type { APIRoute } from "astro";
import {
  DEFAULT_MAX_BYTES,
  SafeFetchError,
  errorToResponse,
  safeFetch,
} from "@/lib/http/safe-fetch";

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "authorization"];
const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "cache-control",
  "etag",
  "last-modified",
];

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export const ALL: APIRoute = async ({ request }) => {
  const targetUrl = request.headers.get("X-Proxy-Target");
  if (!targetUrl) return jsonError("Missing X-Proxy-Target header", 400);

  const endpointBase = request.headers.get("X-Proxy-Endpoint");
  if (!endpointBase) return jsonError("Missing X-Proxy-Endpoint header", 400);

  let parsed: URL;
  try {
    parsed = new URL(targetUrl);
  } catch {
    return jsonError("Invalid target URL", 400);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return jsonError("Target URL must use http or https", 403);
  }

  const normalizedTarget = targetUrl.replace(/\/+$/, "");
  const normalizedBase = endpointBase.replace(/\/+$/, "");
  if (!normalizedTarget.startsWith(normalizedBase)) {
    return jsonError("Target URL does not match the declared endpoint", 403);
  }

  const headers: Record<string, string> = {};
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers[name] = value;
  }

  const hasBody = !["GET", "HEAD"].includes(request.method);
  let body: ArrayBuffer | undefined;
  if (hasBody) {
    const declared = request.headers.get("content-length");
    if (declared && Number(declared) > DEFAULT_MAX_BYTES) {
      return jsonError(
        `Request body exceeds ${DEFAULT_MAX_BYTES} bytes`,
        413,
      );
    }
    body = await request.arrayBuffer();
    if (body.byteLength > DEFAULT_MAX_BYTES) {
      return jsonError(
        `Request body exceeds ${DEFAULT_MAX_BYTES} bytes`,
        413,
      );
    }
  }

  let result;
  try {
    result = await safeFetch(targetUrl, {
      method: request.method,
      headers,
      body,
    });
  } catch (err) {
    if (err instanceof SafeFetchError) return errorToResponse(err);
    const msg = err instanceof Error ? err.message : "Unknown error";
    return jsonError(`Upstream request failed: ${msg}`, 502);
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
