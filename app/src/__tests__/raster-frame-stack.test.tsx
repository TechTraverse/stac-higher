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

import { RasterFrameStack, RASTER_FRAME_LOOKAHEAD } from "@stac-higher/shared";

const FRAMES = ["a", "b", "c", "d"].map((key) => ({
  key,
  tiles: [`http://t/${key}/{z}/{x}/{y}`],
}));

interface Rendered {
  source: string;
  opacity: number;
  transition: unknown;
}

function layers(): Rendered[] {
  return screen.queryAllByTestId("layer").map((el) => {
    const props = JSON.parse(el.dataset.props as string);
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

  it("renders nothing for an empty series", () => {
    render(<RasterFrameStack id="s" frames={[]} index={0} />);
    expect(screen.queryAllByTestId("source")).toHaveLength(0);
  });
});
