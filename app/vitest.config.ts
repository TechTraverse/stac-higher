/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    exclude: ["node_modules", "dist", "e2e"],
    projects: [
      // Everything except the Astro container tests, with the settings above.
      {
        extends: true,
        test: { name: "unit", exclude: ["src/**/*.container.test.ts"] },
      },
      // `*.container.test.ts` render real `.astro` pages through Astro's
      // Container API, which needs Astro's own Vite plugins (#194).
      "./vitest.container.config.ts",
    ],
  },
  resolve: {
    alias: {
      "@/": new URL("./src/", import.meta.url).pathname,
      "@shared/": new URL("../packages/shared/src/", import.meta.url).pathname,
    },
  },
});
