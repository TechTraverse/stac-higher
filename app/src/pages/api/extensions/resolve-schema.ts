import type { APIRoute } from "astro";
import { z } from "zod";
import { getOrFetchSchema } from "@/lib/extensions/schema-cache";
import { SafeFetchError } from "@/lib/http/safe-fetch";

const bodySchema = z.object({
  url: z.string().url("Must be a valid URL"),
});

export const POST: APIRoute = async ({ request }) => {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const parsed = bodySchema.safeParse(body);
  if (!parsed.success) {
    return new Response(
      JSON.stringify({ error: parsed.error.issues[0]?.message ?? "Invalid request" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const { url } = parsed.data;

  // Only allow HTTP(S) URLs
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return new Response(JSON.stringify({ error: "Only HTTP(S) URLs are allowed" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const schema = await getOrFetchSchema(url);
    return new Response(JSON.stringify(schema), {
      status: 200,
      headers: { "Content-Type": "application/schema+json" },
    });
  } catch (err) {
    if (err instanceof SafeFetchError) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: err.status,
        headers: { "Content-Type": "application/json" },
      });
    }
    const message = err instanceof Error ? err.message : "Failed to fetch schema";
    return new Response(JSON.stringify({ error: message }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
