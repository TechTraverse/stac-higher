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
        .getByText(/No processes yet|Ceiling \d+ runs\/hour/)
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

test.describe("Images (C-3)", () => {
  test("is reachable from the sidebar nav", async ({ page }) => {
    await page.goto("/catalogs");
    await page.getByRole("link", { name: "Images", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Images", level: 1 })).toBeVisible();
  });

  test("shows either images or the empty state, never an error", async ({ page }) => {
    await page.goto("/images");
    await expect(
      page.getByText(/No images yet|\d+ images? in the registry/).first(),
    ).toBeVisible();
    await expect(page.getByText(/Failed to load/)).toHaveCount(0);
  });

  test("previews the stored reference in Add image without submitting", async ({ page }) => {
    // Dev-bypass identity is an operator. The dialog is opened but NOT
    // submitted: adding writes a durable row and queues a scan.
    await page.goto("/images");
    await page.getByRole("button", { name: "Add image" }).first().click();
    await expect(page.getByRole("heading", { name: "Add image" })).toBeVisible();
    await page.getByLabel("Image reference").fill("python:3.12-slim");
    await expect(page.getByText("docker.io/library/python:3.12-slim")).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("heading", { name: "Add image" })).toHaveCount(0);
  });
});

test.describe("Deploy form runtime chooser (C-3)", () => {
  test("offers the three runtimes and hides the editor for a container image", async ({ page }) => {
    await page.goto("/processes");
    await expect(
      page.getByText(/No processes yet|Ceiling \d+ runs\/hour/).first(),
    ).toBeVisible();
    const first = page.locator('a[href^="/processes/"]').first();
    // A bare database has no process to open: a legal state for this suite.
    if ((await first.count()) === 0) return;
    await first.click();
    const runtime = page.getByRole("group", { name: "Runtime" });
    // A built-in process shows the read-only built-in card, not the code form.
    if ((await runtime.count()) === 0) return;
    await expect(runtime.getByRole("radio", { name: /Platform image/ })).toBeVisible();
    await expect(runtime.getByRole("radio", { name: /Custom image \+ your code/ })).toBeVisible();
    const container = runtime.getByRole("radio", { name: /Container image/ });
    await expect(container).toBeVisible();
    if (await container.isDisabled()) return; // a member sees the form read-only
    await container.check();
    await expect(page.getByLabel("Command (optional)")).toBeVisible();
    await expect(page.getByLabel("Process code")).toHaveCount(0);
    // Deliberately NOT deployed: nothing is approved without a scanner.
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

  test("opens on the Pipelines view, or the nothing-wired empty state", async ({
    page,
  }) => {
    await page.goto("/graph");
    // P-3 made Pipelines the default view (spec §9.1); the empty state is
    // still the other legal outcome on a bare database.
    await expect(
      page.getByText(/Nothing wired yet|One row per product/).first(),
    ).toBeVisible();
  });

  test("puts the chosen view in the URL", async ({ page }) => {
    await page.goto("/graph");
    const graphTab = page.getByRole("tab", { name: "Graph" });
    // Skipped on a bare database, where there are no tabs to switch.
    if ((await graphTab.count()) === 0) return;
    await graphTab.click();
    await expect(page).toHaveURL(/[?&]view=graph/);
  });

  test("draws real edges for a wired product", async ({ page }) => {
    await page.goto("/graph");
    // Wait for the graph query to settle FIRST: the rows and the empty state
    // are both async, and counting paths before either exists reads as "no
    // edges" on a perfectly healthy platform.
    await expect(
      page.getByText(/Nothing wired yet|One row per product/).first(),
    ).toBeVisible();
    if ((await page.getByText("Nothing wired yet").count()) > 0) return;

    // Nothing wired is a legal state for this suite's shared database; when
    // something IS wired, the row must draw its edges as paths rather than
    // implying them with columns (spec §5.1).
    const paths = page.locator("svg path[data-edge-kind]");
    if ((await paths.count()) === 0) {
      await expect(
        page.getByText(/No products are wired|Not wired/).first(),
      ).toBeVisible();
      return;
    }
    // `toBeAttached`, not `toBeVisible`: a level hop is a horizontal line, and
    // a zero-height bounding box reads as hidden to Playwright.
    await expect(paths.first()).toBeAttached();

    // When the standing GOES demo is seeded its extractor is in the picture,
    // badged — the thing the old five-column view could not show at all.
    const extractor = page.locator('svg path[data-edge-kind="extractor"]');
    if ((await extractor.count()) > 0) {
      await expect(
        page.locator('[data-node-badge="extractor"]').first(),
      ).toBeVisible();
    }
  });

  test("draws the whole graph, with click-to-highlight", async ({ page }) => {
    await page.goto("/graph?view=graph");
    await expect(
      page.getByText(/Nothing wired yet|Every wiring at once/).first(),
    ).toBeVisible();
    if ((await page.getByText("Nothing wired yet").count()) > 0) return;

    const svg = page.getByLabel("The whole pipeline graph");
    if ((await svg.count()) === 0) {
      // Every node an island — legal on a bare shared database.
      await expect(page.getByText(/every node below is an island/)).toBeVisible();
      return;
    }

    // The seeded demo wires GOES (ingest, extractor, source, output, deliver)
    // and the demo chain, so the whole-platform picture is well past four
    // edges. `toBeAttached`: a level hop is a zero-height line to Playwright.
    const paths = svg.locator("path[data-edge-kind]");
    expect(await paths.count()).toBeGreaterThanOrEqual(4);
    await expect(paths.first()).toBeAttached();

    // Clicking a node offers a way into it and fades the rest.
    await page.locator('[data-node-badge="extractor"]').first().click();
    await expect(page.getByRole("link", { name: /Open/ })).toBeVisible();
    await page.getByRole("button", { name: /Clear/ }).click();
    await expect(page.getByRole("link", { name: /Open/ })).toHaveCount(0);
  });

  test("filters the product rows by search", async ({ page }) => {
    await page.goto("/graph");
    const search = page.getByLabel("Find a product");
    if ((await search.count()) === 0) return;
    await search.fill("zzz-no-such-product");
    await expect(page.getByText(/No product matches/)).toBeVisible();
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
    // rather than render an empty box. P-3 replaced the two one-hop lists with
    // the lineage row, so there is now ONE such message.
    await expect(
      page.getByText("Nothing is wired to this collection yet."),
    ).toBeVisible();
  });
});
