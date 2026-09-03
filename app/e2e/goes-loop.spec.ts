import { test, expect, type APIRequestContext, type APIResponse } from "@playwright/test";
import { HeadObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * GOES loop, live (G-7, spec §2). Skipped unless E2E_LIVE_NODD=1: it needs
 * the internet (NODD), the full Docker stack with the pipeline and the
 * process runtime image, a `stac-higher-deliveries` bucket in MinIO, and
 * ~5 minutes. Everything is created through the product's API with unique
 * names and removed afterwards.
 *
 * Four gate outcomes, polled with a 10-minute bound:
 *   1. the source item exists with the scan time as datetime and goes:* properties
 *   2. the output item exists with a `visual` COG asset
 *   3. a tile for it renders through the tile server
 *   4. the COG is delivered to the second MinIO bucket
 */
const LIVE = process.env.E2E_LIVE_NODD === "1";
const STAC = process.env.E2E_STAC_URL ?? "http://localhost:8082";
const TILER = process.env.E2E_TITILER_URL ?? "http://localhost:8084";
const MINIO = process.env.E2E_S3_ENDPOINT ?? "http://localhost:9000";
const GROUP = "earth-observation";
const RUN = Date.now().toString(36);
const SRC = `e2e-goes-src-${RUN}`;
const OUT = `e2e-goes-out-${RUN}`;
const NODD_CONN = `e2e-goes-nodd-${RUN}`;
const DEST_CONN = `e2e-goes-dest-${RUN}`;
const DEST_BUCKET = "stac-higher-deliveries";
const GATE_MS = 10 * 60_000;

// The two scripts are the single source of truth (pipeline/demo/goes): the
// seeder deploys them, the docs quote them, and so does this spec.
const GOES_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../services/pipeline/src/pipeline/demo/goes",
);
const EXTRACTOR_CODE = readFileSync(resolve(GOES_DIR, "extractor.py"), "utf8");
const GEOCOLOR_CODE = readFileSync(resolve(GOES_DIR, "geocolor.py"), "utf8").replaceAll(
  "__OUTPUT_COLLECTION__",
  OUT,
);

const collection = (id: string, description: string) => ({
  type: "Collection", stac_version: "1.0.0", id, description, license: "proprietary",
  extent: { spatial: { bbox: [[-152, 14, -52, 57]] }, temporal: { interval: [["2025-04-07T00:00:00Z", null]] } },
  links: [],
});

const runtime = (timeout_seconds: number, memory_mb: number) => ({
  kind: "inline_python", image: null, memory_mb, timeout_seconds,
  retry: { max_attempts: 2, backoff: "exponential" },
  network: { level: "isolated", hosts: [] },
});

/** The newest MCMIPC key in the current UTC hour (or the previous one when
 * the hour has just begun), listed over plain HTTPS — never through the app. */
async function newestMcmipcKey(request: APIRequestContext): Promise<string> {
  const now = new Date();
  for (const back of [0, 1]) {
    const t = new Date(now.getTime() - back * 3_600_000);
    const start = Date.UTC(t.getUTCFullYear(), 0, 0);
    const doy = String(Math.floor((t.getTime() - start) / 86_400_000)).padStart(3, "0");
    const prefix = `ABI-L2-MCMIPC/${t.getUTCFullYear()}/${doy}/${String(t.getUTCHours()).padStart(2, "0")}/`;
    const res = await request.get(
      `https://noaa-goes19.s3.amazonaws.com/?list-type=2&prefix=${encodeURIComponent(prefix)}`,
    );
    expect(res.ok()).toBeTruthy();
    const keys = [...(await res.text()).matchAll(/<Key>([^<]+)<\/Key>/g)].map((m) => m[1]);
    if (keys.length) return keys[keys.length - 1];
  }
  throw new Error("NODD listed no MCMIPC keys for the current or previous hour");
}

/** `_s20262460401172` -> epoch ms (YYYY DDD HH MM SS + tenths). Compared as an
 * INSTANT, not a string: the extractor writes Python's isoformat and pgstac
 * re-renders the datetime column, so only the value is contractual. */
function scanTimeMs(filename: string): number {
  const digits = /_s(\d{14})/.exec(filename)?.[1];
  if (!digits) throw new Error(`no _sYYYYDDDHHMMSSt token in ${filename}`);
  const dayOne = Date.UTC(Number(digits.slice(0, 4)), 0, 1);
  return (
    dayOne +
    (Number(digits.slice(4, 7)) - 1) * 86_400_000 +
    Number(digits.slice(7, 9)) * 3_600_000 +
    Number(digits.slice(9, 11)) * 60_000 +
    Number(digits.slice(11, 13)) * 1_000 +
    Number(digits[13]) * 100
  );
}

/** ONE budget for the whole four-gate sequence, armed at the start of the test.
 * A per-gate deadline would let four gates sum to 40 minutes inside the
 * 15-minute describe timeout, and then Playwright's own timeout fires instead
 * of the labelled one — losing which gate actually stalled. */
