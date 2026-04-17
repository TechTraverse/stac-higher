/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    exclude: ["node_modules", "dist", "e2e"],
  },
  resolve: {
    alias: {
      "@/": new URL("./src/", import.meta.url).pathname,
      "@shared/": new URL("../packages/shared/src/", import.meta.url).pathname,
    },
  },
});
