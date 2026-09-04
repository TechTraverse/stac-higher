/**
 * `/graph` (P-3, spec §7): the Pipelines view draws one row per collection
 * with one `<path>` per edge, search filters the rows, and the ghost fixture
 * — a graph in which no node is orphaned — yields no "Not wired" section.
 *
 * Query hooks are mocked; what is under test is the render logic over a graph
 * payload, not the fetch.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FIXTURE_GRAPH } from "@stac-higher/shared";

const { usePipelineGraphMock, useAlertsMock } = vi.hoisted(() => ({
  usePipelineGraphMock: vi.fn(),
  useAlertsMock: vi.fn(),
}));

vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => (
      <QueryProvider>{children}</QueryProvider>
    ),
  };
});
vi.mock("@/lib/monitoring/graph-queries", () => ({
  usePipelineGraph: () => usePipelineGraphMock(),
  useFlowHistory: () => ({ data: [] }),
}));
vi.mock("@/lib/monitoring/queries", () => ({
  useAlerts: (state: string) => useAlertsMock(state),
}));

import { PipelineGraphPage } from "@/components/monitoring/PipelineGraph";

const COLLECTIONS = FIXTURE_GRAPH.nodes.filter((n) => n.type === "collection");

beforeAll(() => {
  // The QueryProvider pulls in sonner, which reads matchMedia on mount.
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
  window.history.replaceState(null, "", "/graph");
  usePipelineGraphMock.mockReturnValue({
    data: FIXTURE_GRAPH,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
  useAlertsMock.mockReturnValue({ data: [] });
});

function pipelines(): HTMLElement {
  return screen.getByRole("tabpanel");
}

describe("/graph — Pipelines view", () => {
  it("opens on Pipelines, one row per collection", () => {
    render(<PipelineGraphPage />);
    expect(screen.getByRole("tab", { name: "Pipelines" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const rows = within(pipelines()).getAllByRole("img");
    expect(rows).toHaveLength(COLLECTIONS.length);
    for (const node of COLLECTIONS) {
      expect(
        within(pipelines()).getByLabelText(`${node.label} lineage`),
      ).toBeInTheDocument();
    }
  });

  it("draws one <path> per edge of the row's lineage", () => {
    render(<PipelineGraphPage />);
    const row = within(pipelines()).getByLabelText("goes-geocolor lineage");
    // ingest, extractor, process_source, process_output, deliver.
    expect(row.querySelectorAll("path[data-edge-kind]")).toHaveLength(5);
    expect(
      row.querySelectorAll('path[data-edge-kind="extractor"]'),
    ).toHaveLength(1);
  });

  it("badges the extractor so its role reads without hovering", () => {
    render(<PipelineGraphPage />);
    const badges = pipelines().querySelectorAll('[data-node-badge="extractor"]');
    // One per row whose lineage contains the extractor: its own product's row,
    // and the rows of everything derived downstream of that product.
    expect(badges.length).toBeGreaterThan(0);
  });

  it("shows a chained product's whole chain, not one hop each way", () => {
    render(<PipelineGraphPage />);
    const row = within(pipelines()).getByLabelText("demo-downscaled lineage");
    const chips = row.parentElement as HTMLElement;
    // Back to the source connection AND forward to the far product — the two
    // ends the old one-hop-each-way panel could never reach. (The fixture has
    // a process and a collection both labelled `demo-thumbnails`, as a real
    // platform often does, so the assertion is on hrefs, not on text.)
    const hrefs = Array.from(chips.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("/connections");
    expect(hrefs).toContain("/collections/demo-thumbnails");
    expect(hrefs).toContain("/collections/demo-scenes");
  });

  it("filters rows by search", async () => {
    const user = userEvent.setup();
    render(<PipelineGraphPage />);
    await user.type(screen.getByLabelText("Find a product"), "geocolor");

    expect(
      within(pipelines()).getByLabelText("goes-geocolor lineage"),
    ).toBeInTheDocument();
    expect(
      within(pipelines()).queryByLabelText("demo-scenes lineage"),
    ).not.toBeInTheDocument();
  });

  it("says so when nothing matches", async () => {
    const user = userEvent.setup();
    render(<PipelineGraphPage />);
    await user.type(screen.getByLabelText("Find a product"), "zzz");
    expect(screen.getByText(/No product matches/)).toBeInTheDocument();
  });

  it("renders no Not-wired section when every node has an edge (I-104)", () => {
    render(<PipelineGraphPage />);
    expect(screen.queryByText("Not wired")).not.toBeInTheDocument();
  });

  it("lists a genuinely unwired node under Not wired", () => {
    usePipelineGraphMock.mockReturnValue({
      data: {
        nodes: [
          ...FIXTURE_GRAPH.nodes,
          {
            id: "conn:lonely",
            type: "connection",
            label: "lonely",
            group_id: null,
            meta: {},
          },
        ],
        edges: FIXTURE_GRAPH.edges,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<PipelineGraphPage />);
    expect(screen.getByText("Not wired")).toBeInTheDocument();
    expect(screen.getByText("lonely")).toBeInTheDocument();
  });
});

describe("/graph — the view switch", () => {
  it("puts the chosen view in ?view= so the page is linkable", async () => {
    const user = userEvent.setup();
    render(<PipelineGraphPage />);

    await user.click(screen.getByRole("tab", { name: "Graph" }));
    expect(new URL(window.location.href).searchParams.get("view")).toBe("graph");

    await user.click(screen.getByRole("tab", { name: "Pipelines" }));
    expect(new URL(window.location.href).searchParams.get("view")).toBeNull();
  });

  it("opens on the Graph view when the URL asks for it", () => {
    window.history.replaceState(null, "", "/graph?view=graph");
    render(<PipelineGraphPage />);
    expect(screen.getByRole("tab", { name: "Graph" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

describe("/graph — Graph view", () => {
  const openGraph = async () => {
    const user = userEvent.setup();
    render(<PipelineGraphPage />);
    await user.click(screen.getByRole("tab", { name: "Graph" }));
    return user;
  };

  it("draws the whole graph — one <path> per edge", async () => {
    await openGraph();
    const svg = screen.getByLabelText("The whole pipeline graph");
    expect(svg.querySelectorAll("path[data-edge-kind]")).toHaveLength(
      FIXTURE_GRAPH.edges.length,
    );
    expect(FIXTURE_GRAPH.edges.length).toBeGreaterThanOrEqual(4);
  });

  it("keeps unwired nodes out of the SVG and in the Not-wired row", async () => {
    usePipelineGraphMock.mockReturnValue({
      data: {
        nodes: [
          ...FIXTURE_GRAPH.nodes,
          {
            id: "conn:lonely",
            type: "connection",
            label: "lonely",
            group_id: null,
            meta: {},
          },
        ],
        edges: FIXTURE_GRAPH.edges,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    await openGraph();

    const chips = screen.getByLabelText("The whole pipeline graph")
      .parentElement as HTMLElement;
    expect(within(chips).queryByText("lonely")).not.toBeInTheDocument();
    expect(screen.getByText("Not wired")).toBeInTheDocument();
  });

  it("fades everything outside a clicked node's lineage", async () => {
    const user = await openGraph();
    const chips = screen.getByLabelText("The whole pipeline graph")
      .parentElement as HTMLElement;

    await user.click(within(chips).getByText("goes-abi-mcmipc"));

    const dimmed = (label: string) =>
      (within(chips).getByText(label).closest("button") as HTMLElement).className;
    // Everything in the GOES lineage stays lit…
    expect(dimmed("goes-abi-mcmipc")).not.toContain("opacity-25");
    expect(dimmed("NOAA NODD goes19")).not.toContain("opacity-25");
    // …and the unrelated demo pipeline fades.
    expect(dimmed("demo-scenes")).toContain("opacity-25");
  });

  it("offers an Open link for the selected node, and clears", async () => {
    const user = await openGraph();
    const chips = screen.getByLabelText("The whole pipeline graph")
      .parentElement as HTMLElement;

    await user.click(within(chips).getByText("geocolor-dest"));
    expect(screen.getByRole("link", { name: /Open/ })).toHaveAttribute(
      "href",
      "/connections",
    );

    await user.click(screen.getByRole("button", { name: /Clear/ }));
    expect(screen.queryByRole("link", { name: /Open/ })).not.toBeInTheDocument();
  });

  it("says so when every node is an island", async () => {
    usePipelineGraphMock.mockReturnValue({
      data: {
        nodes: [
          {
            id: "conn:lonely",
            type: "connection",
            label: "lonely",
            group_id: null,
            meta: {},
          },
        ],
        edges: [],
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    await openGraph();
    expect(
      screen.getByText(/Nothing is wired yet — every node below is an island/),
    ).toBeInTheDocument();
  });
});

describe("/graph — states", () => {
  it("shows the empty state on a bare platform", () => {
    usePipelineGraphMock.mockReturnValue({
      data: { nodes: [], edges: [] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<PipelineGraphPage />);
    expect(screen.getByText("Nothing wired yet")).toBeInTheDocument();
  });

  it("surfaces a load error with a retry", () => {
    usePipelineGraphMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("boom"),
      refetch: vi.fn(),
    });
    render(<PipelineGraphPage />);
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
});
