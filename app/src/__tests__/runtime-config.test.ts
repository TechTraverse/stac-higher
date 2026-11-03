// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_RUNTIME_CONFIG,
  getRuntimeConfig,
} from "@/lib/runtime-config";
import {
  buildRuntimeConfig,
  serializeRuntimeConfig,
} from "@/lib/runtime-config/server";
import { DEFAULT_NETWORK_MAX } from "@/lib/processes/network";

afterEach(() => {
  delete globalThis.__STAC_HIGHER_CONFIG__;
  vi.restoreAllMocks();
});

describe("buildRuntimeConfig", () => {
  it("defaults to the compose ports", () => {
    expect(buildRuntimeConfig({})).toEqual({
      builtInCatalogUrl: "http://localhost:8081", titilerUrl: "http://localhost:8084",
      tipgUrl: "http://localhost:8085", cubeServerUrl: "http://localhost:8086",
      processNetworkMax: "isolated",
    });
  });
  it("reads the new names and strips trailing slashes", () => {
    const c = buildRuntimeConfig({
      BUILTIN_CATALOG_PUBLIC_URL: "https://catalog.example.org/", TITILER_URL: "https://tiles.example.org//",
      TIPG_URL: "https://features.example.org", CUBE_SERVER_URL: "https://cubes.example.org/",
    });
    expect(c.builtInCatalogUrl).toBe("https://catalog.example.org");
    expect(c.titilerUrl).toBe("https://tiles.example.org");
    expect(c.cubeServerUrl).toBe("https://cubes.example.org");
  });
  it("falls back to the PUBLIC_* names, and the new name wins when both are set", () => {
    expect(buildRuntimeConfig({ PUBLIC_TITILER_URL: "https://old.example.org" }).titilerUrl).toBe("https://old.example.org");
    expect(buildRuntimeConfig({ TITILER_URL: "https://new.example.org", PUBLIC_TITILER_URL: "https://old.example.org" }).titilerUrl).toBe("https://new.example.org");
  });
  it("treats an empty or blank value as unset", () => {
    expect(buildRuntimeConfig({ TIPG_URL: "  ", PUBLIC_TIPG_URL: "https://old.example.org" }).tipgUrl).toBe("https://old.example.org");
  });
  it("refuses a URL that is not absolute http(s), naming the variable", () => {
    expect(() => buildRuntimeConfig({ TITILER_URL: "tiles.example.org" })).toThrow(/TITILER_URL/);
    expect(() => buildRuntimeConfig({ CUBE_SERVER_URL: "javascript:alert(1)" })).toThrow(/CUBE_SERVER_URL/);
  });
  it("mirrors the enforced PROCESS_NETWORK_MAX, then PUBLIC_PROCESS_NETWORK_MAX", () => {
    expect(buildRuntimeConfig({ PROCESS_NETWORK_MAX: "open", PUBLIC_PROCESS_NETWORK_MAX: "inputs" }).processNetworkMax).toBe("open");
    expect(buildRuntimeConfig({ PUBLIC_PROCESS_NETWORK_MAX: "inputs" }).processNetworkMax).toBe("inputs");
  });
  it("degrades an invalid network cap to the default instead of failing the page", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(buildRuntimeConfig({ PROCESS_NETWORK_MAX: "everything" }).processNetworkMax).toBe("isolated");
  });
});

describe("serializeRuntimeConfig", () => {
  it("is a script that sets the global, and round-trips hostile values (Review Focus 1)", () => {
    const hostile = {
      ...DEFAULT_RUNTIME_CONFIG,
      titilerUrl: "https://x.example.org/</script><script>alert(1)</script><!--\u2028\u2029",
    };
    const script = serializeRuntimeConfig(hostile);
    expect(script.startsWith("window.__STAC_HIGHER_CONFIG__=")).toBe(true);
    expect(script).not.toMatch(/<|\u2028|\u2029/);
    const sandbox: { window: Record<string, unknown> } = { window: {} };
    new Function("window", script)(sandbox.window);
    expect(sandbox.window.__STAC_HIGHER_CONFIG__).toEqual(hostile);
  });
});

describe("getRuntimeConfig", () => {
  it("returns the defaults when nothing was injected", () => {
    expect(getRuntimeConfig()).toEqual(DEFAULT_RUNTIME_CONFIG);
  });
  it("reads the injected object, per key, ignoring non-string or empty values", () => {
    globalThis.__STAC_HIGHER_CONFIG__ = { titilerUrl: "https://tiles.example.org", tipgUrl: "", cubeServerUrl: 42 as never };
    const c = getRuntimeConfig();
    expect(c.titilerUrl).toBe("https://tiles.example.org");
    expect(c.tipgUrl).toBe("http://localhost:8085");
    expect(c.cubeServerUrl).toBe("http://localhost:8086");
  });
  it("pins the network default to the server's DEFAULT_NETWORK_MAX", () => {
    expect(DEFAULT_RUNTIME_CONFIG.processNetworkMax).toBe(DEFAULT_NETWORK_MAX);
  });
});
