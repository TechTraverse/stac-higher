// @vitest-environment node
import { describe, it, expect } from "vitest";
import {
  DELIVERY_RESERVED_MESSAGE,
  ingestConfigSchema,
  parseAssociationCreate,
  parseAssociationUpdate,
} from "@/lib/associations/schemas";

const CONN_UUID = "3a9f1c2e-0000-4000-8000-000000000001";

describe("ingestConfigSchema", () => {
  it("fills §5.1 defaults from a minimal config", () => {
    const parsed = ingestConfigSchema.parse({ source_path: "/out" });
    expect(parsed.include).toEqual([]);
    expect(parsed.exclude).toEqual([]);
    expect(parsed.poll_frequency_seconds).toBe(300);
    expect(parsed.storage_mode).toBe("copy");
    expect(parsed.grouping).toEqual({
      rule: "none",
      timeout_seconds: 900,
      on_timeout: "ingest_partial",
    });
    expect(parsed.metadata.strategy).toBe("raster_auto");
    expect(parsed.metadata.defaults).toEqual({});
    expect(parsed.post_ingest).toBe("leave");
  });

  it("requires a source_path", () => {
    expect(ingestConfigSchema.safeParse({}).success).toBe(false);
  });

  it("enforces the 60s poll-frequency floor", () => {
    expect(
      ingestConfigSchema.safeParse({ source_path: "/o", poll_frequency_seconds: 30 })
        .success,
    ).toBe(false);
  });

  it("accepts leave/delete/move: post-ingest, rejects others", () => {
    for (const v of ["leave", "delete", "move:/archive"]) {
      expect(
        ingestConfigSchema.safeParse({ source_path: "/o", post_ingest: v }).success,
      ).toBe(true);
    }
    expect(
      ingestConfigSchema.safeParse({ source_path: "/o", post_ingest: "move" })
        .success,
    ).toBe(false);
  });

  it("rejects unknown keys (strict contract with the pipeline)", () => {
    expect(
      ingestConfigSchema.safeParse({ source_path: "/o", bogus: true }).success,
    ).toBe(false);
  });

  it("rejects reference mode with post_ingest delete", () => {
    const result = ingestConfigSchema.safeParse({
      source_path: "/out",
      storage_mode: "reference",
      post_ingest: "delete",
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].path).toEqual(["post_ingest"]);
    }
  });

  it("rejects reference mode with post_ingest move", () => {
    const result = ingestConfigSchema.safeParse({
      source_path: "/out",
      storage_mode: "reference",
      post_ingest: "move:/archive",
    });
    expect(result.success).toBe(false);
  });

  it("allows reference mode with post_ingest leave", () => {
    const result = ingestConfigSchema.safeParse({
      source_path: "/out",
      storage_mode: "reference",
      post_ingest: "leave",
    });
    expect(result.success).toBe(true);
  });

  it("still allows copy mode with post_ingest delete", () => {
    const result = ingestConfigSchema.safeParse({
      source_path: "/out",
      storage_mode: "copy",
      post_ingest: "delete",
    });
    expect(result.success).toBe(true);
  });
});

describe("parseAssociationCreate", () => {
  it("accepts a valid ingest association and defaults expectation to null", () => {
    const parsed = parseAssociationCreate({
      connection_id: CONN_UUID,
      direction: "ingest",
      config: { source_path: "/out" },
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.enabled).toBe(true);
      expect(parsed.data.expectation).toBeNull();
    }
  });

  it("rejects direction 'deliver' with the reserved message", () => {
    const parsed = parseAssociationCreate({
      connection_id: CONN_UUID,
      direction: "deliver",
      config: { source_path: "/out" },
    });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(parsed.error.issues[0].message).toBe(DELIVERY_RESERVED_MESSAGE);
      expect(parsed.error.issues[0].path).toEqual(["direction"]);
    }
  });

  it("rejects a non-uuid connection_id", () => {
    const parsed = parseAssociationCreate({
      connection_id: "not-a-uuid",
      direction: "ingest",
      config: { source_path: "/out" },
    });
    expect(parsed.success).toBe(false);
  });
});

describe("parseAssociationUpdate", () => {
  it("accepts a partial patch (enabled only)", () => {
    const parsed = parseAssociationUpdate({ enabled: false });
    expect(parsed.success).toBe(true);
  });

  it("re-validates config when present", () => {
    expect(parseAssociationUpdate({ config: {} }).success).toBe(false);
    expect(
      parseAssociationUpdate({ config: { source_path: "/o" } }).success,
    ).toBe(true);
  });

  it("allows clearing the expectation with null", () => {
    const parsed = parseAssociationUpdate({ expectation: null });
    expect(parsed.success).toBe(true);
  });
});
