import { test, expect, type APIRequestContext } from "@playwright/test";

/**
 * Data-flow tab e2e — the Slice D delivery config flow. Needs the Docker
 * backend (:8081/:8082 pgstac + proxy in pass-through) like extensions.spec.
 *
 * Fixtures are created through the app's own API surface (dev-bypass
 * operator): a collection via the ADR 0008 BFF and an s3 connection, both
 * cleaned up before and after. The delivery association itself is exercised
 * through the UI — create, status empty state, disable/enable, remove.
 */
const COLLECTION_ID = "e2e-data-flow";
const CONNECTION_NAME = "e2e-df-dest";

const collectionBody = {
  id: COLLECTION_ID,
  type: "Collection",
  stac_version: "1.0.0",
  description: "Slice D e2e fixture",
  license: "proprietary",
  extent: {
    spatial: { bbox: [[-180, -90, 180, 90]] },
    temporal: { interval: [[null, null]] },
  },
  links: [],
};

async function deleteFixtures(request: APIRequestContext) {
  await request.delete(`/api/catalog/collections/${COLLECTION_ID}`);
  const res = await request.get("/api/connections");
  if (res.ok()) {
    const body = (await res.json()) as {
      connections?: { id: string; name: string }[];
    };
    for (const c of body.connections ?? []) {
      if (c.name === CONNECTION_NAME) {
        await request.delete(`/api/connections/${c.id}`);
      }
    }
  }
}

test.describe("Data flow tab — delivery half", () => {
  test.beforeAll(async ({ request }) => {
    await deleteFixtures(request);

    const created = await request.post("/api/catalog/collections", {
      data: collectionBody,
    });
    expect(created.ok()).toBeTruthy();

    const conn = await request.post("/api/connections", {
      data: {
        name: CONNECTION_NAME,
        protocol: "s3",
        group_id: "earth-observation",
        config: { bucket: "e2e-dest" },
        credentials: {
          access_key_id: "e2e-key",
          secret_access_key: "e2e-secret",
        },
      },
    });
    expect(conn.ok()).toBeTruthy();
  });

  test.afterAll(async ({ request }) => {
    await deleteFixtures(request);
  });

  test("creates, disables, and removes a delivery destination", async ({
    page,
  }) => {
    await page.goto(`/collections/${COLLECTION_ID}`);
    await page.getByRole("tab", { name: "Data flow" }).click();

    // Both halves render; delivery starts empty.
    await expect(page.getByText("Ingest sources", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Delivery destinations", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("No delivery destinations yet")).toBeVisible();

    // Client-side validation: no connection picked.
    await page.getByRole("button", { name: "Add destination" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Add destination" })
      .click();
    await expect(page.getByText("Pick a connection to deliver to")).toBeVisible();

    // Fill the §5.1 essentials and submit.
    await page.getByRole("combobox", { name: "Connection" }).click();
    await page
      .getByRole("option", { name: `${CONNECTION_NAME} (s3)` })
      .click();
    await page
      .getByLabel("Path template")
      .fill("{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}");
    await page.getByLabel("Item filter (CQL2)").fill("eo:cloud_cover < 20");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Add destination" })
      .click();

    // Card appears with the config summary and empty delivery status.
    await expect(page.getByText(CONNECTION_NAME, { exact: true })).toBeVisible();
    await expect(
      page.getByText("{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}"),
    ).toBeVisible();
    await expect(page.getByText(/No deliveries yet/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Backfill" })).toBeEnabled();

    // Edit round-trips the stored config.
    await page.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByLabel("Path template")).toHaveValue(
      "{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}",
    );
    await expect(page.getByLabel("Item filter (CQL2)")).toHaveValue(
      "eo:cloud_cover < 20",
    );
    await page.getByRole("button", { name: "Cancel" }).click();

    // Disable → backfill becomes unavailable.
    await page.getByRole("switch", { name: "Enabled" }).click();
    await expect(page.getByText("Disabled", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Backfill" })).toBeDisabled();

    // Remove via the shared impact dialog.
    await page.getByRole("button", { name: "Remove" }).click();
    await expect(
      page.getByText("Remove delivery destination", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/History retained/)).toBeVisible();
    await page
      .getByRole("button", { name: "Remove delivery destination" })
      .click();
    await expect(page.getByText("No delivery destinations yet")).toBeVisible();
  });
});
