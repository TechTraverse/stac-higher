import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MapLayer } from "@/lib/map/state";

const { useLayerDataMock } = vi.hoisted(() => ({ useLayerDataMock: vi.fn() }));
vi.mock("@/components/map/useLayerData", () => ({
  useLayerData: (...a: unknown[]) => useLayerDataMock(...a),
}));

import { LayerAssetSelect } from "@/components/map/LayerAssetSelect";

const LAYER: MapLayer = {
  id: "l1",
  kind: "imagery",
  sourceId: "goes-geocolor",
  title: "GeoColor",
  asset: "visual",
  visible: true,
  opacity: 1,
};

function renderSelect(candidates: string[], asset: string | undefined = candidates[0]) {
  useLayerDataMock.mockReturnValue({
    items: [],
    frames: [],
    candidates,
    asset,
    hint: undefined,
    hintSettled: true,
    servingEnabled: true,
  });
  const onAssetChange = vi.fn();
  render(
    <LayerAssetSelect
      layer={LAYER}
      catalogUrl="http://localhost:8081"
      frameSpan={50}
      onAssetChange={onAssetChange}
    />,
  );
  return onAssetChange;
}

beforeEach(() => vi.clearAllMocks());

describe("LayerAssetSelect", () => {
  it("offers the collection's tileable keys when there is a choice", () => {
    renderSelect(["visual", "cmi"]);

    expect(screen.getByRole("combobox", { name: "Layer asset" })).toBeTruthy();
  });

  it("renders nothing when the collection publishes one tileable key", () => {
    // A select with one option is furniture, not a control.
    renderSelect(["visual"]);

    expect(screen.queryByRole("combobox", { name: "Layer asset" })).toBeNull();
  });

  it("reads the layer's data through the page's span", () => {
    renderSelect(["visual", "cmi"]);

    expect(useLayerDataMock).toHaveBeenCalledWith(LAYER, "http://localhost:8081", 50);
  });
});
