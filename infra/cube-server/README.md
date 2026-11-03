# infra/cube-server — tiles and EDR for registered virtual cubes

The compose `cube-server` service (:8086, Z-7, virtual cube spec §8). It
serves every **enabled** cube sink's Icechunk repository as XYZ tiles
(xpublish-tiles) and an OGC EDR API (xpublish-edr), keyed by cube collection
id. Design, routes and caching rules: [`docs/serving.md`](../../docs/serving.md)
"Virtual cubes: the cube-server".

The service is behind the compose profile `cube`, so it is not part of plain
`docker compose up -d` (nor of the CI e2e job). Start it, with its
`cube-db-role` and `cube-silo-user` one-shots, from the repo root:

```
docker compose --profile cube up -d --build cube-server
```

| Module | Job |
|---|---|
| `settings.py` | Env (below) |
| `sinks.py` | The enabled sinks through the read-only role (migration 033); lenient `config` reader pinned by `tests/contract-fixtures/cube-sink-config.json` |
| `registry.py` | Poll, reopen on snapshot/config change, per-sink isolation; `S3RepoOpener` (anonymous `s3://` sources only) |
| `datasets.py` | Keep the sink's variables + grid mapping, geostationary rad → m, `_xpublish_id` |
| `edr.py` | GET-only per-step tolerant `edr/position` (`x-cube-missing-steps`) |
| `caching.py` | Tile `Cache-Control` rules |
| `app.py` | xpublish app, dataset-provider plugin, CORS, `/health`, poll loop + warm-up tile, `main()` |

## Env

| Var | Default (compose) | Meaning |
|---|---|---|
| `CUBE_DB_DSN` | `postgresql://cube_server:cube_server@database:5432/postgis` | **Required.** A login in `stac_higher_cube_reader` (`compose-db-role.sql` locally). |
| `CUBE_S3_ENDPOINT` | `http://minio:9000` | **Required.** Platform object store, path-style. |
| `CUBE_S3_ACCESS_KEY_ID` / `CUBE_S3_SECRET_ACCESS_KEY` | `cube-reader` / `cube-reader-secret` | **Required.** Read-only on `assets/*/_cube/*` (`silo-cube-read-policy.json`, attached by the `cube-silo-user` one-shot). The policy names the `stac-higher` bucket: edit it for another `STAGING_BUCKET`. |
| `PLATFORM_ASSET_BUCKET` | `${STAGING_BUCKET:-stac-higher}` | **Required.** The pipeline's `STAGING_BUCKET`. |
| `CUBE_S3_REGION` | `us-east-1` | |
| `CUBE_POLL_SECONDS` | `15` | Registry poll interval. |
| `CUBE_CORS_ORIGINS` | `*` | Comma-separated. |
| `PORT` | `8086` | |

## Tests

```bash
cd infra/cube-server
uv run --extra dev pytest -q        # DB-gated grant tests skip without DATABASE_URL
uv run --extra dev ruff check .
DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run --extra dev pytest -q
```

The tests need no object store: `tests/cubes.py` builds small virtual cubes on
the local filesystem whose chunks reference per-step files, so deleting a file
reproduces a missing NODD source (`icechunk.StorageError`). The DB-gated tests
need a database the app's migrations ran on (`npm run db:migrate`).

## Bumping the pins

`icechunk`, `xpublish`, `xpublish-tiles` and `xpublish-edr` are pinned
exactly (spec §8.1). `edr.py` reuses xpublish-edr 0.11.1's position pipeline
(`_run_position_query`) and replaces its `/position` route, so re-read that
function and re-run `tests/test_app.py` before moving the xpublish-edr pin.
`icechunk` is also the writer's version (`services/pipeline/uv.lock`, 2.3.0): bump the two together.
The image's bases are the pipeline's; move them together.
