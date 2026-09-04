import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// The layer is pure composition over react-map-gl; mocking the two primitives
// lets the test assert the SPEC handed to maplibre without a WebGL context.
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

import { RasterTileLayer } from "@stac-higher/shared";

function propsOf(testId: string): Record<string, unknown> {
  return JSON.parse(screen.getByTestId(testId).dataset.props as string);
}

describe("RasterTileLayer", () => {
  it("renders a raster source and layer from the given tiles", () => {
    render(
      <RasterTileLayer
        tiles={["http://t/{z}/{x}/{y}.png"]}
        bounds={[-100, 30, -90, 40]}
        opacity={0.8}
        beforeId="item-geometry-fill"
      />,
    );

    expect(propsOf("source")).toMatchObject({
      id: "stac-raster-preview",
      type: "raster",
      tiles: ["http://t/{z}/{x}/{y}.png"],
      tileSize: 256,
      bounds: [-100, 30, -90, 40],
    });
    expect(propsOf("layer")).toMatchObject({
      id: "stac-raster-preview-layer",
      type: "raster",
      source: "stac-raster-preview",
      beforeId: "item-geometry-fill",
      paint: { "raster-opacity": 0.8 },
    });
  });

  it("defaults tile size and opacity, and omits absent bounds", () => {
    render(<RasterTileLayer tiles={["http://t/{z}/{x}/{y}.png"]} />);

    const source = propsOf("source");
    expect(source.tileSize).toBe(256);
    expect(source.bounds).toBeUndefined();
    expect(propsOf("layer")).toMatchObject({ paint: { "raster-opacity": 1 } });
  });

  it("passes zoom limits through when given", () => {
    render(
      <RasterTileLayer tiles={["http://t/{z}/{x}/{y}.png"]} minzoom={2} maxzoom={16} />,
    );
    expect(propsOf("source")).toMatchObject({ minzoom: 2, maxzoom: 16 });
  });

  it("namespaces the source and layer when given an id, so frames can coexist", () => {
    // The collection preview mounts several of these at once (current frame
    // plus lookahead); sharing the default ids would collide in maplibre.
    render(<RasterTileLayer id="frame-3" tiles={["http://t/{z}/{x}/{y}.png"]} />);

    expect(propsOf("source")).toMatchObject({ id: "frame-3" });
    expect(propsOf("layer")).toMatchObject({ id: "frame-3-layer", source: "frame-3" });
  });

  it("pins the opacity transition when a duration is given", () => {
    render(
      <RasterTileLayer
        tiles={["http://t/{z}/{x}/{y}.png"]}
        opacity={0}
        opacityTransitionMs={0}
      />,
    );

    expect(propsOf("layer")).toMatchObject({
      paint: { "raster-opacity": 0, "raster-opacity-transition": { duration: 0, delay: 0 } },
    });
  });
});
