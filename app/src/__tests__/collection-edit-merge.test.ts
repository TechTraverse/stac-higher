import { describe, it, expect, vi } from "vitest";

// CollectionForm pulls in UI and map modules that don't run in jsdom; only
// its pure conversion functions are under test here.
vi.mock("@/components/layout/AppShell", () => ({
  AppShell: ({ children }: { children: unknown }) => children,
}));
vi.mock("@/components/layout/QueryProvider", () => ({
  QueryProvider: ({ children }: { children: unknown }) => children,
}));
vi.mock("@/components/extensions/ExtensionPicker", () => ({ ExtensionPicker: () => null }));
vi.mock("@/components/extensions/ExtensionFields", () => ({ ExtensionFields: () => null }));
vi.mock("@stac-higher/shared", () => ({
  JsonViewer: () => null,
  BboxInput: () => null,
  StacMap: () => null,
  ExtentLayer: () => null,
}));
vi.mock("@/lib/map/bbox", () => ({ bboxToLngLatBounds: vi.fn() }));
vi.mock("@nanostores/react", () => ({ useStore: vi.fn(() => ({ url: "http://localhost:8082" })) }));
vi.mock("@/stores/catalogStore", () => ({ $builtInCatalog: null }));
vi.mock("@/lib/query/collections", () => ({
  useCreateCollection: vi.fn(() => ({ mutate: vi.fn() })),
  useUpdateCollection: vi.fn(() => ({ mutate: vi.fn() })),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  formToStacCollection,
  loadedAsForm,
  stacCollectionToForm,
} from "@/components/collections/CollectionForm";
import { collectionFormSchema, type CollectionFormData } from "@/lib/stac-api/schemas";
import { mergeCollectionEdit, sameJson } from "@/lib/stac-api/collectionEdit";
import type { StacCollection } from "@/lib/stac-api/types";

const DATACUBE = "https://stac-extensions.github.io/datacube/v2.2.0/schema.json";

function cubeAsset(version: string, times: string[]) {
  return {
    href: "s3://stac-higher-staging/assets/goes-cube/_cube/",
    type: "application/vnd.zarr+icechunk",
    roles: ["data"],
    version,
    "stac_higher:cube_sink_id": "sink-1",
    "stac_higher:time_values": times,
  };
}

/** A cube collection as Z-5's writer leaves it, with extension fields. */
function cubeCollection(): StacCollection {
  return {
    type: "Collection",
    stac_version: "1.1.0",
    stac_extensions: [DATACUBE],
    id: "goes-cube",
    title: "GOES C13 cube",
    description: "Rolling virtual cube",
    license: "proprietary",
    extent: {
      spatial: { bbox: [[-150, -60, -20, 60]] },
      temporal: { interval: [["2026-11-02T00:00:00Z", "2026-11-02T01:00:00Z"]] },
    },
    links: [],
    assets: {
      cube: cubeAsset("SNAP1", ["2026-11-02T00:00:00Z", "2026-11-02T01:00:00Z"]),
      thumbnail: {
        href: "https://example.com/thumb.png",
        type: "image/png",
        title: "Thumbnail",
        roles: ["thumbnail"],
        "file:size": 1234,
      } as never,
    },
    "cube:dimensions": {
      t: { type: "temporal", extent: ["2026-11-02T00:00:00Z", "2026-11-02T01:00:00Z"] },
      x: { type: "spatial", axis: "x", extent: [-5434894, 5434894] },
    },
    item_assets: { data: { type: "application/x-netcdf", roles: ["data"] } },
    renders: { c13: { assets: ["cube"], colormap_name: "greys" } },
  } as StacCollection;
}

/** Open the form on `original`, apply `change` to its values, submit. */
function editThroughForm(
  original: StacCollection,
  change: (form: CollectionFormData) => void,
): StacCollection {
  const form = stacCollectionToForm(original);
  change(form);
  const submitted = collectionFormSchema.parse(form) as CollectionFormData;
  return formToStacCollection(submitted, original.stac_version);
}

describe("mergeCollectionEdit through the collection form (#102)", () => {
  it("editing the title keeps cube:dimensions, item_assets and renders byte for byte", () => {
    const original = cubeCollection();
    const edited = editThroughForm(original, (f) => {
      f.title = "Renamed";
    });

    const saved = mergeCollectionEdit({ loaded: loadedAsForm(original), edited, latest: original });

    expect(saved.title).toBe("Renamed");
    const raw = saved as unknown as Record<string, unknown>;
    const before = original as unknown as Record<string, unknown>;
    expect(JSON.stringify(raw["cube:dimensions"])).toBe(JSON.stringify(before["cube:dimensions"]));
    expect(JSON.stringify(raw.item_assets)).toBe(JSON.stringify(before.item_assets));
    expect(JSON.stringify(raw.renders)).toBe(JSON.stringify(before.renders));
    // Everything else is the stored document, unchanged.
    expect(sameJson({ ...raw, title: before.title }, before)).toBe(true);
  });

  it("a stale form does not roll back the cube asset, its time extent or the Datacube entry", () => {
    const original = cubeCollection();
    const edited = editThroughForm(original, (f) => {
      f.description = "Edited while the cube kept appending";
    });
    // The pipeline published twice while the form sat open.
    const latest = cubeCollection();
    latest.assets!.cube = cubeAsset("SNAP3", ["2026-11-02T00:00:00Z", "2026-11-02T02:00:00Z"]) as never;
    latest.extent.temporal = { interval: [["2026-11-02T00:00:00Z", "2026-11-02T02:00:00Z"]] };
    (latest as unknown as Record<string, unknown>)["cube:dimensions"] = { t: { type: "temporal" } };

    const saved = mergeCollectionEdit({ loaded: loadedAsForm(original), edited, latest });

    expect(saved.description).toBe("Edited while the cube kept appending");
    expect(saved.assets!.cube).toEqual(latest.assets!.cube);
    expect(saved.extent.temporal).toEqual(latest.extent.temporal);
    expect((saved as unknown as Record<string, unknown>)["cube:dimensions"]).toEqual({
      t: { type: "temporal" },
    });
    expect(saved.stac_extensions).toEqual([DATACUBE]);
  });

  it("the form cannot edit or delete a pipeline-owned asset", () => {
    const original = cubeCollection();
    const edited = editThroughForm(original, (f) => {
      f.assets = f.assets!.filter((a) => a.key !== "cube");
    });

    const saved = mergeCollectionEdit({ loaded: loadedAsForm(original), edited, latest: original });

    expect(saved.assets!.cube).toEqual(original.assets!.cube);
  });

  it("an edited asset keeps the fields the form doesn't know", () => {
    const original = cubeCollection();
    const edited = editThroughForm(original, (f) => {
      const thumb = f.assets!.find((a) => a.key === "thumbnail")!;
      thumb.asset.title = "Preview";
      thumb.asset.type = undefined;
    });

    const saved = mergeCollectionEdit({ loaded: loadedAsForm(original), edited, latest: original });

    expect(saved.assets!.thumbnail).toEqual({
      href: "https://example.com/thumb.png",
      title: "Preview",
      roles: ["thumbnail"],
      "file:size": 1234,
    });
  });

  it("a user's change wins over the stored value for the fields the form owns", () => {
    const original = cubeCollection();
    const edited = editThroughForm(original, (f) => {
      f.spatial_bbox = [-140, -50, -30, 50];
      f.keywords = ["goes"];
      f.stac_extensions = [];
      f.assets = f.assets!.filter((a) => a.key !== "thumbnail");
    });
    const latest = cubeCollection();
    latest.extent.temporal = { interval: [["2026-11-02T00:00:00Z", "2026-11-02T02:00:00Z"]] };

    const saved = mergeCollectionEdit({ loaded: loadedAsForm(original), edited, latest });

    expect(saved.extent.spatial).toEqual({ bbox: [[-140, -50, -30, 50]] });
    expect(saved.extent.temporal).toEqual(latest.extent.temporal); // not edited: stored wins
    expect(saved.keywords).toEqual(["goes"]);
    expect(saved.stac_extensions).toBeUndefined();
    expect(Object.keys(saved.assets!)).toEqual(["cube"]);
  });
});

describe("mergeCollectionEdit", () => {
  const base: StacCollection = {
    type: "Collection",
    stac_version: "1.0.0",
    id: "c",
    description: "d",
    license: "MIT",
    extent: { spatial: { bbox: [[0, 0, 1, 1]] }, temporal: { interval: [[null, null]] } },
    links: [],
  };

  it("keeps a Datacube entry another writer added after the user changed the extension list", () => {
    const loaded = { ...base, stac_extensions: ["https://a/schema.json"] };
    const edited = { ...base, stac_extensions: ["https://b/schema.json"] };
    const latest = { ...base, stac_extensions: ["https://a/schema.json", DATACUBE] };

    const saved = mergeCollectionEdit({ loaded, edited, latest });

    expect(saved.stac_extensions).toEqual(["https://b/schema.json", DATACUBE]);
  });

  it("keeps an asset another writer added while the form was open", () => {
    const latest = { ...base, assets: { extra: { href: "https://x/y.tif" } } };

    const saved = mergeCollectionEdit({ loaded: base, edited: { ...base, title: "T" }, latest });

    expect(saved.assets).toEqual({ extra: { href: "https://x/y.tif" } });
    expect(saved.title).toBe("T");
  });

  it("clearing an optional field removes it", () => {
    const loaded = { ...base, title: "Old" };
    const edited = { ...base, title: undefined };

    const saved = mergeCollectionEdit({ loaded, edited, latest: loaded });

    expect("title" in saved).toBe(false);
  });
});
