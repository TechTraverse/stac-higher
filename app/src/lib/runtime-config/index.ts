/**
 * Runtime configuration for browser code (R-1, spec §8).
 *
 * Islands read ONLY this accessor. The server renders the object into the HTML
 * shell as `window.__STAC_HIGHER_CONFIG__` (see `Layout.astro`), so one built
 * image serves any deployment; nothing here is inlined at build time, unlike
 * Vite-inlined `PUBLIC_*` vars.
 *
 * Server code must call `buildRuntimeConfig` from `./server` instead: on the
 * server this accessor sees no global and returns the defaults.
 *
 * Keep this module dependency-free: it lands in every island chunk.
 */

export interface RuntimeConfig {
  builtInCatalogUrl: string;
  titilerUrl: string;
  tipgUrl: string;
  cubeServerUrl: string;
  processNetworkMax: string;
}

export const RUNTIME_CONFIG_GLOBAL = "__STAC_HIGHER_CONFIG__";

/** Compose-stack ports. `processNetworkMax` is a literal (not imported from
 * `@/lib/processes/network`, which would pull zod into every island chunk);
 * a unit test pins it equal to `DEFAULT_NETWORK_MAX`. */
export const DEFAULT_RUNTIME_CONFIG: Readonly<RuntimeConfig> = Object.freeze({
  builtInCatalogUrl: "http://localhost:8081",
  titilerUrl: "http://localhost:8084",
  tipgUrl: "http://localhost:8085",
  cubeServerUrl: "http://localhost:8086",
  processNetworkMax: "isolated",
});

declare global {
  // eslint-disable-next-line no-var
  var __STAC_HIGHER_CONFIG__: Partial<RuntimeConfig> | undefined;
}

/** Read per call (no memoizing). Each key comes from the injected object only
 * when it is a non-empty string; otherwise the default applies. */
export function getRuntimeConfig(): RuntimeConfig {
  const injected = globalThis[RUNTIME_CONFIG_GLOBAL];
  const out: RuntimeConfig = { ...DEFAULT_RUNTIME_CONFIG };
  if (!injected || typeof injected !== "object") return out;
  for (const key of Object.keys(DEFAULT_RUNTIME_CONFIG) as (keyof RuntimeConfig)[]) {
    const value = injected[key];
    if (typeof value === "string" && value !== "") out[key] = value;
  }
  return out;
}
