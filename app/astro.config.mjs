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

  vite: {
    plugins: [tailwindcss()],
    resolve: {
      alias: {
        // app-local alias
        '@': path.resolve(__dirname, 'src'),
        // shared package's internal alias — resolves @shared/* to packages/shared/src/*
        '@shared': path.resolve(__dirname, '../packages/shared/src'),
      },
    },
  },
});
