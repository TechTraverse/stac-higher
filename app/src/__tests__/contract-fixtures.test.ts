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
import { EXPECTATION_BREACH_KIND } from "@/lib/monitoring/api";
import { ALERT_KIND_LABEL } from "@/components/monitoring/shared";

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

describe("alert kind enum (tests/contract-fixtures/alert-kinds.json)", () => {
  // pinned-enum style (P7-H): the pipeline suite pins its writer constants
  // (MONITOR_KINDS, WEBHOOK_FAILED_KIND) against the same lists.
  const fixture = JSON.parse(
    readFileSync(
      fileURLToPath(
        new URL(
          "../../../tests/contract-fixtures/alert-kinds.json",
          import.meta.url,
        ),
      ),
      "utf8",
    ),
  ) as { kinds: string[]; monitor_kinds: string[]; notify_kinds: string[] };

  it("the writer lists partition the full enum exactly", () => {
    expect([...fixture.monitor_kinds, ...fixture.notify_kinds]).toEqual(
      fixture.kinds,
    );
    expect(new Set(fixture.kinds).size).toBe(fixture.kinds.length);
  });

  it("pins the app's expectation-breach kind literals", () => {
    expect(EXPECTATION_BREACH_KIND.ingest).toBe("ingest_inactivity");
    expect(EXPECTATION_BREACH_KIND.deliver).toBe("delivery_slo");
    expect(fixture.monitor_kinds).toContain(EXPECTATION_BREACH_KIND.ingest);
    expect(fixture.monitor_kinds).toContain(EXPECTATION_BREACH_KIND.deliver);
  });

  it("push_rejected is a monitor-owned kind (Phase 7 §8)", () => {
    expect(fixture.monitor_kinds).toContain("push_rejected");
  });

  it("the monitoring UI labels every kind in the enum", () => {
    for (const kind of fixture.kinds) {
      expect(ALERT_KIND_LABEL[kind], `label for ${kind}`).toBeTruthy();
    }
  });
});
