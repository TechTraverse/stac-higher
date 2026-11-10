import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { forwardRef, useEffect, useImperativeHandle } from "react";
import type { StacCollection } from "@/lib/stac-api/types";

// A LngLat stand-in: wrap() folds lng into [-180, 180].
function mockLngLat(lng: number, lat: number) {
  return { lng, lat, wrap: () => ({ lng: ((((lng + 180) % 360) + 360) % 360) - 180, lat }) };
}

const { sourceMounts, useItemsMock, useCollectionSettingsMock, useItemTileJsonMock, fakeMap } = vi.hoisted(() => {
  const handlers = new Map<string, Set<(e: unknown) => void>>();
  return {
    sourceMounts: [] as string[],
    useItemsMock: vi.fn(),
    useCollectionSettingsMock: vi.fn(),
    useItemTileJsonMock: vi.fn(),
    fakeMap: {
      handlers,
      on: (type: string, fn: (e: unknown) => void) => {
        if (!handlers.has(type)) handlers.set(type, new Set());
        handlers.get(type)!.add(fn);
      },
      off: (type: string, fn: (e: unknown) => void) => handlers.get(type)?.delete(fn),
      emit: (type: string, e: unknown = {}) => handlers.get(type)?.forEach((fn) => fn(e)),
      areTilesLoaded: () => true,
      fitBounds: () => {},
      getMap: () => undefined,
    },
  };
});

vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: () => useCollectionSettingsMock(),
}));
vi.mock("@/lib/serving/queries", async (orig) => {
  const actual = await orig<typeof import("@/lib/serving/queries")>();
  return { ...actual, useItemTileJson: (...a: unknown[]) => useItemTileJsonMock(...a) };
});
vi.mock("react-map-gl/maplibre", () => ({
  default: forwardRef(function MockMap(
    { children, onClick, onLoad }: { children?: React.ReactNode; onClick?: (e: unknown) => void; onLoad?: () => void },
    ref,
  ) {
    useImperativeHandle(ref, () => fakeMap as never);
    useEffect(() => onLoad?.(), [onLoad]);
    return (
      <div data-testid="map">
        <button
          data-testid="map-click"
          onClick={() => onClick?.({ lngLat: mockLngLat(-97.123456, 38.5) })}
        />
        <button
          data-testid="map-click-wrapped"
          onClick={() => onClick?.({ lngLat: mockLngLat(-200, 38.5) })}
        />
        <button
          data-testid="map-click-close"
          onClick={() => onClick?.({ lngLat: mockLngLat(-97.1234561, 38.5000004) })}
        />
        {children}
      </div>
    );
  }),
  Source: function MockSource({ children, ...props }: Record<string, unknown> & { children?: React.ReactNode }) {
    useEffect(() => {
      sourceMounts.push(String(props.id));
    }, []); // eslint-disable-line react-hooks/exhaustive-deps
    return (
      <div data-testid="source" data-props={JSON.stringify(props)}>
        {children}
      </div>
    );
  },
  Layer: (props: Record<string, unknown>) => (
    <div data-testid="layer" data-props={JSON.stringify(props)} />
  ),
  NavigationControl: () => <div />,
  ScaleControl: () => <div />,
  useMap: () => ({ current: undefined }),
}));

import { CUBE_TILE_MAX_RETRIES } from "@/lib/serving/cube-retry";
import { CollectionPreviewTab } from "@/components/collections/CollectionPreviewTab";

const NEWEST = "2026-10-03T19:25:00.714359936Z";

function times(n: number): string[] {
  const base = Date.parse("2026-10-03T17:00:00Z");
  return Array.from({ length: n }, (_, i) => {
    const iso = new Date(base + i * 300_000).toISOString(); // 2026-..T..:..:00.000Z
    return iso.replace(".000Z", ".714359936Z");
  });
}

function cubeCollection(timeValues?: string[], bbox: number[] = [-137, 14, -52, 54]): StacCollection {
  return {
    type: "Collection",
    stac_version: "1.0.0",
    id: "goes19-c13-cube",
    description: "",
    license: "proprietary",
    extent: { spatial: { bbox: [bbox] }, temporal: { interval: [[null, null]] } },
    links: [],
    assets: {
      cube: {
        href: "s3://b/cube",
        type: "application/vnd.zarr+icechunk",
        ...(timeValues ? { "stac_higher:time_values": timeValues } : {}),
      },
    },
  } as unknown as StacCollection;
}

