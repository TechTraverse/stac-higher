// The runtime config through the real request path (#194): the app's
// middleware, then a real page rendered by Astro's Container API with the
// locals the middleware set. A bad browser-facing URL must be a 500 before
// the page starts streaming, on page routes only.
import type { APIContext } from "astro";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import { getContainerRenderer } from "@astrojs/react/container-renderer";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { onRequest } from "@/middleware";
import IndexPage from "@/pages/index.astro";

let container: AstroContainer;

beforeAll(async () => {
  const renderers = await loadRenderers([getContainerRenderer()]);
  container = await AstroContainer.create({ renderers });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const cookies = { get: () => undefined, set: () => {}, delete: () => {} };

/** One request through `onRequest`; `next` renders the index page with the
 * locals the middleware populated, as Astro does for a page route. */
async function request(path: string, next?: () => Promise<Response>) {
  const url = new URL(path, "http://localhost:4321");
  const req = new Request(url);
  const locals = {} as App.Locals;
  const context = { url, request: req, locals, cookies } as unknown as APIContext;
  const render = vi.fn(next ?? (() => container.renderToResponse(IndexPage, { request: req, locals })));
  const response = await onRequest(context, render);
  return { response: response as Response, render };
}

describe("runtime config on the request path", () => {
  it("injects the per-request config into a page", async () => {
    vi.stubEnv("TITILER_URL", "https://tiles.example.org/");
    const first = await request("/");
    expect(first.response.status).toBe(200);
    const html = await first.response.text();
    expect(html).toContain("window.__STAC_HIGHER_CONFIG__=");
    expect(html).toContain('"titilerUrl":"https://tiles.example.org"');

    // Read per request: a new value (a restart, in a deployment) shows up
    // without a rebuild.
    vi.stubEnv("TITILER_URL", "https://tiles2.example.org");
    const second = await (await request("/")).response.text();
    expect(second).toContain('"titilerUrl":"https://tiles2.example.org"');
  });

  it("answers a bad URL with a 500 before the page renders, naming the variable in the log only", async () => {
    vi.stubEnv("TITILER_URL", "tiles.example.org");
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const { response, render } = await request("/map");

    expect(response.status).toBe(500);
    expect(render).not.toHaveBeenCalled();
    const body = await response.text();
    expect(body).toBe("Internal server error");
    expect(body).not.toContain("tiles.example.org");
    expect(error.mock.calls.flat().join(" ")).toMatch(/TITILER_URL must be an absolute http\(s\) URL/);
  });

  it("leaves API routes alone when a browser-facing URL is bad", async () => {
    vi.stubEnv("TITILER_URL", "tiles.example.org");
    const { response, render } = await request(
      "/api/catalog/collections",
      async () => new Response("{}", { status: 200 }),
    );
    expect(render).toHaveBeenCalledOnce();
    expect(response.status).toBe(200);
  });
});
