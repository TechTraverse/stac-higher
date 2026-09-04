// @vitest-environment node
// (server-side db code — no DOM involved)
/**
 * `loadGraph`'s node queries (M5-E) — specifically the collection union, whose
 * process branches must exclude soft-deleted processes exactly as
 * `loadGraphEdges` does (I-104).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import { query } from "@/lib/db/connection";
import { loadGraph } from "@/lib/graph/storage";

const mockQuery = vi.mocked(query);

const LIVE_PROCESS = "3a9f1c2e-0000-4000-8000-0000000000a1";
const DEAD_PROCESS = "3a9f1c2e-0000-4000-8000-0000000000a2";

/**
 * A tiny stand-in for the platform tables. `query` is dispatched on the SQL
 * text (the four `loadGraph` queries plus `loadGraphEdges`' four run
 * concurrently, so call ORDER is not a reliable key), and each handler applies
 * the same `deleted_at` semantics the real rows would.
 */
function fakeDatabase(options: { collectionUnion: (sql: string) => string[] }) {
  mockQuery.mockImplementation((async (sql: string) => {
    const rows = (() => {
      // --- loadGraph's own node queries ---
      if (/FROM stac_higher\.connections\b/.test(sql)) return [];
      if (/^\s*SELECT id, name, group_id, enabled/.test(sql)) {
        // A soft-deleted process is not a node either.
        return [
          {
            id: LIVE_PROCESS,
            name: "goes-geocolor",
            group_id: null,
            enabled: true,
            current_revision: "rev-1",
          },
        ];
      }
      if (/coalesce\(s\.serving_enabled/.test(sql)) {
        return options.collectionUnion(sql).map((collection_id) => ({
          collection_id,
          group_id: null,
          archived: false,
          serving_enabled: false,
        }));
      }
      // --- loadGraphEdges ---
      if (/FROM stac_higher\.process_sources s\b/.test(sql)) {
        return [
          {
            id: "src-1",
            process_id: LIVE_PROCESS,
            collection_id: "goes-abi-mcmipc",
          },
        ];
      }
      if (/FROM stac_higher\.process_outputs o\b/.test(sql)) {
        return [
          {
            id: "out-1",
            process_id: LIVE_PROCESS,
            collection_id: "goes-geocolor",
          },
        ];
      }
      return [];
    })();
    return { rows, rowCount: rows.length };
  }) as never);
}

/** The SQL the collection union actually ran, for shape assertions. */
function collectionUnionSql(): string {
  const call = mockQuery.mock.calls.find(([sql]) =>
    /coalesce\(s\.serving_enabled/.test(sql as string),
  );
  if (!call) throw new Error("collection union query never ran");
  return call[0] as string;
}

beforeEach(() => {
  mockQuery.mockReset();
});

describe("loadGraph — collection nodes", () => {
  it("joins processes and excludes soft-deleted ones on both branches", async () => {
    fakeDatabase({ collectionUnion: () => [] });
    await loadGraph(null);

    const sql = collectionUnionSql();
    expect(sql).toMatch(
      /FROM stac_higher\.process_sources ps\s+JOIN stac_higher\.processes p ON p\.id = ps\.process_id\s+WHERE p\.deleted_at IS NULL/,
    );
    expect(sql).toMatch(
      /FROM stac_higher\.process_outputs po\s+JOIN stac_higher\.processes p ON p\.id = po\.process_id\s+WHERE p\.deleted_at IS NULL/,
    );
  });

  it("keeps the collections of a live process and drops a deleted one's ghosts", async () => {
    // The fake honours the query's own filter: rows belonging to the deleted
    // process (`e2e-goes-*`, left behind by an e2e run) are excluded by the
    // JOIN, exactly as PostgreSQL would exclude them.
    const rowsByProcess: Record<string, string[]> = {
      [LIVE_PROCESS]: ["goes-abi-mcmipc", "goes-geocolor"],
      [DEAD_PROCESS]: ["e2e-goes-src", "e2e-goes-out"],
    };
    fakeDatabase({
      collectionUnion: (sql) =>
        /p\.deleted_at IS NULL/.test(sql)
          ? rowsByProcess[LIVE_PROCESS]
          : [...rowsByProcess[LIVE_PROCESS], ...rowsByProcess[DEAD_PROCESS]],
    });

    const graph = await loadGraph(null);

    const collections = graph.nodes
      .filter((node) => node.type === "collection")
      .map((node) => node.label)
      .sort();
    expect(collections).toEqual(["goes-abi-mcmipc", "goes-geocolor"]);
    expect(collections).not.toContain("e2e-goes-src");

    // And every surviving collection is genuinely wired — no degree-0 ghost.
    const touched = new Set(graph.edges.flatMap((e) => [e.from, e.to]));
    for (const node of graph.nodes) expect(touched.has(node.id)).toBe(true);
  });
});
