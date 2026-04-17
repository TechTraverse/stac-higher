import { promises as dns } from "node:dns";
import { isIP } from "node:net";

export const DEFAULT_TIMEOUT_MS = 10_000;
export const DEFAULT_MAX_BYTES = 5 * 1024 * 1024;

export class SafeFetchError extends Error {
  constructor(
    message: string,
    public readonly code:
      | "invalid_url"
      | "blocked_host"
      | "timeout"
      | "too_large"
      | "upstream",
    public readonly status: number,
  ) {
    super(message);
    this.name = "SafeFetchError";
  }
}

export interface SafeFetchOptions {
  method?: string;
  headers?: HeadersInit;
  body?: BodyInit;
  timeoutMs?: number;
  maxBytes?: number;
}

export interface SafeFetchResult {
  status: number;
  headers: Headers;
  body: Uint8Array;
}

function getAllowList(): Set<string> {
  const raw = process.env.SAFE_FETCH_ALLOW_HOSTS ?? "";
  return new Set(
    raw
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
  );
}

export function isPrivateIPv4(addr: string): boolean {
  const parts = addr.split(".").map(Number);
  if (parts.length !== 4 || parts.some((p) => Number.isNaN(p))) return true;
  const [a, b] = parts;
  if (a === 0) return true;
  if (a === 10) return true;
  if (a === 127) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a >= 224) return true;
  return false;
}

export function isPrivateIPv6(addr: string): boolean {
  const lower = addr.toLowerCase();
  if (lower === "::" || lower === "::1") return true;
  if (lower.startsWith("fe80:") || lower.startsWith("fe80::")) return true;
  if (lower.startsWith("fc") || lower.startsWith("fd")) return true;
  if (lower.startsWith("ff")) return true;
  if (lower.startsWith("::ffff:")) {
    const v4 = lower.slice("::ffff:".length);
    if (isIP(v4) === 4) return isPrivateIPv4(v4);
  }
  return false;
}

export function isPrivateAddress(addr: string): boolean {
  const kind = isIP(addr);
  if (kind === 4) return isPrivateIPv4(addr);
  if (kind === 6) return isPrivateIPv6(addr);
  return true;
}

function stripBrackets(host: string): string {
  if (host.startsWith("[") && host.endsWith("]")) {
    return host.slice(1, -1);
  }
  return host;
}

async function assertHostAllowed(hostname: string): Promise<void> {
  const host = stripBrackets(hostname.toLowerCase());
  const allow = getAllowList();
  if (allow.has(host)) return;

  const literal = isIP(host);
  if (literal) {
    if (isPrivateAddress(host)) {
      throw new SafeFetchError(
        `Host ${host} resolves to a private/loopback address`,
        "blocked_host",
        403,
      );
    }
    return;
  }

  let addrs: { address: string }[];
  try {
    addrs = await dns.lookup(host, { all: true });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "DNS lookup failed";
    throw new SafeFetchError(`DNS lookup failed: ${msg}`, "upstream", 502);
  }

  for (const { address } of addrs) {
    if (isPrivateAddress(address)) {
      throw new SafeFetchError(
        `Host ${host} resolves to a private/loopback address (${address})`,
        "blocked_host",
        403,
      );
    }
  }
}

export async function safeFetch(
  url: string,
  opts: SafeFetchOptions = {},
): Promise<SafeFetchResult> {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new SafeFetchError("Invalid URL", "invalid_url", 400);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new SafeFetchError(
      "Only http and https URLs are allowed",
      "invalid_url",
      403,
    );
  }

  await assertHostAllowed(parsed.hostname);

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_BYTES;

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: opts.method ?? "GET",
      headers: opts.headers,
      body: opts.body,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    if (err instanceof Error && err.name === "TimeoutError") {
      throw new SafeFetchError(
        `Upstream request timed out after ${timeoutMs}ms`,
        "timeout",
        504,
      );
    }
    const msg = err instanceof Error ? err.message : "Unknown error";
    throw new SafeFetchError(`Upstream request failed: ${msg}`, "upstream", 502);
  }

  const declared = upstream.headers.get("content-length");
  if (declared && Number(declared) > maxBytes) {
    throw new SafeFetchError(
      `Upstream response exceeds ${maxBytes} bytes`,
      "too_large",
      413,
    );
  }

  const chunks: Uint8Array[] = [];
  let received = 0;

  if (upstream.body) {
    const reader = upstream.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        received += value.byteLength;
        if (received > maxBytes) {
          try {
            await reader.cancel();
          } catch {
            // ignore
          }
          throw new SafeFetchError(
            `Upstream response exceeds ${maxBytes} bytes`,
            "too_large",
            413,
          );
        }
        chunks.push(value);
      }
    }
  } else {
    const buf = new Uint8Array(await upstream.arrayBuffer());
    if (buf.byteLength > maxBytes) {
      throw new SafeFetchError(
        `Upstream response exceeds ${maxBytes} bytes`,
        "too_large",
        413,
      );
    }
    chunks.push(buf);
  }

  const body = new Uint8Array(received || chunks.reduce((n, c) => n + c.byteLength, 0));
  let offset = 0;
  for (const c of chunks) {
    body.set(c, offset);
    offset += c.byteLength;
  }

  return { status: upstream.status, headers: upstream.headers, body };
}

export function errorToResponse(err: unknown): Response {
  if (err instanceof SafeFetchError) {
    return new Response(JSON.stringify({ error: err.message }), {
      status: err.status,
      headers: { "Content-Type": "application/json" },
    });
  }
  const msg = err instanceof Error ? err.message : "Unknown error";
  return new Response(JSON.stringify({ error: msg }), {
    status: 500,
    headers: { "Content-Type": "application/json" },
  });
}
