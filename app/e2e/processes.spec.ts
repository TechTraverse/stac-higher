import { test, expect } from "@playwright/test";

/**
 * UI-surface e2e for the Phase 9 process pages and the pipeline graph
 * (M5-F, spec §7/§10).
 *
 * Needs the app DB (every route here reads `stac_higher.*`), which the Docker
 * stack provides — the same precondition as monitoring.spec.ts and
 * data-flow.spec.ts.
 *
 * Read-mostly by design. Creating a process is a durable write and executing
 * one needs the Docker executor, so the full create→deploy→run loop belongs
 * to the M5-G gate rehearsal, not to a suite that runs against a shared DB.
 */
test.describe("Processes", () => {
  test("is reachable from the sidebar nav", async ({ page }) => {
    await page.goto("/catalogs");
    await page.getByRole("link", { name: "Processes" }).click();
    await expect(
      page.getByRole("heading", { name: "Processes", level: 1 }),
    ).toBeVisible();
  });

  test("shows either processes or the empty state, never an error", async ({
    page,
  }) => {
    await page.goto("/processes");
    await expect(
      page
        .getByText(/No processes yet|Ceiling: \d+ runs\/hour/)
        .first(),
    ).toBeVisible();
  });

  test("offers a create action to an operator", async ({ page }) => {
    // Dev-bypass identity is an operator, so the button is present; the
    // dialog is opened but deliberately NOT submitted.
    await page.goto("/processes");
    const create = page.getByRole("button", { name: "New process" });
    await expect(create).toBeVisible();
    await create.click();
    await expect(
      page.getByRole("heading", { name: "New process" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
  });
});

test.describe("Pipeline graph", () => {
  test("is reachable from the sidebar nav", async ({ page }) => {
    await page.goto("/catalogs");
    await page.getByRole("link", { name: "Pipeline graph" }).click();
    await expect(
      page.getByRole("heading", { name: "Pipeline graph", level: 1 }),
    ).toBeVisible();
  });

  test("renders the pipeline columns or the nothing-wired empty state", async ({
    page,
  }) => {
    await page.goto("/graph");
    // UI-7 renamed the columns to the five-stage pipeline; the empty state is
    // still the other legal outcome on a bare database.
    await expect(
      page
        .getByText(
          /Nothing wired yet|Source connections|Source products|Processes|Derived products|Destinations/,
        )
        .first(),
    ).toBeVisible();
  });

  test("does not surface an error state", async ({ page }) => {
    // The graph joins several tables; a shape error here would show as the
    // shared ErrorState rather than an empty graph.
    await page.goto("/graph");
    await expect(page.getByText(/Failed to load/)).toHaveCount(0);
  });
});

const LINEAGE_COLLECTION = "e2e-m5f-lineage";

test.describe("Collection lineage panel", () => {
  // Its own fixture collection rather than "whatever happens to be first":
  // the Data-flow tab is built-in-catalog only, and a shared DB makes
  // "the first collection" a different thing on every run.
  test.beforeAll(async ({ request }) => {
    await request.delete(`/api/catalog/collections/${LINEAGE_COLLECTION}`);
    const created = await request.post("/api/catalog/collections", {
      data: {
        type: "Collection",
        stac_version: "1.0.0",
        id: LINEAGE_COLLECTION,
        description: "M5-F lineage panel fixture",
        license: "proprietary",
        extent: {
          spatial: { bbox: [[-180, -90, 180, 90]] },
          temporal: { interval: [[null, null]] },
        },
        links: [],
      },
    });
    expect(created.ok()).toBeTruthy();
  });

  test.afterAll(async ({ request }) => {
    await request.delete(`/api/catalog/collections/${LINEAGE_COLLECTION}`);
  });

  test("renders inside the Data flow tab", async ({ page }) => {
    await page.goto(`/collections/${LINEAGE_COLLECTION}`);
    await page.getByRole("tab", { name: "Data flow" }).click();

    await expect(page.getByRole("heading", { name: "Lineage" })).toBeVisible();
    // A brand-new collection is wired to nothing, and the panel must say so
    // rather than render an empty box.
    await expect(
      page.getByText("Nothing feeds this collection"),
    ).toBeVisible();
    await expect(page.getByText("This collection feeds nothing")).toBeVisible();
  });
});
