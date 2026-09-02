import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useItemTileJson } from "@/lib/serving/queries";

const fetchMock = vi.fn();

function Probe({
  collectionId,
  itemId,
  assetKey,
  enabled,
}: {
  collectionId: string;
  itemId: string;
  assetKey: string | null;
  enabled: boolean;
}) {
  const { data, error } = useItemTileJson(collectionId, itemId, assetKey, enabled);
  if (error) return <div>error</div>;
  if (data) return <div>tiles:{data.tiles.length}</div>;
  return <div>idle</div>;
}

function renderProbe(props: {
  collectionId: string;
  itemId: string;
  assetKey: string | null;
  enabled: boolean;
}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Probe {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("useItemTileJson", () => {
  it("fetches the item tilejson when enabled and exposes its tiles", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          tilejson: "2.2.0",
          tiles: ["http://t/{z}/{x}/{y}@1x?assets=visual"],
          bounds: [-100, 30, -90, 40],
        }),
        { status: 200 },
      ),
    );

    renderProbe({ collectionId: "c", itemId: "i", assetKey: "visual", enabled: true });

    expect(await screen.findByText("tiles:1")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/collections/c/items/i/WebMercatorQuad/tilejson.json?assets=visual",
    );
  });

  it("does not fetch without an asset key", () => {
    renderProbe({ collectionId: "c", itemId: "i", assetKey: null, enabled: true });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not fetch when serving is disabled", () => {
    renderProbe({ collectionId: "c", itemId: "i", assetKey: "visual", enabled: false });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces a non-OK response as an error without retrying", async () => {
    fetchMock.mockResolvedValueOnce(new Response("nope", { status: 404 }));

    renderProbe({ collectionId: "c", itemId: "i", assetKey: "visual", enabled: true });

    expect(await screen.findByText("error")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects a tilejson document with no tiles", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ tilejson: "2.2.0", tiles: [] }), { status: 200 }),
    );

    renderProbe({ collectionId: "c", itemId: "i", assetKey: "visual", enabled: true });

    expect(await screen.findByText("error")).toBeInTheDocument();
  });
});
