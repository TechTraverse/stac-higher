# Z-7 · cube-server: Tiles and EDR for Registered Cubes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `cube-server` image and compose service on **:8086** that serves every enabled cube sink's Icechunk repository as XYZ map tiles and an OGC EDR API. It never takes a store URL from a client.

**Architecture:**
- **New package `stac_higher_cube_server`** under `infra/cube-server/`, with its own `pyproject.toml` + `uv.lock`. It is built on xpublish 0.5.2 + xpublish-tiles 0.9.2 + xpublish-edr 0.11.1.
- **An in-memory `CubeRegistry`** polls `stac_higher.cube_sinks` every `CUBE_POLL_SECONDS` through a read-only DB role. It reopens a cube whenever `main`'s snapshot, the sink's variables or its source prefixes change.
- **An xpublish dataset-provider plugin** serves the registry, keyed by cube collection id: `/datasets/{cube_collection}/tiles/…` and `/datasets/{cube_collection}/edr/…`.
- **Three spike fixes:**
  - geostationary `x`/`y` are rescaled to metres;
  - a `Cache-Control` middleware plus a warm-up tile after each (re)open;
  - a per-step tolerant `edr/position` route.

**Tech Stack:** Python 3.12, icechunk 2.2.2, zarr 3, xarray, xpublish 0.5.2, xpublish-tiles 0.9.2, xpublish-edr 0.11.1, psycopg 3, uvicorn, httpx (warm-up through `ASGITransport`), pytest, ruff. App side: one migration (033) + vitest pins.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §8 (the server), §3.3 (the asset shape and the exact `t` strings), §13 (anonymous sources only, no server-side caches), §15 (the Z-7 row). Issue #93. ADR 0022. Working reference: `docs/research/2026-10-03-virtual-cube-spike/cube_server.py`, `map.html`, `q2_check.py`.

## Global Constraints

- **Worktree:** `.claude/worktrees/z7-cube-server`, branch `feat/z7-cube-server`, off `origin/main` at 515b8cd (Z-6 merged). Never work in the main checkout.
- **Pins (spec §8.1), exact:** `icechunk==2.2.2`, `zarr>=3,<4`, `xpublish==0.5.2`, `xpublish-tiles==0.9.2`, `xpublish-edr==0.11.1`, `psycopg[binary]>=3.2,<4`, `uvicorn`, `httpx`, `xarray`. Dev extra: `pytest>=8`, `ruff>=0.8`. Copy the ruff rule set from `infra/titiler/pyproject.toml`.
- **Env (spec §8.1):**
  - `CUBE_DB_DSN` (required)
  - `CUBE_S3_ENDPOINT`, `CUBE_S3_ACCESS_KEY_ID`, `CUBE_S3_SECRET_ACCESS_KEY` (required)
  - `CUBE_S3_REGION` (default `us-east-1`)
  - `PLATFORM_ASSET_BUCKET` (required)
  - `CUBE_POLL_SECONDS` (default `15`)
  - `CUBE_CORS_ORIGINS` (default `*`, comma-separated)
  - `PORT` (default `8086`)
- **Repository location:** `s3://{PLATFORM_ASSET_BUCKET}/assets/{cube_collection_id}/_cube`. Open it exactly as `services/pipeline/src/pipeline/cubes/icerepo.py::cube_storage` does: `ic.s3_storage(..., force_path_style=True, allow_http=endpoint.startswith("http://"))` with an explicit endpoint.
- **Virtual chunk access:** authorize exactly `cube_sinks.source_prefixes`, each as `ic.s3_credentials(anonymous=True)`. A prefix not starting with `s3://` makes that sink an error ("unsupported source prefix"), never an open (spec §13: anonymous S3 only in v1).
- **Never accept a store URL, bucket or prefix from a request.** Dataset ids are cube collection ids that the registry loaded from the DB, and nothing else.
- **Read-only everywhere:** the DB role can only `SELECT` the sink columns listed in Task 1. The Silo user can only read `assets/*/_cube/*`. The server never opens a writable Icechunk session.
- **No server-side chunk or frame cache** (spec §8.3, §13).
- **Gates:** each task ends green on what it touches. The final task runs `npm run verify` (repo root) and, from `infra/cube-server/`, `uv run --extra dev pytest` + `uv run --extra dev ruff check .`. The pipeline is not touched. Docker, compose bring-up, e2e and the dev server are lead-only.

## Decisions this plan takes (flag in the PR)

