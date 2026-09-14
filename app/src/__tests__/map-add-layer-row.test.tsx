import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";

const { useItemsMock, useCollectionSettingsMock } = vi.hoisted(() => ({
  useItemsMock: vi.fn(),
  useCollectionSettingsMock: vi.fn(),
}));

vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: (...a: unknown[]) => useCollectionSettingsMock(...a),
}));

import {
  AddLayerCollectionRow,
  ADD_LAYER_PROBE_LIMIT,
} from "@/components/map/AddLayerCollectionRow";

const COLLECTION = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "goes-geocolor",
  title: "GOES GeoColor",
  description: "",
  license: "proprietary",
  extent: { spatial: { bbox: [[-137, 14, -52, 54]] }, temporal: { interval: [[null, null]] } },
  links: [],
} as unknown as StacCollection;

function item(id: string, assets: StacItem["assets"]): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: null,
    properties: { datetime: "2026-09-04T06:11:17.300000Z" },
    links: [],
    assets,
  };
}

const TILEABLE = [item("c", { visual: { href: "v.tif", roles: ["visual"] } })];
const NOT_TILEABLE = [item("c", { thumb: { href: "t.png", type: "image/png" } })];

function renderRow(
  added: Array<"footprints" | "imagery"> = [],
  onAdd = vi.fn(),
) {
  render(
    <AddLayerCollectionRow
      collection={COLLECTION}
      catalogUrl="http://localhost:8081"
      isAdded={(kind) => added.includes(kind as "footprints" | "imagery")}
      onAdd={onAdd}
    />,
  );
  return onAdd;
}

beforeEach(() => {
  vi.clearAllMocks();
  useItemsMock.mockReturnValue({ data: { features: TILEABLE } });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
});

describe("AddLayerCollectionRow", () => {
  it("always offers footprints, under V-2's test id and copy", () => {
    renderRow();

    const footprints = screen.getByTestId("map-add-footprints-goes-geocolor");
    expect(footprints.textContent).toContain("Footprints");
    expect(screen.getByText("GOES GeoColor")).toBeTruthy();
  });

  it("offers imagery when serving is on and the newest items carry a tileable asset", () => {
    renderRow();

    expect(screen.getByTestId("map-add-imagery-goes-geocolor")).toBeTruthy();
    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "goes-geocolor",
      expect.objectContaining({ limit: ADD_LAYER_PROBE_LIMIT, sortby: "-datetime" }),
    );
  });

  it("hides imagery when the product does not advertise serving", () => {
    useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });
    renderRow();

    expect(screen.queryByTestId("map-add-imagery-goes-geocolor")).toBeNull();
    expect(screen.getByTestId("map-add-footprints-goes-geocolor")).toBeTruthy();
  });

  it("hides imagery when nothing recent carries a tileable asset", () => {
    useItemsMock.mockReturnValue({ data: { features: NOT_TILEABLE } });
    renderRow();

    expect(screen.queryByTestId("map-add-imagery-goes-geocolor")).toBeNull();
  });

  it("hides imagery while the probe is still in flight", () => {
    useItemsMock.mockReturnValue({ data: undefined });
    renderRow();

    expect(screen.queryByTestId("map-add-imagery-goes-geocolor")).toBeNull();
  });

  it("shows a kind already on the map as Added and disabled", () => {
    renderRow(["footprints"]);

    const footprints = screen.getByTestId("map-add-footprints-goes-geocolor");
    expect(footprints).toBeDisabled();
    expect(footprints.textContent).toContain("Added");
    expect(screen.getByTestId("map-add-imagery-goes-geocolor")).not.toBeDisabled();
  });

  it("reports the kind and collection when a button is pressed", async () => {
    const onAdd = renderRow();

    await userEvent.click(screen.getByTestId("map-add-imagery-goes-geocolor"));

    expect(onAdd).toHaveBeenCalledWith("imagery", COLLECTION);
  });
});
