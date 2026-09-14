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
 * The FRAME layers only, BOTTOM of the stack first.
 *
 * Tree order is now the REVERSE of draw order: the frames chain `beforeId` on
 * each other, react-map-gl creates layers during render in tree order, and
 * maplibre drops an `addLayer` whose target is not in the style yet — so the
 * top frame has to be rendered first (and the anchor last). Reversing here
 * keeps these assertions reading the way the stack draws, bottom to top.
 */
function layers(): Rendered[] {
  return frameProps().map((props) => {
    const paint = props.paint as Record<string, unknown>;
    return {
      source: props.source as string,
      opacity: paint["raster-opacity"] as number,
      transition: paint["raster-opacity-transition"],
    };
  });
}

/** Frame layer props, bottom first. */
function frameProps(): Record<string, unknown>[] {
  return allLayers()
    .filter((props) => props.type === "raster")
    .reverse();
}

/** `[layer id, beforeId]` per frame, bottom first — the draw-order chain. */
function chain(): [string, string | undefined][] {
  return frameProps().map((p) => [p.id as string, p.beforeId as string | undefined]);
}

/** The always-mounted anchor the page chains on (V-2 Task 2, spec §11.2). */
function anchor(): Record<string, unknown> | undefined {
  return allLayers().find((p) => p.type !== "raster");
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

  it("passes bounds and zoom limits to every frame and chains beforeId downward", () => {
    // Only the TOP frame carries the caller's target; every frame below draws
    // beneath the one above it, and the anchor beneath the lowest frame.
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

    expect(chain()).toEqual([
      ["s-frame-0-layer", "s-frame-1-layer"],
      ["s-frame-1-layer", "top"],
    ]);
    expect(anchor()).toMatchObject({ id: "s-anchor", beforeId: "s-frame-0-layer" });
  });

  it("re-chains a backward step so the previous frame moves back underneath (I-112)", () => {
    // Walking 1 → 2 → 1: frame 2 was added ABOVE frame 1 on the way up and
    // maplibre keeps it there unless something moves it. react-map-gl calls
    // moveLayer only when `beforeId` changes, so the chain has to say it.
    const { rerender } = render(<RasterFrameStack id="s" frames={FRAMES} index={1} beforeId="top" />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={2} beforeId="top" />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={1} beforeId="top" />);

    expect(layers().map((l) => [l.source, l.opacity])).toEqual([
      ["s-frame-2", 1],
      ["s-frame-1", 1],
    ]);
    expect(chain()).toEqual([
      ["s-frame-2-layer", "s-frame-1-layer"],
      ["s-frame-1-layer", "top"],
    ]);
  });

  it("keeps the anchor mounted for an empty series, but no sources", () => {
    // The anchor is what a layer ABOVE this one chains its beforeId to. If it
    // came and went with the data, that chaining would silently break every
    // time a product had no frames yet.
    render(<RasterFrameStack id="s" frames={[]} index={0} />);

    expect(allLayers().map((l) => l.id)).toEqual(["s-anchor"]);
    expect(screen.queryAllByTestId("source")).toHaveLength(0);
    // With nothing to chain to, the anchor takes the caller's target — which
    // is what lets an imagery layer with no frames stay a chain target. (The
    // mock round-trips props through JSON, which drops an undefined-valued
    // key entirely, so this reads the value directly rather than via
    // toMatchObject — vitest's subsetEquality requires the key to exist.)
    expect(anchor()?.beforeId).toBeUndefined();
  });

  it("mounts the anchor LAST in the tree, and lowest on the map", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={0} beforeId="top" />);

    expect(rasterFrameStackAnchorId("s")).toBe("s-anchor");
    // V-2 rendered the anchor first and let every frame carry the caller's
    // beforeId; the chain (I-112) makes each frame's target the frame above
    // it, so the tree runs top-down and the anchor — whose target is the
    // bottom-most frame — must come last or its target would not exist yet.
    const rendered = allLayers();
    expect(rendered[rendered.length - 1]).toMatchObject({
      id: "s-anchor",
      type: "background",
      beforeId: "s-frame-0-layer",
      paint: { "background-opacity": 0 },
    });
    // A background layer takes no source — that is why it can never 404 or
    // sit in the style waiting for empty tiles.
    expect(rendered[rendered.length - 1]).not.toHaveProperty("source");
    // Still the bottom of the stack in maplibre terms: everything above it
    // chains down to it.
    expect(chain()[0]?.[0]).toBe("s-frame-0-layer");
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

  it("hides every mounted frame when not visible, and leaves the anchor alone", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={1} visible={false} />);

    expect(frameProps().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
    ]);
    // The anchor paints nothing in either state; hiding it would only risk
    // maplibre dropping it as a chaining target.
    expect(anchor()).not.toHaveProperty("layout");
    expect(screen.queryAllByTestId("source")).toHaveLength(2);
  });
});
