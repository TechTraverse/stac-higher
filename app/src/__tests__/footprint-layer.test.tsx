import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { StacItem } from "@/lib/stac-api/types";

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

import { FootprintLayer } from "@stac-higher/shared";

function item(id: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "c",
    geometry: { type: "Point", coordinates: [0, 0] },
    properties: { datetime: "2026-09-04T00:00:00Z" },
    links: [],
    assets: {},
  };
}

function sources(): Record<string, unknown>[] {
  return screen.getAllByTestId("source").map((el) => JSON.parse(el.dataset.props as string));
}
function layers(): Record<string, unknown>[] {
  return screen.getAllByTestId("layer").map((el) => JSON.parse(el.dataset.props as string));
}

describe("FootprintLayer", () => {
  it("keeps the legacy source and layer ids when no id is given", () => {
    render(<FootprintLayer items={[item("a")]} />);

    expect(sources()[0]).toMatchObject({ id: "stac-footprints", type: "geojson" });
    expect(layers().map((l) => l.id)).toEqual(["stac-footprint-fill", "stac-footprint-line"]);
    expect(layers()[0]).not.toHaveProperty("beforeId");
  });

  it("namespaces the source and layers by id so two can share a map", () => {
    render(
      <>
        <FootprintLayer id="layer-a" items={[item("a")]} />
        <FootprintLayer id="layer-b" items={[item("b")]} />
      </>,
    );

    expect(sources().map((s) => s.id)).toEqual(["layer-a", "layer-b"]);
    expect(layers().map((l) => l.id)).toEqual([
      "layer-a-fill",
      "layer-a-line",
      "layer-b-fill",
      "layer-b-line",
    ]);
    expect(layers()[2]).toMatchObject({ source: "layer-b" });
  });

  it("applies opacity and beforeId to both layers", () => {
    render(<FootprintLayer id="layer-a" items={[item("a")]} opacity={0.4} beforeId="top" />);

    const [fill, line] = layers();
    expect(fill).toMatchObject({ beforeId: "top" });
    expect(line).toMatchObject({ beforeId: "top", paint: { "line-opacity": 0.4 } });
    expect((fill.paint as Record<string, unknown>)["fill-opacity"]).toEqual([
      "*",
      0.4,
      ["case", ["boolean", ["feature-state", "hover"], false], 0.25, 0.1],
    ]);
  });

  it("drops items without geometry and marks the selected one", () => {
    render(<FootprintLayer items={[item("a"), { ...item("b"), geometry: null }]} selectedId="a" />);

    const data = sources()[0].data as GeoJSON.FeatureCollection;
    expect(data.features).toHaveLength(1);
    expect(data.features[0].properties).toMatchObject({ id: "a", selected: true });
  });

  it("hides both layers without unmounting the source", () => {
    // Unmounting would drop the GeoJSON source and re-diff the whole feature
    // collection on every toggle; visibility is a layout property.
    const { rerender } = render(<FootprintLayer id="layer-a" items={[item("a")]} />);
    expect(layers().map((l) => l.layout)).toEqual([
      { visibility: "visible" },
      { visibility: "visible" },
    ]);

    rerender(<FootprintLayer id="layer-a" items={[item("a")]} visible={false} />);

    expect(sources().map((s) => s.id)).toEqual(["layer-a"]);
    expect(layers().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
    ]);
  });
});
