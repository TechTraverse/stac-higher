// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

const REPO_ROOT = join(__dirname, "..", "..", "..");
const ROOTS = ["app/src", "packages/shared/src"];
const EXTENSIONS = [".ts", ".tsx", ".astro", ".mjs"];

function isTestOrStory(rel: string): boolean {
  return (
    rel.split(sep).includes("__tests__") || /\.(test|stories)\.[^/\\]+$/.test(rel)
  );
}

/** Repo-relative (posix) paths of non-test sources whose text matches. */
function filesMatching(pattern: RegExp): string[] {
  const hits: string[] = [];
  for (const root of ROOTS) {
    const dir = join(REPO_ROOT, root);
    for (const entry of readdirSync(dir, { recursive: true }) as string[]) {
      if (!EXTENSIONS.some((ext) => entry.endsWith(ext))) continue;
      if (isTestOrStory(entry)) continue;
      const full = join(dir, entry);
      if (pattern.test(readFileSync(full, "utf8"))) {
        hits.push(relative(REPO_ROOT, full).split(sep).join("/"));
      }
    }
  }
  return hits.sort();
}

describe("runtime config audit", () => {
  it("no source reads import.meta.env.PUBLIC_* (browser config is runtime, spec §8)", () => {
    expect(filesMatching(/import\.meta\.env\.PUBLIC_/)).toEqual([]);
  });

  it("import.meta.env is read only for the build-mode flag in auth/config.ts", () => {
    expect(filesMatching(/import\.meta\.env/)).toEqual(["app/src/lib/auth/config.ts"]);
  });
});