1. **Migration 033 creates a NOLOGIN group role `stac_higher_cube_reader`** with `USAGE` on schema `stac_higher` and column-level `SELECT (id, cube_collection_id, enabled, config, source_prefixes)` on `stac_higher.cube_sinks`.
   - The role creation and grants sit in one `DO` block. If the app's DB user lacks `CREATEROLE`, the block catches `insufficient_privilege` and `RAISE WARNING`s. A managed database's app user might lack it, and the migration must not take the app down. An operator then creates the role and grants by hand; `docs/backend.md` gives the SQL.
   - The **login** user is a deployment concern. Locally a one-shot `cube-db-role` (pgstac image, `psql`) creates `cube_server` / `cube_server` `IN ROLE stac_higher_cube_reader`, creating the group role first if it is missing. Either order works: grants to the group role reach its members whenever they are made.
2. **Build context is `infra/cube-server`**, not the repo root (the image-scanner/pipeline pattern, not titiler's), with its own `.dockerignore`. The image needs `pyproject.toml` + `uv.lock` as well as the package, and the repo-root `.dockerignore` stays untouched. *Deviation from the issue text, which assumed the titiler pattern.*
3. **`edr/position` is GET-only on the cube-server.** xpublish-edr registers GET+POST as one route. The plan replaces that route and does not re-implement the POST body parser. The UI uses GET (spec §9). `area` and `cube` stay xpublish-edr's own and fail on a missing step (spec §8.4).
4. **`x-cube-missing-steps`** is always present on a position response: the **count** of steps that came back null (`0` when none). It goes in `Access-Control-Expose-Headers` so the browser can read it.
5. **`Cache-Control`:**
   - `public, max-age=31536000, immutable` only on a **200** tile response whose query has a `t` without a `::` method prefix (an exact value);
   - `public, max-age=60` on any other 200 tile;
   - `no-store` on any tile response that is not 200.
   
   A `nearest::` selector is not pinned: once the step is trimmed it resolves to a different one. Non-tile routes are left alone.
6. **A cube with zero steps, or no repository yet, is not served** (404). Health shows it as `empty` or `pending`, not as an error.
7. **CORS** is permissive by default (`*`, GET/HEAD only). This is the same posture as titiler until #34.
8. **`_xpublish_id` = `"{cube_collection_id}:{snapshot_id}"`**, so xpublish-tiles' grid cache invalidates on every reopen.
9. **A failed refresh keeps serving what is open.**
   - A DB error (including `UndefinedTable` / `InsufficientPrivilege` before the app's first request has run migrations) keeps every open dataset and records `last_reload_error`.
   - A per-sink error keeps that sink's previous dataset, if any, and records the error under its id.

## Review Focus

1. **A tile for a `t` the server's snapshot doesn't have.** The asset's `time_values` can be up to one poll ahead of the server, or a step can just have been trimmed. Expected: a 4xx (never a 500) with `Cache-Control: no-store`, so the browser retries after the next reload. Pinned by `test_unknown_exact_t_is_not_cached_immutably` (Task 5).
2. **The cube-server starts before the app has migrated.** `stac_higher.cube_sinks` or the grant doesn't exist yet. Expected: health 200 with `last_reload_error` set, no crash, and the cubes load on the first poll after migrations. Pinned by `test_db_error_keeps_open_datasets_and_records_it` (Task 3).
3. **A sink is disabled or deleted while its dataset is open.** Expected: 404 within one poll. Pinned by `test_disabled_or_deleted_sink_is_dropped` (Task 3).
4. **One sink's repository is broken** (unreadable, unsupported prefix, zero steps). Expected: every other sink still loads. Pinned by `test_one_broken_sink_does_not_block_others` (Task 3).
5. **The browser reads the EDR CSV from another origin.** Expected: `Access-Control-Allow-Origin` is present, and `x-cube-missing-steps` is listed in `Access-Control-Expose-Headers`. Pinned by `test_cors_exposes_missing_steps_header` (Task 5).

Documented, not tested: a reload that stalls longer than Z-6's `CUBE_SNAPSHOT_RETENTION` (1 h) leaves the server on an expired snapshot whose manifests GC may delete. Health's `last_reload_error` is the signal (`docs/serving.md`).

---

### Task 1: Migration 033 — the read-only cube reader role

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append `033_cube_reader_role` after `032_cube_sinks`)
- Modify: `app/src/__tests__/cubes-migration.test.ts` (new `describe` block)

**Interfaces:**
- Produces: role `stac_higher_cube_reader` (NOLOGIN) with `USAGE ON SCHEMA stac_higher` and `SELECT (id, cube_collection_id, enabled, config, source_prefixes) ON stac_higher.cube_sinks`. Tasks 3 and 6 rely on these names.

- [ ] **Step 1: Write the failing tests** in `cubes-migration.test.ts`, reading `migrationEntry("033_cube_reader_role")`:
  - `it("runs after 032")`: index of `"033_cube_reader_role"` > index of `"032_cube_sinks"`.
  - `it("creates the role NOLOGIN only if missing")`: `toMatch(/IF NOT EXISTS \(SELECT 1 FROM pg_roles WHERE rolname = 'stac_higher_cube_reader'\)/)` and `toContain("CREATE ROLE stac_higher_cube_reader NOLOGIN")`.
  - `it("grants only the sink columns the server reads")`: `toContain("GRANT SELECT (id, cube_collection_id, enabled, config, source_prefixes) ON stac_higher.cube_sinks TO stac_higher_cube_reader")` and `not.toMatch(/GRANT SELECT ON stac_higher\.cube_sinks/)` and `not.toContain("cube_appends")`.
  - `it("never fails the app on a user without CREATEROLE")`: `toMatch(/EXCEPTION\s+WHEN insufficient_privilege THEN\s+RAISE WARNING/)`.
  - `it("never creates a login or sets a password")`: `expect(sql).not.toContain("PASSWORD")` and `expect(sql.replaceAll("NOLOGIN", "")).not.toMatch(/\bLOGIN\b/)`.

- [ ] **Step 2: Run** `npx vitest run src/__tests__/cubes-migration.test.ts` from `app/`. Expected: the new block FAILS (no entry).

- [ ] **Step 3: Append the migration** `{ name: "033_cube_reader_role", sql: ... }`. It is one `DO $do$ BEGIN … EXCEPTION WHEN insufficient_privilege THEN RAISE WARNING '…create stac_higher_cube_reader by hand, see docs/backend.md'; END $do$;` holding the conditional `CREATE ROLE`, the schema `USAGE` grant and the column `SELECT` grant. Add a comment naming spec §8.1 and Decision 1.

- [ ] **Step 4: Run** the same vitest command. Expected: PASS.

- [ ] **Step 5: Commit** `Z-7: migration 033 — read-only cube reader role`.

---

### Task 2: Package scaffold, sink reader, dataset preparation

**Files:**
- Create: `infra/cube-server/pyproject.toml`, `uv.lock` (`uv lock`), `.gitignore` (copy titiler's), `.dockerignore` (`.venv`, `tests`, `**/__pycache__`, `.pytest_cache`, `.ruff_cache`)
- Create: `infra/cube-server/stac_higher_cube_server/__init__.py`, `settings.py`, `sinks.py`, `datasets.py`
- Create: `infra/cube-server/tests/conftest.py`, `tests/cubes.py` (the local-cube fixture factory), `tests/test_sinks.py`, `tests/test_datasets.py`

**Interfaces:**
- Produces:
  - `settings.Settings` (frozen dataclass, the Global Constraints env) and `Settings.from_env(env: Mapping[str, str] = os.environ) -> Settings`, which raises `ValueError` naming each missing required var.
  - `sinks.SinkRow(id: str, cube_collection_id: str, append_dim: str, variables: tuple[str, ...], source_prefixes: tuple[str, ...])`, frozen and hashable.
  - `sinks.parse_sink_config(config: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]` returns `(append_dim, variables)`. It is lenient like the pipeline's reader: it ignores unknown keys, de-duplicates in order, and raises `ValueError` on a missing or blank `append_dim` or empty `variables`.
  - `sinks.PgSinkSource(dsn: str).enabled_sinks() -> list[SinkRow]`: one short-lived autocommit connection (`connect_timeout=5`) running `SELECT id::text, cube_collection_id, config, source_prefixes FROM stac_higher.cube_sinks WHERE enabled ORDER BY cube_collection_id`. A row whose config fails `parse_sink_config` is skipped with a WARNING log.
  - `datasets.prepare_dataset(raw: xr.Dataset, *, variables: Sequence[str], append_dim: str, collection_id: str, snapshot_id: str) -> xr.Dataset`: keeps `variables` + their grid-mapping variable (the `grid_mapping` attr of the first variable, if present), rescales, and sets `attrs["_xpublish_id"]` and `attrs["_cube_append_dim"] = append_dim` (Task 4's position route reads it).
  - `datasets.rescale_geostationary(ds: xr.Dataset) -> xr.Dataset`: the spike's `ds_m` transform. Only when the grid mapping's `grid_mapping_name == "geostationary"` and `x.attrs["units"] == "rad"`; `x`/`y` are multiplied by `perspective_point_height` and keep their attrs with `units="m"`. Otherwise it returns `ds` unchanged.
  - `tests/cubes.py::make_local_cube(tmp_path, *, steps: int, geostationary: bool = True) -> LocalCube`. It builds a small Icechunk repo on `ic.local_filesystem_storage` whose `CMI` chunks (one per step, `compressors=None`) are **virtual refs** to per-step files under a `file://` container. It has `t` (datetime64[ns]), `x`/`y` in radians with `units="rad"`, and a `goes_imager_projection` scalar with geostationary attrs (`perspective_point_height=35786023.0`), and `CMI.attrs["grid_mapping"]`. `LocalCube` exposes `storage`, `prefix` (the `file://…/` container), `credentials` (`{prefix: None}`; suppress the deprecation warning in conftest), `append(n)` (commits n more steps), `trim_to(n)`, `delete_source(step)` and `snapshot_id`. Verified on icechunk 2.2.2 (planning session): a deleted source file raises `icechunk.StorageError` on read.

- [ ] **Step 1: Write the failing tests.**
  - `test_sinks.py`:
    - `test_reads_every_pipeline_accepted_fixture_case`: load `../../tests/contract-fixtures/cube-sink-config.json`. For every case with `"pipeline": "accept"`, `parse_sink_config` returns its `append_dim` and de-duplicated `variables`. For the reject cases about `append_dim` or `variables`, it raises.
    - `test_settings_names_every_missing_var`: `from_env({})` raises, and the message contains `CUBE_DB_DSN`, `CUBE_S3_ENDPOINT` and `PLATFORM_ASSET_BUCKET`.
    - `test_settings_defaults`: poll `15`, port `8086`, region `us-east-1`, CORS `("*",)`.
  - `test_datasets.py`:
    - `test_geostationary_xy_become_metres`: `x[0] == raw.x[0] * 35786023.0` and `x.attrs["units"] == "m"`.
    - `test_non_geostationary_is_untouched`.
    - `test_metres_already_is_untouched` (`units="m"`).
    - `test_keeps_variables_and_grid_mapping_only`: a raw dataset with `CMI`, `DQF`, `goes_imager_projection` and `variables=["CMI"]` yields data vars `{"CMI", "goes_imager_projection"}`.
    - `test_xpublish_id_names_collection_and_snapshot`: `"c1:SNAP"`, and `attrs["_cube_append_dim"] == "t"`.
    - `test_make_local_cube_round_trips`: open with `xr.open_zarr(..., chunks=None)` and read a step's values. This guards the fixture.

- [ ] **Step 2: Run** `uv run --extra dev pytest -q` from `infra/cube-server/`. Expected: FAIL (modules missing).

- [ ] **Step 3: Implement** `pyproject.toml` (hatchling, `packages = ["stac_higher_cube_server"]`, script `cube-server = "stac_higher_cube_server.app:main"`), then `settings.py`, `sinks.py`, `datasets.py` and the fixture factory. Run `uv lock`.

- [ ] **Step 4: Run** `uv run --extra dev pytest -q && uv run --extra dev ruff check .`. Expected: PASS, no lint.

- [ ] **Step 5: Commit** `Z-7: cube-server package — settings, sink reader, dataset prep`.

---

### Task 3: The registry — poll, reopen on change, isolate failures

**Files:**
- Create: `infra/cube-server/stac_higher_cube_server/registry.py`
- Create: `infra/cube-server/tests/test_registry.py`, `tests/test_pg_sinks.py` (DB-gated)

**Interfaces:**
- Consumes: `SinkRow`, `PgSinkSource`, `prepare_dataset`, `make_local_cube` (Task 2).
- Produces:
  - `registry.SinkSource` (Protocol): `enabled_sinks() -> list[SinkRow]`.
  - `registry.RepoOpener` (Protocol): `open(row: SinkRow) -> ic.Repository | None`. `None` means the repository doesn't exist yet. It raises `UnsupportedSourceError(ValueError)` for a non-`s3://` prefix.
  - `registry.S3RepoOpener(settings: Settings)`: the production opener (Global Constraints storage and credentials). It caches one `ic.Repository` per `(cube_collection_id, source_prefixes)`.
  - `registry.CubeRegistry(source: SinkSource, opener: RepoOpener)`:
    - `.refresh() -> list[str]` returns the collection ids (re)opened this call, for the warm-up;
    - `.get(collection_id: str) -> xr.Dataset | None`;
    - `.ids() -> list[str]` (sorted);
    - `.health() -> dict` with keys `datasets` (`{id: {"snapshot_id", "steps", "opened_at"}}`), `pending` (ids), `empty` (ids), `errors` (`{id: message}`), `last_reload_at`, `last_reload_error`.
  - An open entry is keyed by `(snapshot_id, variables, source_prefixes, append_dim)`. A refresh with the same key reuses it. `lookup_branch("main")` is the only per-poll read for an unchanged sink. Dataset opening uses `repo.readonly_session(snapshot_id=…)` (the looked-up id, never the branch, so the key and the data agree), then `xr.open_zarr(session.store, zarr_format=3, consolidated=False, chunks=None, decode_coords="all")`. Entries swap by single dict assignment.

- [ ] **Step 1: Write the failing tests** (`test_registry.py`, with a `FakeSource` returning a mutable list and a `LocalOpener` over `make_local_cube`s):
  - `test_opens_enabled_sinks_and_serves_by_collection_id`: two steps; `get("c1")` has 2 `t`; `get("nope") is None`.
  - `test_reopens_when_main_moves`: `append(1)` → `refresh()` returns `["c1"]`, the new dataset has 3 `t`, and `_xpublish_id` changed.
  - `test_unchanged_snapshot_is_not_reopened`: second `refresh()` returns `[]` and `get("c1")` is the same object.
  - `test_reopens_when_variables_change`.
  - `test_disabled_or_deleted_sink_is_dropped`: the row is removed from `FakeSource` → `get("c1") is None` after one refresh.
  - `test_trimmed_to_zero_steps_is_empty_not_served`: `trim_to(0)` → `get` is `None`, `"c1" in health()["empty"]`.
  - `test_missing_repository_is_pending`: the opener returns `None` → `pending`, no error.
  - `test_one_broken_sink_does_not_block_others`: sink A's opener raises `UnsupportedSourceError` (`file://` prefix through `S3RepoOpener`'s check) and sink B is fine → B served, `errors["A"]` set.
  - `test_failed_reopen_keeps_previous_dataset`: open, then make the opener raise → the old dataset is still served and the error is recorded.
  - `test_db_error_keeps_open_datasets_and_records_it`: the source raises `psycopg.errors.UndefinedTable` → every dataset is kept, `last_reload_error` contains `UndefinedTable`, and the next good refresh clears it.
  - `test_pg_sinks.py` (skip unless `DATABASE_URL`, after the app's migrations): insert a sink as the superuser, then `SET ROLE stac_higher_cube_reader`:
    - `PgSinkSource`-equivalent SELECT returns it;
    - `SELECT last_error FROM stac_higher.cube_sinks` raises `InsufficientPrivilege`;
    - `SELECT 1 FROM stac_higher.cube_appends` raises `InsufficientPrivilege`.
    
    Clean up the row in `finally`. (PgSinkSource takes a DSN, so the test runs the same SQL on its own connection after `SET ROLE`. Extract the SQL as `sinks.ENABLED_SINKS_SQL` for that.)

- [ ] **Step 2: Run** `uv run --extra dev pytest tests/test_registry.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement `registry.py`.** Log one structured line per (re)open and per error (`extra={"cube_collection_id": …, "snapshot_id": …}`).

- [ ] **Step 4: Run** the full suite plus ruff. Expected: PASS (the DB test skips locally without `DATABASE_URL`; CI runs it, see Task 6).

- [ ] **Step 5: Commit** `Z-7: cube registry — poll, reopen on snapshot change, per-sink isolation`.

---

### Task 4: Tolerant position series and Cache-Control rules

**Files:**
- Create: `infra/cube-server/stac_higher_cube_server/edr.py`, `caching.py`
- Create: `infra/cube-server/tests/test_edr.py`, `tests/test_caching.py`

**Interfaces:**
- Produces:
  - `edr.load_steps_tolerant(ds: xr.Dataset, dim: str, *, concurrency: int = 16) -> tuple[xr.Dataset, int]` loads each `dim` step separately and concurrently (`asyncio.gather` over `ds.isel({dim: i}).load_async()` behind a `Semaphore(concurrency)`, run with `asyncio.run`, since the route runs in the threadpool). A step raising `icechunk.StorageError` becomes NaN in every data variable. Any other exception propagates. It returns the re-concatenated dataset (same `dim` order and values) and the count of missing steps.
  - `edr.TolerantEdrPlugin(CfEdrPlugin)` overrides `dataset_router(deps)`. It takes the parent's router, removes the route whose path ends in `/position`, and adds a GET `/position`. That route reuses xpublish-edr's pipeline from `xpublish_edr/plugin.py::_run_position_query` (0.11.1): `query.select` → `prepare_spatial_grid(…, require_regular=True)` → `project_geometry` → `select_by_position` → `project_dataset`, with the same 404/422 mapping. It replaces `_load_dataset` with `load_steps_tolerant(ds, append_dim)` (the append dim is read from `ds.attrs["_cube_append_dim"]`, set by Task 2's `prepare_dataset`), formats with `position_formats()[query.format]` or `to_cf_covjson`, and sets the header `x-cube-missing-steps: <count>`. If a formatter returns a non-`Response` (covjson dict), wrap it in `JSONResponse` so the header can be set.
  - `caching.cache_control(path: str, query: Mapping[str, str], status: int) -> str | None`. A tile path matches `^/datasets/[^/]+/tiles/[^/]+/\d+/\d+/\d+$`; its values are Decision 5's. Any other path returns `None`.

- [ ] **Step 1: Write the failing tests.**
  - `test_edr.py` on a 3-step `make_local_cube` with `delete_source(1)`:
    - `test_missing_step_is_nan_and_counted`: values `[v0, nan, v2]` at one pixel, count `1`, `t` unchanged.
    - `test_no_missing_steps_counts_zero`.
    - `test_other_errors_propagate`: monkeypatch one step's load to raise `RuntimeError` → raises.
  - `test_caching.py`, one test per row:
    - exact `t` + 200 → immutable;
    - `t=nearest::…` + 200 → `max-age=60`;
    - no `t` + 200 → `max-age=60`;
    - exact `t` + 404 → `no-store`;
    - `/datasets/c1/edr/position` → `None`;
    - `/datasets/c1/tiles/WebMercatorQuad` (metadata) → `None`.

- [ ] **Step 2: Run** them. Expected: FAIL.

- [ ] **Step 3: Implement** `edr.py` and `caching.py`.

- [ ] **Step 4: Run** the suite plus ruff. Expected: PASS.

- [ ] **Step 5: Commit** `Z-7: tolerant EDR position series and tile Cache-Control rules`.

---

### Task 5: The app — provider plugin, middleware, health, poll loop, warm-up

**Files:**
- Create: `infra/cube-server/stac_higher_cube_server/app.py`
- Create: `infra/cube-server/tests/test_app.py`

**Interfaces:**
- Consumes: `CubeRegistry` (Task 3), `TolerantEdrPlugin`, `cache_control` (Task 4), `Settings` (Task 2).
- Produces:
  - `app.CubeRegistryProvider(Plugin)` (`name = "cube-registry"`, a `registry` field; set `arbitrary_types_allowed`) with `@hookimpl get_datasets() -> list[str]` = `registry.ids()` and `@hookimpl get_dataset(dataset_id) -> xr.Dataset | None` = `registry.get(dataset_id)`. xpublish turns `None` into a 404.
  - `app.create_app(registry: CubeRegistry, settings: Settings, *, poll: bool = True) -> FastAPI`:
    - It builds `xpublish.Rest({}, plugins={"tiles": TilesPlugin(), "edr": TolerantEdrPlugin()}, app_kws={"lifespan": …})`, registers the provider, and returns `rest.app`.
    - It adds the `Cache-Control` middleware (sets the header from `cache_control(...)` when it returns a value).
    - It adds `CORSMiddleware` (origins from settings, methods GET/HEAD, `expose_headers=["x-cube-missing-steps"]`).
    - It adds `GET /health` → `{"status": "ok", **registry.health()}` (always 200).
    - With `poll`, the lifespan starts a task that runs `await asyncio.to_thread(registry.refresh)` every `settings.poll_seconds` (the first immediately) and warms each id returned.
  - `app.warm_up(app: FastAPI, collection_id: str, ds: xr.Dataset) -> None` (async): GET `/datasets/{id}/tiles/WebMercatorQuad/0/0/0?variables={first var}&t={last t, exact ns ISO}&style=raster/gray&width=256&height=256&f=png` through `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))`. It logs the duration and never raises.
  - `app.main()`: `Settings.from_env()`, `CubeRegistry(PgSinkSource(settings.db_dsn), S3RepoOpener(settings))`, `uvicorn.run(create_app(...), host="0.0.0.0", port=settings.port)`.

- [ ] **Step 1: Write the failing tests** (`TestClient(create_app(registry, settings, poll=False))`, registry pre-refreshed over a 3-step local cube `c1`; `t0` = the exact ns string of step 0):
  - `test_unregistered_id_is_404`: `/datasets/nope/tiles/WebMercatorQuad/0/0/0?…` → 404.
  - `test_pinned_tile_is_immutable`: tile with `t=t0` → 200, `image/png`, `cache-control` contains `immutable`.
  - `test_unpinned_tile_is_short_lived` → `max-age=60`.
  - `test_unknown_exact_t_is_not_cached_immutably`: `t=2000-01-01T00:00:00.000000000` → status `>= 400` and `< 500`, `cache-control == "no-store"`. If xpublish-tiles answers a 500 here, map it in the middleware to 404 for tile routes whose body names a selection error. Record the actual behaviour in the PR.
  - `test_position_returns_null_for_missing_step`: `delete_source(1)`, GET `/datasets/c1/edr/position?coords=POINT(lon lat)&parameter-name=CMI&f=csv` at a point inside the fixture grid → 200, the CSV's middle `CMI` field is empty, header `x-cube-missing-steps: 1`. Choose the fixture grid so a lon/lat inside it is known. A tiny geostationary grid over Kansas works: `(-97, 38.5)`, as the spike used.
  - `test_cors_exposes_missing_steps_header`: same request with `Origin: http://localhost:4321` → `access-control-allow-origin` present and `access-control-expose-headers` contains `x-cube-missing-steps`.
  - `test_health_reports_datasets_and_errors`: `datasets["c1"]["steps"] == 3`, `last_reload_error is None`.
  - `test_reload_is_served_without_restart`: `append(1)`, `registry.refresh()`, and the tile for the new step's exact `t` → 200.

- [ ] **Step 2: Run** `uv run --extra dev pytest tests/test_app.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement `app.py`.**

- [ ] **Step 4: Run** the whole suite plus ruff. Expected: PASS.

- [ ] **Step 5: Commit** `Z-7: cube-server app — provider plugin, caching, CORS, health, poll + warm-up`.

---

### Task 6: Image, compose, Silo user, DB login, CI, docs

**Files:**
- Create: `infra/cube-server/Dockerfile`, `infra/cube-server/README.md`, `infra/cube-server/silo-cube-read-policy.json`, `infra/cube-server/compose-db-role.sql`
- Modify: `docker-compose.yml` (`minio-init`; new `cube-db-role` and `cube-server` services)
- Modify: `.github/workflows/containers.yml` (matrix), `.github/workflows/ci.yml` (new job)
- Modify: `docs/serving.md`, `docs/backend.md`, `docs/FEATURES.md`, `.env.example` if it lists service ports (check)

**Interfaces:**
- Consumes: `cube-server` console script (Task 2), role names (Task 1).
- Produces: compose service `cube-server` at `http://localhost:8086`. Z-8 builds its URLs on `/datasets/{cube_collection}/tiles/WebMercatorQuad/{z}/{y}/{x}?variables=…&t=…&style=…&colorscalerange=…&width=256&height=256&f=png` and `/datasets/{cube_collection}/edr/position?coords=POINT(lon lat)&parameter-name=…&f=csv`.

- [ ] **Step 1: Dockerfile.** Use the pipeline's two-stage uv pattern with the **same digest-pinned bases** as `services/pipeline/Dockerfile`: `uv sync --frozen --no-dev --no-install-project`, then the package. Runtime: `libexpat1` only if import needs it (check), a system user `cube` (non-root), `EXPOSE 8086`, a `HEALTHCHECK` on `/health`, `CMD ["cube-server"]`. Build-verify locally only if Docker is free, otherwise CI is the check (lead-only singleton).

- [ ] **Step 2: Silo user.**
  - `silo-cube-read-policy.json`: `s3:GetObject` on `arn:aws:s3:::stac-higher/assets/*/_cube/*`, plus `s3:ListBucket` on `arn:aws:s3:::stac-higher` with `Condition: StringLike s3:prefix ["assets/*/_cube/*"]`.
  - `minio-init` mounts it read-only and appends: `mc admin policy create local cube-read /policy/cube-read.json`, `mc admin user add local cube-reader cube-reader-secret`, and an idempotent attach (`mc admin policy entities local --user cube-reader | grep -q cube-read || mc admin policy attach local cube-read --user cube-reader`). The bucket name stays `stac-higher`, as minio-init hard-codes it today. Note in the README that a non-default `STAGING_BUCKET` needs the policy edited.

- [ ] **Step 3: DB login one-shot.**
  - `compose-db-role.sql`: an idempotent `DO` block creating `stac_higher_cube_reader NOLOGIN` if missing, and `cube_server LOGIN PASSWORD 'cube_server'` if missing, then `GRANT stac_higher_cube_reader TO cube_server`.
  - Service `cube-db-role`: the pgstac image, `psql -v ON_ERROR_STOP=1 -h database -f /sql/role.sql`, PG env as `pgstac-migrate`, `depends_on: database healthy`, `restart: "no"`.

- [ ] **Step 4: Service `cube-server`.**
  - `build: {context: ./infra/cube-server}`, `image: stac-higher-cube-server:local`, port `8086:8086`.
  - Env:
    - `CUBE_DB_DSN=postgresql://cube_server:cube_server@database:5432/postgis`
    - `CUBE_S3_ENDPOINT=http://minio:9000`
    - `CUBE_S3_ACCESS_KEY_ID=cube-reader`, `CUBE_S3_SECRET_ACCESS_KEY=cube-reader-secret`
    - `PLATFORM_ASSET_BUCKET=${STAGING_BUCKET:-stac-higher}`
    - `CUBE_POLL_SECONDS=15`
  - `depends_on`: `database` healthy, `cube-db-role` and `minio-init` completed successfully.
  - Healthcheck on `/health` like titiler's.
  
  Comment the auth posture (anonymous, same as titiler until #34) and that it reaches NODD directly (egress rides #68).

- [ ] **Step 5: CI.**
  - `containers.yml` matrix: `{name: cube-server, dockerfile: infra/cube-server/Dockerfile, context: infra/cube-server}`. The Trivy step already covers it.
  - `ci.yml`: a new job `cube-server`, "Cube server · ruff, pytest (with pgstac)". Copy the `pipeline` job's pgstac service, node setup, `npm ci` and app-schema steps, then run `uv sync --frozen --extra dev`, `uv run ruff check .` and `uv run pytest -q` with `DATABASE_URL` in `infra/cube-server`. `cache-dependency-glob: infra/cube-server/uv.lock`.
  - Run `actionlint` locally if installed.

- [ ] **Step 6: Docs.**
  - `docs/serving.md`:
    - replace "The `cube-server` … is Z-7" with a **cube-server** section: routes and URL shapes (above), the registry and reload, the auth posture, Cache-Control rules, the warm-up;
    - GET-only `position` with null steps and `x-cube-missing-steps`;
    - `area`/`cube` failing on a missing step;
    - the expired-snapshot note from Review Focus;
    - no chunk cache.
  - `docs/backend.md`:
    - stack table row `cube-server` `:8086`;
    - the env vars;
    - the `cube-db-role` one-shot;
    - the manual role SQL for a DB user without `CREATEROLE` (Decision 1).
  - `docs/FEATURES.md`: Z-7 row ✅ with entry points.
  - `infra/cube-server/README.md`: env table, tests, bumping the pins.

- [ ] **Step 7: Run** `docker compose config -q` (parses compose, no containers started). Expected: exit 0.

- [ ] **Step 8: Commit** `Z-7: cube-server image, compose service, read-only Silo user + DB login, CI, docs`.

---

### Task 7: Gates and the PR

- [ ] **Step 1:** `npm run verify` (repo root). Expected: green.
- [ ] **Step 2:** `uv run --extra dev pytest -q && uv run --extra dev ruff check .` in `infra/cube-server/`. Expected: green. Optionally run with `DATABASE_URL` against a throwaway Postgres to exercise `test_pg_sinks.py`; never against the standing demo's DB without the lead.
- [ ] **Step 3:** Push and open the PR to `main`: title `Z-7: cube-server — tiles and EDR for registered cubes`; body starts `Closes #93`, lists the gates, Decisions 1–9 (Decision 2 and 3 as deviations), and the lead-only steps below.
- [ ] **Lead-only (after CI is green, with the lead's go-ahead, since the stack carries the standing GOES demo):**
  - `docker compose up -d minio-init cube-db-role cube-server` (additive).
  - Create a throwaway source + cube collection + sink (window `max_steps: 12`) on the GOES prefix, and wait for 2–3 commits.
  - Check: `curl :8086/health`; a pinned tile (200, immutable, ~0.4 s warm); a position CSV at Kansas matching `q2_check.py`; a tile for a trimmed `t` (4xx, `no-store`).
  - Tear the throwaway sink and collections down.
