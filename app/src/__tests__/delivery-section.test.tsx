import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { Association, Delivery } from "@/lib/associations/types";
import type { Connection } from "@/lib/connections/types";

const {
  useDeliveriesMock,
  redeliverMutate,
  requestBackfillMutate,
  createMutate,
  updateMutate,
} = vi.hoisted(() => ({
  useDeliveriesMock: vi.fn(),
  redeliverMutate: vi.fn(),
  requestBackfillMutate: vi.fn(),
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
}));

vi.mock("@/lib/associations/queries", () => ({
  useDeliveries: () => useDeliveriesMock(),
  useRedeliver: () => ({ mutate: redeliverMutate, isPending: false }),
  useRequestBackfill: () => ({
    mutate: requestBackfillMutate,
    isPending: false,
  }),
  useBackfill: () => ({ data: undefined }),
  useCreateAssociation: () => ({ mutate: createMutate, isPending: false }),
  useUpdateAssociation: () => ({ mutate: updateMutate, isPending: false }),
  useDeleteAssociation: () => ({ mutate: vi.fn(), isPending: false }),
  useAssociationDeleteImpact: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  };
});

import { DeliverySection } from "@/components/collections/DeliverySection";

beforeAll(() => {
  // Radix Select needs these in jsdom.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.HTMLElement.prototype.hasPointerCapture = vi.fn() as never;
});

const COLLECTION = "sentinel-2";
const ASSOC_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";

function deliverAssociation(overrides: Partial<Association> = {}): Association {
  return {
    id: ASSOC_ID,
    collection_id: COLLECTION,
    connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
    direction: "deliver",
    enabled: true,
    config: {
      path_template: "{collection}/{item_id}/{filename}",
      item_filter: null,
      asset_keys: null,
      payload: { item_json: true, checksums: "sha256", completion_marker: true },
      on_update: "redeliver",
      overwrite: "if_newer",
      retry: { max_attempts: 5, backoff: "exponential" },
      max_concurrent_transfers: 4,
    },
    expectation: null,
    flow_stats: {},
    created_by: "user-1",
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-02T00:00:00.000Z",
    connection: { name: "S3 dest", protocol: "s3", status: "ok" },
    ...overrides,
  };
}

function delivery(overrides: Partial<Delivery> = {}): Delivery {
  return {
    id: "3a9f1c2e-0000-4000-8000-0000000000d1",
    association_id: ASSOC_ID,
    item_id: "item-1",
    status: "delivered",
    attempts: 1,
    bytes: 1024,
    error: null,
    next_attempt_at: null,
    delivered_at: "2026-07-25T01:00:00.000Z",
    created_at: "2026-07-25T00:00:00.000Z",
    updated_at: "2026-07-25T01:00:00.000Z",
    ...overrides,
  };
}

function s3Connection(): Connection {
  return {
    id: "3a9f1c2e-0000-4000-8000-000000000001",
    name: "S3 dest",
    description: "",
    protocol: "s3",
    config: { bucket: "dest" },
    credentials_set: true,
    host_key: null,
    group_id: "g1",
    created_by: "u1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    enabled: true,
    status: "ok",
    last_checked_at: null,
    last_error: null,
  };
}

function renderSection(associations: Association[]) {
  return render(
    <DeliverySection
      collectionId={COLLECTION}
      associations={associations}
      connections={[s3Connection()]}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useDeliveriesMock.mockReturnValue({
    data: {
      deliveries: [],
      counts: { pending: 0, delivering: 0, delivered: 0, failed: 0, dead: 0 },
    },
    isLoading: false,
    error: null,
  });
});

