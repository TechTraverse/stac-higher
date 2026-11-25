# R-10 · Demo seed as an in-cluster Job against a deployed stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub #146 (epic #136).

**Goal:** `pipeline.demo` gains an in-cluster mode (`--in-cluster`) that runs against the endpoints a deployment configures instead of compose's published ports, never builds or inspects images, and seals the Terraform `demo-destination` key into `goes-seed --deliver`'s connection instead of MinIO keys. The chart gains a `demoSeed` Job, off by default, that runs the chosen seeds once.

**Architecture:** The CLI keeps its compose defaults byte for byte; a top-level `--in-cluster` flag switches endpoint resolution to the environment the chart's Job sets (the pipeline's own ConfigMaps plus a few `DEMO_*` variables). In that mode the platform bucket is reached exactly as the pipeline reaches it (`build_platform_client(Settings.from_env())`: blank `STAGING_S3_*` keys mean the default credential chain, i.e. the pipeline ServiceAccount's workload identity). The chart renders one plain (non-hook) Job whose name carries a hash of its pod template, so it runs once per distinct configuration under both `helm upgrade --install` and Argo CD/Flux, with one container per seed run in a fixed order (all but the last as init containers). The destination key reaches the pod from Secrets Manager through a runtime ExternalSecret (or a deployment-supplied Secret in `existing` mode).

**Tech Stack:** Python 3.12 + argparse/boto3/psycopg (pipeline image), Helm 3.22 / 4.3, External Secrets Operator `external-secrets.io/v1`, vitest render tests over `helm template` (`infra/helm/stac-higher/tests/render.mjs`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §6.1 (demo seed row), §6.2 (`demoSeed`), §7.1 (`demo-destination`), §8 ("Demo seed against a deployed stack"); ADR 0024. Inputs: R-5's `helm_values.demoDestination` (`bucket`, `region`, `credentialsSecretArn`; secret JSON `access_key_id` / `secret_access_key`), R-6's chart (`infra/helm/stac-higher/`, plan `2026-11-03-r6-helm-chart.md`).

## Global Constraints

### Layout

- The chart is `infra/helm/stac-higher/` (the issue's `charts/stac-higher/` is stale; R-6 ruling).
- R-7 extends the same chart concurrently. R-10 adds **new files** (`templates/demo-seed.yaml`, `tests/values/demo-seed.yaml`, `app/src/__tests__/helm/demo-seed.test.ts`) and makes the smallest edits to shared ones: the `demoSeed` block of `values.yaml`, `values.schema.json` and the chart README, one `run` line in `scripts/check-helm.sh`. `_helpers.tpl` and the `validate` template are not touched: R-10's cross-field rules live in its own template.

### CLI: in-cluster mode

- Top-level flag `--in-cluster` (before the subcommand). Without it nothing changes: the endpoint flags keep their compose defaults.
- With it, every endpoint flag left unset is read from the environment (an explicit flag still wins):

  | Arg | Env | Required |
  |---|---|---|
  | `--stac-url` | `DEMO_STAC_URL` (the in-cluster stac-fastapi URL, writes go straight to it as on compose's :8082) | yes |
  | `--database-url` | `DATABASE_URL` (no userinfo; libpq takes `PGUSER`/`PGPASSWORD`/`PGSSLMODE`/`PGSSLROOTCERT` from env) | yes |
  | `--bucket` | `STAGING_BUCKET` | yes |
  | `--titiler-url` | `DEMO_PUBLIC_TITILER_URL` | no (printed hints only) |
  | `--ui-url` (new, top level) | `DEMO_PUBLIC_APP_URL` | no (printed hints only) |
  | `--app-url` (images-*) | `DEMO_APP_URL` (the app Service) | yes for images-* |

  `--s3-endpoint` is compose-only and refused in-cluster: the platform bucket is reached the way the pipeline reaches it.
- The process runtime image: in-cluster the seeds never build (`--no-build-image` is required; without it they refuse before any write) and never run `docker image inspect` (no daemon in a pod). The image is the deployment's `PROCESS_RUNTIME_IMAGE`.
- `goes-seed --deliver` in-cluster seals `{"access_key_id", "secret_access_key"}` from `DEMO_DESTINATION_ACCESS_KEY_ID` / `DEMO_DESTINATION_SECRET_ACCESS_KEY` into the `goes-geocolor-dest` connection with config `{"bucket": DEMO_DESTINATION_BUCKET, "region": DEMO_DESTINATION_REGION}` (no endpoint, virtual-hosted style; a shape the s3 contract fixture already accepts). It does not create the bucket (Terraform did; the user key is write-only). All four variables are checked before any write, like the master key. `--internal-s3-endpoint` is compose-only. Compose behaviour (MinIO keys, `ensure_bucket`) is unchanged.
- `teardown` and `goes-teardown` delete platform bytes directly, which ADR 0011 forbids outside `asset_gc`; in-cluster they refuse. `status`, `goes-status`, `cube-*` and `images-*` work in either mode.
- The CLI prints with `say()`; it adds no logger calls (so no `extra` keys to police).

### Chart: the `demoSeed` Job

Values (all under `demoSeed`, defaults in parentheses):

| Key | Default | Notes |
|---|---|---|
| `enabled` | `false` | renders the Job (and the destination ExternalSecret) |
| `seeds` | `[seed, goes-seed]` | any of `seed`, `goes-seed`, `cube-seed`, `images-seed`, unique; always run in that order (images-seed needs goes-seed's collection) |
| `stacUrl` | `""` | in-cluster stac-fastapi URL; required when enabled (R-7 owns the Service) |
| `deliver` | `false` | `goes-seed --deliver` to `demoDestination` |
| `existingDestinationSecret` | `""` | `secrets.mode=existing`: Secret with `access_key_id`/`secret_access_key` |
| `appBearer.secretName` / `.key` | `""` / `token` | bearer token Secret for `images-seed` (operator identity); required when it is chosen |
| `extraArgs` | `{}` | per-seed extra CLI args, keyed by seed name |
| `backoffLimit` | `6` | pod retries while the stack comes up |
| `activeDeadlineSeconds` | `3600` | images-seed may wait 1800 s for scans |
| `resources` | requests `cpu: 100m, memory: 512Mi`, limits `memory: 1Gi` | per container |

- Plain Job, **not a hook**: it needs the app migrations (done by the pre-install hooks) and the running catalog plane, and a post-sync hook would re-seed on every Argo sync. Name `<fullname>-demo-seed-<8 hex of sha256(pod template)>` (fullname ≤ 42 → ≤ 61 chars). Unchanged values = the same Job = no re-run; a new image or seed setting = a new Job (the old one is pruned). No `ttlSecondsAfterFinished` (a vanished Job would be recreated by the next sync). Re-run by hand: `kubectl delete job` then upgrade/sync.
- Re-seeding replaces the demo processes' revisions and their run history (documented seed behaviour); acceptable for a demo environment and it carries new process code from a new image.
- Pod: pipeline image, the pipeline ServiceAccount (its workload identity writes the scene COG to the platform bucket; no new IAM), `automountServiceAccountToken: false`, uid 999, read-only root, `/tmp` emptyDir, the runtime db-CA volume, `envFrom` the shared + pipeline env ConfigMaps, `PGUSER`/`PGPASSWORD` from the runtime owner Secret, `CREDENTIALS_MASTER_KEY` from the platform Secret. Runtime Secrets only, never hook copies (I-156). Scheduling follows `pipeline.nodeSelector/tolerations/affinity`.
- Per-container secrets only where needed: the destination key only in `goes-seed` (with `deliver`), the bearer only in `images-seed`.
- The destination ExternalSecret `<fullname>-demo-destination` (json properties `access_key_id`, `secret_access_key` of `demoDestination.credentialsSecretArn`) renders only when enabled + deliver + `external-secrets` mode.
- Render-time refusals (each message starts with the key path): `demoSeed.stacUrl` missing or not http(s); `demoSeed.seeds` empty/unknown/duplicate (schema); `demoSeed.appBearer.secretName` missing with images-seed; `demoSeed.deliver` without goes-seed, without `demoDestination.bucket`, or without the key source for the secrets mode; `demoSeed.extraArgs.<k>` for a seed not chosen.
- No NetworkPolicy: the pod only makes egress (open, spec §6.5). It carries `app.kubernetes.io/component: demo-seed`, which the catalog plane's ingress policy (R-7) must admit on the STAC API.

### Rules

- Placeholders only (`example.com`, `000000000000`). No account details (ADR 0024).
- Commits `R-10: <what>` with trailer `Co-Authored-By: Claude Opus <noreply@anthropic.com>`.
- Gates: `uv run pytest` + `uv run ruff check .` in `services/pipeline/`; `scripts/check-helm.sh` and `npx vitest run src/__tests__/helm/` (as `helm.yml` runs them); `npm run verify` (the render tests live in `app/`).
- No Docker, cluster, AWS, dev server or seed run against the local stack. The live run is R-11 (#147).

## Review Focus

1. **Compose mode is unchanged**: every default, MinIO-key sealing and `ensure_bucket` path stays. Test: `test_compose_defaults_are_unchanged`, `test_compose_deliver_still_seals_minio_keys`.
2. **No write before a refusal**: a missing destination variable or a build request in-cluster changes nothing. Tests: `test_cluster_deliver_refuses_before_any_write`, `test_cluster_seed_refuses_to_build`.
3. **The Job is stable across renders and changes name when its pod changes**, so Helm and Argo neither re-run it needlessly nor hit an immutable-field error. Test: `the Job name is a stable hash of its pod`.
4. **Secrets stay least-privilege and runtime-only.** Test: `secrets reach only the container that needs them`.

---

## File structure

```
services/pipeline/src/pipeline/demo/__main__.py      # --in-cluster, --ui-url, endpoint resolution, scene upload via platform_s3_client
services/pipeline/src/pipeline/demo/platform.py      # in_cluster(), platform_s3_client(), ensure_runtime_image(in_cluster=)
services/pipeline/src/pipeline/demo/goes/seed.py     # delivery_destination(), teardown refusal, cluster-aware hints
services/pipeline/src/pipeline/demo/images/seed.py   # cluster-aware UI hint
services/pipeline/src/pipeline/demo/README.md        # "In-cluster mode"
services/pipeline/tests/test_demo_in_cluster.py      # new
infra/helm/stac-higher/templates/demo-seed.yaml      # new: Job + destination ExternalSecret + refusals
infra/helm/stac-higher/tests/values/demo-seed.yaml   # new scenario
infra/helm/stac-higher/{values.yaml,values.schema.json,README.md}
scripts/check-helm.sh                                # one run line
app/src/__tests__/helm/demo-seed.test.ts             # new
docs/FEATURES.md, docs/ISSUES.md (I-169)
```

### Task 1: CLI in-cluster mode (pytest first)

- [ ] Write `tests/test_demo_in_cluster.py`: endpoint resolution (env, explicit flag wins, missing required names the variable, `--s3-endpoint` refused, compose defaults unchanged); `ensure_runtime_image(in_cluster=True)` refuses a build and never calls docker; `platform_s3_client` uses `build_platform_client` in-cluster and `s3_client` on compose; `--in-cluster` reaches the subcommand.
- [ ] Run: fails (no `--in-cluster`).
- [ ] Implement in `platform.py` and `__main__.py`; `seed` uploads through `platform_s3_client`; teardown refuses in-cluster.
- [ ] Run pytest + ruff; commit.

### Task 2: `goes-seed --deliver` seals the demo-destination key

- [ ] Tests: in-cluster deliver seals the env key (decrypts to exactly the two keys), config `{bucket, region}` parses with `parse_s3_config` and builds an `S3Adapter`, no `ensure_bucket`; missing variables refuse before any write (names them); compose deliver still seals MinIO keys and creates the bucket; `goes-teardown` refuses in-cluster.
- [ ] Implement `delivery_destination(args, env)`; resolve it right after the master key.
- [ ] images-seed: cluster UI hint uses `--ui-url`.
- [ ] Run pytest + ruff; commit.

### Task 3: Chart `demoSeed` Job

- [ ] `demo-seed.test.ts`: off by default; renders one Job + ExternalSecret with the scenario; container order and args (`--in-cluster`, `--no-build-image` for seed/goes-seed, `--deliver`, extraArgs); env from runtime Secrets only; per-container secrets; hardening; stable hash name; existing mode uses `existingDestinationSecret`; each refusal.
- [ ] `values.yaml` / schema / `templates/demo-seed.yaml` / `tests/values/demo-seed.yaml` / `check-helm.sh` line; README rows.
- [ ] Run `scripts/check-helm.sh`, the helm vitest suite; commit.

### Task 4: Docs and gates

- [ ] `demo/README.md` "In-cluster mode"; chart README section; `FEATURES.md` R-10 row; `ISSUES.md` I-169 (what in-cluster cannot do yet: STAC API policy wiring, images-seed bearer lifetime and scans behind K-5, teardown refused).
- [ ] Full gates; commit.

## Review changes (2026-11-25)

- The Job name hashes the pod template rendered with selector labels only, so a chart-only release (new version or appVersion, same digests) keeps the name; a new image, seed list, args, env or Secret reference changes it.
- `demoSeed.backoffLimit` defaults to `0` (a pod retry redoes every completed seed and wipes their run history); start-up ordering moves into the pod: `--wait-ready SECONDS` (`demoSeed.waitReadySeconds`, default 600) waits for the STAC API and, for `images-*`, the app.
- `CREDENTIALS_MASTER_KEY` only in `goes-seed` and `cube-seed`.
- The app NetworkPolicy adds the seed pod as a peer when `appIngressFrom` is set and `images-seed` is chosen.
- `images-seed` is documented to fail the Job before K-5 (leave it out until then); direct catalog/DB writes without `audit_log` rows recorded as an accepted deviation (I-169, renumbered from I-164).