function renderTab(collection: StacCollection) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CollectionPreviewTab collection={collection} collectionId={collection.id} endpointUrl="http://x" />
    </QueryClientProvider>,
  );
}

function sources(): { id: string; tiles: string[] }[] {
  return screen.queryAllByTestId("source").map((el) => JSON.parse(el.dataset.props as string));
}

beforeEach(() => {
  fakeMap.handlers.clear();
  sourceMounts.length = 0;
  useItemsMock.mockReset();
  useCollectionSettingsMock.mockReset();
  useItemTileJsonMock.mockReset();
  useItemsMock.mockReturnValue({ data: { features: [] }, isLoading: false });
  useCollectionSettingsMock_default();
});

function useCollectionSettingsMock_default() {
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });
  useItemTileJsonMock.mockReturnValue({ data: undefined, isFetched: true });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("CollectionPreviewTab, cube branch", () => {
  it("renders a cube with serving off, without touching the item hooks", () => {
    const all = times(30);
    all[29] = NEWEST;
    renderTab(cubeCollection(all));

    expect(screen.getByTestId("map")).toBeTruthy();
    expect(useItemsMock).not.toHaveBeenCalled();
    expect(useItemTileJsonMock).not.toHaveBeenCalled();
    expect(screen.getByText("24 / 24")).toBeTruthy();
    expect(screen.getByText("2026-10-03 19:25 UTC")).toBeTruthy();
  });

  it("carries the newest t verbatim in the tile url", () => {
    const all = times(30);
    all[29] = NEWEST;
    renderTab(cubeCollection(all));

    const urls = sources().map((s) => s.tiles[0]);
    expect(urls.some((u) => u.includes(`t=${encodeURIComponent(NEWEST)}`) && u.includes("/{z}/{y}/{x}?"))).toBe(true);
  });

  it("shows the full window on toggle", () => {
    renderTab(cubeCollection(times(30)));

    fireEvent.click(screen.getByRole("switch", { name: "Show full window (30 steps)" }));
    expect(screen.getByText("30 / 30")).toBeTruthy();
  });

  it("offers no toggle for a short cube", () => {
    renderTab(cubeCollection(times(10)));
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("shows the empty state for a cube with no steps", () => {
    renderTab(cubeCollection([]));
    expect(screen.getByText("The cube has no steps yet")).toBeTruthy();
    renderTab(cubeCollection(undefined));
    expect(screen.getAllByText("The cube has no steps yet")).toHaveLength(2);
  });

  it("charts a clicked point and reports the missing steps", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        "t,CMI,longitude,latitude\n2026-10-03 17:00:00.714359936,251.5,-97,38.5\n2026-10-03 17:05:00.714359936,,-97,38.5\n",
        { status: 200, headers: { "x-cube-missing-steps": "1" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderTab(cubeCollection(times(5)));

    expect(screen.getByText("Click the map for a time series at that point.")).toBeTruthy();
    fireEvent.click(screen.getByTestId("map-click"));

    await waitFor(() => expect(screen.getByRole("img", { name: "CMI at 38.5000, -97.1235" })).toBeTruthy());
    expect(screen.getByText(/1 of 2 steps unavailable/)).toBeTruthy();
    expect(String(fetchMock.mock.calls[0][0])).toContain("POINT(-97.1235%2038.5)");
  });

  it("wraps a click on a world copy before it reaches the EDR url", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("t,CMI\n", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderTab(cubeCollection(times(5)));
    fireEvent.click(screen.getByTestId("map-click-wrapped"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toContain("POINT(160%2038.5)");
  });

  it("fetches once for two clicks that differ past the 4th decimal", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response("t,CMI\n", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderTab(cubeCollection(times(5)));
    fireEvent.click(screen.getByTestId("map-click"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("map-click-close"));
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("says so when the series cannot be fetched", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("no", { status: 404 })));
    renderTab(cubeCollection(times(5)));
    fireEvent.click(screen.getByTestId("map-click"));
    await waitFor(() => expect(screen.getByText(/Time series unavailable: .*404/)).toBeTruthy());
  });

  it("counts frames as their tiles load and hides the line at N", () => {
    renderTab(cubeCollection(times(2)));
    expect(screen.getByText("Loaded 0 of 2 frames")).toBeTruthy();

    act(() => fakeMap.emit("idle"));
    expect(screen.getByText("Loaded 1 of 2 frames")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Previous frame"));
    act(() => fakeMap.emit("idle"));
    expect(screen.queryByText(/Loaded \d+ of/)).toBeNull();
  });

  it("remounts a frame after a 422, keeping its url", () => {
    vi.useFakeTimers();
    renderTab(cubeCollection(times(2)));
    const before = sources().map((s) => s.tiles[0]);
    const mountsBefore = [...sourceMounts];

    act(() => fakeMap.emit("error", { sourceId: "cube-frame-1", error: { status: 422 } }));
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    // The attempt bump changed the frame's key, so its source remounted; the
    // url is unchanged because an unknown query parameter selects a dimension.
    expect(sources().map((s) => s.tiles[0])).toEqual(before);
    expect(sourceMounts.filter((id) => id === "cube-frame-1").length).toBe(
      mountsBefore.filter((id) => id === "cube-frame-1").length + 1,
    );
  });

  it("retries a t-selector 422 but never a style 422", async () => {
    vi.useFakeTimers();
    renderTab(cubeCollection(times(2)));
    const mounts = () => sourceMounts.filter((id) => id === "cube-frame-1").length;
    const start = mounts();
    const fail = (detail: unknown) =>
      fakeMap.emit("error", {
        sourceId: "cube-frame-1",
        error: { status: 422, body: new Blob([JSON.stringify({ detail })]) },
      });
    // The body is read asynchronously: let the Blob read settle, then fire the timer.
    const settle = async () => {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
    };

    act(() => fail([{ type: "value_error", loc: ["query", "style"], msg: "variant 'gray_r' is not supported" }]));
    await settle();
    expect(mounts()).toBe(start);

    act(() => fail("Invalid indexer: {'t': '2026-10-03T17:07:36.714359936Z'}."));
    await settle();
    expect(mounts()).toBe(start + 1);
  });

  it("counts one attempt per timer and stops at the retry cap", () => {
    vi.useFakeTimers();
    renderTab(cubeCollection(times(2)));
    const mounts = () => sourceMounts.filter((id) => id === "cube-frame-1").length;
    const start = mounts();
    const fail = () => fakeMap.emit("error", { sourceId: "cube-frame-1", error: { status: 422 } });

    // Two retries inside one act, so no render separates them: the second
    // must count from the first (attempts 0 -> 2, one remount), not from a
    // stale 0 (which would leave the count at 1).
    act(() => {
      fail();
      vi.advanceTimersByTime(5000);
      fail();
      vi.advanceTimersByTime(5000);
    });
    expect(mounts()).toBe(start + 1);

    // One more attempt reaches the cap (3); later 422s schedule nothing.
    for (let i = 0; i < 5; i++) {
      act(() => {
        fail();
        vi.advanceTimersByTime(5000);
      });
    }
    expect(mounts()).toBe(start + 2);
    expect(CUBE_TILE_MAX_RETRIES).toBe(3);
  });

  it("omits bounds for an antimeridian-crossing or 3-D bbox", () => {
    const boundsOf = () => sources().map((s) => (s as unknown as { bounds?: unknown }).bounds);
    const { unmount } = renderTab(cubeCollection(times(2)));
    expect(boundsOf().every((b) => Array.isArray(b))).toBe(true);
    unmount();
    const a = renderTab(cubeCollection(times(2), [170, -10, -170, 10]));
    expect(boundsOf().every((b) => b === undefined)).toBe(true);
    a.unmount();
    renderTab(cubeCollection(times(2), [-137, 14, 0, -52, 54, 100]));
    expect(boundsOf().every((b) => b === undefined)).toBe(true);
  });

  it("leaves an item collection on the item path", () => {
    const c = { ...cubeCollection([]), assets: {} } as StacCollection;
    renderTab(c);
    expect(useItemsMock).toHaveBeenCalled();
  });
});