describe("DeliverySection", () => {
  it("renders the empty state when there are no delivery destinations", () => {
    renderSection([]);
    expect(
      screen.getByText("No delivery destinations yet"),
    ).toBeInTheDocument();
  });

  it("renders the config summary and empty delivery status", () => {
    renderSection([deliverAssociation()]);
    expect(screen.getByText("S3 dest")).toBeInTheDocument();
    expect(
      screen.getByText("{collection}/{item_id}/{filename}"),
    ).toBeInTheDocument();
    expect(screen.getByText(/No deliveries yet/)).toBeInTheDocument();
  });

  it("surfaces status counts, per-cycle attempts, and redeliver on dead rows", () => {
    useDeliveriesMock.mockReturnValue({
      data: {
        deliveries: [
          delivery(),
          delivery({
            id: "3a9f1c2e-0000-4000-8000-0000000000d2",
            item_id: "item-2",
            status: "dead",
            attempts: 5,
            error: "connection refused",
          }),
        ],
        counts: { pending: 0, delivering: 0, delivered: 1, failed: 0, dead: 1 },
      },
      isLoading: false,
      error: null,
    });
    renderSection([deliverAssociation()]);

    expect(screen.getByText("1 delivered")).toBeInTheDocument();
    expect(screen.getByText("1 dead")).toBeInTheDocument();
    expect(screen.getByText("connection refused")).toBeInTheDocument();
    // Attempts are labeled as per-cycle, not lifetime (I-44 semantics).
    expect(screen.getByText(/per delivery cycle/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Redeliver/ }));
    expect(redeliverMutate).toHaveBeenCalledWith(
      "3a9f1c2e-0000-4000-8000-0000000000d2",
      expect.anything(),
    );
  });

  it("requests a backfill for the association", () => {
    renderSection([deliverAssociation()]);
    fireEvent.click(screen.getByRole("button", { name: /Backfill/ }));
    expect(requestBackfillMutate).toHaveBeenCalledWith(
      ASSOC_ID,
      expect.anything(),
    );
  });

  it("disables backfill on a disabled association and toggles enabled", () => {
    renderSection([deliverAssociation({ enabled: false })]);
    expect(screen.getByRole("button", { name: /Backfill/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("switch", { name: "Enabled" }));
    expect(updateMutate).toHaveBeenCalledWith(
      { id: ASSOC_ID, input: { enabled: true } },
      expect.anything(),
    );
  });

  it("creates a delivery association with the full §5.1 config shape", () => {
    renderSection([]);
    fireEvent.click(screen.getByRole("button", { name: /Add destination/ }));

    // Pick the connection through the Radix select.
    fireEvent.click(screen.getByRole("combobox", { name: "Connection" }));
    fireEvent.click(screen.getByRole("option", { name: "S3 dest (s3)" }));

    fireEvent.change(screen.getByLabelText("Path template"), {
      target: { value: "{collection}/{yyyy}/{item_id}/{filename}" },
    });
    fireEvent.change(screen.getByLabelText("Item filter (CQL2)"), {
      target: { value: "eo:cloud_cover < 20" },
    });
    fireEvent.change(screen.getByLabelText("Asset keys"), {
      target: { value: "visual, thumbnail" },
    });
    fireEvent.click(screen.getByRole("switch", { name: "Item JSON sidecar" }));

    fireEvent.click(screen.getByRole("button", { name: "Add destination" }));

    expect(createMutate).toHaveBeenCalledTimes(1);
    const [input] = createMutate.mock.calls[0];
    expect(input).toEqual({
      connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
      direction: "deliver",
      enabled: true,
      expectation: null,
      config: {
        path_template: "{collection}/{yyyy}/{item_id}/{filename}",
        item_filter: "eo:cloud_cover < 20",
        asset_keys: ["visual", "thumbnail"],
        payload: { item_json: true, checksums: null, completion_marker: false },
        on_update: "redeliver",
        overwrite: "if_newer",
        retry: { max_attempts: 5, backoff: "exponential" },
        max_concurrent_transfers: 4,
      },
    });
  });

  it("seeds the edit dialog from the association config", () => {
    renderSection([deliverAssociation()]);
    fireEvent.click(screen.getByRole("button", { name: /Edit/ }));

    expect(screen.getByLabelText("Path template")).toHaveValue(
      "{collection}/{item_id}/{filename}",
    );
    expect(screen.getByLabelText("Max attempts")).toHaveValue(5);

    fireEvent.change(screen.getByLabelText("Path template"), {
      target: { value: "products/{item_id}/{filename}" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    const [args] = updateMutate.mock.calls[0];
    expect(args.id).toBe(ASSOC_ID);
    expect(args.input.config.path_template).toBe(
      "products/{item_id}/{filename}",
    );
    // Existing payload toggles survive the round-trip.
    expect(args.input.config.payload).toEqual({
      item_json: true,
      checksums: "sha256",
      completion_marker: true,
    });
  });
});
