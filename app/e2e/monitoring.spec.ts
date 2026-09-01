import { test, expect } from "@playwright/test";

/**
 * UI-surface e2e for /monitoring + the header alert bell (M2-D, spec §7).
 * Needs the app DB (alerts/flows/channels routes hit stac_higher.*), which
 * the Docker stack provides — same precondition as data-flow.spec.ts.
 * Read-mostly: the one write is the caller's own read watermark, which the
 * page advances on open.
 */
test.describe("Monitoring page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/monitoring");
  });

  test("is reachable from the sidebar nav and shows the bell", async ({
    page,
  }) => {
    await page.goto("/catalogs");
    await expect(page.getByTestId("alert-bell")).toBeVisible();
    await page.getByRole("link", { name: "Monitoring" }).click();
    await expect(
      page.getByRole("heading", { name: "Monitoring", level: 1 }),
    ).toBeVisible();
  });

  test("renders the three cards with data or their empty states", async ({
    page,
  }) => {
    // Alerts: either rows or the empty state — never an error card.
    const alerts = page.getByTestId("alerts-card");
    await expect(alerts).toBeVisible();
    await expect(
      alerts.getByText(/No open alerts|firing|acknowledged/).first(),
    ).toBeVisible();

    const flows = page.getByTestId("flows-card");
    await expect(flows).toBeVisible();
    await expect(
      flows.getByText(/No data flows|ingest|deliver/).first(),
    ).toBeVisible();

    const channels = page.getByTestId("channels-card");
    await expect(channels).toBeVisible();
    await expect(
      channels.getByText(/No notification channels|webhook|in_app/).first(),
    ).toBeVisible();
  });

  test("switches the alert list to the resolved view", async ({ page }) => {
    await page.getByTestId("alerts-view-resolved").click();
    await expect(
      page
        .getByTestId("alerts-card")
        .getByText(/No resolved alerts|resolved/)
        .first(),
    ).toBeVisible();
  });

  test("manages a webhook channel end to end", async ({ page }) => {
    const url = `https://hooks.example.com/e2e-${Date.now()}`;

    await page.getByTestId("channel-add").click();
    await page.getByTestId("channel-url").fill(url);
    await page.getByTestId("channel-submit").click();

    const row = page.getByText(url, { exact: true });
    await expect(row).toBeVisible();

    // Clean up: remove the channel we just created (scoped to its row).
    await page
      .getByTestId("channels-card")
      .locator("div")
      .filter({ hasText: url })
      .getByRole("button", { name: "Remove channel" })
      .last()
      .click();
    await expect(page.getByText(url, { exact: true })).toHaveCount(0);
  });
});
