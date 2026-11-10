/// <reference types="vite/client" />
/**
 * Where maplibre-gl 6 loads its web worker from. Imported for its side effect
 * by every module that constructs a maplibre map (`StacMap`,
 * `ItemGeometryEditor`), before the first map exists.
 *
 * maplibre 6 ships ESM and, by default, loads its worker from the file next to
 * its own module: `new URL("./maplibre-gl-worker.mjs", import.meta.url)`. The
 * name is computed (`-dev` or not), so Vite cannot see it as an asset, and once
 * the main module is bundled that sibling does not exist: a 404 from
 * `/node_modules/.vite/deps/` under `astro dev` and from `/_astro/` in the
 * production build. Without a worker no vector tile is ever parsed, so the
 * basemap never draws and the map's `load` never fires.
 *
 * `?worker&url` makes Vite bundle the worker as its own entry, including its
 * `./maplibre-gl-shared.mjs` import (a plain `?url` would copy the file alone
 * and that import would 404), and hands back the emitted URL. maplibre starts
 * it as a module worker; the configs set `worker.format: "es"` to match.
 */
import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(workerUrl);

/** The worker URL maplibre was given (exported so a test can pin it). */
export const MAPLIBRE_WORKER_URL: string = workerUrl;
