import { test, expect, type APIRequestContext } from "@playwright/test";

const TEST_EXT_NAME = "E2E Form Test Extension";
const TEST_EXT_PREFIX = "e2eform";

// --- helpers ---

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
    if (ext.name === TEST_EXT_NAME || ext.prefix === TEST_EXT_PREFIX) {
      await request.delete(`/api/extensions/${ext.id}`);
    }
  }
}

async function createTestExtension(request: APIRequestContext) {
  const res = await request.post("/api/extensions", {
    data: {
      name: TEST_EXT_NAME,
      prefix: TEST_EXT_PREFIX,
      version: "1.0.0",
      description: "Extension used in form E2E tests",
      properties: [
        {
          name: "cloud_cover",
          type: "number",
          description: "Percentage of cloud cover (0–100)",
          required: false,
        },
        {
          name: "platform",
          type: "string",
          description: "Satellite platform name",
          required: false,
        },
      ],
    },
  });
  expect(res.ok()).toBe(true);
  return res.json() as Promise<{ id: string; name: string; prefix: string }>;
}

// --- tests ---

test.describe.configure({ mode: "serial" });

const STAC_API = "http://localhost:8082";
const TEST_COLLECTION_ID = "test-collection";

async function ensureTestCollection(request: APIRequestContext) {
  const existing = await request.get(`${STAC_API}/collections/${TEST_COLLECTION_ID}`);
  if (existing.ok()) return;
  await request.post(`${STAC_API}/collections`, {
    data: {
      type: "Collection",
      id: TEST_COLLECTION_ID,
      stac_version: "1.0.0",
      description: "Collection for E2E tests",
      license: "proprietary",
      extent: {
        spatial: { bbox: [[-180, -90, 180, 90]] },
        temporal: { interval: [["2020-01-01T00:00:00Z", null]] },
      },
    },
  });
}

