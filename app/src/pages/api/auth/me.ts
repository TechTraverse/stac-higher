/**
 * GET /api/auth/me — the current request's canonical auth context.
 *
 * The Header island consumes this to render the sign-in button / user menu /
 * dev-identity badge. Never exposes tokens — only the mapped identity.
 */
import type { APIRoute } from "astro";
import { getAuthConfig } from "@/lib/auth/config";
import { anonymous } from "@/lib/auth/types";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth ?? anonymous(getAuthConfig().mode);
  return new Response(JSON.stringify(auth), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
};
