# C-5 images live gate: a replicable images loop (`pipeline.demo images-*`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save the C-5 live gate's configuration as an idempotent CLI (`python -m pipeline.demo images-seed | images-status | images-teardown`) plus a manual-testing recipe, so anyone can rebuild the user-images scenario next to the GOES demo and walk the gate by hand or programmatically.

**Architecture:** A new `pipeline.demo.images` module. Images are added and exceptions granted through the **app's HTTP API** (the image write gate, validation and audit are the thing under test), using an admin dev-bypass app. The two demo processes are installed straight into the database with the existing `pipeline.demo.platform.install_process`, exactly as `goes-seed` does. The seed refuses to install a process on an image that is not `approved`. A committed compose overlay switches the pipeline to a stricter policy for the "flag an in-use image" step.

**Tech Stack:** Python 3.12, psycopg 3, stdlib `urllib` (the demo's existing `platform.request` helper), pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` §15 (the gate) and GitHub #54. The gate was walked live by the lead on 2026-09-30 before this plan (results in "Live gate record" below); this plan saves that configuration.

## Global Constraints

- Worktree `.claude/worktrees/c5-images-live-gate`, branch `feat/c5-images-live-gate` (GitHub #54, epic #56). Never work in the main checkout.
- Gates: `npm run verify` from the worktree root, and `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`. Teammates never run Docker, the dev server, e2e, or the live seed.
- Python text: no en/em dashes or other ambiguous unicode (ruff RUF001-003). Lines at most 100 characters.
- No DDL, no new dependency. `pipeline.demo` output goes through `say()`; it is a CLI, not the pipeline's structured logger.
- The standing GOES demo is never modified: the seed installs its own processes with their own fixed ids and its own output collection.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Fixed names and ids (verbatim):
  - `GEOCOLOR_IMG_ID = "60e50000-0000-4000-8000-000000000011"`, `GEOCOLOR_IMG_REVISION_ID = "60e50000-0000-4000-8000-000000000012"`, process name `goes-geocolor-img`, output collection `goes-geocolor-img`.
  - `CANARY_ID = "60e50000-0000-4000-8000-000000000013"`, `CANARY_REVISION_ID = "60e50000-0000-4000-8000-000000000014"`, process name `images-canary` (no outputs).
  - `CREATED_BY = "images-seed"`, group `earth-observation` (the GOES demo's `GROUP`).
  - Demo images: `RUNTIME_IMAGE = "ghcr.io/techtraverse/stac-higher-process-runtime:latest"`, `SLIM_IMAGE = "python:3.12-slim"`; optional `LARGE_IMAGE = "python:3.12"` (`--with-large`) and `KEV_IMAGE = "vulnerables/cve-2014-6271"` (`--with-kev`).

## Review Focus

1. **Re-running `images-seed` on a stack that already has the images** must not add a second row per image (an add of an existing reference creates a provisional row that later folds; the seed looks each one up first). Task 1 test "an existing non-revoked image is reused, not re-added".
2. **The runtime image fails the default policy today** (fixed CRITICALs in openssl/libssl3, #60). Without `--exception-days` the seed must stop short of installing `goes-geocolor-img` and say why, not install a process on a rejected image. Task 1 test "a rejected image blocks the install unless an exception is requested".
3. **The app is not running, or runs as an operator.** The seed must fail with a message naming `DEV_AUTH_IDENTITY` (exception grant needs admin), not a traceback. Task 1 test "a 403 on the exception names the admin identity".
4. **A revoked row for a demo reference** (the gate revokes the KEV image) must not be reused: revoked is terminal, so the seed adds a fresh row. Task 1 test "a revoked row is not reused".
5. **`images-teardown` must not touch the GOES demo** (`goes-geocolor`, `goes-abi-metadata`, their collections) and must not delete image rows unless `--images` is passed. Task 2 test "teardown removes only its own processes and collection".

## Live gate record (2026-09-30, lead, current stack, no volume wipe; the user chose not to wipe)

| Step | Result |
|---|---|
| 1 `python:3.12-slim` via the UI | re-add created a provisional row that folded into the existing approved row by digest; approved; scan history shows C-4 diff lines |
| 2 exception | the GHCR runtime image was **rejected** (critical_fixed openssl/libssl3 + old unfixed CRITICALs); admin granted a 14-day exception in the UI |
| 3 kind 2 GOES | `goes-geocolor-img` (a twin of `goes-geocolor`, same code) redeployed in the UI as "Custom image + your code" on the runtime digest; the next granule's run succeeded in 44 s and published a `visual` COG |
| 4 KEV image | `vulnerables/cve-2014-6271` rejected: `kev:CVE-2014-6271`, `kev:CVE-2014-6278`, `kev:CVE-2014-7169` |
| 5 flag in use | canary process on `python:3.12-slim`; pipeline + app switched to `strict-demo.json` (`block.high_unfixed: true`); UI "Rescan now" -> `flagged` in ~2 min; `process_image_flagged` fired within a minute ("new deploys are refused, runs continue"); process reads Degraded; a UI deploy was refused (409 "only an approved image can be deployed"); the next triggered run launched and succeeded; default policy restored + rescan -> approved, alert auto-resolved |
| 6 digest pins | `docker image ls --digests`: the only daemon pull is `ghcr.io/techtraverse/stac-higher-process-runtime@sha256:1d13f0a6...` with tag `<none>`; scans never pull through the daemon |

Measurements (admission scans, `docker stats` sampled at 1 s; Docker Desktop, Apple silicon, amd64 platform):

| Image | Scan wall time | Peak scanner memory |
|---|---|---|
| `python:3.12-slim` | 82 s | 407 MiB |
| `python:3.12` (~1 GB) | 101 s | 794 MiB |
| GHCR runtime image | 109 s | 315 MiB |
| `vulnerables/cve-2014-6271` | 83 s | 325 MiB |

## File map

| File | Task | Responsibility |
|---|---|---|
| `services/pipeline/src/pipeline/demo/images/__init__.py` (new) | 1 | package marker, `canary_code()` |
| `services/pipeline/src/pipeline/demo/images/seed.py` (new) | 1, 2 | `AppClient`, `seed`, `status`, `teardown` |
| `services/pipeline/src/pipeline/demo/__main__.py` | 2 | the three subcommands and flags |
| `services/pipeline/tests/test_demo_images.py` (new) | 1, 2 | pure tests with a fake app client and a fake DB |
| `infra/image-policy/strict-demo.json`, `infra/compose.strict-image-policy.yml` (new, already written by the lead in the worktree) | 3 | the stricter policy overlay |
| `services/pipeline/src/pipeline/demo/README.md`, the container-images spec (addendum), `docs/ISSUES.md`, `docs/FEATURES.md` | 3 | the recipe, the measurements, I-124/I-125 re-scope |

---

### Task 1: The images seed core (`pipeline.demo.images`)

**Files:**
- Create: `services/pipeline/src/pipeline/demo/images/__init__.py`, `services/pipeline/src/pipeline/demo/images/seed.py`
- Test: `services/pipeline/tests/test_demo_images.py`

**Interfaces:**
- Consumes: `pipeline.demo.platform.{say, request, install_process, remove_process, put_collection, enable_serving, check_migrations}`; `pipeline.demo.goes.geocolor_code`; `pipeline.demo.goes.seed.{GEOCOLOR_RUNTIME, GROUP, SOURCE_COLLECTION, TRIGGER}`. Read `platform.py` first: reuse `request()` if its signature fits a JSON POST with extra headers, otherwise add a small `json_request(url, *, method, body, headers)` beside it in `platform.py` (keep `request()` unchanged).
- Produces:
  - `class AppClient` with `__init__(self, base_url: str, bearer: str | None = None)`, `find_image(reference: str, tag: str | None) -> dict | None` (GET `/api/images?q=...`, returns the first row whose `reference` and `tag_at_add` match and whose `status != "revoked"`), `add_image(reference: str) -> dict` (POST `/api/images` `{"reference": ...}`, returns the created image dict), `get_image(image_id: str) -> dict` (GET `/api/images/{id}`), `grant_exception(image_id: str, reason: str, expires_at: str) -> dict` (POST `/api/images/{id}/exception`). Every request sends `Origin: <base_url>` (Astro CSRF) and, when set, `Authorization: Bearer <bearer>`. A non-2xx raises `AppError(status: int, body: str)`.
  - `class DemoImage(NamedTuple)`: `key: str`, `reference: str` (as typed by a user, e.g. `python:3.12-slim`).
  - `ensure_image(client, demo: DemoImage) -> dict`: reuse `find_image` else `add_image`.
  - `wait_for_scans(client, ids: list[str], *, timeout_s: int, poll_s: float = 10.0, sleep=time.sleep, clock=time.monotonic) -> dict[str, dict]`: poll `get_image` until every status is terminal (`approved | rejected | flagged | revoked | scan_failed`) or the timeout passes; returns the last image dict per id.
  - `image_snapshot(image: dict) -> dict`: `{"id": image["id"], "reference": image["reference"], "digest": image["digest"]}`.
  - `kind2_runtime(image: dict, base: dict) -> dict`: a copy of `base` with `"kind": "inline_python_on_image"` and `"image": image_snapshot(image)`.
  - `require_deployable(image: dict, name: str) -> None`: raises `SeedError` with a one-line reason unless `image["status"] == "approved"`.
  - `canary_code() -> str` in `__init__.py`: `'import os\nprint("images-canary: launched on", os.environ.get("STAC_HIGHER_RUN_ID", "?"))\n'`.

**What `seed(args)` does, in order** (it is wired to the CLI in Task 2; write it here so the tests can drive it through a fake client and fake connection):
1. `check_migrations(args.database_url, "030_container_images")` (use the exact migration name from `app/src/lib/db/migrations`/the migration index; grep for `030_`).
2. Check the GOES source collection exists (`GET {stac_url}/collections/goes-abi-mcmipc` returns 200); if not, raise `SeedError("run `python -m pipeline.demo goes-seed` first: goes-abi-mcmipc is missing")`.
3. For each demo image (runtime, slim, plus `--with-large`/`--with-kev`): `ensure_image`, print `say(f"  image {ref}: {status}")`.
4. Unless `--no-wait`: `wait_for_scans(..., timeout_s=args.scan_timeout)` (default 1800), then print each verdict with its first three `reasons`.
5. If the runtime image is `rejected` or `flagged` and `args.exception_days` is set: `grant_exception(id, reason=EXCEPTION_REASON, expires_at=<now + days, ISO 8601 with offset, seconds precision>)`. `EXCEPTION_REASON = "images-seed: platform runtime image for the kind-2 GOES demo (C-5); base-image CVEs pending #60"`. On `AppError` 403 raise `SeedError("granting an exception needs an ADMIN app identity: start the dev server with DEV_AUTH_IDENTITY='{\"roles\":[\"admin\"]}'")`.
6. Re-read the runtime and slim images; `require_deployable` each (skip the install with a printed reason, exit code 2, if either is not deployable).
7. `put_collection` + `enable_serving` the `goes-geocolor-img` collection (same extent/licence shape as `goes-seed`'s `collection_documents`, description "GeoColor COGs from goes-geocolor-img: the goes-geocolor code on an approved user image (kind 2, C-5).").
8. `install_process` `goes-geocolor-img` (kind `transform`, `max_runs_per_hour=120`, runtime `kind2_runtime(runtime_image, GEOCOLOR_RUNTIME)`, code `geocolor_code("goes-geocolor-img")`, sources `((SOURCE_COLLECTION, TRIGGER),)`, outputs `("goes-geocolor-img",)`).
9. `install_process` `images-canary` (kind `transform`, `max_runs_per_hour=60`, runtime `kind2_runtime(slim_image, GEOCOLOR_RUNTIME | {"memory_mb": 256, "timeout_seconds": 60})`, code `canary_code()`, sources `((SOURCE_COLLECTION, TRIGGER),)`, no outputs).
10. Print where to look (the same table style as `goes-seed`): `/images`, `/processes`, the `goes-geocolor-img` items, and the strict-policy recipe line.

- [ ] **Step 1: Write the failing tests** in `services/pipeline/tests/test_demo_images.py`, using a `FakeClient` (a dict of images keyed by id; records calls; can be told to return 403 on `grant_exception`) and the existing pattern in `tests/test_demo_goes.py` for faking `install_process`/`put_collection` (read it first and follow it: monkeypatch the names imported into `pipeline.demo.images.seed`). Tests, each asserting behaviour:
  - `test_an_existing_non_revoked_image_is_reused_not_re_added`: FakeClient holds an approved `docker.io/library/python` `tag_at_add="3.12-slim"`; `ensure_image` returns it and `add_image` is never called.
  - `test_a_revoked_row_is_not_reused`: the only row is revoked; `ensure_image` calls `add_image` once.
  - `test_wait_for_scans_returns_when_all_are_terminal` and `test_wait_for_scans_stops_at_the_timeout` (inject `sleep`/`clock`).
  - `test_kind2_runtime_snapshots_the_digest_and_keeps_the_base`: kind `inline_python_on_image`, `image == {"id", "reference", "digest"}`, `memory_mb`/`network` copied, base dict not mutated.
  - `test_a_rejected_image_blocks_the_install_unless_an_exception_is_requested`: runtime `rejected`, no `exception_days` -> exit code 2 and `install_process` never called; with `exception_days=14` -> `grant_exception` called once with an `expires_at` ~14 days out, then both processes installed.
  - `test_a_403_on_the_exception_names_the_admin_identity`: `SeedError` message contains `DEV_AUTH_IDENTITY`.
  - `test_a_missing_goes_source_collection_stops_before_any_write`.
  - `test_app_client_sends_origin_and_bearer` (monkeypatch the request function; assert headers).
- [ ] **Step 2: Run them to see them fail.** `cd services/pipeline && uv run pytest tests/test_demo_images.py -q` -> ImportError.
- [ ] **Step 3: Implement** `images/__init__.py` and `images/seed.py` per the interfaces above. Keep functions small; `seed()` returns an int exit code.
- [ ] **Step 4: Run the tests to see them pass**, then the full gates (pytest -q, ruff incl. the scanner, `npm run verify`).
- [ ] **Step 5: Commit** `feat(demo): images-seed core — demo images through the app API, kind-2 GOES twin and canary (C-5)`.

### Task 2: `images-status`, `images-teardown` and the CLI

**Files:**
- Modify: `services/pipeline/src/pipeline/demo/images/seed.py`, `services/pipeline/src/pipeline/demo/__main__.py`
- Test: `services/pipeline/tests/test_demo_images.py`

**Interfaces:**
- Produces: `status(args) -> int`, `teardown(args) -> int`; subcommands `images-seed`, `images-status`, `images-teardown`.
- Flags (all subcommands take the demo's existing `--stac-url` and `--database-url`, defaulted as the other subcommands do; read `__main__.py` for how they are shared):
  - `images-seed`: `--app-url` (default `http://127.0.0.1:4321`), `--bearer` (default env `STAC_HIGHER_BEARER` or none), `--with-large`, `--with-kev`, `--exception-days N` (int, 1..90, default none), `--no-wait`, `--scan-timeout S` (default 1800).
  - `images-status`: `--app-url`, `--bearer`.
  - `images-teardown`: `--images` (also DELETE the demo images' rows by id through SQL `DELETE FROM stac_higher.container_images WHERE id = ANY(%s)` only for rows whose reference is one of the demo references AND that no process revision still uses; print what was skipped), `--force` (skip the "is it in use" check for the two processes only).

`status` prints: each demo image (reference:tag, status, digest[:19], last_scanned_at, exception expiry if any, first three reasons); both processes' last five runs (status, created_at, seconds); open `process_image_flagged` alerts for them; the item count of `goes-geocolor-img` (`GET {stac_url}/collections/goes-geocolor-img/items?limit=1` `numberMatched`, or count through the DB if the API omits it).

`teardown` removes `images-canary` and `goes-geocolor-img` with `remove_process`, deletes the `goes-geocolor-img` collection (`DELETE {stac_url}/collections/goes-geocolor-img`, 404 tolerated), and with `--images` deletes image rows as above. It never touches `goes-geocolor`, `goes-abi-metadata` or their collections.

- [ ] **Step 1: Failing tests:** `test_teardown_removes_only_its_own_processes_and_collection` (records `remove_process` ids == {CANARY_ID, GEOCOLOR_IMG_ID}; the collection DELETE targets only `goes-geocolor-img`); `test_teardown_keeps_images_without_the_flag`; `test_the_cli_registers_the_three_subcommands` (parse `["images-seed", "--exception-days", "14", "--with-kev"]` and assert the namespace and `func`).
- [ ] **Step 2:** run, see them fail. **Step 3:** implement. **Step 4:** tests + full gates. **Step 5:** commit `feat(demo): images-status and images-teardown (C-5)`.

### Task 3: The recipe, the measurements and the issue re-scope (docs)

**Files:**
- Add to the commit: `infra/image-policy/strict-demo.json`, `infra/compose.strict-image-policy.yml` (already in the worktree, untracked; review them, do not change semantics).
- Modify: `services/pipeline/src/pipeline/demo/README.md`, `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` (a new final section "Addendum: C-5 live gate measurements (2026-09-30)"), `docs/ISSUES.md` (I-124, I-125), `docs/FEATURES.md` (the C-5 row), `docs/backend.md` (one line under the compose overlays mentioning `infra/compose.strict-image-policy.yml`).

- [ ] **Step 1: README "Images loop (`images-seed`)"** section after the GOES section, with: what it builds (the ASCII diagram style of the GOES section: demo images -> scans -> `goes-geocolor-img` on the runtime digest, `images-canary` on `python:3.12-slim`); preconditions (stack up, `goes-seed` done, scanner image `stac-higher-image-scanner:local` built via `docker buildx bake -f services/process-runtime/docker-bake.hcl image-scanner`, an ADMIN dev server: `DEV_AUTH_IDENTITY='{"roles":["admin"]}' npm run dev` from `app/` with the repo `.env` sourced); the commands (`images-seed --exception-days 14 --with-kev`, `images-status`, `images-teardown [--images]`); **the manual C-5 walk-through in the UI**, one numbered line per gate step from the "Live gate record" table above, naming the exact UI control (Add image, Grant exception, Custom image + your code, Rescan now, Revoke image) and what to see; the strict-policy toggle (the overlay command, the matching `PROCESS_IMAGE_POLICY_FILE` for the dev server, and the restore command); how to sample scan memory (`docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' | grep stac-scan-` in a 1 s loop); a "What to look at afterwards" table.
- [ ] **Step 2: Spec addendum**: the measurements table and the six-step results table from "Live gate record" verbatim, plus: "measured on Docker Desktop (Apple silicon) scanning linux/amd64; a Linux amd64 host will differ; the policy's `scan_limits.memory_mb: 4096` leaves 5x headroom over the largest measured peak."
- [ ] **Step 3: ISSUES**: I-124 gets a line "Measured (C-5): admission scans took 82-109 s for images up to ~1 GB, so the held slot is minutes, not the 900 s bound; still closes with K-4." I-125 gets "C-5 pulled 3 docker.io images anonymously (scanner registry reads) without hitting the limit; unchanged." Do not change their status markers. I-123 is already re-scoped (#68): leave it.
- [ ] **Step 4: FEATURES** C-5 row -> done, entry points `python -m pipeline.demo images-seed`, `infra/compose.strict-image-policy.yml`, README "Images loop".
- [ ] **Step 5:** `npm run verify`; commit `docs: C-5 images loop recipe, measurements and I-124/I-125 notes`.

### Task 4: Live proof and PR (LEAD ONLY)

- [ ] Remove the lead's hand-made UI canary (`c5-image-canary`) with `remove_process`; keep `goes-geocolor-img` (the seed adopts its fixed id).
- [ ] With the admin dev server on :4399: `uv run python -m pipeline.demo images-seed --app-url http://127.0.0.1:4399 --exception-days 14 --with-kev` twice (second run must add nothing and reinstall idempotently); `images-status`; wait for one `images-canary` run and one `goes-geocolor-img` run to succeed.
- [ ] `images-teardown` then `images-seed` again, proving a rebuild; leave the scenario seeded.
- [ ] Rebase, gates, PR `Closes #54` (body: the live gate record, measurements, what was folded from #65, deviations), CI, squash-merge.
