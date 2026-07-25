// @vitest-environment node
/**
 * Golden-fixture contract tests (ISSUE I-53): the Zod half. The same documents
 * are run through the Python parsers by
 * services/pipeline/tests/test_contract_fixtures.py — a §5.1 shape or default
 * drifting on either side fails one of the suites. Fixture format and the
 * accept/reject semantics: tests/contract-fixtures/README.md.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import type { ZodType } from "zod";
import {
  deliveryConfigSchema,
  ingestConfigSchema,
} from "@/lib/associations/schemas";

interface FixtureCase {
  name: string;
  config: unknown;
  app: "accept" | "reject";
  pipeline: "accept" | "reject";
}

interface FixtureFile {
  minimal: unknown;
  defaults: unknown;
  cases: FixtureCase[];
}

function loadFixture(name: string): FixtureFile {
  const path = fileURLToPath(
    new URL(`../../../tests/contract-fixtures/${name}`, import.meta.url),
  );
  return JSON.parse(readFileSync(path, "utf8"));
}

function describeDirection(file: string, schema: ZodType) {
  const fixture = loadFixture(file);

  it("writes exactly the golden defaults for the minimal document", () => {
    expect(schema.parse(fixture.minimal)).toEqual(fixture.defaults);
  });

  it.each(fixture.cases)("$app: $name", ({ config, app }) => {
    expect(schema.safeParse(config).success).toBe(app === "accept");
  });
}

describe("ingest config contract (tests/contract-fixtures/ingest-config.json)", () => {
  describeDirection("ingest-config.json", ingestConfigSchema);
});

describe("delivery config contract (tests/contract-fixtures/delivery-config.json)", () => {
  describeDirection("delivery-config.json", deliveryConfigSchema);
});
