import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { StacCollection } from "@/lib/stac-api/types";

const { mutateMock, getCollectionMock, toastError } = vi.hoisted(() => ({
  mutateMock: vi.fn(),
  getCollectionMock: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/components/layout/AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/extensions/ExtensionPicker", () => ({ ExtensionPicker: () => null }));
vi.mock("@/components/extensions/ExtensionFields", () => ({ ExtensionFields: () => null }));
vi.mock("@stac-higher/shared", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  JsonViewer: () => null,
  BboxInput: () => null,
  StacMap: () => null,
  ExtentLayer: () => null,
}));
vi.mock("@nanostores/react", () => ({ useStore: vi.fn(() => ({ url: "http://localhost:8082" })) }));
vi.mock("@/stores/catalogStore", () => ({ $builtInCatalog: null }));
vi.mock("@/lib/query/collections", () => ({
  useCreateCollection: vi.fn(() => ({ mutate: vi.fn() })),
  useUpdateCollection: vi.fn(() => ({ mutate: mutateMock })),
}));
vi.mock("@/lib/stac-api/collections", () => ({
  getCollection: (...a: unknown[]) => getCollectionMock(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: toastError } }));

import { CollectionFormPage } from "@/components/collections/CollectionForm";

const ORIGINAL = {
  type: "Collection",
  stac_version: "1.1.0",
  id: "goes-cube",
  title: "GOES C13 cube",
  description: "Rolling virtual cube",
  license: "proprietary",
  extent: {
    spatial: { bbox: [[-150, -60, -20, 60]] },
    temporal: { interval: [["2026-11-02T00:00:00Z", "2026-11-02T01:00:00Z"]] },
  },
  links: [],
  "cube:dimensions": { t: { type: "temporal" } },
  item_assets: { data: { roles: ["data"] } },
} as unknown as StacCollection;

describe("CollectionForm edit submit (#102)", () => {
  beforeEach(() => {
    mutateMock.mockReset();
    getCollectionMock.mockReset();
    toastError.mockReset();
  });

  it("re-reads the stored product and PUTs the merge, keeping fields the form doesn't own", async () => {
    const latest = { ...ORIGINAL, "cube:dimensions": { t: { type: "temporal", extent: ["a", "b"] } } };
    getCollectionMock.mockResolvedValue(latest);
    render(<CollectionFormPage existingCollection={ORIGINAL} />);

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Renamed" } });
    fireEvent.submit(screen.getByLabelText("Title").closest("form")!);

    await waitFor(() => expect(mutateMock).toHaveBeenCalledTimes(1));
    expect(getCollectionMock).toHaveBeenCalledWith("goes-cube", "http://localhost:8082");
    const { collectionId, data } = mutateMock.mock.calls[0][0];
    expect(collectionId).toBe("goes-cube");
    expect(data.title).toBe("Renamed");
    expect(data["cube:dimensions"]).toEqual(latest["cube:dimensions"]);
    expect(data.item_assets).toEqual({ data: { roles: ["data"] } });
  });

  it("does not PUT when the re-read fails", async () => {
    getCollectionMock.mockRejectedValue(new Error("502 Bad Gateway"));
    render(<CollectionFormPage existingCollection={ORIGINAL} />);

    fireEvent.submit(screen.getByLabelText("Title").closest("form")!);

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastError.mock.calls[0][0]).toContain("could not re-read the product");
    expect(mutateMock).not.toHaveBeenCalled();
  });
});
