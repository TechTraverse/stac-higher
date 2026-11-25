/// <reference types="vitest/config" />
// Vitest project for tests that render `.astro` pages through Astro's
// Container API (`experimental_AstroContainer`). `getViteConfig` loads
// `astro.config.mjs`, so `.astro` imports, `astro:*` virtual modules and the
// `@/` alias resolve as they do in the app. Referenced from `vitest.config.ts`.
import { getViteConfig } from "astro/config";

export default getViteConfig({
  test: {
    name: "astro-container",
    environment: "node",
    include: ["src/**/*.container.test.ts"],
  },
});
