// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { CHART_DIR } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** Key paths of depth 1 and 2: `global`, `global.imageDigests`, ... */
function keyPaths(values: Obj): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(values)) {
    out.push(k);
    if (isObj(v)) for (const k2 of Object.keys(v)) out.push(`${k}.${k2}`);
  }
  return out;
}

describe("chart README", () => {
  it("documents every values key to two levels", () => {
    const values = parse(readFileSync(join(CHART_DIR, "values.yaml"), "utf8")) as Obj;
    const readme = readFileSync(join(CHART_DIR, "README.md"), "utf8");
    const missing = keyPaths(values).filter((p) => !readme.includes("`" + p + "`"));
    expect(missing).toEqual([]);
  });
});
