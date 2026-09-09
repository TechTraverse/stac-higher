import { test, expect } from "@playwright/test";

/**
 * UI-surface e2e for /connections. Exercises navigation, the per-protocol
 * wizard, and client-side validation without performing any writes (no
 * credentials backend or pipeline required). Full test-connection polling is
 * covered by unit tests, since it needs the pipeline drain job.
 */
test.describe("Connections page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/connections");
  });

  test("is reachable from the sidebar nav", async ({ page }) => {
    await page.goto("/catalogs");
    await page.getByRole("link", { name: "Connections" }).click();
    await expect(
      page.getByRole("heading", { name: "Connections", level: 1 }),
    ).toBeVisible();
  });

  test("opens the wizard and renders per-protocol fields", async ({ page }) => {
    await page.getByRole("button", { name: "Add Connection" }).first().click();

    // Default protocol is s3. Matched by ROLE, not by label alone: G-1 added
    // an "Anonymous (public bucket)" switch to this form, and getByLabel does
    // substring matching, so getByLabel("Bucket") resolves to both the input
    // and that switch — a strict-mode violation, not a missing field.
    await expect(page.getByRole("textbox", { name: "Bucket" })).toBeVisible();
    await expect(page.getByLabel("Access key ID")).toBeVisible();

    // Switch to sftp → SSH-family config + credential fields appear.
    // The protocol Select became a type-card radiogroup in UI-6; each card's
    // accessible name is still the raw protocol string.
    await page.getByRole("radio", { name: "sftp", exact: true }).click();
    await expect(page.getByLabel("Host")).toBeVisible();
    await expect(page.getByLabel("Private key")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Bucket" })).toHaveCount(0);
  });

  test("blocks submit with validation errors on an empty form", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Add Connection" }).first().click();
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText("Name is required")).toBeVisible();
  });
});
