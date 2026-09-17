import { test, expect, type APIRequestContext } from "@playwright/test";

/**
 * UI-surface e2e for /map (V-2, spec §4/§6). Needs only the pgstac-backed
 * STAC API the Docker stack serves as the built-in catalog — the same
 * precondition extensions.spec.ts and proxy.spec.ts already assume.
 *
 * Imagery (titiler) and vector tiles (tipg) are live-only paths and belong to
 * V-3/V-4; nothing here touches a tile server.
 *
 * maplibre's layer list lives inside the canvas and is not readable from the
 * page, so this asserts the DOM contract only. That a footprints layer mounts
 * a source under its namespaced id is covered by map-page.test.tsx.
 *
 * The product these tests add is created here through the ADR 0008 BFF, the
 * same fixture pattern collection-settings.spec.ts and data-flow.spec.ts use.
 * They previously reached for whatever the built-in catalog happened to hold
 * and took `.first()` of it, which passes on a workstation carrying the demo
 * seed and fails on a fresh stack with nothing in the catalog — as CI does.
 * Owning the fixture also means these assertions are about the map page
 * rather than about which product sorts first.
 */
const COLLECTION_ID = "e2e-map";
const FOOTPRINTS = `map-add-footprints-${COLLECTION_ID}`;

const collectionBody = {
  id: COLLECTION_ID,
  type: "Collection",
  stac_version: "1.0.0",
  description: "V-2 map e2e fixture",
  license: "proprietary",
  extent: {
    spatial: { bbox: [[-180, -90, 180, 90]] },
    temporal: { interval: [[null, null]] },
  },
  links: [],
};

async function deleteFixtures(request: APIRequestContext) {
  await request.delete(`/api/catalog/collections/${COLLECTION_ID}`);
}

test.describe("Map page", () => {
  test.beforeAll(async ({ request }) => {
    await deleteFixtures(request);
    const created = await request.post("/api/catalog/collections", {
      data: collectionBody,
    });
    expect(created.ok()).toBeTruthy();
  });

  test.afterAll(async ({ request }) => {
    await deleteFixtures(request);
  });

  test("is reachable from the sidebar and starts empty", async ({ page }) => {
    await page.goto("/monitoring");
    await page.getByRole("link", { name: "Map" }).click();

    await expect(page).toHaveURL(/\/map$/);
    await expect(page.getByTestId("map-layer-panel")).toBeVisible();
    await expect(page.getByText("No layers yet")).toBeVisible();
    await expect(page.getByTestId("map-layer-row")).toHaveCount(0);
  });

  test("lists the built-in catalog's products and adds one as footprints", async ({
    page,
  }) => {
    await page.goto("/map");
    await page.getByTestId("map-add-layer").click();

    const option = page.getByTestId(FOOTPRINTS);
    await expect(option).toBeVisible();

    await option.click();

    const row = page.getByTestId("map-layer-row");
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("map-layer-visible")).toBeVisible();
    await expect(row.getByTestId("map-layer-remove")).toBeVisible();
    // No hover has happened, so no tooltip — and no error surface anywhere
    // on the page (spec §4.7).
    await expect(page.getByTestId("map-tooltip")).toHaveCount(0);

    // Adding the same product again is offered as "Added" and disabled.
    await page.getByTestId("map-add-layer").click();
    await expect(page.getByTestId(FOOTPRINTS)).toBeDisabled();
  });

  test("removes the layer it added", async ({ page }) => {
    await page.goto("/map");
    await page.getByTestId("map-add-layer").click();
    await page.getByTestId(FOOTPRINTS).click();
    await expect(page.getByTestId("map-layer-row")).toHaveCount(1);

    await page.getByTestId("map-layer-remove").click();

    await expect(page.getByTestId("map-layer-row")).toHaveCount(0);
    await expect(page.getByText("No layers yet")).toBeVisible();
  });
});
