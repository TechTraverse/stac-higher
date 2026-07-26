import type { APIRoute } from "astro";
import { timingSafeEqual } from "node:crypto";
import { forwardUpstream } from "@/lib/http/forward";
import { jsonResponse } from "@/lib/http/response";
import { DEFAULT_MAX_BYTES } from "@/lib/http/safe-fetch";

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "authorization"];
const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "cache-control",
  "etag",
  "last-modified",
];

const jsonError = (message: string, status: number): Response =>
  jsonResponse(status, { error: message });

function tokensMatch(a: string, b: string): boolean {
  const aBuf = Buffer.from(a);
  const bBuf = Buffer.from(b);
  if (aBuf.length !== bBuf.length) return false;
  return timingSafeEqual(aBuf, bBuf);
}

export const ALL: APIRoute = async ({ request }) => {
  // Layer 1: reject explicitly cross-site browser calls. Sec-Fetch-Site is set
  // by all modern browsers; absent header (server-to-server, older clients)
  // falls through to the token check below.
  if (request.headers.get("sec-fetch-site") === "cross-site") {
    return jsonError("Cross-site proxy requests are not allowed", 403);
  }

  // Layer 2: deployer-opt-in shared token. When PROXY_AUTH_TOKEN is set the
  // proxy is locked down; clients must inject X-Proxy-Auth with the matching
  // value (e.g. via a trusted reverse proxy or Astro middleware).
  const expectedToken = process.env.PROXY_AUTH_TOKEN;
  if (expectedToken) {
    const provided = request.headers.get("x-proxy-auth") ?? "";
    if (!provided || !tokensMatch(provided, expectedToken)) {
      return jsonError("Unauthorized", 401);
    }
  }

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

  return forwardUpstream(
    targetUrl,
    { method: request.method, headers, body },
    FORWARDED_RESPONSE_HEADERS,
  );
};
