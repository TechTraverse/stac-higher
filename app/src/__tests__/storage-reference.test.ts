// @vitest-environment node
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { query } from "@/lib/db/connection";
import { lookupReferenceHref } from "@/lib/storage/reference";

const mockQuery = vi.mocked(query);
beforeEach(() => mockQuery.mockReset());

describe("lookupReferenceHref", () => {
  it("returns the source_href when a referenced row matches", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ source_href: "http://src/scene.tif" }] } as never);
    const href = await lookupReferenceHref("col", "scene", "scene.tif");
    expect(href).toBe("http://src/scene.tif");
  });

  it("returns null when no referenced row matches", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] } as never);
    const href = await lookupReferenceHref("col", "scene", "scene.tif");
    expect(href).toBeNull();
  });
});