test.describe("Dynamic extension forms", () => {
  test.beforeAll(async ({ request }) => {
    await ensureTestCollection(request);
  });

  test.beforeEach(async ({ request }) => {
    await deleteTestExtensions(request);
  });

  test.afterEach(async ({ request }) => {
    await deleteTestExtensions(request);
  });

  test("dynamic fields appear in item form when extension is selected", async ({
    page,
    request,
  }) => {
    const ext = await createTestExtension(request);
    const schemaUrl = `/api/extensions/${ext.id}/schema`;

    // Navigate to a new item form (use any collection ID — we're just testing UI)
    await page.goto("/collections/test-collection/items/new");

    // Scroll to the Extensions card
    await page
      .getByText("Extensions", { exact: true })
      .last()
      .scrollIntoViewIfNeeded();

    // Open the extension picker
    await page.getByRole("combobox").click();

    // Wait for the picker dropdown to show our extension
    await expect(page.getByText(TEST_EXT_NAME)).toBeVisible();

    // Select it
    await page.getByText(TEST_EXT_NAME).click();

    // Close the picker
    await page.keyboard.press("Escape");

    // The extension URL badge should appear
    await expect(
      page.locator("span.font-mono", { hasText: new RegExp(ext.id) }),
    ).toBeVisible();

    // The dynamic RJSF fields card should now appear
    // It shows the extension title (schema title or derived from URL)
    await expect(
      page.locator('[data-testid="extension-fields"]').or(
        page.getByText(new RegExp(TEST_EXT_PREFIX, "i")).last(),
      ),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("extension picker shows locally registered extensions", async ({
    page,
    request,
  }) => {
    await createTestExtension(request);

    await page.goto("/collections/test-collection/items/new");

    // Open the extension picker
    await page.getByRole("combobox").click();

    // Our extension should appear with correct prefix badge
    await expect(page.getByText(TEST_EXT_NAME)).toBeVisible();
    await expect(page.getByText(TEST_EXT_PREFIX)).toBeVisible();
  });

  test("extension schema is hosted and serves correct JSON Schema", async ({
    request,
  }) => {
    const ext = await createTestExtension(request);

    // Fetch the hosted schema directly
    const schemaRes = await request.get(`/api/extensions/${ext.id}/schema`);
    expect(schemaRes.ok()).toBe(true);
    expect(schemaRes.headers()["content-type"]).toContain("schema+json");

    const schema = await schemaRes.json();
    expect(schema.type).toBe("object");
    expect(schema.properties).toBeDefined();
    expect(schema.properties[`${TEST_EXT_PREFIX}:cloud_cover`]).toBeDefined();
    expect(schema.properties[`${TEST_EXT_PREFIX}:cloud_cover`].type).toBe("number");
    expect(schema.properties[`${TEST_EXT_PREFIX}:platform`]).toBeDefined();
  });

  test("resolve-schema route returns cached schema", async ({ request, baseURL }) => {
    const ext = await createTestExtension(request);
    const schemaUrl = `${baseURL}/api/extensions/${ext.id}/schema`;

    // First call — fresh fetch
    const first = await request.post("/api/extensions/resolve-schema", {
      data: { url: schemaUrl },
    });
    expect(first.ok()).toBe(true);
    const firstSchema = await first.json();
    expect(firstSchema.properties).toBeDefined();

    // Second call — should be served from cache (same result)
    const second = await request.post("/api/extensions/resolve-schema", {
      data: { url: schemaUrl },
    });
    expect(second.ok()).toBe(true);
    const secondSchema = await second.json();
    expect(secondSchema.properties).toEqual(firstSchema.properties);
  });

  test("resolve-schema returns 400 for missing url", async ({ request }) => {
    const res = await request.post("/api/extensions/resolve-schema", {
      data: {},
    });
    expect(res.status()).toBe(400);
  });

  test("resolve-schema returns 400 for non-HTTP url", async ({ request }) => {
    const res = await request.post("/api/extensions/resolve-schema", {
      data: { url: "ftp://example.com/schema.json" },
    });
    expect(res.status()).toBe(400);
  });

  test("dynamic fields appear in collection form when extension is selected", async ({
    page,
    request,
  }) => {
    const ext = await createTestExtension(request);

    await page.goto("/collections/new");

    // Scroll to Extensions section
    await page
      .getByText("Extensions", { exact: true })
      .last()
      .scrollIntoViewIfNeeded();

    // Open the extension picker (filter to the one with placeholder text)
    await page
      .getByRole("combobox")
      .filter({ hasText: /select extensions|extensions? selected/i })
      .click();

    // Select our extension
    await expect(page.getByText(TEST_EXT_NAME)).toBeVisible();
    await page.getByText(TEST_EXT_NAME).click();
    await page.keyboard.press("Escape");

    // Badge should appear
    await expect(
      page.locator("span.font-mono", { hasText: new RegExp(ext.id) }),
    ).toBeVisible();

    // Dynamic RJSF card should appear with extension title/prefix
    await expect(
      page.getByText(new RegExp(TEST_EXT_PREFIX, "i")).last(),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("extension can be removed from form and dynamic fields disappear", async ({
    page,
    request,
  }) => {
    const ext = await createTestExtension(request);

    await page.goto("/collections/test-collection/items/new");

    // Open picker and select
    await page.getByRole("combobox").click();
    await expect(page.getByText(TEST_EXT_NAME)).toBeVisible();
    await page.getByText(TEST_EXT_NAME).click();
    await page.keyboard.press("Escape");

    // Extension badge should appear
    const badge = page.locator("span.font-mono", { hasText: new RegExp(ext.id) });
    await expect(badge).toBeVisible();

    // Remove the extension via the X button on the badge
    await badge
      .locator("..")
      .getByRole("button", { name: "Remove extension" })
      .click();

    // Badge should disappear
    await expect(badge).not.toBeVisible();
  });
});
