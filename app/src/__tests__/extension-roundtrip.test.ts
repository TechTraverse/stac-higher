import { describe, it, expect, vi } from "vitest";

// Mock UI components and map dependencies that don't work in jsdom
vi.mock("@/components/layout/AppShell", () => ({
  AppShell: ({ children }: { children: unknown }) => children,
}));
vi.mock("@/components/layout/QueryProvider", () => ({
  QueryProvider: ({ children }: { children: unknown }) => children,
}));
vi.mock("@/components/items/ItemGeometryEditor", () => ({ ItemGeometryEditor: () => null }));
vi.mock("@/components/extensions/ExtensionPicker", () => ({ ExtensionPicker: () => null }));
vi.mock("@/components/extensions/ExtensionFields", () => ({ ExtensionFields: () => null }));
vi.mock("@stac-higher/shared", () => ({
  JsonViewer: () => null,
  BboxInput: () => null,
  StacMap: () => null,
  ExtentLayer: () => null,
  FootprintLayer: () => null,
  bboxToLngLatBounds: () => ({ west: 0, south: 0, east: 1, north: 1 }),
  geometryToBbox: () => [0, 0, 1, 1],
}));
vi.mock("@/lib/map/bbox", () => ({
  bboxToLngLatBounds: vi.fn(() => ({ west: 0, south: 0, east: 1, north: 1 })),
  geometryToBbox: vi.fn(() => [0, 0, 1, 1]),
}));
vi.mock("@nanostores/react", () => ({ useStore: vi.fn(() => ({ url: "http://localhost:8082" })) }));
vi.mock("@/stores/catalogStore", () => ({ $builtInCatalog: null }));
vi.mock("@/lib/query/items", () => ({
  useCreateItem: vi.fn(() => ({ mutate: vi.fn() })),
  useUpdateItem: vi.fn(() => ({ mutate: vi.fn() })),
}));
vi.mock("@/lib/query/collections", () => ({
  useCreateCollection: vi.fn(() => ({ mutate: vi.fn() })),
  useUpdateCollection: vi.fn(() => ({ mutate: vi.fn() })),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { formToStacItem, stacItemToForm } from "@/components/items/ItemForm";
import { formToStacCollection, stacCollectionToForm } from "@/components/collections/CollectionForm";
import type { ItemFormData } from "@/lib/stac-api/schemas";
import type { CollectionFormData } from "@/lib/stac-api/schemas";
import type { StacItem } from "@/lib/stac-api/types";
import type { StacCollection } from "@/lib/stac-api/types";

// ── Item round-trip tests ─────────────────────────────────────────────────────

describe("formToStacItem — extension_properties", () => {
  const baseFormData: ItemFormData = {
    id: "test-item",
    datetime: "2024-01-15T12:00",
    geometry: null,
    properties: [],
    assets: [],
    stac_extensions: ["https://example.com/eo/schema.json"],
    extension_properties: {},
  };

  it("merges extension_properties values into item.properties", () => {
    const data: ItemFormData = {
      ...baseFormData,
      extension_properties: {
        "https://example.com/eo/schema.json": {
          "eo:cloud_cover": 12,
          "eo:bands": [{ name: "B1" }],
        },
      },
    };
    const item = formToStacItem(data, "my-collection");
    expect(item.properties["eo:cloud_cover"]).toBe(12);
    expect(item.properties["eo:bands"]).toEqual([{ name: "B1" }]);
  });

  it("merges properties from multiple extension schemas", () => {
    const data: ItemFormData = {
      ...baseFormData,
      stac_extensions: [
        "https://example.com/eo/schema.json",
        "https://example.com/sar/schema.json",
      ],
      extension_properties: {
        "https://example.com/eo/schema.json": { "eo:cloud_cover": 5 },
        "https://example.com/sar/schema.json": { "sar:instrument_mode": "IW" },
      },
    };
    const item = formToStacItem(data, "my-collection");
    expect(item.properties["eo:cloud_cover"]).toBe(5);
    expect(item.properties["sar:instrument_mode"]).toBe("IW");
  });

  it("extension_properties override manual properties with the same key", () => {
    const data: ItemFormData = {
      ...baseFormData,
      properties: [{ key: "eo:cloud_cover", value: "99" }],
      extension_properties: {
        "https://example.com/eo/schema.json": { "eo:cloud_cover": 1 },
      },
    };
    const item = formToStacItem(data, "my-collection");
    // extProps are merged after additionalProps so they win
    expect(item.properties["eo:cloud_cover"]).toBe(1);
  });

  it("handles undefined extension_properties gracefully", () => {
    const data: ItemFormData = {
      ...baseFormData,
      extension_properties: undefined,
    };
    const item = formToStacItem(data, "my-collection");
    expect(item.properties).toBeDefined();
  });

  it("handles empty extension_properties gracefully", () => {
    const data: ItemFormData = {
      ...baseFormData,
      extension_properties: { "https://example.com/eo/schema.json": {} },
    };
    const item = formToStacItem(data, "my-collection");
    expect(item.properties.datetime).toBeDefined();
  });

  it("includes stac_extensions in output when present", () => {
    const item = formToStacItem(baseFormData, "my-collection");
    expect(item.stac_extensions).toEqual(["https://example.com/eo/schema.json"]);
  });

  it("omits stac_extensions from output when empty", () => {
    const data: ItemFormData = { ...baseFormData, stac_extensions: [] };
    const item = formToStacItem(data, "my-collection");
    expect(item.stac_extensions).toBeUndefined();
  });
});

describe("stacItemToForm — extension_properties", () => {
  const baseItem: StacItem = {
    type: "Feature",
    stac_version: "1.0.0",
    id: "test-item",
    geometry: null,
    bbox: undefined,
    properties: {
      datetime: "2024-01-15T12:00:00Z",
      "eo:cloud_cover": 20,
      "sar:instrument_mode": "IW",
    },
    links: [],
    assets: {},
    collection: "my-collection",
    stac_extensions: [
      "https://example.com/eo/schema.json",
      "https://example.com/sar/schema.json",
    ],
  };

  it("seeds each extension URL with the full item.properties", () => {
    const form = stacItemToForm(baseItem);
    expect(form.extension_properties).toBeDefined();
    const eoProps = form.extension_properties!["https://example.com/eo/schema.json"];
    expect(eoProps).toMatchObject({ "eo:cloud_cover": 20 });
  });

  it("seeds all extension URLs with the same properties object", () => {
    const form = stacItemToForm(baseItem);
    const eoProps = form.extension_properties!["https://example.com/eo/schema.json"];
    const sarProps = form.extension_properties!["https://example.com/sar/schema.json"];
    expect(eoProps).toMatchObject({ "eo:cloud_cover": 20 });
    expect(sarProps).toMatchObject({ "sar:instrument_mode": "IW" });
  });

  it("produces empty extension_properties when no stac_extensions", () => {
    const item: StacItem = { ...baseItem, stac_extensions: undefined };
    const form = stacItemToForm(item);
    expect(form.extension_properties).toEqual({});
  });

  it("preserves stac_extensions in form data", () => {
    const form = stacItemToForm(baseItem);
    expect(form.stac_extensions).toEqual([
      "https://example.com/eo/schema.json",
      "https://example.com/sar/schema.json",
    ]);
  });
});

describe("item form full round-trip", () => {
  it("survives form → item → form round-trip without data loss", () => {
    const originalItem: StacItem = {
      type: "Feature",
      stac_version: "1.0.0",
      id: "roundtrip-item",
      geometry: null,
      bbox: undefined,
      properties: {
        datetime: "2024-03-01T00:00:00Z",
        "eo:cloud_cover": 42,
      },
      links: [],
      assets: {},
      collection: "test-col",
      stac_extensions: ["https://example.com/eo/schema.json"],
    };

    const formData = stacItemToForm(originalItem);
    // Simulate the user not touching extension fields (extension_properties seeded from properties)
    const reconstructed = formToStacItem(formData, "test-col", "1.0.0");

    expect(reconstructed.id).toBe("roundtrip-item");
    expect(reconstructed.stac_extensions).toEqual(["https://example.com/eo/schema.json"]);
    expect(reconstructed.properties["eo:cloud_cover"]).toBe(42);
  });
});

// ── Collection round-trip tests ───────────────────────────────────────────────

describe("formToStacCollection — extension_properties", () => {
  const baseFormData: CollectionFormData = {
    id: "test-collection",
    title: "Test",
    description: "A test collection",
    license: "CC-BY-4.0",
    spatial_bbox: [-180, -90, 180, 90],
    temporal_start: "",
    temporal_end: "",
    stac_extensions: ["https://example.com/eo/schema.json"],
    extension_properties: {},
  };

  it("merges extension_properties into summaries", () => {
    const data: CollectionFormData = {
      ...baseFormData,
      extension_properties: {
        "https://example.com/eo/schema.json": {
          "eo:cloud_cover": [0, 100],
          "eo:bands": [{ name: "B1" }, { name: "B2" }],
        },
      },
    };
    const collection = formToStacCollection(data);
    expect(collection.summaries?.["eo:cloud_cover"]).toEqual([0, 100]);
    expect(collection.summaries?.["eo:bands"]).toEqual([{ name: "B1" }, { name: "B2" }]);
  });

  it("merges properties from multiple extension schemas into summaries", () => {
    const data: CollectionFormData = {
      ...baseFormData,
      stac_extensions: [
        "https://example.com/eo/schema.json",
        "https://example.com/sar/schema.json",
      ],
      extension_properties: {
        "https://example.com/eo/schema.json": { "eo:cloud_cover": [0, 50] },
        "https://example.com/sar/schema.json": { "sar:frequency_band": ["C"] },
      },
    };
    const collection = formToStacCollection(data);
    expect(collection.summaries?.["eo:cloud_cover"]).toEqual([0, 50]);
    expect(collection.summaries?.["sar:frequency_band"]).toEqual(["C"]);
  });

  it("omits summaries when extension_properties are all empty", () => {
    const data: CollectionFormData = {
      ...baseFormData,
      extension_properties: { "https://example.com/eo/schema.json": {} },
    };
    const collection = formToStacCollection(data);
    expect(collection.summaries).toBeUndefined();
  });

  it("handles undefined extension_properties", () => {
    const data: CollectionFormData = {
      ...baseFormData,
      extension_properties: undefined,
    };
    const collection = formToStacCollection(data);
    expect(collection.summaries).toBeUndefined();
  });
});

describe("stacCollectionToForm — extension_properties", () => {
  const baseCollection: StacCollection = {
    type: "Collection",
    stac_version: "1.0.0",
    id: "test-collection",
    description: "A test collection",
    license: "CC-BY-4.0",
    extent: {
      spatial: { bbox: [[-180, -90, 180, 90]] },
      temporal: { interval: [[null, null]] },
    },
    links: [],
    stac_extensions: [
      "https://example.com/eo/schema.json",
      "https://example.com/sar/schema.json",
    ],
    summaries: {
      "eo:cloud_cover": [0, 100],
      "sar:instrument_mode": ["IW", "EW"],
    },
  };

  it("seeds each extension URL with collection.summaries", () => {
    const form = stacCollectionToForm(baseCollection);
    expect(form.extension_properties).toBeDefined();
    const eoProps = form.extension_properties!["https://example.com/eo/schema.json"];
    expect(eoProps).toMatchObject({ "eo:cloud_cover": [0, 100] });
  });

  it("seeds all extension URLs with the same summaries", () => {
    const form = stacCollectionToForm(baseCollection);
    const sarProps = form.extension_properties!["https://example.com/sar/schema.json"];
    expect(sarProps).toMatchObject({ "sar:instrument_mode": ["IW", "EW"] });
  });

  it("produces empty extension_properties when no stac_extensions", () => {
    const collection: StacCollection = { ...baseCollection, stac_extensions: undefined };
    const form = stacCollectionToForm(collection);
    expect(form.extension_properties).toEqual({});
  });

  it("seeds with empty object when collection has no summaries", () => {
    const collection: StacCollection = { ...baseCollection, summaries: undefined };
    const form = stacCollectionToForm(collection);
    expect(form.extension_properties!["https://example.com/eo/schema.json"]).toEqual({});
  });
});

describe("collection form full round-trip", () => {
  it("survives form → collection → form round-trip without data loss", () => {
    const originalCollection: StacCollection = {
      type: "Collection",
      stac_version: "1.0.0",
      id: "roundtrip-col",
      description: "Round-trip test",
      license: "MIT",
      extent: {
        spatial: { bbox: [[-10, -10, 10, 10]] },
        temporal: { interval: [[null, null]] },
      },
      links: [],
      stac_extensions: ["https://example.com/eo/schema.json"],
      summaries: { "eo:cloud_cover": [0, 50] },
    };

    const formData = stacCollectionToForm(originalCollection);
    const reconstructed = formToStacCollection(formData, "1.0.0");

    expect(reconstructed.id).toBe("roundtrip-col");
    expect(reconstructed.stac_extensions).toEqual(["https://example.com/eo/schema.json"]);
    expect(reconstructed.summaries?.["eo:cloud_cover"]).toEqual([0, 50]);
  });
});
