import { test, expect } from "@playwright/test";

test.describe("Endpoints page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/endpoints");
    await page.evaluate(() => localStorage.clear());
    await page.reload();
  });

  test("shows empty state when no endpoints configured", async ({ page }) => {
    await expect(page.getByText("No endpoints configured")).toBeVisible();
    await expect(page.getByRole("button", { name: "Add Your First Endpoint" })).toBeVisible();
  });

  test("can add an endpoint and it appears in the list", async ({ page }) => {
    await page.getByRole("button", { name: "Add Your First Endpoint" }).click();

    await page.getByLabel("Name").fill("Local STAC API");
    await page.getByLabel("URL").fill("http://localhost:8082");
    await page.getByRole("button", { name: "Add" }).click();

    const main = page.getByRole("main");
    await expect(main.getByText("Local STAC API")).toBeVisible();
    await expect(main.getByText("http://localhost:8082")).toBeVisible();
    await expect(main.getByText("Active")).toBeVisible();
  });

  test("new endpoint becomes active automatically", async ({ page }) => {
    await page.getByRole("button", { name: "Add Your First Endpoint" }).click();

    await page.getByLabel("Name").fill("Test API");
    await page.getByLabel("URL").fill("http://localhost:9999");
    await page.getByRole("button", { name: "Add" }).click();

    const main = page.getByRole("main");
    await expect(main.getByText("Active")).toBeVisible();
  });

  test("can delete an endpoint", async ({ page }) => {
    await page.getByRole("button", { name: "Add Your First Endpoint" }).click();
    await page.getByLabel("Name").fill("To Delete");
    await page.getByLabel("URL").fill("http://localhost:1234");
    await page.getByRole("button", { name: "Add" }).click();

    const main = page.getByRole("main");
    await expect(main.getByText("To Delete")).toBeVisible();

    const trashButton = main.locator("button").filter({ has: page.locator("[data-lucide='trash-2'], .lucide-trash2, .lucide-trash-2") });
    await trashButton.click();

    await page.getByRole("button", { name: "Delete" }).last().click();

    await expect(page.getByText("No endpoints configured")).toBeVisible();
  });
});
