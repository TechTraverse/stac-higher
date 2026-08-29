import { test, expect, type APIRequestContext } from "@playwright/test";

/**
 * Collection Settings tab e2e (M2-E, spec §7). Needs the Docker backend like
 * data-flow.spec.ts: a fixture collection is created via the ADR 0008 BFF
 * (dev-bypass operator), settings are edited through the UI, and persistence
 * is asserted across a reload.
 */
const COLLECTION_ID = "e2e-settings";

const collectionBody = {
  id: COLLECTION_ID,
  type: "Collection",
  stac_version: "1.0.0",
  description: "M2-E settings e2e fixture",
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

test.describe("Collection Settings tab", () => {
  test.beforeAll(async ({ request }) => {
    await deleteFixtures(request);
    const created = await request.post("/api/catalog/collections", {
      data: collectionBody,
    });
    expect(created.ok()).toBeTruthy();
    // Reset any settings row left by an earlier run (PUT is idempotent).
    // Asserted: a silent 400 here (e.g. the payload missing a newly required
    // field) leaks prior-run state into every assertion below.
    const reset = await request.put(
      `/api/collections/${COLLECTION_ID}/settings`,
      {
        data: {
          group_id: null,
          externally_writable: false,
          retention_days: null,
          gc_grace_days: 30,
          archived: false,
          serving_enabled: false,
        },
      },
    );
    expect(reset.ok()).toBeTruthy();
  });

  test.afterAll(async ({ request }) => {
    await deleteFixtures(request);
  });

  test("edits and persists settings through the UI", async ({ page }) => {
    await page.goto(`/collections/${COLLECTION_ID}`);
    await page.getByRole("tab", { name: "Settings" }).click();

    // Defaults render (sparse table → keep forever / 30 / unarchived).
    const retention = page.getByTestId("settings-retention");
    await expect(retention).toHaveValue("");
    await expect(page.getByTestId("settings-grace")).toHaveValue("30");

    await retention.fill("14");
    await page.getByTestId("settings-grace").fill("7");
    await page.getByTestId("settings-archived").click();
    await page.getByTestId("settings-save").click();

    // M2-F: archiving/tightening retention first shows the counted dry-run
    // (warn-and-proceed, ADR 0009 §6 / 0011) — confirm to apply.
    await expect(page.getByTestId("settings-impact")).toBeVisible();
    await page.getByTestId("settings-confirm").click();
    await expect(page.getByText("Collection settings saved")).toBeVisible();

    // Persisted: reload, reopen the tab, values survive.
    await page.reload();
    await page.getByRole("tab", { name: "Settings" }).click();
    await expect(page.getByTestId("settings-retention")).toHaveValue("14");
    await expect(page.getByTestId("settings-grace")).toHaveValue("7");
    // Radix switch reflects persisted state via aria-checked (plain
    // getByText("archived") is ambiguous: badge + label + description).
    await expect(page.getByTestId("settings-archived")).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("serving toggle reveals links and persists", async ({ page }) => {
    await page.goto(`/collections/${COLLECTION_ID}`);
    await page.getByRole("tab", { name: "Settings" }).click();

    // Off by default; links hidden.
    const toggle = page.getByTestId("settings-serving");
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(page.getByTestId("settings-serving-links")).toHaveCount(0);

    // On → the titiler/tipg links appear; save persists across reload.
    await toggle.click();
    await expect(page.getByTestId("settings-serving-links")).toBeVisible();
    await page.getByTestId("settings-save").click();
    await expect(page.getByText("Collection settings saved")).toBeVisible();
    await page.reload();
    await page.getByRole("tab", { name: "Settings" }).click();
    await expect(page.getByTestId("settings-serving")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await expect(page.getByTestId("settings-serving-links")).toBeVisible();
  });

  test("rejects an invalid retention value client-side", async ({ page }) => {
    await page.goto(`/collections/${COLLECTION_ID}`);
    await page.getByRole("tab", { name: "Settings" }).click();
    await page.getByTestId("settings-retention").fill("0");
    await expect(page.getByText(/whole number of days/)).toBeVisible();
    await expect(page.getByTestId("settings-save")).toBeDisabled();
  });
});
