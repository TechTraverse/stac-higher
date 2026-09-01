import { test, expect } from "@playwright/test";

test.describe("Catalogs page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/catalogs");
    await page.evaluate(() => localStorage.clear());
    await page.reload();
  });

  test("seeds the built-in catalog as the platform catalog and undeletable", async ({ page }) => {
    const builtIn = page.getByTestId("catalog-card-built-in");
    await expect(builtIn.getByText("Built-in Catalog")).toBeVisible();
    await expect(builtIn.getByText("Platform", { exact: true })).toBeVisible();
    await expect(builtIn.getByRole("link", { name: "Open products" })).toBeVisible();
    await expect(builtIn.getByRole("button", { name: "Edit catalog" })).toHaveCount(0);
    await expect(builtIn.getByRole("button", { name: "Delete catalog" })).toHaveCount(0);
  });

  test("can add a catalog and it appears in the list", async ({ page }) => {
    await page.getByRole("button", { name: "Add Catalog" }).click();

    await page.getByLabel("Name").fill("Local STAC API");
    await page.getByLabel("URL").fill("http://localhost:8082");
    await page.getByRole("button", { name: "Add" }).click();

    const card = page
      .locator('[data-testid^="catalog-card-"]')
      .filter({ hasText: "Local STAC API" });
    await expect(card).toBeVisible();
    await expect(card.getByText("http://localhost:8082")).toBeVisible();
  });

  // UI-10: there is no global "active catalog". The built-in catalog opens the
  // product surface; every other catalog opens its own read-only browser.
  test("an added catalog gets a read-only Browse entry, not a product surface", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Add Catalog" }).click();
    await page.getByLabel("Name").fill("Test API");
    await page.getByLabel("URL").fill("http://localhost:9999");
    await page.getByRole("button", { name: "Add" }).click();

    const builtIn = page.getByTestId("catalog-card-built-in");
    const added = page
      .locator('[data-testid^="catalog-card-"]')
      .filter({ hasText: "Test API" });

    await expect(builtIn.getByRole("link", { name: "Open products" })).toBeVisible();
    await expect(added.getByText("Platform", { exact: true })).toHaveCount(0);

    await added.getByRole("link", { name: "Browse" }).click();
    // UI-15: the Browse link carries ?src= so the link is shareable.
    await expect(page).toHaveURL(
      /\/catalogs\/[^/]+\/collections\?src=http%3A%2F%2Flocalhost%3A9999$/,
    );
    await expect(page.getByRole("heading", { name: "Collections", level: 1 })).toBeVisible();
    await expect(page.getByText("Read-only")).toBeVisible();
    // The browser mints no writes.
    await expect(page.getByRole("link", { name: /Create/ })).toHaveCount(0);
  });

  test("can delete a catalog", async ({ page }) => {
    await page.getByRole("button", { name: "Add Catalog" }).click();
    await page.getByLabel("Name").fill("To Delete");
    await page.getByLabel("URL").fill("http://localhost:1234");
    await page.getByRole("button", { name: "Add" }).click();

    const added = page
      .locator('[data-testid^="catalog-card-"]')
      .filter({ hasText: "To Delete" });
    await expect(added).toBeVisible();

    await added.getByRole("button", { name: "Delete catalog" }).click();
    await page.getByRole("button", { name: "Delete" }).last().click();

    await expect(added).not.toBeVisible();
    const builtIn = page.getByTestId("catalog-card-built-in");
    await expect(builtIn.getByText("Platform", { exact: true })).toBeVisible();
  });
});
