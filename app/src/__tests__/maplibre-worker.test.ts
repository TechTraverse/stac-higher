/**
 * maplibre-gl 6 must be told where its worker is: by default it looks for
 * `maplibre-gl-worker.mjs` next to its own module, which does not exist once
 * Vite has bundled maplibre (a 404 in dev and in the production build: no
 * vector basemap, and the map's `load` never fires).
 */
import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";

const { setWorkerUrl } = vi.hoisted(() => ({ setWorkerUrl: vi.fn() }));
vi.mock("maplibre-gl", () => ({ setWorkerUrl }));

describe("maplibre worker url", () => {
  it("hands maplibre the bundled worker before any map exists", async () => {
    const { MAPLIBRE_WORKER_URL } = await import("@shared/lib/map/maplibre-worker");
    expect(setWorkerUrl).toHaveBeenCalledTimes(1);
    expect(setWorkerUrl).toHaveBeenCalledWith(MAPLIBRE_WORKER_URL);
    expect(MAPLIBRE_WORKER_URL).toMatch(/maplibre-gl-worker/);
  });

  it("is imported by every module that constructs a maplibre map", () => {
    // A <Map> created before setWorkerUrl starts its workers from the 404 default.
    const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
    expect(read("../../../packages/shared/src/components/map/StacMap.tsx")).toContain(
      'import "@shared/lib/map/maplibre-worker";',
    );
    expect(read("../components/items/ItemGeometryEditor.tsx")).toContain("MAPLIBRE_WORKER_URL");
  });
});
