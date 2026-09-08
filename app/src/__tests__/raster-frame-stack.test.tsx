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

import {
  RasterFrameStack,
  RASTER_FRAME_LOOKAHEAD,
  rasterFrameStackAnchorId,
} from "@stac-higher/shared";

const FRAMES = ["a", "b", "c", "d"].map((key) => ({
  key,
  tiles: [`http://t/${key}/{z}/{x}/{y}`],
}));

interface Rendered {
  source: string;
  opacity: number;
  transition: unknown;
}

function allLayers(): Record<string, unknown>[] {
  return screen.queryAllByTestId("layer").map((el) => JSON.parse(el.dataset.props as string));
}

/**
 * The FRAME layers only. The stack also mounts a zero-opacity background
 * layer as the stable `beforeId` anchor (spec §11.2) — not a frame, no
 * source, no raster paint.
 */
function layers(): Rendered[] {
  return allLayers()
    .filter((props) => props.type === "raster")
    .map((props) => {
      const paint = props.paint as Record<string, unknown>;
      return {
        source: props.source as string,
        opacity: paint["raster-opacity"] as number,
        transition: paint["raster-opacity-transition"],
      };
    });
}

describe("RasterFrameStack", () => {
  it("warms exactly one frame ahead, at zero opacity", () => {
    expect(RASTER_FRAME_LOOKAHEAD).toBe(1);
    render(<RasterFrameStack id="s" frames={FRAMES} index={1} />);

    expect(layers()).toEqual([
      { source: "s-frame-1", opacity: 1, transition: { duration: 0, delay: 0 } },
      { source: "s-frame-2", opacity: 0, transition: { duration: 0, delay: 0 } },
    ]);
  });

  it("keeps the previous frame painted beneath the current one", () => {
    // Tiles render far slower than a step; the outgoing frame stays up until
    // the incoming one covers it, otherwise every step flashes empty.
    const { rerender } = render(<RasterFrameStack id="s" frames={FRAMES} index={2} />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={1} />);

    // The raw window is [previous=2, current=1, lookahead=2]; deduped, frame 2
    // is mounted once, as the previous frame, at full opacity.
    expect(layers().map((l) => [l.source, l.opacity])).toEqual([
      ["s-frame-2", 1],
      ["s-frame-1", 1],
    ]);
  });

  it("wraps the lookahead around the end of the series", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={3} />);

    expect(layers().map((l) => l.source)).toEqual(["s-frame-3", "s-frame-0"]);
  });

  it("applies the layer opacity to the visible frames only", () => {
    const { rerender } = render(<RasterFrameStack id="s" frames={FRAMES} index={0} opacity={0.5} />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={1} opacity={0.5} />);

    expect(layers().map((l) => l.opacity)).toEqual([0.5, 0.5, 0]);
  });

  it("passes bounds, zoom limits and beforeId to every frame", () => {
    render(
      <RasterFrameStack
        id="s"
        frames={FRAMES}
        index={0}
        bounds={[-1, -1, 1, 1]}
        minzoom={2}
        maxzoom={7}
        beforeId="top"
      />,
    );

    const sources = screen
      .getAllByTestId("source")
      .map((el) => JSON.parse(el.dataset.props as string));
    for (const s of sources) {
      expect(s).toMatchObject({ bounds: [-1, -1, 1, 1], minzoom: 2, maxzoom: 7 });
    }
    for (const el of screen.getAllByTestId("layer")) {
      expect(JSON.parse(el.dataset.props as string)).toMatchObject({ beforeId: "top" });
    }
  });

  it("keeps the anchor mounted for an empty series, but no sources", () => {
    // The anchor is what a layer ABOVE this one chains its beforeId to. If it
    // came and went with the data, that chaining would silently break every
    // time a product had no frames yet.
    render(<RasterFrameStack id="s" frames={[]} index={0} />);

    expect(allLayers().map((l) => l.id)).toEqual(["s-anchor"]);
    expect(screen.queryAllByTestId("source")).toHaveLength(0);
  });

  it("mounts the anchor first, beneath the frames, at zero opacity", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={0} beforeId="top" />);

    expect(rasterFrameStackAnchorId("s")).toBe("s-anchor");
    const [anchor, ...rest] = allLayers();
    expect(anchor).toMatchObject({
      id: "s-anchor",
      type: "background",
      beforeId: "top",
      paint: { "background-opacity": 0 },
    });
    // A background layer takes no source — that is why it can never 404 or
    // sit in the style waiting for empty tiles.
    expect(anchor).not.toHaveProperty("source");
    // Frames keep the CALLER's beforeId: both they and the anchor insert
    // before "top", and the anchor got there first, so it stays lowest.
    expect(rest.every((l) => l.beforeId === "top")).toBe(true);
  });

  it("collapses to one frame at full opacity for a one-frame series", () => {
    render(<RasterFrameStack id="s" frames={FRAMES.slice(0, 1)} index={0} />);

    expect(layers().map((l) => [l.source, l.opacity])).toEqual([["s-frame-0", 1]]);
  });

  it("wraps an out-of-range index into the series instead of blanking the map", () => {
    // A shared time axis or a series that shrank can hand this component an
    // index past the end; it must still show a frame, not paint everything 0.
    render(<RasterFrameStack id="s" frames={FRAMES} index={5} />);

    expect(layers().map((l) => [l.source, l.opacity])).toEqual([
      ["s-frame-1", 1],
      ["s-frame-2", 0],
    ]);
  });

  it("survives a negative or non-finite index", () => {
    const { rerender } = render(<RasterFrameStack id="s" frames={FRAMES} index={-1} />);
    expect(layers().map((l) => [l.source, l.opacity])).toEqual([
      ["s-frame-3", 1],
      ["s-frame-0", 0],
    ]);

    rerender(<RasterFrameStack id="s" frames={FRAMES} index={Number.NaN} />);
    expect(layers().some((l) => l.opacity === 1)).toBe(true);
  });
});
