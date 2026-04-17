import { test, expect } from "@playwright/test";

test.describe("Proxy feature", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/catalogs");
    await page.evaluate(() => localStorage.clear());
    await page.reload();
  });

  test("can add catalog with proxy enabled and see Proxied badge", async ({
    page,
  }) => {
    await page
      .getByRole("button", { name: "Add Your First Catalog" })
      .click();

    await page.getByLabel("Name").fill("Proxied API");
    await page.getByLabel("URL").fill("http://localhost:8082");
    await page.getByLabel("Proxy through server").click();
    await page.getByRole("button", { name: "Add" }).click();

    const main = page.getByRole("main");
    await expect(main.getByText("Proxied API")).toBeVisible();
    await expect(main.getByText("Proxied")).toBeVisible();
    await expect(main.getByText("Active")).toBeVisible();
  });

  test("catalog without proxy does not show Proxied badge", async ({
    page,
  }) => {
    await page
      .getByRole("button", { name: "Add Your First Catalog" })
      .click();

    await page.getByLabel("Name").fill("Direct API");
    await page.getByLabel("URL").fill("http://localhost:8082");
    await page.getByRole("button", { name: "Add" }).click();

    const main = page.getByRole("main");
    await expect(main.getByText("Direct API")).toBeVisible();
    await expect(main.getByText("Proxied")).not.toBeVisible();
  });

  test("test connection works through proxy when backend is running", async ({
    page,
  }) => {
    await page
      .getByRole("button", { name: "Add Your First Catalog" })
      .click();

    await page.getByLabel("Name").fill("Proxied Local");
    await page.getByLabel("URL").fill("http://localhost:8082");
    await page.getByLabel("Proxy through server").click();
    await page.getByRole("button", { name: "Add" }).click();

    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Test Connection" }).click();

    await expect(
      page.getByText(/Connected to Proxied Local/),
    ).toBeVisible({ timeout: 10000 });
  });
});
