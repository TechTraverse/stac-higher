/**
 * Virtual cube sink card (Z-8b): create form, API error rendering, the layout
 * lock, the first-append write refusal, the status badge for every health
 * state, and the viewer / operator split.
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, act, fireEvent, waitFor, within } from "@testing-library/react";
import type { CubeSinkView } from "@/lib/cubes/queries";
import type { ApiCubeSink } from "@/lib/cubes/storage";
import { CUBE_SINK_HEALTH_LABEL, CUBE_SINK_HEALTH_STATES } from "@/lib/cubes/status";
import { CUBE_FORM_DEFAULTS, formToPutBody } from "@/lib/cubes/form";

const m = vi.hoisted(() => ({
  sinkState: {
    data: undefined as unknown,
    isLoading: false,
    isError: false,
    error: null as unknown,
    refetch: vi.fn(),
  } as { data: unknown; isLoading: boolean; isError?: boolean; error?: unknown; refetch?: () => void },
  fresh: vi.fn(),
  putPending: false,
  patchPending: false,
  sources: { data: [] as unknown[], isSuccess: true },
  putMutate: vi.fn(),
  patchMutate: vi.fn(),
}));

vi.mock("@/lib/cubes/queries", async (orig) => ({
  ...(await orig<typeof import("@/lib/cubes/queries")>()),
  useCubeSink: () => m.sinkState,
  useFreshCubeSink: () => m.fresh,
  useReferenceSources: () => m.sources,
  usePutCubeSink: () => ({ mutate: m.putMutate, isPending: m.putPending }),
  usePatchCubeSink: () => ({ mutate: m.patchMutate, isPending: m.patchPending }),
}));

import { CubeSinkApiError } from "@/lib/cubes/queries";
import { CubeSinkCard } from "@/components/collections/CubeSinkCard";

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  Element.prototype.setPointerCapture = vi.fn();
});

const NOW = new Date().toISOString();

function makeSink(over: Partial<ApiCubeSink> = {}): ApiCubeSink {
  return {
    id: "sink-1",
    source_collection_id: "goes19-cmipc-c13",
    cube_collection_id: "goes19-c13-cube",
    enabled: true,
    config: {
      parser: "hdf5",
      append_dim: "t",
      variables: ["CMI", "DQF"],
      loadable_variables: ["t", "x", "y", "goes_imager_projection"],
      asset_key: "cube",
      window: { max_steps: 288, max_age: "24h" },
      on_late: "skip",
    },
    source_prefixes: [],
    last_snapshot_id: null,
    last_appended_at: null,
    last_maintained_at: null,
    last_maintenance: null,
    last_error: null,
    created_by: "u1",
    created_at: NOW,
    updated_at: NOW,
    version: "v1",
    ...over,
  };
}

function makeView(
  sink: Partial<ApiCubeSink> = {},
  counts: Partial<CubeSinkView["ledger"]["counts"]> = {},
  recent: CubeSinkView["ledger"]["recent"] = [],
): CubeSinkView {
  return {
    sink: makeSink(sink),
    ledger: {
      counts: { pending: 0, appended: 0, skipped: 0, failed: 0, ...counts },
      recent,
    },
  };
}

const SOURCES = [
  { collection_id: "goes19-cmipc-c13", title: null, associations: [], refusal: null },
  { collection_id: "http-src", title: null, associations: [], refusal: "unsupported_source_protocol" },
  { collection_id: "goes19-c13-cube", title: null, associations: [], refusal: null },
];

function renderCard(canAct = true) {
  return render(<CubeSinkCard collectionId="goes19-c13-cube" canAct={canAct} />);
}

beforeEach(() => {
  m.putMutate.mockReset();
  m.patchMutate.mockReset();
  m.sinkState = { data: null, isLoading: false, isError: false, error: null, refetch: vi.fn() };
  m.fresh.mockReset();
  m.fresh.mockImplementation(async () => m.sinkState.data);
  m.putPending = false;
  m.patchPending = false;
  m.sources = { data: SOURCES, isSuccess: true };
});

function choose(optionName: RegExp) {
  fireEvent.keyDown(screen.getByTestId("cube-sink-source"), { key: "Enter" });
  fireEvent.click(screen.getByRole("option", { name: optionName }));
}

describe("CubeSinkCard create", () => {
  it("creates the sink with the default body plus the chosen source", async () => {
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-setup"));
    choose(/^goes19-cmipc-c13$/);
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    await waitFor(() => expect(m.putMutate).toHaveBeenCalled());
    expect(m.putMutate.mock.calls[0][0]).toEqual(
      formToPutBody({ ...CUBE_FORM_DEFAULTS, source_collection_id: "goes19-cmipc-c13" }, true),
    );
  });

  it("excludes the collection itself and disables refused sources with the reason", () => {
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-setup"));
    fireEvent.keyDown(screen.getByTestId("cube-sink-source"), { key: "Enter" });
    expect(screen.queryByRole("option", { name: /goes19-c13-cube/ })).toBeNull();
    const refused = screen.getByRole("option", { name: /http-src.*not an S3 source/ });
    expect(refused).toHaveAttribute("aria-disabled", "true");
  });

  it("hints when no source is eligible", () => {
    m.sources = { data: [SOURCES[1]], isSuccess: true };
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-setup"));
    expect(screen.getByTestId("cube-sink-no-source").textContent).toMatch(
      /No eligible source: add a reference-mode ingest association on an anonymous S3 connection first\./,
    );
  });

  it("renders a 422 source code under the picker", async () => {
    m.putMutate.mockImplementation((_b, opts) =>
      opts.onError(new CubeSinkApiError("Cube sinks read s3 only", 422, "unsupported_source_protocol")),
    );
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-setup"));
    choose(/^goes19-cmipc-c13$/);
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    expect(await screen.findByTestId("cube-sink-error-source_collection_id")).toHaveTextContent(
      "Cube sinks read s3 only",
    );
  });

  it("renders 400 details next to the matching input", async () => {
    m.putMutate.mockImplementation((_b, opts) =>
      opts.onError(
        new CubeSinkApiError("Validation failed", 400, undefined, [
          { path: ["config", "window", "max_steps"], message: "Too small" },
        ]),
      ),
    );
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-setup"));
    choose(/^goes19-cmipc-c13$/);
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    expect(await screen.findByTestId("cube-sink-error-max_steps")).toHaveTextContent("Too small");
  });

  it("blocks save on client validation errors", async () => {
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-setup"));
    choose(/^goes19-cmipc-c13$/);
    fireEvent.change(screen.getByTestId("cube-sink-max-steps"), { target: { value: "0" } });
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    expect(await screen.findByTestId("cube-sink-error-max_steps")).toBeInTheDocument();
    expect(m.putMutate).not.toHaveBeenCalled();
  });

  it("shows a form-level alert for other server refusals", async () => {
    m.putMutate.mockImplementation((_b, opts) =>
      opts.onError(new CubeSinkApiError("Item '_cube' exists", 409, "reserved_item_id")),
    );
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-setup"));
    choose(/^goes19-cmipc-c13$/);
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    expect(await screen.findByTestId("cube-sink-alert")).toHaveTextContent("Item '_cube' exists");
  });
});

describe("CubeSinkCard existing sink", () => {
  it("locks source and layout once the repository exists, keeps the window editable", async () => {
    m.sinkState = { data: makeView({ last_snapshot_id: "ABCDEF0123456789", enabled: false }), isLoading: false };
    renderCard();
    expect(screen.getByTestId("cube-sink-locked-note")).toHaveTextContent(
      "Locked: the cube repository exists. Only the window can change.",
    );
    expect(screen.getByTestId("cube-sink-source")).toBeDisabled();
    for (const id of ["append-dim", "variables", "loadable-variables"]) {
      expect(screen.getByTestId(`cube-sink-${id}`)).toBeDisabled();
    }
    const steps = screen.getByTestId("cube-sink-max-steps");
    expect(steps).not.toBeDisabled();
    fireEvent.change(steps, { target: { value: "100" } });
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    await waitFor(() => expect(m.putMutate).toHaveBeenCalled());
    const body = m.putMutate.mock.calls[0][0];
    expect(body.enabled).toBe(false);
    expect(body.config.window.max_steps).toBe(100);
  });

  it("refuses writes while the first append is pending", () => {
    m.sinkState = { data: makeView({ last_snapshot_id: null }, { pending: 3 }), isLoading: false };
    renderCard();
    expect(screen.getByTestId("cube-sink-save")).toBeDisabled();
    expect(screen.getByTestId("cube-sink-enabled")).toBeDisabled();
    expect(screen.getByTestId("cube-sink-pending-note")).toHaveTextContent(
      "The first append is running. Changes wait until it commits",
    );
  });

  it("allows writes with no snapshot and nothing pending", () => {
    m.sinkState = { data: makeView({ last_snapshot_id: null }, { pending: 0 }), isLoading: false };
    renderCard();
    expect(screen.getByTestId("cube-sink-save")).not.toBeDisabled();
    expect(screen.getByTestId("cube-sink-enabled")).not.toBeDisabled();
    expect(screen.queryByTestId("cube-sink-pending-note")).toBeNull();
  });

  it("shows the reload alert on a layout-locked 409", async () => {
    m.sinkState = { data: makeView(), isLoading: false };
    m.putMutate.mockImplementation((_b, opts) =>
      opts.onError(new CubeSinkApiError("locked", 409, "cube_layout_locked")),
    );
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    expect(await screen.findByTestId("cube-sink-alert")).toHaveTextContent(
      "The sink changed since this page loaded. It has been reloaded; review and save again.",
    );
  });

  it("toggles with PATCH and shows a refusal inline", async () => {
    m.sinkState = { data: makeView(), isLoading: false };
    m.patchMutate.mockImplementation((_b, opts) =>
      opts.onError(new CubeSinkApiError("changed, reload", 409, "cube_sink_conflict")),
    );
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-enabled"));
    await waitFor(() =>
      expect(m.patchMutate).toHaveBeenCalledWith({ enabled: false }, expect.anything()),
    );
    expect(await screen.findByTestId("cube-sink-toggle-alert")).toHaveTextContent("changed, reload");
  });
});

describe("CubeSinkCard review fixes", () => {
  const setSink = (data: unknown) => {
    m.sinkState = { ...m.sinkState, data, isLoading: false };
  };

  it("submit: a fresh read that finds the first append running blocks the PUT", async () => {
    setSink(makeView({ last_snapshot_id: null }, { pending: 0 })); // cached: quiet
    m.fresh.mockImplementation(async () => {
      const fresh = makeView({ last_snapshot_id: null }, { pending: 2 });
      setSink(fresh); // the real fetch also updates the cache the card reads
      return fresh;
    });
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    await waitFor(() => expect(m.fresh).toHaveBeenCalled());
    expect(await screen.findByTestId("cube-sink-pending-note")).toBeInTheDocument();
    expect(m.putMutate).not.toHaveBeenCalled();
  });

  it("toggle: a fresh read that finds the first append running blocks the PATCH", async () => {
    setSink(makeView({ last_snapshot_id: null }, { pending: 0 }));
    m.fresh.mockImplementation(async () => {
      const fresh = makeView({ last_snapshot_id: null }, { pending: 1 });
      setSink(fresh);
      return fresh;
    });
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-enabled"));
    await waitFor(() => expect(m.fresh).toHaveBeenCalled());
    expect(await screen.findByTestId("cube-sink-pending-note")).toBeInTheDocument();
    expect(m.patchMutate).not.toHaveBeenCalled();
  });

  it("writes go ahead when the fresh read is quiet", async () => {
    setSink(makeView());
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    await waitFor(() => expect(m.putMutate).toHaveBeenCalled());
    expect(m.fresh).toHaveBeenCalled();
  });

  it("renders a 409 first_append_pending as the first-append note", async () => {
    setSink(makeView());
    m.putMutate.mockImplementation((_b, opts) =>
      opts.onError(new CubeSinkApiError("first append", 409, "first_append_pending")),
    );
    renderCard();
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    expect(await screen.findByTestId("cube-sink-pending-note")).toBeInTheDocument();
    expect(screen.queryByTestId("cube-sink-alert")).toBeNull();
  });

  it("re-seeds after a 409 cube_layout_locked when only last_snapshot_id changed", async () => {
    setSink(makeView());
    m.putMutate.mockImplementation((_b, opts) => {
      opts.onError(new CubeSinkApiError("locked", 409, "cube_layout_locked"));
    });
    const { rerender } = renderCard();
    fireEvent.change(screen.getByTestId("cube-sink-max-steps"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("cube-sink-save"));
    await screen.findByTestId("cube-sink-alert");
    expect(screen.getByTestId("cube-sink-append-dim")).not.toBeDisabled();
    // The refetch lands: same version, first commit set the snapshot.
    const refetched = makeView({ last_snapshot_id: "SNAP1" });
    refetched.sink.config.window = { max_steps: 100, max_age: "24h" };
    setSink(refetched);
    rerender(<CubeSinkCard collectionId="goes19-c13-cube" canAct />);
    await waitFor(() => expect(screen.getByTestId("cube-sink-append-dim")).toBeDisabled());
    expect(screen.getByTestId("cube-sink-max-steps")).toHaveValue("100");
  });

  it("toggling Enabled keeps unsaved edits", async () => {
    setSink(makeView());
    const { rerender } = renderCard();
    fireEvent.change(screen.getByTestId("cube-sink-max-steps"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("cube-sink-enabled"));
    await waitFor(() => expect(m.patchMutate).toHaveBeenCalled());
    // PATCH bumped the version.
    setSink(makeView({ enabled: false, version: "v2" }));
    rerender(<CubeSinkCard collectionId="goes19-c13-cube" canAct />);
    await act(async () => {});
    expect(screen.getByTestId("cube-sink-max-steps")).toHaveValue("5");
  });

  it("a failed GET shows an error state with retry, never the setup button", () => {
    m.sinkState = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new CubeSinkApiError("boom", 500),
      refetch: vi.fn(),
    };
    renderCard();
    expect(screen.getByTestId("cube-sink-error")).toHaveTextContent("boom");
    expect(screen.queryByTestId("cube-sink-setup")).toBeNull();
    expect(screen.queryByTestId("cube-sink-save")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(m.sinkState.refetch).toHaveBeenCalled();
  });

  it("disables Save and the switch while a PUT or a PATCH is pending", () => {
    setSink(makeView());
    m.putPending = true;
    const { unmount } = renderCard();
    expect(screen.getByTestId("cube-sink-save")).toBeDisabled();
    expect(screen.getByTestId("cube-sink-enabled")).toBeDisabled();
    unmount();
    m.putPending = false;
    m.patchPending = true;
    renderCard();
    expect(screen.getByTestId("cube-sink-save")).toBeDisabled();
    expect(screen.getByTestId("cube-sink-enabled")).toBeDisabled();
  });
});

describe("CubeSinkStatus health badge", () => {
  const OLD = new Date(Date.now() - 3 * 86_400_000).toISOString();
  const cases: Record<(typeof CUBE_SINK_HEALTH_STATES)[number], Partial<ApiCubeSink>> = {
    disabled: { enabled: false },
    error: { last_error: "boom" },
    attention: {
      last_maintenance: {
        status: "attention",
        started_at: NOW,
        attention: ["repo_size"],
        durations_ms: {},
      },
    },
    stale: { last_appended_at: OLD, last_snapshot_id: "abc" },
    waiting: { last_appended_at: null },
    ok: { last_appended_at: NOW, last_snapshot_id: "abc" },
  };
  for (const state of CUBE_SINK_HEALTH_STATES) {
    it(`renders the ${state} state`, () => {
      m.sinkState = { data: makeView(cases[state]), isLoading: false };
      renderCard();
      const badge = screen.getByTestId("cube-sink-health");
      expect(badge).toHaveAttribute("data-state", state);
      expect(badge).toHaveTextContent(CUBE_SINK_HEALTH_LABEL[state]);
    });
  }

  it("lists reasons, counts, last error, snapshot, maintenance and recent rows", () => {
    m.sinkState = {
      data: makeView(
        {
          last_error: "kaboom",
          last_snapshot_id: "0123456789abcdefXYZ",
          last_appended_at: NOW,
          last_maintenance: {
            status: "attention",
            started_at: NOW,
            finished_at: NOW,
            attention: ["repo_size", "brand_new_reason"],
            durations_ms: {},
            publish_outcome: "published",
            gc: {
              snapshots_deleted: 0, manifests_deleted: 0, chunks_deleted: 0,
              transaction_logs_deleted: 0, attributes_deleted: 0,
              bytes_deleted: 2048, objects_failed_to_delete: 0, delete_errors: [],
            },
            total_bytes: 1048576,
            warn_bytes: 10485760,
          },
        },
        { appended: 5, failed: 1 },
        [
          { item_id: "item-1", item_datetime: NOW, status: "skipped", reason: "late", snapshot_id: null, attempts: 1, updated_at: NOW },
        ],
      ),
      isLoading: false,
    };
    renderCard();
    expect(screen.getByTestId("cube-sink-reasons")).toHaveTextContent("Last append failed");
    expect(screen.getByTestId("cube-sink-last-error")).toHaveTextContent("kaboom");
    expect(screen.getByTestId("cube-sink-snapshot")).toHaveTextContent("0123456789ab");
    expect(screen.getByTestId("cube-sink-snapshot").textContent).not.toContain("XYZ");
    expect(screen.getByTestId("cube-sink-counts")).toHaveTextContent("Appended5");
    const maint = screen.getByTestId("cube-sink-maintenance");
    expect(maint).toHaveTextContent("Needs attention");
    expect(maint).toHaveTextContent("2.0 KB deleted");
    expect(maint).toHaveTextContent("1.0 MB of 10.0 MB");
    expect(within(screen.getByTestId("cube-sink-recent")).getByText("item-1")).toBeInTheDocument();
    expect(screen.getByTestId("cube-sink-recent")).toHaveTextContent("Arrived after a newer step");
  });

  it("says the size readout is pending without total_bytes", () => {
    m.sinkState = {
      data: makeView({
        last_maintenance: { status: "ok", started_at: NOW, attention: [], durations_ms: {} },
      }),
      isLoading: false,
    };
    renderCard();
    expect(screen.getByTestId("cube-sink-maintenance")).toHaveTextContent("Size readout pending");
  });
});

describe("CubeSinkCard viewers", () => {
  it("renders nothing without a sink", () => {
    const { container } = renderCard(false);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows status only, without form or switch, when a sink exists", () => {
    m.sinkState = { data: makeView(), isLoading: false };
    renderCard(false);
    expect(screen.getByTestId("cube-sink-status")).toBeInTheDocument();
    expect(screen.queryByTestId("cube-sink-save")).toBeNull();
    expect(screen.queryByTestId("cube-sink-enabled")).toBeNull();
  });

  it("shows a skeleton while loading", () => {
    m.sinkState = { data: undefined, isLoading: true };
    renderCard();
    expect(screen.getByTestId("cube-sink-loading")).toBeInTheDocument();
  });
});
