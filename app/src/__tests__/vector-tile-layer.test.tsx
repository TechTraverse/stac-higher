import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("react-map-gl/maplibre", () => ({
  Source: ({ children, ...props }: Record<string, unknown> & { children?: React.ReactNode }) => (
    <div data-testid="source" data-props={JSON.stringify(props)}>
      {children}
    </div>
  ),
  Layer: (props: Record<string, unknown>) => (
    <div data-testid="layer" data-props={JSON.stringify(props)} />
  ),
}));

import { VectorTileLayer, vectorTileLayers, vectorTileLayerIds } from "@stac-higher/shared";

function source(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("source").dataset.props as string);
}
function layers(): Record<string, unknown>[] {
  return screen.getAllByTestId("layer").map((el) => JSON.parse(el.dataset.props as string));
}

describe("VectorTileLayer", () => {
  it("mounts one vector source from the TileJSON url with fill, line and circle layers", () => {
    render(<VectorTileLayer id="v" url="http://tipg/collections/public.roads/tiles/WebMercatorQuad/tilejson.json" />);

    expect(source()).toEqual({
      id: "v",
      type: "vector",
      url: "http://tipg/collections/public.roads/tiles/WebMercatorQuad/tilejson.json",
    });
    expect(layers().map((l) => [l.id, l.type, l["source-layer"]])).toEqual([
      ["v-fill", "fill", "default"],
      ["v-line", "line", "default"],
      ["v-circle", "circle", "default"],
    ]);
  });

  it("draws each geometry type once, via geometry-type filters", () => {
    render(<VectorTileLayer id="v" url="http://t/tilejson.json" />);

    const [fill, line, circle] = layers();
    expect(fill.filter).toEqual(["==", ["geometry-type"], "Polygon"]);
    expect(line.filter).toBeUndefined(); // outlines polygons and draws lines
    expect(circle.filter).toEqual(["==", ["geometry-type"], "Point"]);
  });

  it("takes a custom source layer, opacity and beforeId", () => {
    render(<VectorTileLayer id="v" url="http://t/tilejson.json" sourceLayer="countries" opacity={0.3} beforeId="top" />);

    for (const l of layers()) {
      expect(l).toMatchObject({ "source-layer": "countries", beforeId: "top" });
    }
    const [fill, line, circle] = layers();
    expect(fill.paint).toMatchObject({ "fill-opacity": 0.3 * 0.2 });
    expect(line.paint).toMatchObject({ "line-opacity": 0.3 });
    expect(circle.paint).toMatchObject({ "circle-opacity": 0.3 });
  });

  it("exposes the specs as a function for callers that need the ids", () => {
    const specs = vectorTileLayers("v", "default");
    expect(Object.values(specs).map((s) => s.id)).toEqual(["v-fill", "v-line", "v-circle"]);
  });

  it("hides all three layers without dropping the vector source", () => {
    render(<VectorTileLayer id="v" url="http://t/tilejson.json" visible={false} />);

    expect(source()).toMatchObject({ id: "v", type: "vector" });
    expect(layers().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
      { visibility: "none" },
    ]);
  });

  it("draws at the documented default opacities (fill 0.2, line 1, circle 1)", () => {
    const { fill, line, circle } = vectorTileLayers("v", "default");
    expect(fill.paint).toMatchObject({ "fill-opacity": 0.2 });
    expect(line.paint).toMatchObject({ "line-opacity": 1 });
    expect(circle.paint).toMatchObject({ "circle-opacity": 1 });
  });

  it("exposes the layer ids without building specs", () => {
    expect(vectorTileLayerIds("v")).toEqual({ fill: "v-fill", line: "v-line", circle: "v-circle" });
  });
});
