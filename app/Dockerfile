# syntax=docker/dockerfile:1
# STAC Higher app — Astro SSR (@astrojs/node, standalone) built from the
# npm-workspaces root (single lockfile + packages/shared are required).
# Build context is the REPO ROOT, plus the `fixtures` named context that
# carries the built-in extractor registry (X-4 — the repo-root .dockerignore
# excludes tests/, and the registry is imported at BUILD time by
# app/src/lib/extractors/registry.ts, so it must be at the path that import
# resolves to before `npm run build`):
#   docker build -f app/Dockerfile --build-context fixtures=tests/contract-fixtures .

FROM node:22-bookworm-slim AS build
WORKDIR /srv
COPY package.json package-lock.json ./
COPY app/package.json app/
COPY packages/shared/package.json packages/shared/
RUN npm ci
COPY app app
COPY packages/shared packages/shared
COPY --from=fixtures builtin-extractors.json tests/contract-fixtures/builtin-extractors.json
# The SSR bundle externalizes runtime deps (pg, @aws-sdk, react, zod, ...), so
# prune to production node_modules and ship them alongside dist/.
RUN npm run build --workspace app \
    && npm prune --omit=dev \
    && mkdir -p app/node_modules

FROM node:22-bookworm-slim
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=4321
WORKDIR /srv
COPY --from=build /srv/package.json ./package.json
COPY --from=build /srv/node_modules ./node_modules
# workspace symlink target for @stac-higher/shared (bundled at build time, but
# keeps node_modules resolution intact)
COPY --from=build /srv/packages/shared ./packages/shared
COPY --from=build /srv/app/package.json ./app/package.json
COPY --from=build /srv/app/node_modules ./app/node_modules
COPY --from=build /srv/app/dist ./app/dist
USER node
EXPOSE 4321
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["node", "-e", "fetch(`http://127.0.0.1:${process.env.PORT ?? 4321}/`).then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["node", "app/dist/server/entry.mjs"]
