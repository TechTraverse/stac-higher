import { describe, it, expect } from "vitest";
import {
  stacLinkSchema,
  stacAssetSchema,
  stacProviderSchema,
  collectionFormSchema,
  itemFormSchema,
} from "@/lib/stac-api/schemas";

describe("stacLinkSchema", () => {
  it("accepts a valid link", () => {
    const result = stacLinkSchema.safeParse({
      href: "https://example.com",
      rel: "self",
    });
    expect(result.success).toBe(true);
  });

  it("accepts optional type and title", () => {
    const result = stacLinkSchema.safeParse({
      href: "https://example.com",
      rel: "root",
      type: "application/json",
      title: "Root",
    });
    expect(result.success).toBe(true);
  });

  it("rejects missing href", () => {
    const result = stacLinkSchema.safeParse({ rel: "self" });
    expect(result.success).toBe(false);
  });

  it("rejects empty rel", () => {
    const result = stacLinkSchema.safeParse({
      href: "https://example.com",
      rel: "",
    });
    expect(result.success).toBe(false);
  });
});

describe("stacAssetSchema", () => {
  it("accepts a minimal asset", () => {
    const result = stacAssetSchema.safeParse({ href: "/data/file.tif" });
    expect(result.success).toBe(true);
  });

  it("accepts full asset", () => {
    const result = stacAssetSchema.safeParse({
      href: "https://storage.example.com/data.tif",
      type: "image/tiff",
      title: "Data",
      description: "COG file",
      roles: ["data", "visual"],
    });
    expect(result.success).toBe(true);
  });

  it("rejects empty href", () => {
    const result = stacAssetSchema.safeParse({ href: "" });
    expect(result.success).toBe(false);
  });
});

describe("stacProviderSchema", () => {
  it("accepts a minimal provider", () => {
    const result = stacProviderSchema.safeParse({ name: "ACME" });
    expect(result.success).toBe(true);
  });

  it("accepts full provider", () => {
    const result = stacProviderSchema.safeParse({
      name: "ACME",
      description: "Data provider",
      roles: ["producer", "host"],
      url: "https://acme.example.com",
    });
    expect(result.success).toBe(true);
  });

  it("accepts empty string url", () => {
    const result = stacProviderSchema.safeParse({
      name: "ACME",
      url: "",
    });
    expect(result.success).toBe(true);
  });

  it("rejects invalid provider role", () => {
    const result = stacProviderSchema.safeParse({
      name: "ACME",
      roles: ["invalid-role"],
    });
    expect(result.success).toBe(false);
  });

  it("rejects empty name", () => {
    const result = stacProviderSchema.safeParse({ name: "" });
    expect(result.success).toBe(false);
  });
});

describe("collectionFormSchema", () => {
  const validCollection = {
    id: "my-collection",
    description: "A test collection",
    license: "CC-BY-4.0",
    spatial_bbox: [-180, -90, 180, 90],
  };

  it("accepts a valid minimal collection", () => {
    const result = collectionFormSchema.safeParse(validCollection);
    expect(result.success).toBe(true);
  });

  it("accepts a full collection", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      title: "My Collection",
      temporal_start: "2020-01-01T00:00:00Z",
      temporal_end: "2024-12-31T23:59:59Z",
      keywords: ["test", "satellite"],
      providers: [{ name: "ACME", roles: ["producer"] }],
      assets: [
        {
          key: "thumbnail",
          asset: { href: "https://example.com/thumb.png", type: "image/png" },
        },
      ],
      links: [{ href: "https://example.com", rel: "self" }],
    });
    expect(result.success).toBe(true);
  });

  it("rejects empty id", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      id: "",
    });
    expect(result.success).toBe(false);
  });

  it("rejects id with spaces", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      id: "my collection",
    });
    expect(result.success).toBe(false);
  });

  it("rejects id with special characters", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      id: "my@collection!",
    });
    expect(result.success).toBe(false);
  });

  it("accepts id with hyphens and underscores", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      id: "my_collection-2024",
    });
    expect(result.success).toBe(true);
  });

  it("rejects empty description", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      description: "",
    });
    expect(result.success).toBe(false);
  });

  it("rejects empty license", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      license: "",
    });
    expect(result.success).toBe(false);
  });

  it("rejects bbox with wrong length", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      spatial_bbox: [-180, -90],
    });
    expect(result.success).toBe(false);
  });

  it("rejects bbox with non-numbers", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      spatial_bbox: ["a", "b", "c", "d"],
    });
    expect(result.success).toBe(false);
  });

  it("accepts empty optional arrays", () => {
    const result = collectionFormSchema.safeParse({
      ...validCollection,
      keywords: [],
      providers: [],
      assets: [],
      links: [],
    });
    expect(result.success).toBe(true);
  });
});

describe("itemFormSchema", () => {
  const validItem = {
    id: "item-001",
    datetime: "2024-01-15T12:00:00",
    geometry: null,
  };

  it("accepts a valid minimal item", () => {
    const result = itemFormSchema.safeParse(validItem);
    expect(result.success).toBe(true);
  });

  it("accepts item with geometry", () => {
    const result = itemFormSchema.safeParse({
      ...validItem,
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-105, 40],
            [-104, 40],
            [-104, 41],
            [-105, 41],
            [-105, 40],
          ],
        ],
      },
    });
    expect(result.success).toBe(true);
  });

  it("accepts item with properties and assets", () => {
    const result = itemFormSchema.safeParse({
      ...validItem,
      properties: [
        { key: "eo:cloud_cover", value: "15" },
        { key: "platform", value: "sentinel-2a" },
      ],
      assets: [
        {
          key: "visual",
          asset: { href: "https://example.com/B04.tif", type: "image/tiff" },
        },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("rejects empty id", () => {
    const result = itemFormSchema.safeParse({ ...validItem, id: "" });
    expect(result.success).toBe(false);
  });

  it("rejects empty datetime", () => {
    const result = itemFormSchema.safeParse({ ...validItem, datetime: "" });
    expect(result.success).toBe(false);
  });

  it("accepts null geometry", () => {
    const result = itemFormSchema.safeParse({ ...validItem, geometry: null });
    expect(result.success).toBe(true);
  });

  it("accepts Point geometry", () => {
    const result = itemFormSchema.safeParse({
      ...validItem,
      geometry: { type: "Point", coordinates: [-105.0, 40.0] },
    });
    expect(result.success).toBe(true);
  });

  it("rejects property with empty key", () => {
    const result = itemFormSchema.safeParse({
      ...validItem,
      properties: [{ key: "", value: "test" }],
    });
    expect(result.success).toBe(false);
  });

  it("rejects asset with empty key", () => {
    const result = itemFormSchema.safeParse({
      ...validItem,
      assets: [{ key: "", asset: { href: "https://example.com/data.tif" } }],
    });
    expect(result.success).toBe(false);
  });
});
