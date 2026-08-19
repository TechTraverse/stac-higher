/**
 * Shared upstream-forwarding tail for passthrough routes (/api/proxy and the
 * ADR 0008 BFF): safeFetch the target, map SSRF-guard errors to their JSON
 * responses, and rebuild the upstream response copying only an allowlist of
 * headers.
 */
import { jsonResponse } from "./response";
import {
  SafeFetchError,
  errorToResponse,
  safeFetch,
  type SafeFetchResult,
} from "./safe-fetch";

export async function forwardUpstream(
  targetUrl: string,
  init: { method: string; headers: Record<string, string>; body?: ArrayBuffer },
  responseHeaderAllowlist: readonly string[],
): Promise<Response> {
  let result: SafeFetchResult;
  try {
    result = await safeFetch(targetUrl, init);
  } catch (err) {
    if (err instanceof SafeFetchError) return errorToResponse(err);
    const msg = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(502, { error: `Upstream request failed: ${msg}` });
  }

  const headers = new Headers();
  for (const name of responseHeaderAllowlist) {
    const value = result.headers.get(name);
    if (value) headers.set(name, value);
  }
  // TS 5.9 types Uint8Array<ArrayBufferLike>, which BodyInit's ArrayBufferView<ArrayBuffer> rejects
  return new Response(result.body as BodyInit, { status: result.status, headers });
}
