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
  deliveryExpectationSchema,
  ingestConfigSchema,
  ingestExpectationSchema,
} from "@/lib/associations/schemas";
import { webhookChannelConfigSchema } from "@/lib/notifications/schemas";
import {
  isStagedHref,
  parseStagedHref,
  stagedHref,
  StorageKeyError,
} from "@/lib/storage/keys";
import {
  PUSH_REJECTION_REASONS,
  pushUploadStatusSchema,
  STAGED_UPLOAD_STATUSES,
  TERMINAL_STAGED_UPLOAD_STATUSES,
} from "@/lib/uploads/schemas";

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

describe("ingest expectation contract (tests/contract-fixtures/ingest-expectation.json)", () => {
  describeDirection("ingest-expectation.json", ingestExpectationSchema);
});

describe("delivery expectation contract (tests/contract-fixtures/delivery-expectation.json)", () => {
  describeDirection("delivery-expectation.json", deliveryExpectationSchema);
});

describe("webhook channel config contract (tests/contract-fixtures/webhook-channel-config.json)", () => {
  describeDirection("webhook-channel-config.json", webhookChannelConfigSchema);
});

// ---------------------------------------------------------------------------
// Phase 7 fixtures (spec §10) — non-config styles; formats documented in
// tests/contract-fixtures/README.md ("Additional fixture styles").
// ---------------------------------------------------------------------------

interface GrammarCase {
  name: string;
  href: string;
  detected: boolean;
  parse: { upload_id: string; filename: string } | null;
}

describe("staged asset href grammar (tests/contract-fixtures/staged-asset-href.json)", () => {
  const fixture = JSON.parse(
    readFileSync(
      fileURLToPath(
        new URL(
          "../../../tests/contract-fixtures/staged-asset-href.json",
          import.meta.url,
        ),
      ),
      "utf8",
    ),
  ) as { cases: GrammarCase[] };

  it.each(fixture.cases)("detect — $name", ({ href, detected }) => {
    expect(isStagedHref(href)).toBe(detected);
  });

  it.each(fixture.cases)("parse — $name", ({ href, parse }) => {
    if (parse === null) {
      expect(() => parseStagedHref(href)).toThrow(StorageKeyError);
    } else {
      expect(parseStagedHref(href)).toEqual({
        uploadId: parse.upload_id,
        filename: parse.filename,
      });
      // Round-trip: re-minting from the parsed parts reproduces the href.
      expect(stagedHref(parse.upload_id, parse.filename)).toBe(href);
    }
  });
});

interface StatusCase {
  name: string;
  doc: unknown;
  app: "accept" | "reject";
  pipeline: "accept" | "reject";
}

describe("push upload status contract (tests/contract-fixtures/push-upload-status.json)", () => {
  const fixture = JSON.parse(
    readFileSync(
      fileURLToPath(
        new URL(
          "../../../tests/contract-fixtures/push-upload-status.json",
          import.meta.url,
        ),
      ),
      "utf8",
    ),
  ) as {
    statuses: string[];
    terminal: string[];
    reasons: string[];
    cases: StatusCase[];
  };

  it("pins the status enum on both sides", () => {
    expect(fixture.statuses).toEqual([...STAGED_UPLOAD_STATUSES]);
    expect(fixture.terminal).toEqual([...TERMINAL_STAGED_UPLOAD_STATUSES]);
  });

  it("pins the closed rejection-reason set", () => {
    expect(fixture.reasons).toEqual([...PUSH_REJECTION_REASONS]);
  });

  it.each(fixture.cases)("$app: $name", ({ doc, app }) => {
    expect(pushUploadStatusSchema.safeParse(doc).success).toBe(app === "accept");
  });
});
