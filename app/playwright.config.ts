import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.E2E_PORT ?? "4321";
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: "html",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    extraHTTPHeaders: {
      Origin: BASE_URL,
    },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${PORT}`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      SAFE_FETCH_ALLOW_HOSTS: "localhost,127.0.0.1",
      SAFE_FETCH_LOG: "0",
      // Astro 7 auto-daemonizes `astro dev` when it detects an AI-agent
      // environment, which makes Playwright's webServer see an early exit.
      // Setting this disables the auto-detection so the server stays foreground.
      ASTRO_DEV_BACKGROUND: "0",
    },
  },
});
