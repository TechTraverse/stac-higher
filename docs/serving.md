# OGC API serving (titiler-pgstac + tipg)

Pre-M5 hardening item (pulled forward from Phase 8's stretch, sequenced
2026-08-27; implemented 2026-08-29). Two off-the-shelf services join the
compose stack and give the platform an OGC-conformance story beyond the STAC
API itself:

| Service | Image | Port | Serves |
|---|---|---|---|
| `titiler` | `ghcr.io/stac-utils/titiler-pgstac:1.7.2` | :8084 | OGC API Tiles for **rasters**, per STAC collection/search, straight off pgstac |
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
- **Asset-href caveat (raster).** titiler reads asset hrefs out of item JSON.
  Platform-ingested items carry app-relative `/api/assets/...` hrefs (ADR
  0005 — bytes are only reachable through the app), which the tiler cannot
  resolve. What tiles today: reference-mode items whose hrefs are absolute
  URLs, and any item with `s3://stac-higher/...` hrefs (the compose service
  carries `AWS_S3_ENDPOINT=minio:9000` credentials for the platform bucket).
  Serving canonical platform assets properly needs either absolute asset
  hrefs (`ASSET_HREF_BASE` set to an absolute base at ingest time) or a
  titiler-side href rewrite — deliberately NOT solved here; it is a Phase 8
  cloud-deployment question (tracked in ISSUES I-68).

## Env

| Var | Default | Meaning |
|---|---|---|
| `PUBLIC_TITILER_URL` | `http://localhost:8084` | Browser-facing titiler base the UI links to |
| `PUBLIC_TIPG_URL` | `http://localhost:8085` | Browser-facing tipg base the UI links to |

## Trying it

```bash
docker compose up -d titiler tipg
curl http://localhost:8084/healthz
curl http://localhost:8085/healthz
# per-collection raster metadata (any pgstac collection id):
curl http://localhost:8084/collections/<collection-id>/info
# OGC API landing pages / conformance:
curl http://localhost:8084/api.html   # titiler OpenAPI
curl http://localhost:8085/           # tipg landing (HTML/JSON)
```

Then toggle **OGC serving** on a collection's Settings tab (operator+) and the
links appear on the page.
