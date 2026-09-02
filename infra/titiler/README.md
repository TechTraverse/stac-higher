# infra/titiler — derived titiler-pgstac image

The compose `titiler` service is `ghcr.io/stac-utils/titiler-pgstac:1.7.2`
plus one small Python package, `stac_higher_titiler`, whose `main` module
wraps `_get_asset_info` on both upstream readers (`PgSTACReader` for item
endpoints, `SimpleSTACReader` for collection/search mosaics) and then imports
the upstream FastAPI `app` unchanged. The wrapper maps the platform's
app-relative asset hrefs to the objects behind them:

```
/api/assets/{collection}/{item}/{filename}
  → s3://{PLATFORM_ASSET_BUCKET}/assets/{collection}/{item}/{filename}
```

Every other href (absolute URLs, `s3://`, `vrt://`, malformed paths) passes
through untouched. Nothing changes in what the catalog stores — this is the
*local* answer to ISSUES I-68 (GOES spec §7.1); design context in
`docs/serving.md`.

## Env

| Var | Default (compose) | Meaning |
|---|---|---|
| `PLATFORM_ASSET_BUCKET` | `${STAGING_BUCKET:-stac-higher}` | **Required.** The platform bucket — the pipeline's `STAGING_BUCKET`. Import fails loudly without it. |
| `ASSET_HREF_BASE` | `/api/assets` | The href base the pipeline writes (`ASSET_HREF_BASE`). |

Everything else (Postgres, GDAL, `AWS_*`) is the upstream image's env.

## Tests

```bash
cd infra/titiler
uv run --extra dev pytest              # pure mapper tests; reader tests skip
uv run --extra dev --extra titiler pytest   # + reader tests against titiler.pgstac 1.7.2
uv run --extra dev ruff check .
```

The `titiler` extra pulls rasterio/psycopg wheels (a few minutes, linux/amd64
or macOS). `tests/test_readers.py` uses `pytest.importorskip`, so the pure
tests run anywhere.

## Bumping titiler-pgstac

Move the tag in `Dockerfile`, the `titiler` extra in `pyproject.toml`, and the
image table in `docs/serving.md` together. titiler-pgstac 3.x (rio-tiler 9)
changed `_get_asset_info`'s return shape — re-run the reader tests with
`--extra titiler` before moving the pin, and adjust `main.py`'s wrapper if the
`url` key moved.
