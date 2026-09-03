/**
 * The ingest form's W-1 fields — date window, path template, per-poll cap.
 * The semantics under test are in the config builder: a blank field must be
 * ABSENT from the payload (the schema is `.strict()`), and a stored config's
 * values must seed the edit form so saving does not drop them.
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { Association } from "@/lib/associations/types";
import type { Connection } from "@/lib/connections/types";

const { createMutate, updateMutate } = vi.hoisted(() => ({
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
}));

vi.mock("@/lib/associations/queries", () => ({
  useCreateAssociation: () => ({ mutate: createMutate, isPending: false }),
  useUpdateAssociation: () => ({ mutate: updateMutate, isPending: false }),
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

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IngestFormDialog — date window (W-1)", () => {
  it("sends window, path_template and max_files_per_poll when filled", () => {
    renderDialog();
    pickConnection();
    fireEvent.change(screen.getByLabelText("Source path"), {
      target: { value: "ABI-L2-MCMIPC/" },
    });
    fireEvent.change(screen.getByLabelText("Window begin"), {
      target: { value: "-6h" },
    });
    fireEvent.change(screen.getByLabelText("Path template"), {
      target: { value: "{Y}/{j}/{H}/" },
    });
    fireEvent.change(screen.getByLabelText("Max files per poll"), {
      target: { value: "200" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));

    expect(createMutate).toHaveBeenCalledTimes(1);
    const [input] = createMutate.mock.calls[0];
    expect(input.config).toMatchObject({
      source_path: "ABI-L2-MCMIPC/",
      window: { begin: "-6h", end: null },
      path_template: "{Y}/{j}/{H}/",
      max_files_per_poll: 200,
    });
  });

  it("sends the end bound when given", () => {
    renderDialog();
    pickConnection();
    fireEvent.change(screen.getByLabelText("Source path"), {
      target: { value: "/out" },
    });
    fireEvent.change(screen.getByLabelText("Window begin"), {
      target: { value: "-2h" },
    });
    fireEvent.change(screen.getByLabelText("Window end"), {
      target: { value: "-10m" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));

    const [input] = createMutate.mock.calls[0];
    expect(input.config.window).toEqual({ begin: "-2h", end: "-10m" });
  });

  it("omits all three keys when the fields are blank", () => {
    renderDialog();
    pickConnection();
    fireEvent.change(screen.getByLabelText("Source path"), {
      target: { value: "/out" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));

    expect(createMutate).toHaveBeenCalledTimes(1);
    const [input] = createMutate.mock.calls[0];
    expect(input.config).not.toHaveProperty("window");
    expect(input.config).not.toHaveProperty("path_template");
    expect(input.config).not.toHaveProperty("max_files_per_poll");
  });

  it("seeds the edit form from a stored config and keeps the fields on save", () => {
    renderDialog(
      ingestAssociation({
        source_path: "ABI-L2-MCMIPC/",
        include: [],
        exclude: [],
        poll_frequency_seconds: 300,
        settle: "auto",
        storage_mode: "reference",
        grouping: { rule: "none", timeout_seconds: 900, on_timeout: "ingest_partial" },
        metadata: { strategy: "raster_auto", defaults: {} },
        post_ingest: "leave",
        window: { begin: "-1h", end: null },
        path_template: "{Y}/{j}/{H}/",
        max_files_per_poll: 4,
      }),
    );

    expect(screen.getByLabelText("Window begin")).toHaveValue("-1h");
    expect(screen.getByLabelText("Window end")).toHaveValue("");
    expect(screen.getByLabelText("Path template")).toHaveValue("{Y}/{j}/{H}/");
    expect(screen.getByLabelText("Max files per poll")).toHaveValue(4);

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    const [args] = updateMutate.mock.calls[0];
    expect(args.input.config).toMatchObject({
      window: { begin: "-1h", end: null },
      path_template: "{Y}/{j}/{H}/",
      max_files_per_poll: 4,
    });
  });
});
