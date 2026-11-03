/**
 * Server side of the runtime config (R-1, spec §8): resolves the object from
 * the process environment and serializes it for injection into the HTML shell.
 * Browser code reads it through `getRuntimeConfig` in `./index`.
 */
import { DEFAULT_NETWORK_MAX, getNetworkMax } from "@/lib/processes/network";
import { DEFAULT_RUNTIME_CONFIG, RUNTIME_CONFIG_GLOBAL, type RuntimeConfig } from "./index";

type Env = Record<string, string | undefined>;

/** Env names per key, first non-empty wins. The `PUBLIC_*` names are the
 * legacy Vite-inlined spellings, kept as fallbacks. */
export const RUNTIME_CONFIG_ENV: { readonly [K in keyof RuntimeConfig]: readonly string[] } = {
  builtInCatalogUrl: ["BUILTIN_CATALOG_PUBLIC_URL", "PUBLIC_BUILTIN_CATALOG_URL"],
  titilerUrl: ["TITILER_URL", "PUBLIC_TITILER_URL"],
  tipgUrl: ["TIPG_URL", "PUBLIC_TIPG_URL"],
  cubeServerUrl: ["CUBE_SERVER_URL"],
  processNetworkMax: ["PROCESS_NETWORK_MAX", "PUBLIC_PROCESS_NETWORK_MAX"],
};

function firstSet(names: readonly string[], env: Env): { name: string; raw: string } | undefined {
  for (const name of names) {
    const raw = env[name]?.trim();
    if (raw) return { name, raw };
  }
  return undefined;
}

export function runtimeConfigValue<K extends keyof RuntimeConfig>(
  key: K,
  env: Env = process.env,
): RuntimeConfig[K] {
  const found = firstSet(RUNTIME_CONFIG_ENV[key], env);
  if (!found) return DEFAULT_RUNTIME_CONFIG[key];

  if (key === "processNetworkMax") {
    try {
      return getNetworkMax({ PROCESS_NETWORK_MAX: found.raw }) as RuntimeConfig[K];
    } catch (err) {
      // The deploy route still enforces the real cap; the UI mirror degrades.
      console.warn(`${found.name}: ${(err as Error).message}; using ${DEFAULT_NETWORK_MAX}`);
      return DEFAULT_NETWORK_MAX as RuntimeConfig[K];
    }
  }

  let protocol: string;
  try {
    protocol = new URL(found.raw).protocol;
  } catch {
    protocol = "";
  }
  if (protocol !== "http:" && protocol !== "https:") {
    throw new Error(`${found.name} must be an absolute http(s) URL; got ${JSON.stringify(found.raw)}`);
  }
  return found.raw.replace(/\/+$/, "") as RuntimeConfig[K];
}

export function buildRuntimeConfig(env: Env = process.env): RuntimeConfig {
  return {
    builtInCatalogUrl: runtimeConfigValue("builtInCatalogUrl", env),
    titilerUrl: runtimeConfigValue("titilerUrl", env),
    tipgUrl: runtimeConfigValue("tipgUrl", env),
    cubeServerUrl: runtimeConfigValue("cubeServerUrl", env),
    processNetworkMax: runtimeConfigValue("processNetworkMax", env),
  };
}

/** A script body that sets the global. `<` and the JS line separators are
 * escaped so the JSON cannot close the script element or break the literal. */
export function serializeRuntimeConfig(config: RuntimeConfig): string {
  const json = JSON.stringify(config)
    .replace(/</g, "\\u003c")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
  return `window.${RUNTIME_CONFIG_GLOBAL}=${json};`;
}
