// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ApiCubeSink, CubeLedgerRow } from "@/lib/cubes/storage";
import type { CubeMaintenance } from "@/lib/cubes/schemas";
import {
  CUBE_APPENDED_REASON_LABEL,
  CUBE_FAILED_REASON_LABEL,
  CUBE_SKIP_REASON_LABEL,
  cubeLedgerReasonLabel,
  cubeSinkHealth,
  referenceSourceRefusal,
} from "@/lib/cubes/status";

describe("cubeLedgerReasonLabel", () => {
  it("labels a duplicate on an appended row", () => {
    expect(cubeLedgerReasonLabel("appended", "duplicate")).toBe(CUBE_APPENDED_REASON_LABEL.duplicate);
  });

  it("labels crash_loop and shows other failed text verbatim", () => {
    expect(cubeLedgerReasonLabel("failed", "crash_loop")).toBe(CUBE_FAILED_REASON_LABEL.crash_loop);
    expect(cubeLedgerReasonLabel("failed", "OSError: [Errno 13] denied")).toBe("OSError: [Errno 13] denied");
  });

  it("labels a skip reason and passes an unknown one through", () => {
    expect(cubeLedgerReasonLabel("skipped", "late")).toBe(CUBE_SKIP_REASON_LABEL.late);
    expect(cubeLedgerReasonLabel("skipped", "from_the_future")).toBe("from_the_future");
  });

  it("null reason is null", () => {
    expect(cubeLedgerReasonLabel("appended", null)).toBeNull();
  });

  it("a reason under the wrong status is not relabelled", () => {
    expect(cubeLedgerReasonLabel("skipped", "duplicate")).toBe("duplicate");
  });
});

const docs = (
  JSON.parse(
    readFileSync(
      fileURLToPath(new URL("../../../tests/contract-fixtures/cube-maintenance-summary.json", import.meta.url)),
      "utf8",
    ),
  ) as { documents: Record<string, CubeMaintenance> }
).documents;

const NOW = new Date("2026-11-03T13:00:00Z");
const HOUR = 3_600_000;
const baseConfig: ApiCubeSink["config"] = {
  parser: "hdf5",
  append_dim: "t",
  variables: ["CMI"],
  loadable_variables: ["t"],
  asset_key: "cube",
  on_late: "skip",
};

function sink(over: Partial<Pick<ApiCubeSink, "enabled" | "last_error" | "last_maintenance" | "last_appended_at" | "config">> = {}) {
  return {
    enabled: true,
    last_error: null,
    last_maintenance: null,
    last_appended_at: "2026-11-03T12:00:00.000Z",
    config: { ...baseConfig, window: { max_age: "24h" } },
    ...over,
  };
}

const failedRow = { status: "failed", reason: "crash_loop" } as CubeLedgerRow;
const ledger = (recent: CubeLedgerRow[] = []) => ({ recent });
const sinceNow = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

describe("cubeSinkHealth", () => {
  it("healthy", () => {
    expect(cubeSinkHealth(sink(), ledger(), NOW)).toEqual({ state: "ok", reasons: [] });
  });

  it("disabled wins over everything, and keeps the error as a reason", () => {
    expect(cubeSinkHealth(sink({ enabled: false, last_error: "source_collection_deleted" }), ledger([failedRow]), NOW)).toEqual({
      state: "disabled",
      reasons: ["last_error"],
    });
  });

  it("last_error is error", () => {
    expect(
      cubeSinkHealth(sink({ last_error: "boom", last_maintenance: docs.publish_failed }), ledger(), NOW),
    ).toEqual({ state: "error", reasons: ["last_error"] });
  });

  it("a failed maintenance run is error", () => {
    expect(cubeSinkHealth(sink({ last_maintenance: docs.failed }), ledger(), NOW)).toEqual({
      state: "error",
      reasons: ["maintenance_failed"],
    });
  });

  it("maintenance attention carries its reasons", () => {
    expect(cubeSinkHealth(sink({ last_maintenance: docs.publish_failed }), ledger(), NOW)).toEqual({
      state: "attention",
      reasons: ["publish_failed"],
    });
  });

  it("a failed row among recent is attention", () => {
    expect(cubeSinkHealth(sink(), ledger([failedRow]), NOW).reasons).toContain("failed_rows");
    expect(cubeSinkHealth(sink({ last_maintenance: docs.measure_failed }), ledger([failedRow]), NOW)).toEqual({
      state: "attention",
      reasons: ["measure_failed", "failed_rows"],
    });
  });

  it("stale once the whole max_age window has passed without an append", () => {
    expect(cubeSinkHealth(sink({ last_appended_at: sinceNow(24 * HOUR + 1) }), ledger(), NOW)).toEqual({
      state: "stale",
      reasons: ["no_append_within_window"],
    });
    expect(cubeSinkHealth(sink({ last_appended_at: sinceNow(24 * HOUR) }), ledger(), NOW).state).toBe("ok");
  });

  it("never stale without max_age", () => {
    const week = sinceNow(7 * 24 * HOUR);
    const steps = { ...baseConfig, window: { max_steps: 288 } };
    expect(cubeSinkHealth(sink({ config: steps, last_appended_at: week }), ledger(), NOW).state).toBe("ok");
    const none = { ...baseConfig };
    expect(cubeSinkHealth(sink({ config: none, last_appended_at: week }), ledger(), NOW).state).toBe("ok");
  });

  it("waiting before the first append", () => {
    expect(cubeSinkHealth(sink({ last_appended_at: null }), ledger(), NOW)).toEqual({ state: "waiting", reasons: [] });
  });

  it("attention outranks stale and waiting", () => {
    expect(
      cubeSinkHealth(sink({ last_appended_at: null, last_maintenance: docs.measure_failed }), ledger([failedRow]), NOW).state,
    ).toBe("attention");
  });
});

describe("referenceSourceRefusal", () => {
  it("no reference ingest", () => {
    expect(referenceSourceRefusal([])?.code).toBe("no_reference_ingest");
  });
  it("an anonymous s3 source qualifies", () => {
    expect(referenceSourceRefusal([{ protocol: "s3", anonymous: true }])).toBeNull();
  });
  it("a non-s3 association refuses the source, naming the protocol", () => {
    const refusal = referenceSourceRefusal([
      { protocol: "s3", anonymous: true },
      { protocol: "sftp", anonymous: false },
    ]);
    expect(refusal?.code).toBe("unsupported_source_protocol");
    expect(refusal?.error).toContain("sftp");
  });
  it("a signed s3 association", () => {
    expect(referenceSourceRefusal([{ protocol: "s3", anonymous: false }])?.code).toBe("signed_source_unsupported");
  });
  it("the protocol check comes before the signed check", () => {
    expect(
      referenceSourceRefusal([
        { protocol: "ftp", anonymous: false },
        { protocol: "s3", anonymous: false },
      ])?.code,
    ).toBe("unsupported_source_protocol");
  });
});
