import { test, expect } from "@playwright/test";

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
 */
test.describe("Map page", () => {
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

    // The stack's built-in catalog carries the demo products; an empty
    // catalog would make this assertion, not the page, the thing that failed.
    const options = page.locator('[data-testid^="map-add-footprints-"]');
    await expect(options.first()).toBeVisible();

    await options.first().click();

    const row = page.getByTestId("map-layer-row");
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("map-layer-visible")).toBeVisible();
    await expect(row.getByTestId("map-layer-remove")).toBeVisible();
    // No hover has happened, so no tooltip — and no error surface anywhere
    // on the page (spec §4.7).
    await expect(page.getByTestId("map-tooltip")).toHaveCount(0);

    // Adding the same product again is offered as "Added" and disabled.
    await page.getByTestId("map-add-layer").click();
    await expect(options.first()).toBeDisabled();
  });

  test("removes the layer it added", async ({ page }) => {
    await page.goto("/map");
    await page.getByTestId("map-add-layer").click();
    await page.locator('[data-testid^="map-add-footprints-"]').first().click();
    await expect(page.getByTestId("map-layer-row")).toHaveCount(1);

    await page.getByTestId("map-layer-remove").click();

    await expect(page.getByTestId("map-layer-row")).toHaveCount(0);
    await expect(page.getByText("No layers yet")).toBeVisible();
  });
});