let deadline = Number.POSITIVE_INFINITY;

async function poll<T>(label: string, fn: () => Promise<T | null>, everyMs = 10_000): Promise<T> {
  while (Date.now() < deadline) {
    const value = await fn();
    if (value !== null) return value;
    await new Promise((r) => setTimeout(r, everyMs));
  }
  throw new Error(`gate budget of ${GATE_MS / 1000}s exhausted waiting for: ${label}`);
}

/* eslint-disable @typescript-eslint/no-explicit-any */
async function json<T = any>(res: APIResponse, what: string): Promise<T> {
  if (!res.ok()) throw new Error(`${what}: ${res.status()} ${await res.text()}`);
  return res.json() as Promise<T>;
}

test.describe("GOES loop against live NODD", () => {
  test.skip(!LIVE, "set E2E_LIVE_NODD=1 (needs internet, Docker stack, pipeline, runtime image)");
  test.describe.configure({ timeout: GATE_MS + 5 * 60_000 });

  const ids = { extractor: "", geocolor: "", nodd: "", dest: "", ingest: "", deliver: "" };

  test.afterAll(async ({ request }) => {
    // Order matters: an extractor named by an association refuses deletion.
    if (ids.ingest) await request.delete(`/api/collections/${SRC}/connections/${ids.ingest}`);
    if (ids.deliver) await request.delete(`/api/collections/${OUT}/connections/${ids.deliver}`);
    for (const p of [ids.geocolor, ids.extractor]) if (p) await request.delete(`/api/processes/${p}`);
    for (const c of [ids.nodd, ids.dest]) if (c) await request.delete(`/api/connections/${c}`);
    for (const c of [OUT, SRC]) await request.delete(`/api/catalog/collections/${c}`);
  });

  test("ingests one file, extracts, composes, tiles and delivers", async ({ request }) => {
    const key = await newestMcmipcKey(request);
    const filename = key.split("/").pop()!;
    const expectedMs = scanTimeMs(filename);

    // --- wiring, all through the product API -------------------------------
    for (const [id, description] of [[SRC, "GOES e2e source"], [OUT, "GOES e2e output"]] as const) {
      await json(
        await request.post("/api/catalog/collections", { data: collection(id, description) }),
        `collection ${id}`,
      );
    }
    ids.nodd = (await json(await request.post("/api/connections", { data: {
      name: NODD_CONN, protocol: "s3", group_id: GROUP,
      config: { bucket: "noaa-goes19", region: "us-east-1", anonymous: true },
    } }), "nodd connection")).id;

    ids.extractor = (await json(await request.post("/api/processes", { data: {
      name: `goes-abi-metadata-${RUN}`, description: "GOES e2e extractor", group_id: GROUP,
      kind: "extractor", enabled: true, max_runs_per_hour: 600,
    } }), "extractor")).id;
    await json(await request.post(`/api/processes/${ids.extractor}/revisions`, { data: {
      runtime: runtime(120, 1024), code: EXTRACTOR_CODE, env: [],
    } }), "extractor deploy");

    ids.geocolor = (await json(await request.post("/api/processes", { data: {
      name: `goes-geocolor-${RUN}`, description: "GOES e2e process", group_id: GROUP,
      kind: "transform", enabled: true, max_runs_per_hour: 120,
    } }), "geocolor")).id;
    await json(await request.post(`/api/processes/${ids.geocolor}/revisions`, { data: {
      runtime: runtime(600, 2048), code: GEOCOLOR_CODE, env: [],
    } }), "geocolor deploy");
    await json(await request.post(`/api/processes/${ids.geocolor}/sources`, { data: {
      collection_id: SRC, trigger: { kind: "item_event", item_filter: null }, expectation: null, enabled: true,
    } }), "geocolor source");
    await json(await request.post(`/api/processes/${ids.geocolor}/outputs`, { data: { collection_id: OUT } }), "geocolor output");

    // Serving on the output collection. The settings PUT carries the WHOLE row
    // (collectionSettingsUpdateSchema is strict, every field required).
    await json(await request.put(`/api/collections/${OUT}/settings`, { data: {
      group_id: GROUP, externally_writable: false, retention_days: null, retention_max_items: null,
      gc_grace_days: 30, archived: false, serving_enabled: true,
    } }), "serving");

    ids.dest = (await json(await request.post("/api/connections", { data: {
      name: DEST_CONN, protocol: "s3", group_id: GROUP,
      config: { bucket: DEST_BUCKET, region: "us-east-1", endpoint: "http://minio:9000", force_path_style: true },
      credentials: { access_key_id: "minioadmin", secret_access_key: "minioadmin" },
    } }), "dest connection")).id;
    ids.deliver = (await json(await request.post(`/api/collections/${OUT}/connections`, { data: {
      connection_id: ids.dest, direction: "deliver", enabled: true, expectation: null,
      config: {
        path_template: "goes/{item_id}/{filename}", item_filter: null, asset_keys: null,
        payload: { item_json: false, checksums: null, completion_marker: false },
        on_update: "redeliver", overwrite: "if_newer",
        retry: { max_attempts: 5, backoff: "exponential" }, max_concurrent_transfers: 4,
      },
    } }), "deliver association")).id;

    // The ingest association goes LAST so the first poll finds everything wired.
    ids.ingest = (await json(await request.post(`/api/collections/${SRC}/connections`, { data: {
      connection_id: ids.nodd, direction: "ingest", enabled: true, expectation: null,
      config: {
        source_path: "ABI-L2-MCMIPC/", include: [`**/${filename}`], exclude: [],
        poll_frequency_seconds: 60, storage_mode: "reference",
        path_template: "{Y}/{j}/{H}/", window: { begin: "-2h", end: null }, max_files_per_poll: 1,
        grouping: { rule: "none", timeout_seconds: 900, on_timeout: "ingest_partial" },
        metadata: { strategy: "extractor", extractor: { process_id: ids.extractor } },
        post_ingest: "leave",
      },
    } }), "ingest association")).id;

    // Everything is wired; the four gates now share one 10-minute budget.
    deadline = Date.now() + GATE_MS;

    // --- gate 1: the extracted source item --------------------------------
    const source = await poll("source item", async () => {
      const res = await request.get(`${STAC}/collections/${SRC}/items?limit=10`);
      if (!res.ok()) return null;
      const features = (await res.json()).features ?? [];
      return features.find((f: any) =>
        Object.values(f.assets ?? {}).some((a: any) => String(a.href).endsWith(filename)),
      ) ?? null;
    });
    expect(Date.parse(source.properties.datetime)).toBe(expectedMs);
    expect(source.properties.platform).toBe("goes-19");
    expect(source.properties["goes:scene_id"]).toBeTruthy();
    expect(source.geometry?.type).toBe("Polygon");
    // The built-in raster path found no georeferencing (I-101); the extractor's
    // footprint is the real one, not the collection-extent fallback.
    expect(source.properties["stac_higher:geometry_source"]).toBeUndefined();

    // --- gate 2: the GeoColor output item ---------------------------------
    const outId = `${source.id}-geocolor`;
    const output = await poll("output item", async () => {
      const res = await request.get(`${STAC}/collections/${OUT}/items/${encodeURIComponent(outId)}`);
      return res.ok() ? await res.json() : null;
    });
    // One filename per output item — the geocolor script writes `{out_id}.tif`.
    expect(output.assets.visual.href).toBe(`/api/assets/${OUT}/${outId}/${outId}.tif`);
    expect(output.assets.visual.roles).toContain("visual");
    // Geostationary CRS: no EPSG code, so rio-stac 0.12 emits `proj:wkt2`
    // (`proj:epsg` is null and the script drops null properties).
    expect(output.properties["proj:wkt2"]).toBeTruthy();

    // --- gate 3: a tile renders --------------------------------------------
    const tilejson = await json(await request.get(
      `${TILER}/collections/${OUT}/items/${encodeURIComponent(outId)}/WebMercatorQuad/tilejson.json?assets=visual`,
    ), "tilejson");
    const [lon, lat, z] = tilejson.center as [number, number, number];
    const n = 2 ** z;
    const x = Math.floor(((lon + 180) / 360) * n);
    const y = Math.floor(((1 - Math.log(Math.tan((lat * Math.PI) / 180) + 1 / Math.cos((lat * Math.PI) / 180)) / Math.PI) / 2) * n);
    const tileUrl = String(tilejson.tiles[0]).replace("{z}", String(z)).replace("{x}", String(x)).replace("{y}", String(y));
    const tile = await request.get(tileUrl);
    expect(tile.ok()).toBeTruthy();
    expect(tile.headers()["content-type"]).toMatch(/^image\//);

    // --- gate 4: delivered ---------------------------------------------------
    await poll("delivery", async () => {
      // Symmetric with gates 1 and 2: a transient non-2xx retries, never aborts.
      const res = await request.get(`/api/collections/${OUT}/connections/${ids.deliver}/deliveries`);
      if (!res.ok()) return null;
      const rows: any[] = (await res.json()).deliveries ?? [];
      return rows.find((r) => r.item_id === outId && r.status === "delivered") ?? null;
    });
    const s3 = new S3Client({
      endpoint: MINIO, region: "us-east-1", forcePathStyle: true,
      credentials: { accessKeyId: "minioadmin", secretAccessKey: "minioadmin" },
    });
    const head = await s3.send(new HeadObjectCommand({ Bucket: DEST_BUCKET, Key: `goes/${outId}/${outId}.tif` }));
    expect(head.ContentLength ?? 0).toBeGreaterThan(100_000);
  });
});
