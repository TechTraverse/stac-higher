# OGC API serving (titiler-pgstac + tipg)

Pre-M5 hardening item (pulled forward from Phase 8's stretch, sequenced
2026-08-27; implemented 2026-08-29). Two off-the-shelf services join the
compose stack and give the platform an OGC-conformance story beyond the STAC
API itself:

| Service | Image | Port | Serves |
|---|---|---|---|
| `titiler` | `stac-higher-titiler:local` — derived from `ghcr.io/stac-utils/titiler-pgstac:1.7.2` (`infra/titiler/`) | :8084 | OGC API Tiles for **rasters**, per STAC collection/search, straight off pgstac |
| `tipg` | `ghcr.io/developmentseed/tipg:1.0.1` | :8085 | OGC API Features + Tiles for **vector tables** in the shared PostGIS database |

Version pins follow eoAPI's tested combination for pgstac 0.9.x.

## The design pass, in short

- **Toggle semantics — link-level only.** `collection_settings.serving_enabled`
  (migration 019, app-only column) controls whether the collection page's
  Settings tab **advertises** the serving endpoints. Nothing gates requests to
  the services themselves: until per-collection read visibility (I-1) lands,
  anything either service can see is effectively public. The UI copy says so,
  and the toggle exists now so the semantics (and audit trail — the Settings
  PUT is guard-audited like every other settings change) are already in place
  when I-1 makes it enforceable.
- **What the collection page links to.** The serving panel (visible when the
  toggle is on) links:
  - *Raster tiles* → the titiler-pgstac **collection** endpoints
    (`{PUBLIC_TITILER_URL}/collections/{collection_id}/info` as the entry
    point; tilejson/tile/viewer routes hang off the same prefix). These are
    true per-STAC-collection endpoints — titiler queries pgstac directly.
  - *Vector features/tiles* → the tipg **landing page**
    (`{PUBLIC_TIPG_URL}/`). tipg serves database tables and functions, not
    STAC collections, so this is a stack-wide surface, linked for the OGC
    Features/Tiles API story rather than per-collection data.
- **Canonical hrefs are mapped in the tile server.** Items keep their
  app-relative `/api/assets/{collection}/{item}/{filename}` hrefs (ADR 0005 —
  bytes are only reachable through the app); the derived image rewrites them
  to `s3://{PLATFORM_ASSET_BUCKET}/assets/{collection}/{item}/{filename}`
  inside both readers (`infra/titiler/stac_higher_titiler/hrefmap.py` — the
  `PgSTACReader` item endpoints and the `SimpleSTACReader` collection/search
  mosaics), so platform assets tile with no change to the catalog. The
  compose service carries `AWS_S3_ENDPOINT=minio:9000` credentials for the
  platform bucket. Reference-mode absolute URLs and explicit `s3://` hrefs
  pass through as before. This is the *local* answer to I-68 (GOES spec
  §7.1, G-4); the cloud deployment chooses between the same mapping (with
  the deployment's bucket) and presign integration.

## Env

| Var | Default | Meaning |
|---|---|---|
| `PUBLIC_TITILER_URL` | `http://localhost:8084` | Browser-facing titiler base the UI links to |
| `PUBLIC_TIPG_URL` | `http://localhost:8085` | Browser-facing tipg base the UI links to |
| `PLATFORM_ASSET_BUCKET` | `${STAGING_BUCKET:-stac-higher}` | (titiler) The platform bucket canonical hrefs map into — mirrors the pipeline's `STAGING_BUCKET`. Required; the derived image refuses to start without it |
| `ASSET_HREF_BASE` | `/api/assets` | (titiler) The href base the pipeline writes — mirrors the pipeline's `ASSET_HREF_BASE` |

## Trying it

```bash
docker compose build titiler          # the derived image (infra/titiler/)
docker compose up -d titiler tipg
curl http://localhost:8084/healthz
curl http://localhost:8085/healthz
# per-collection raster metadata (any pgstac collection id):
curl http://localhost:8084/collections/<collection-id>/info
# a tile from a canonical (/api/assets) asset — the href mapper at work:
curl -o tile.png "http://localhost:8084/collections/<coll>/items/<item>/tiles/WebMercatorQuad/0/0/0.png?assets=visual"
# OGC API landing pages / conformance:
curl http://localhost:8084/api.html   # titiler OpenAPI
curl http://localhost:8085/           # tipg landing (HTML/JSON)
```

Then toggle **OGC serving** on a collection's Settings tab (operator+) and the
links appear on the page.

## Bumping titiler-pgstac

The pin lives in three places that move together: `infra/titiler/Dockerfile`
(`FROM`), `infra/titiler/pyproject.toml` (the `titiler` test extra) and the
image table above. The href mapper wraps rio-tiler 7's `_get_asset_info`;
titiler-pgstac 3.x (rio-tiler 9) changed that method's return shape — run
`cd infra/titiler && uv run --extra dev --extra titiler pytest` against the
candidate version before moving the pin, and adjust `main.py` if the `url`
key moved. Details: `infra/titiler/README.md`.
