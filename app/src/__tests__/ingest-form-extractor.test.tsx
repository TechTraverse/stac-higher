/**
 * The ingest form's extractor metadata strategy (G-6): the picker offers only
 * the connection's group's extractor processes and the submitted config
 * carries `extractor.process_id`. Modelled on `ingest-form-window.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { Association } from "@/lib/associations/types";
import type { Connection } from "@/lib/connections/types";

const { createMutate, updateMutate, builtinMutate } = vi.hoisted(() => ({
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
  builtinMutate: vi.fn(),
}));

vi.mock("@/lib/associations/queries", () => ({
  useCreateAssociation: () => ({ mutate: createMutate, isPending: false }),
  useUpdateAssociation: () => ({ mutate: updateMutate, isPending: false }),
}));

vi.mock("@/lib/extractors/queries", () => ({
  useBuiltinExtractors: () => ({
    data: [
      { id: "stactools-goes", label: "GOES-R ABI (L1b / L2)", supports: "single_file" },
      { id: "stactools-goes-glm", label: "GOES-R GLM lightning (L2 LCFA)", supports: "single_file" },
      { id: "stactools-sentinel2", label: "Sentinel-2 L1C / L2A (ESA)", supports: "grouped" },
    ],
  }),
}));

vi.mock("@/lib/processes/queries", () => ({
  useCreateBuiltinProcess: () => ({ mutate: builtinMutate, isPending: false }),
  useProcesses: () => ({
    data: [
      {
        id: "5c9f1c2e-0000-4000-8000-0000000000e1",
        name: "goes-abi-metadata",
        kind: "extractor",
        group_id: "g1",
        builtin_id: null,
      },
      {
        id: "5c9f1c2e-0000-4000-8000-0000000000e4",
        name: "GOES-R GLM lightning (L2 LCFA)",
        kind: "extractor",
        group_id: "g1",
        builtin_id: "stactools-goes-glm",
      },
      {
        id: "5c9f1c2e-0000-4000-8000-0000000000e2",
        name: "other-group-extractor",
        kind: "extractor",
        group_id: "g2",
      },
      {
        id: "5c9f1c2e-0000-4000-8000-0000000000e3",
        name: "a-transform",
        kind: "transform",
        group_id: "g1",
      },
    ],
  }),
}));

import { IngestFormDialog } from "@/components/collections/IngestFormDialog";

beforeAll(() => {
  // Radix Select needs these in jsdom.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.HTMLElement.prototype.hasPointerCapture = vi.fn() as never;
});

const COLLECTION = "goes-geocolor";
const CONNECTION_ID = "3a9f1c2e-0000-4000-8000-000000000001";

function s3Connection(): Connection {
  return {
    id: CONNECTION_ID,
    name: "NODD",
    description: "",
    protocol: "s3",
    config: { bucket: "noaa-goes19" },
    credentials_set: false,
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

function ingestAssociation(config: Record<string, unknown>): Association {
  return {
    id: "3a9f1c2e-0000-4000-8000-0000000000a1",
    collection_id: COLLECTION,
    connection_id: CONNECTION_ID,
    direction: "ingest",
    enabled: true,
    config,
    expectation: null,
    flow_stats: {},
    created_by: "user-1",
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-02T00:00:00.000Z",
    connection: { name: "NODD", protocol: "s3", status: "ok" },
  };
}

function renderDialog(editing: Association | null = null) {
  return render(
    <IngestFormDialog
      collectionId={COLLECTION}
      open
      editing={editing}
      connections={[s3Connection()]}
      onOpenChange={vi.fn()}
    />,
  );
}

function pickConnection() {
  fireEvent.click(screen.getByRole("combobox", { name: "Connection" }));
  fireEvent.click(screen.getByRole("option", { name: "NODD (s3)" }));
}

function pickExtractorStrategy() {
  fireEvent.click(screen.getByRole("combobox", { name: "Metadata strategy" }));
  fireEvent.click(screen.getByRole("option", { name: "extractor process" }));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IngestFormDialog — extractor metadata strategy (G-6)", () => {
  it("offers only the connection group's extractors and emits extractor.process_id", () => {
    renderDialog();
    pickConnection();
    fireEvent.change(screen.getByLabelText("Source path"), {
      target: { value: "ABI-L2-MCMIPC/" },
    });
    pickExtractorStrategy();

    fireEvent.click(
      screen.getByRole("combobox", { name: "Extractor process" }),
    );
    expect(
      screen.getByRole("option", { name: "goes-abi-metadata" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "other-group-extractor" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "a-transform" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: "goes-abi-metadata" }));

    fireEvent.click(screen.getByRole("button", { name: "Add source" }));

    expect(createMutate).toHaveBeenCalledTimes(1);
    const payload = createMutate.mock.calls[0][0];
    expect(payload.config.metadata).toEqual({
      strategy: "extractor",
      extractor: { process_id: "5c9f1c2e-0000-4000-8000-0000000000e1" },
      defaults: {},
    });
  });

  it("refuses to submit an extractor strategy with no process picked", () => {
    renderDialog();
    pickConnection();
    fireEvent.change(screen.getByLabelText("Source path"), {
      target: { value: "ABI-L2-MCMIPC/" },
    });
    pickExtractorStrategy();

    fireEvent.click(screen.getByRole("button", { name: "Add source" }));

    expect(createMutate).not.toHaveBeenCalled();
  });

  it("seeds the picker from a stored extractor config", () => {
    const a = ingestAssociation({
      source_path: "x/",
      metadata: {
        strategy: "extractor",
        extractor: { process_id: "5c9f1c2e-0000-4000-8000-0000000000e1" },
      },
    });
    renderDialog(a);
    expect(
      screen.getByRole("combobox", { name: "Extractor process" }),
    ).toHaveTextContent("goes-abi-metadata");
  });
});

describe("IngestFormDialog — built-in extractors (X-4)", () => {
  it("offers the registry under Built-in, minus what the group already has, filtered by grouping", () => {
    renderDialog();
    pickConnection();
    pickExtractorStrategy();
    fireEvent.click(screen.getByRole("combobox", { name: "Extractor process" }));
    // Not yet instantiated in g1 and single_file ⇒ offered for grouping "none".
    expect(screen.getByRole("option", { name: "GOES-R ABI (L1b / L2)" })).toBeInTheDocument();
    // Already instantiated in g1: listed as the group's process, not as a registry pick.
    expect(
      screen.getByRole("option", { name: "GOES-R GLM lightning (L2 LCFA) · built-in" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("option", { name: /GLM lightning/ })).toHaveLength(1);
    // grouped ⇒ hidden while the grouping rule is "none".
    expect(
      screen.queryByRole("option", { name: "Sentinel-2 L1C / L2A (ESA)" }),
    ).not.toBeInTheDocument();
  });

  it("resolves a built-in pick to a group process before storing its id", () => {
    builtinMutate.mockImplementation((_input, callbacks) =>
      callbacks.onSuccess({ id: "5c9f1c2e-0000-4000-8000-0000000000e9" }),
    );
    renderDialog();
    pickConnection();
    fireEvent.change(screen.getByLabelText("Source path"), {
      target: { value: "ABI-L2-MCMIPC/" },
    });
    pickExtractorStrategy();
    fireEvent.click(screen.getByRole("combobox", { name: "Extractor process" }));
    fireEvent.click(screen.getByRole("option", { name: "GOES-R ABI (L1b / L2)" }));

    fireEvent.click(screen.getByRole("button", { name: "Add source" }));

    expect(builtinMutate).toHaveBeenCalledTimes(1);
    expect(builtinMutate.mock.calls[0][0]).toEqual({
      builtin_id: "stactools-goes",
      group_id: "g1",
    });
    expect(createMutate).toHaveBeenCalledTimes(1);
    expect(createMutate.mock.calls[0][0].config.metadata).toEqual({
      strategy: "extractor",
      extractor: { process_id: "5c9f1c2e-0000-4000-8000-0000000000e9" },
      defaults: {},
    });
  });
});
