/**
 * `LineagePanel` (P-3, spec §5.1): the collection page draws the SAME lineage
 * row as the `/graph` Pipelines view, keeping its 30-day flow strips beneath.
 *
 * The regression this guards is what the two one-hop lists could not do:
 * `goes-geocolor`'s panel must reach back past its process to the connection
 * the data actually came from.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { FIXTURE_GRAPH } from "@stac-higher/shared";

const { usePipelineGraphMock, useFlowHistoryMock, useAlertsMock } = vi.hoisted(
  () => ({
    usePipelineGraphMock: vi.fn(),
    useFlowHistoryMock: vi.fn(),
    useAlertsMock: vi.fn(),
  }),
);

vi.mock("@/lib/monitoring/graph-queries", () => ({
  usePipelineGraph: () => usePipelineGraphMock(),
  useFlowHistory: (...args: unknown[]) => useFlowHistoryMock(...args),
}));
vi.mock("@/lib/monitoring/queries", () => ({
  useAlerts: (state: string) => useAlertsMock(state),
}));

import { LineagePanel } from "@/components/collections/LineagePanel";

beforeAll(() => {
  // FlowStrip measures nothing, but jsdom still needs matchMedia for sonner's
  // siblings in the shared bundle.
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
});

beforeEach(() => {
  vi.clearAllMocks();
  usePipelineGraphMock.mockReturnValue({
    data: FIXTURE_GRAPH,
    isLoading: false,
  });
  useFlowHistoryMock.mockReturnValue({ data: [] });
  useAlertsMock.mockReturnValue({ data: [] });
});

describe("LineagePanel", () => {
  it("draws the whole chain, not one hop each way", () => {
    render(<LineagePanel collectionId="goes-geocolor" />);
    const dag = screen.getByLabelText("goes-geocolor lineage");
    const chips = dag.parentElement as HTMLElement;
    const hrefs = Array.from(chips.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    // Two hops upstream, past the process, to the source connection.
    expect(hrefs).toContain("/connections");
    expect(hrefs).toContain("/collections/goes-abi-mcmipc");
    // And forward to the delivery destination.
    expect(dag.querySelectorAll("path[data-edge-kind]")).toHaveLength(5);
  });

  it("keeps a 30-day strip per flow, and only for flows that have one", () => {
    render(<LineagePanel collectionId="goes-geocolor" />);
    // `goes-geocolor` touches two edges: the process_output that feeds it (no
    // flow_stats of its own) and the deliver association (which has them).
    expect(useFlowHistoryMock).toHaveBeenCalledTimes(1);
    expect(useFlowHistoryMock).toHaveBeenCalledWith(
      "association",
      "assoc-geocolor-deliver",
    );
  });

  it("draws no second strip for an extractor edge", () => {
    // The extractor shares its association id with the ingest edge that
    // already carries the strip.
    render(<LineagePanel collectionId="goes-abi-mcmipc" />);
    const subjects = useFlowHistoryMock.mock.calls.map((call) => call[1]);
    expect(subjects.filter((id) => id === "assoc-goes-ingest")).toHaveLength(1);
  });

  it("says so when the collection is wired to nothing", () => {
    usePipelineGraphMock.mockReturnValue({
      data: {
        nodes: [
          {
            id: "coll:lonely",
            type: "collection",
            label: "lonely",
            group_id: null,
            meta: {},
          },
        ],
        edges: [],
      },
      isLoading: false,
    });
    render(<LineagePanel collectionId="lonely" />);
    expect(
      screen.getByText("Nothing is wired to this collection yet."),
    ).toBeInTheDocument();
  });

  it("shows the loading skeleton while the graph is in flight", () => {
    usePipelineGraphMock.mockReturnValue({ data: undefined, isLoading: true });
    const { container } = render(<LineagePanel collectionId="goes-geocolor" />);
    // `LoadingState` renders skeletons, not its message.
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
