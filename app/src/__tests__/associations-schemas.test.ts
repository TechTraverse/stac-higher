// @vitest-environment node
import { describe, it, expect } from "vitest";
import {
  deliveryConfigSchema,
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

  it("accepts a valid delivery association create payload", () => {
    const parsed = parseAssociationCreate({
      connection_id: CONN_UUID,
      direction: "deliver",
      config: { path_template: "{collection}/{item_id}/{filename}" },
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.direction).toBe("deliver");
      expect(parsed.data.enabled).toBe(true);
      expect(parsed.data.expectation).toBeNull();
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

describe("deliveryConfigSchema (§5.1)", () => {
  it("applies defaults for a minimal delivery config", () => {
    const parsed = deliveryConfigSchema.parse({
      path_template: "{collection}/{item_id}/{filename}",
    });
    expect(parsed.item_filter).toBeNull();
    expect(parsed.asset_keys).toBeNull();
    expect(parsed.payload).toEqual({
      item_json: false,
      checksums: null,
      completion_marker: false,
    });
    expect(parsed.on_update).toBe("redeliver");
    expect(parsed.overwrite).toBe("if_newer");
    expect(parsed.retry).toEqual({ max_attempts: 5, backoff: "exponential" });
    expect(parsed.max_concurrent_transfers).toBe(4);
  });

  it("requires a non-empty path_template", () => {
    expect(() => deliveryConfigSchema.parse({ path_template: "" })).toThrow();
  });

  it("accepts a delivery association create payload", () => {
    const result = parseAssociationCreate({
      connection_id: "11111111-1111-4111-8111-111111111111",
      direction: "deliver",
      config: { path_template: "{collection}/{item_id}/{filename}" },
    });
    expect(result.success).toBe(true);
  });
});
