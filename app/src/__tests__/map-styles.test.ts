import { describe, it, expect } from "vitest";
import {
  FOOTPRINT_SOURCE,
  footprintFillLayer,
  footprintLineLayer,
  footprintLayerIds,
  footprintLayers,
} from "@stac-higher/shared";

describe("footprint layer specs", () => {
  it("keeps the legacy ids for the default source", () => {
    // The item, search and browse pages mount one FootprintLayer with these
    // exact ids; renaming them would silently detach any interactivity.
    expect(footprintLayerIds(FOOTPRINT_SOURCE)).toEqual({
      fill: "stac-footprint-fill",
      line: "stac-footprint-line",
    });
    expect(footprintFillLayer.id).toBe("stac-footprint-fill");
    expect(footprintLineLayer.id).toBe("stac-footprint-line");
    expect(footprintFillLayer.source).toBe(FOOTPRINT_SOURCE);
  });

  it("namespaces ids and the source for any other source id", () => {
    const { fill, line } = footprintLayers("layer-a");
    expect(fill).toMatchObject({ id: "layer-a-fill", type: "fill", source: "layer-a" });
    expect(line).toMatchObject({ id: "layer-a-line", type: "line", source: "layer-a" });
  });

  it("scales the fill's hover expression and the line by the given opacity", () => {
    const { fill, line } = footprintLayers("layer-a", 0.5);
    expect(fill.paint).toMatchObject({
      "fill-opacity": ["*", 0.5, ["case", ["boolean", ["feature-state", "hover"], false], 0.25, 0.1]],
    });
    expect(line.paint).toMatchObject({ "line-opacity": 0.5 });
  });

  it("emits the plain hover expression at full opacity", () => {
    // The default spec must be byte-identical to what the single-layer pages
    // rendered before: no wrapping expression, no line-opacity key.
    const { fill, line } = footprintLayers(FOOTPRINT_SOURCE);
    expect(fill.paint).toEqual(footprintFillLayer.paint);
    expect(line.paint).toEqual(footprintLineLayer.paint);
    expect((fill.paint as Record<string, unknown>)["fill-opacity"]).toEqual([
      "case",
      ["boolean", ["feature-state", "hover"], false],
      0.25,
      0.1,
    ]);
    expect(line.paint).not.toHaveProperty("line-opacity");
  });
});
