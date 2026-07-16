/**
 * Canonical JSON response helper for API routes. Lives in a neutral `http`
 * module so any route can build a JSON body without importing a domain-scoped
 * module (the connections routes re-export this).
 */
export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
