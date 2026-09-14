import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { LayerKind } from "@/lib/map/state";

const tipgMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/serving/queries", () => ({
  useTipgCollections: tipgMock,
  useItemTileJson: () => ({ data: undefined, isFetched: false }),
}));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: () => ({ data: { servingEnabled: false } }),
}));
vi.mock("@/lib/query/items", () => ({ useItems: () => ({ data: undefined }) }));

import { AddLayerPopover } from "@/components/map/AddLayerPopover";

function renderPicker(
  onAddVector = vi.fn(),
  isAdded: (kind: LayerKind, sourceId: string) => boolean = () => false,
) {
  render(
    <AddLayerPopover
      collections={[]}
      catalogUrl="http://stac"
      isAdded={isAdded}
      onAdd={vi.fn()}
      onAddVector={onAddVector}
    />,
  );
  fireEvent.click(screen.getByTestId("map-add-layer"));
  return onAddVector;
}

beforeEach(() => {
  tipgMock.mockReset();
});

describe("AddLayerPopover — Vector tiles", () => {
  it("does not ask tipg until the picker opens", () => {
    tipgMock.mockReturnValue({ data: undefined, isError: false });
    render(
      <AddLayerPopover collections={[]} catalogUrl="u" isAdded={() => false} onAdd={vi.fn()} onAddVector={vi.fn()} />,
    );
    expect(tipgMock).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByTestId("map-add-layer"));
    expect(tipgMock).toHaveBeenLastCalledWith(true);
  });

  it("lists tipg's collections and adds one as a vector layer", () => {
    tipgMock.mockReturnValue({
      data: [{ id: "public.roads", title: "Roads" }, { id: "public.st_hexagongrid" }],
      isError: false,
    });
    const onAddVector = renderPicker();
    expect(screen.getByText("Vector tiles")).toBeInTheDocument();
    expect(screen.getByText("Roads")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("map-add-vector-public.roads"));
    expect(onAddVector).toHaveBeenCalledWith({ id: "public.roads", title: "Roads" });
  });

  it("shows an added collection as Added and disabled", () => {
    tipgMock.mockReturnValue({ data: [{ id: "public.roads", title: "Roads" }], isError: false });
    renderPicker(vi.fn(), (kind, id) => kind === "vector" && id === "public.roads");
    const button = screen.getByTestId("map-add-vector-public.roads");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Added");
  });

  it("says 'no vector tiles published' when tipg is unreachable or empty — never an error", () => {
    tipgMock.mockReturnValue({ data: undefined, isError: true });
    renderPicker();
    expect(screen.getByTestId("map-vector-empty")).toHaveTextContent("no vector tiles published");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the same note for an empty list", () => {
    tipgMock.mockReturnValue({ data: [], isError: false });
    renderPicker();
    expect(screen.getByTestId("map-vector-empty")).toBeInTheDocument();
  });
});
