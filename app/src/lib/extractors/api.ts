/** Client for `/api/extractors/*` (X-4): the built-in extractor registry. */
import type { BuiltinExtractor } from "./schemas";

export async function listBuiltinExtractors(): Promise<BuiltinExtractor[]> {
  const res = await fetch("/api/extractors/builtin", { credentials: "same-origin" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    throw new Error(
      (typeof body.error === "string" && body.error) || `Request failed: ${res.status}`,
    );
  }
  return ((await res.json()) as { extractors: BuiltinExtractor[] }).extractors;
}
