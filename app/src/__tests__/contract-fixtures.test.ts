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
import { s3ConfigSchema } from "@/lib/connections/schemas";
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
import {
  builtinExtractorSchema,
  moduleName,
  parseBuiltinExtractors,
  pinFor,
} from "@/lib/extractors/schemas";
import {
  processEnvSchema,
  processExpectationSchema,
  processRuntimeReadSchema,
  processRuntimeSchema,
  processTriggerSchema,
} from "@/lib/processes/schemas";
import { hardwareProfileSetSchema } from "@/lib/processes/hardware";
import {
  DEPLOY_IMAGE_STATUSES,
  IMAGE_GATE_REASONS,
  IMAGE_SCAN_KINDS,
  IMAGE_SCAN_STATUSES,
  IMAGE_STATUSES,
  IMAGE_STATUS_LABEL,
  LAUNCH_IMAGE_STATUSES,
  type ImageStatus,
} from "@/lib/images/status";
import { isImageDigest, isImageReference } from "@/lib/images/reference";
import { imagePolicySchema, registryAllowed } from "@/lib/images/policy";
import { imageScanResultSchema } from "@/lib/images/scan-result";

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

describe("s3 connection config contract (tests/contract-fixtures/s3-connection-config.json)", () => {
  describeDirection("s3-connection-config.json", s3ConfigSchema);
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
// Phase 9 / M5-0 fixtures (design spec §3) — the process shapes. `env` and
// `expectation` are ordinary minimal/defaults/cases documents; `trigger` and
// `runtime` are the discriminated-union style (one minimal/defaults pair per
// arm), documented in tests/contract-fixtures/README.md.
// ---------------------------------------------------------------------------

describe("process expectation contract (tests/contract-fixtures/process-expectation.json)", () => {
  describeDirection("process-expectation.json", processExpectationSchema);
});

describe("process env contract (tests/contract-fixtures/process-env.json)", () => {
  describeDirection("process-env.json", processEnvSchema);
});

interface UnionFixture {
  discriminator: string;
  variants: Record<string, { minimal: unknown; defaults: unknown }>;
  cases: FixtureCase[];
}

/**
 * One minimal/defaults pair per union arm, then the shared cases.
 * `defaultsSchema` differs from `caseSchema` only where a write gate refuses
 * an arm the shape itself admits (process runtime's `container`) — the
 * defaults still have to round-trip through the full shape.
 */
function describeUnion(
  file: string,
  caseSchema: ZodType,
  defaultsSchema: ZodType = caseSchema,
) {
  const fixture = loadFixture(file) as unknown as UnionFixture;

  it.each(Object.entries(fixture.variants))(
    "writes exactly the golden defaults for the minimal %s document",
    (_arm, variant) => {
      expect(defaultsSchema.parse(variant.minimal)).toEqual(variant.defaults);
    },
  );

  it.each(fixture.cases)("$app: $name", ({ config, app }) => {
    expect(caseSchema.safeParse(config).success).toBe(app === "accept");
  });
}

describe("process trigger contract (tests/contract-fixtures/process-trigger.json)", () => {
  describeUnion("process-trigger.json", processTriggerSchema);
});

describe("process runtime contract (tests/contract-fixtures/process-runtime.json)", () => {
  // The fixture's `app` column is the WRITE gate (network rule); defaults round-trip through the read schema.
  describeUnion(
    "process-runtime.json",
    processRuntimeSchema,
    processRuntimeReadSchema,
  );

  it("the write gate accepts every kind's minimal document (C-1: the image check is the route's)", () => {
    const fixture = loadFixture("process-runtime.json") as unknown as UnionFixture;
    for (const [kind, variant] of Object.entries(fixture.variants)) {
      expect(processRuntimeSchema.safeParse(variant.minimal).success, kind).toBe(true);
    }
    expect(Object.keys(fixture.variants)).toEqual([
      "inline_python",
      "inline_python_on_image",
      "container",
    ]);
  });
});

describe("hardware profiles contract (tests/contract-fixtures/hardware-profiles.json)", () => {
  const fixture = loadFixture("hardware-profiles.json") as unknown as {
    document: unknown;
    cases: { name: string; document: unknown; app: "accept" | "reject" }[];
  };
  it.each(fixture.cases)("$name", ({ document, app }) => {
    const doc = document === "$document" ? fixture.document : document;
    expect(hardwareProfileSetSchema.safeParse(doc).success).toBe(app === "accept");
  });
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
  ) as {
    kinds: string[];
    monitor_kinds: string[];
    notify_kinds: string[];
    declared_kinds: string[];
  };

  it("the writer lists partition the full enum exactly", () => {
    expect([
      ...fixture.monitor_kinds,
      ...fixture.declared_kinds,
      ...fixture.notify_kinds,
    ]).toEqual(fixture.kinds);
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

  it("the three process kinds are monitor-owned (Phase 9 §8)", () => {
    // M5-0 parked them in `declared_kinds` rather than guessing a writer;
    // M5-E claimed all three for the monitor, because each is evaluated as a
    // CONDITION observed from state — which is what makes auto-resolve fall
    // out of the condition's absence.
    for (const kind of [
      "process_stalled",
      "process_failed",
      "process_rate_limited",
    ]) {
      expect(fixture.monitor_kinds, kind).toContain(kind);
      expect(fixture.declared_kinds, kind).not.toContain(kind);
    }
  });

  it("the monitoring UI labels every kind in the enum", () => {
    for (const kind of fixture.kinds) {
      expect(ALERT_KIND_LABEL[kind], `label for ${kind}`).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// X-1: the built-in extractor registry (X-queue spec §5). `registry` style —
// the document IS the golden value, so there is no minimal/defaults pair: the
// cases run against a single entry and the file is parsed whole.
// ---------------------------------------------------------------------------

describe("built-in extractor registry (tests/contract-fixtures/builtin-extractors.json)", () => {
  const fixture = loadFixture("builtin-extractors.json") as unknown as {
    style: string;
    extractors: unknown[];
    cases: FixtureCase[];
  };

  it.each(fixture.cases)("$app: $name", ({ config, app }) => {
    expect(builtinExtractorSchema.safeParse(config).success).toBe(
      app === "accept",
    );
  });

  it("parses the whole registry", () => {
    const entries = parseBuiltinExtractors(fixture);
    expect(fixture.style).toBe("registry");
    expect(entries).toHaveLength(fixture.extractors.length);
    // Eleven, not the spec's fourteen: noaa-nwm, noaa-sst and hls exist only
    // as untagged GitHub repos and have never been released to PyPI, so they
    // carry no pin (lead's call 2026-09-04; I-107).
    expect(entries).toHaveLength(11);
  });

  it("derives the import name and the image pin the same way the pipeline does", () => {
    const entries = parseBuiltinExtractors(fixture);
    const byId = Object.fromEntries(entries.map((e) => [e.id, e]));
    expect(moduleName(byId["stactools-goes"])).toBe("stactools.goes");
    expect(moduleName(byId["stactools-goes-glm"])).toBe("stactools.goes_glm");
    expect(pinFor(byId["stactools-goes"])).toBe("stactools-goes==0.1.8");
    for (const entry of entries) {
      expect(moduleName(entry)).not.toContain("-");
    }
  });

  it("refuses a registry with a duplicate id", () => {
    const one = fixture.extractors[0];
    expect(() =>
      parseBuiltinExtractors({ ...fixture, extractors: [one, one] }),
    ).toThrow();
  });
});

// ---------------------------------------------------------------------------
// C queue, C-1 (container-images spec §3/§4): the image vocabularies and the
// reference/digest grammar.
// ---------------------------------------------------------------------------

describe("image status vocabularies (tests/contract-fixtures/image-status.json)", () => {
  const fixture = loadFixture("image-status.json") as unknown as {
    image_statuses: string[];
    deploy_statuses: string[];
    launch_statuses: string[];
    scan_kinds: string[];
    scan_statuses: string[];
    gate_reasons: string[];
  };

  it("pins every vocabulary verbatim, order included", () => {
    expect(fixture.image_statuses).toEqual([...IMAGE_STATUSES]);
    expect(fixture.deploy_statuses).toEqual([...DEPLOY_IMAGE_STATUSES]);
    expect(fixture.launch_statuses).toEqual([...LAUNCH_IMAGE_STATUSES]);
    expect(fixture.scan_kinds).toEqual([...IMAGE_SCAN_KINDS]);
    expect(fixture.scan_statuses).toEqual([...IMAGE_SCAN_STATUSES]);
    expect(fixture.gate_reasons).toEqual([...IMAGE_GATE_REASONS]);
  });

  it("deploy ⊆ launch ⊆ statuses: flagged launches but does not deploy", () => {
    for (const s of fixture.deploy_statuses) expect(fixture.launch_statuses).toContain(s);
    for (const s of fixture.launch_statuses) expect(fixture.image_statuses).toContain(s);
    expect(fixture.launch_statuses).toContain("flagged");
    expect(fixture.deploy_statuses).not.toContain("flagged");
  });

  it("labels every status, so a status cannot land unlabelled (the ALERT_KIND_LABEL rule)", () => {
    for (const status of fixture.image_statuses) {
      expect(IMAGE_STATUS_LABEL[status as ImageStatus], status).toBeTruthy();
    }
  });
});

describe("image reference grammar (tests/contract-fixtures/image-reference.json)", () => {
  const fixture = loadFixture("image-reference.json") as unknown as {
    cases: { name: string; value: string; reference: boolean }[];
    digest_cases: { name: string; value: string; digest: boolean }[];
  };
  it.each(fixture.cases)("reference — $name", ({ value, reference }) => {
    expect(isImageReference(value)).toBe(reference);
  });
  it.each(fixture.digest_cases)("digest — $name", ({ value, digest }) => {
    expect(isImageDigest(value)).toBe(digest);
  });
});

interface PolicyCaseShape {
  patch?: Record<string, unknown>;
  block_patch?: Record<string, unknown>;
  remove?: string[];
  block_remove?: string[];
}

/** image-policy.json's case rule: patch, block_patch, then the removals. */
function policyDoc(document: Record<string, unknown>, c: PolicyCaseShape): Record<string, unknown> {
  const doc: Record<string, unknown> = { ...document, ...(c.patch ?? {}) };
  const block: Record<string, unknown> = {
    ...(document.block as Record<string, unknown>),
    ...(c.block_patch ?? {}),
  };
  for (const key of c.block_remove ?? []) delete block[key];
  doc.block = block;
  for (const key of c.remove ?? []) delete doc[key];
  return doc;
}

describe("image policy contract (tests/contract-fixtures/image-policy.json)", () => {
  const fixture = loadFixture("image-policy.json") as unknown as {
    document: Record<string, unknown> & { allowed_registries: string[] };
    cases: (PolicyCaseShape & { name: string; app: "accept" | "reject" })[];
    registry_cases: { name: string; host: string; allowed: boolean }[];
  };

  it("the in-repo default policy IS the fixture document", () => {
    const shipped = JSON.parse(
      readFileSync(
        fileURLToPath(new URL("../../../infra/image-policy/default.json", import.meta.url)),
        "utf8",
      ),
    );
    expect(shipped).toEqual(fixture.document);
  });

  it.each(fixture.cases)("$app: $name", (c) => {
    expect(imagePolicySchema.safeParse(policyDoc(fixture.document, c)).success).toBe(
      c.app === "accept",
    );
  });

  it.each(fixture.registry_cases)("registry — $name", ({ host, allowed }) => {
    expect(registryAllowed(host, fixture.document.allowed_registries)).toBe(allowed);
  });
});

describe("image scan result contract (tests/contract-fixtures/image-scan-result.json)", () => {
  const fixture = loadFixture("image-scan-result.json") as unknown as {
    document: Record<string, unknown>;
    cases: {
      name: string;
      doc?: unknown;
      patch?: Record<string, unknown>;
      remove?: string[];
      app: "accept" | "reject";
    }[];
  };

  function scanDoc(c: (typeof fixture.cases)[number]): unknown {
    if (c.doc !== undefined) return c.doc;
    const doc: Record<string, unknown> = { ...fixture.document, ...(c.patch ?? {}) };
    for (const key of c.remove ?? []) delete doc[key];
    return doc;
  }

  it.each(fixture.cases)("$app: $name", (c) => {
    expect(imageScanResultSchema.safeParse(scanDoc(c)).success).toBe(c.app === "accept");
  });
});
