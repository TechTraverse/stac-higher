// @ts-check
import { defineConfig } from 'astro/config';
import path from 'path';
import { fileURLToPath } from 'url';

import react from '@astrojs/react';
import node from '@astrojs/node';
import tailwindcss from '@tailwindcss/vite';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// https://astro.build/config
export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  integrations: [react()],

  // The dev toolbar is a fixed, bottom-centre overlay, and Playwright's clicks
  // land on it instead of whatever sits underneath — `extensions.spec.ts`'s
  // "Update Extension" button retried 57 times against
  // `<astro-dev-toolbar> intercepts pointer events`. Astro exposes no env
  // switch, so the Playwright webServer sets E2E=1 and we disable it there
  // only; ordinary `npm run dev` keeps the toolbar.
  devToolbar: { enabled: process.env.E2E !== '1' },

  vite: {
    plugins: [/** @type {import('vite').PluginOption} */ (tailwindcss())],
    resolve: {
      alias: {
        // app-local alias
        '@': path.resolve(__dirname, 'src'),
        // shared package's internal alias — resolves @shared/* to packages/shared/src/*
        '@shared': path.resolve(__dirname, '../packages/shared/src'),
      },
    },
    optimizeDeps: {
      // Every page mounts its React island with `client:only="react"`, so Vite's
      // dep scanner never traverses into island code and starts the dev server
      // with only astro/react pre-bundled. These deps are then discovered one at
      // a time at request time; each discovery re-runs the optimizer and bumps
      // `browserHash`, while already-cached module transforms keep emitting the
      // previous generation's `/node_modules/.vite/deps/x.js?v=<old>` URLs. Those
      // URLs answer `504 Outdated Optimize Dep` with an empty content-type, which
      // the browser reports as NS_ERROR_CORRUPTED_CONTENT / "disallowed MIME type"
      // and the island fails to hydrate.
      //
      // Listing the client-side deps here pre-bundles them in one pass at startup,
      // so the optimizer set is complete before the first request. Keep this in
      // sync when a client component pulls in a new third-party package.
      // Type-only packages (e.g. `geojson`) must NOT be listed — they have no
      // runtime entry to pre-bundle.
      include: [
        'react',
        'react-dom',
        'react-dom/client',
        'react-hook-form',
        '@hookform/resolvers/zod',
        'zod',
        '@tanstack/react-query',
        'nanostores',
        '@nanostores/react',
        '@nanostores/persistent',
        '@rjsf/core',
        '@rjsf/utils',
        '@rjsf/validator-ajv8',
        'radix-ui',
        'lucide-react',
        'sonner',
        'next-themes',
        '@codemirror/state',
        '@codemirror/view',
        '@codemirror/commands',
        '@codemirror/language',
        '@codemirror/lang-python',
        '@codemirror/lang-json',
        '@lezer/highlight',
        'maplibre-gl',
        'react-map-gl/maplibre',
        'class-variance-authority',
        'tailwind-merge',
        'clsx',
      ],
    },
  },
});
