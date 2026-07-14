import { test, expect, type APIRequestContext } from "@playwright/test";

const TEST_EXTENSION_NAME = "E2E Test Extension";
const TEST_EXTENSION_PREFIX = "e2etest";

async function deleteTestExtensions(request: APIRequestContext) {
  const res = await request.get("/api/extensions");
  if (!res.ok()) return;
  const data = await res.json();
  const exts = (data.extensions ?? []) as Array<{
    id: string;
    name: string;
    prefix: string;
  }>;
  for (const ext of exts) {
    if (
      ext.name === TEST_EXTENSION_NAME ||
      ext.prefix === TEST_EXTENSION_PREFIX
    ) {
      await request.delete(`/api/extensions/${ext.id}`);
    }
  }
}

test.describe.configure({ mode: "serial" });

test.describe("Extensions", () => {
  test.beforeEach(async ({ request }) => {
    await deleteTestExtensions(request);
  });

  test.afterEach(async ({ request }) => {
    await deleteTestExtensions(request);
  });

  test("create extension and verify it appears in list", async ({ page }) => {
    await page.goto("/extensions");
    await page.getByRole("link", { name: "Create Extension" }).click();

    await expect(page).toHaveURL("/extensions/new");

    await page.getByLabel("Extension Name").fill(TEST_EXTENSION_NAME);
    await page.getByLabel("Prefix").fill(TEST_EXTENSION_PREFIX);
    await page.getByLabel("Version").fill("1.0.0");
    await page.getByLabel("Description").fill("Extension created by E2E test");

    // The first property row is pre-filled — fill in the property name
    await page
      .locator('input[placeholder="cloud_cover"]')
      .first()
      .fill("test_value");

    await page.getByRole("button", { name: "Create Extension" }).click();

    // Should redirect to detail page
    await expect(page).toHaveURL(/\/extensions\//);
    await expect(
      page.getByRole("heading", { name: TEST_EXTENSION_NAME }),
    ).toBeVisible();
  });

  test("extension appears in list after creation", async ({ page, request }) => {
    // Create via API
    await request.post("/api/extensions", {
      data: {
        name: TEST_EXTENSION_NAME,
        prefix: TEST_EXTENSION_PREFIX,
        version: "1.0.0",
        description: "Test extension",
        properties: [
          {
            name: "test_prop",
            type: "string",
            description: "A test property",
            required: false,
          },
        ],
      },
    });

    await page.goto("/extensions");

    const card = page.getByRole("link", {
      name: new RegExp(TEST_EXTENSION_NAME),
    });
    await expect(card.first()).toBeVisible();
    await expect(card.first()).toContainText(TEST_EXTENSION_PREFIX);
  });

  test("view extension detail page", async ({ page, request }) => {
    const createRes = await request.post("/api/extensions", {
      data: {
        name: TEST_EXTENSION_NAME,
        prefix: TEST_EXTENSION_PREFIX,
        version: "1.0.0",
        description: "Test extension for detail view",
        properties: [
          {
            name: "cloud_cover",
            type: "number",
            description: "Cloud cover percentage",
            required: true,
          },
        ],
      },
    });
    const ext = await createRes.json();

    await page.goto(`/extensions/${ext.id}`);

    await expect(
      page.getByRole("heading", { name: TEST_EXTENSION_NAME }),
    ).toBeVisible();
    await expect(
      page.getByText("Test extension for detail view"),
    ).toBeVisible();

    // Properties tab should show the property
    await expect(
      page.getByRole("cell", { name: new RegExp(`${TEST_EXTENSION_PREFIX}:cloud_cover`) }),
    ).toBeVisible();
  });

  test("edit extension", async ({ page, request }) => {
    const createRes = await request.post("/api/extensions", {
      data: {
        name: TEST_EXTENSION_NAME,
        prefix: TEST_EXTENSION_PREFIX,
        version: "1.0.0",
        description: "Original description",
        properties: [
          {
            name: "test_prop",
            type: "string",
            description: "Original property",
            required: false,
          },
        ],
      },
    });
    const ext = await createRes.json();

    await page.goto(`/extensions/${ext.id}`);
    await page.getByRole("link", { name: "Edit" }).click();

    await expect(page).toHaveURL(`/extensions/${ext.id}/edit`);

    // Update description
    const descriptionField = page.getByLabel("Description");
    await descriptionField.clear();
    await descriptionField.fill("Updated description");

    await page.getByRole("button", { name: "Update Extension" }).click();

    // Should redirect back to detail
    await expect(page).toHaveURL(`/extensions/${ext.id}`);
    await expect(page.getByText("Updated description")).toBeVisible();
  });

  test("delete extension", async ({ page, request }) => {
    const createRes = await request.post("/api/extensions", {
      data: {
        name: TEST_EXTENSION_NAME,
        prefix: TEST_EXTENSION_PREFIX,
        version: "1.0.0",
        description: "Extension to delete",
        properties: [
          {
            name: "test_prop",
            type: "string",
            description: "Test",
            required: false,
          },
        ],
      },
    });
    const ext = await createRes.json();

    await page.goto(`/extensions/${ext.id}`);

    // Click delete button
    await page.getByRole("button", { name: "Delete" }).click();

    // Confirm in dialog
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Delete" })
      .click();

    // Should redirect to list
    await expect(page).toHaveURL("/extensions");
    // The deleted extension's detail page link should be gone
    await expect(
      page.locator(`a[href="/extensions/${ext.id}"]`),
    ).toHaveCount(0);
  });
});
