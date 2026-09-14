# Bring-your-own container images + image scanning — design

**Date:** 2026-09-13
**Status:** **draft, pending lead review.** Written in a planning session
with the lead (no dev work); the decisions in §2 are the lead's answers,
the decisions in §14 were taken by the agent and stand unless overturned.
The C queue in `TODO.md` is copied from §15; nothing may start until the
lead approves this spec and ADR 0021.
**Scope source:** ADR 0013 "Slice-1 scope" (the deferred `container`
runtime kind), Phase 9 spec §4 ("user-image supply-chain review stays out
of the first accreditation surface"), ADR 0019 §Consequences ("user-supplied
images remain refused"), K8s spec §12 (the refusal is explicitly NOT lifted
there), ROADMAP §5.6 runtime shape, NIST SP 800-190 §4.1–4.2, the FedRAMP
*Vulnerability Scanning Requirements for Containers* (30-day window, per-
image inventory, a deploy-time restriction mechanism), CISA BOD 26-04 (KEV
first).

## 1. Problem

A process today is inline Python injected into a **platform-built** image.
That was the right first accreditation surface, and it is now the wall
every real workload hits: Satpy for GOES (`docs/processes.md` "The GOES
worked example" names a Satpy image as the documented next step), PyTorch
(I-95 records the demand), and any existing tool that already ships as a
container. The contract has carried `runtime.kind = "container"` and
`runtime.image` since M5-0 precisely so nothing was foreclosed — the app's
write gate refuses it (`CONTAINER_RUNTIME_REFUSAL`, `schemas.ts:180`) and
the UI never shows it.

Lifting the refusal without a supply-chain story would put an unscanned
Docker Hub image inside the FISMA-High data plane. So this spec lifts it
**behind a gate**: every user image is scanned before it can be deployed,
runs only by the digest that was scanned, is rescanned daily against a
fresh vulnerability database, and is tracked — metadata only, never the
bytes — in a platform-wide registry with a dashboard.

The AWS Lambda console is the UX reference: a function is *either* code
you type *or* a container image you point at; the image tag is resolved
to a digest at deploy time and the digest is what runs.

## 2. Decisions taken with the lead (2026-09-13)

| # | Question | Decision |
|---|---|---|
| 1 | What does a user image replace? | **Both modes.** (a) a custom image as the dependency bundle with the platform still injecting inline code; (b) the image IS the process (its own entrypoint speaks the run contract). |
| 2 | Which registries? | **Public + private with credentials.** Docker Hub, GHCR, any OCI registry incl. ECR; private pulls use a stored registry credential in the existing encrypted envelope. |
| 3 | Who decides an image is usable? | **Policy gate + admin exceptions.** A passing scan auto-approves; a failing image can be approved by an admin with a recorded, expiring exception, audited. |
| 4 | A rescan newly fails an image a deployed process uses? | **Alert + block new deploys; runs continue.** The image becomes `flagged`; new revisions referencing it are refused; triggered runs keep launching so a Sunday-night CVE does not halt production; an admin grants an exception or disables the process. |
| 5 | Sequencing vs the K queue? | **Own queue (C), ships on the current `DockerExecutor` now.** Coordinates with K-1 only at the runtime schema; Kubernetes admission enforcement is a K-5/K-6 addendum. |
| 6 | Scanner engine? | **Syft + Grype.** SBOM once per digest (Syft), vulnerability match with native CISA KEV + EPSS + risk score (Grype), daily SBOM-only rescans without re-pulling. CI keeps Trivy for the platform's own images. |
| 7 | Visibility of the registry? | **Platform-wide catalog, group-owned pull credentials.** Every scanned image is visible to all members; an image pulled with a group's credential can be referenced only by that group's processes. |
| 8 | Where does the scan run? | **As a platform run through the `Executor` seam** (approach A), not a subprocess in the worker, not an external scanning registry. |
| 9 | Contract shape? | **A third kind**, not a nullable `image` on `inline_python`, so the discriminated union stays single-field. |

Sections 2, 3–4, 5–7 of the in-session design were approved as presented;
§1 was revised per decision 9.

## 3. The contract: three runtime kinds

`runtime.kind` becomes `inline_python | inline_python_on_image | container`.

```jsonc
// 1. unchanged — platform image chosen by alias, code injected
{ "kind": "inline_python", "runtime_image": "default", "image": null, /* limits, retry, network, hardware */ }

// 2. NEW — your image as the dependency bundle, our runner + your code
{ "kind": "inline_python_on_image",
  "image": { "id": "…uuid…", "reference": "ghcr.io/org/satpy-runtime", "digest": "sha256:…" },
  "runtime_image": null, /* limits, retry, network, hardware */ }

// 3. LIFTED — the image is the process
{ "kind": "container",
  "image": { "id": "…uuid…", "reference": "docker.io/org/tool", "digest": "sha256:…" },
  "command": ["tool", "--run"],          // optional: overrides the image's CMD (never its USER)
  "runtime_image": null, /* limits, retry, network, hardware */ }
```

Rules (both runtimes' schemas; fixture `process-runtime.json` gains the
third variant and flips every `container` case that was reject/accept):

- `image` is an **immutable snapshot** `{id, reference, digest}` of a
  `container_images` row at deploy time. `reference` is the normalized
  repository (`registry/namespace/name`, no tag); `digest` is the
  manifest digest that runs. Revisions are immutable, so a later
  re-scan, revocation or deletion never rewrites a stored revision — the
  same stance as hardware profiles (unknown profile ⇒ dead run).
- `image` is required for kinds 2 and 3, must be `null`/absent for kind 1
  (reject/reject otherwise). `runtime_image` is required-absent (`null`)
  for kinds 2 and 3: an alias names a platform-built image, so combining
  the two has no meaning at launch.
- `code` is required for kinds 1 and 2 and refused for kind 3
  (`processRevisionCreateSchema`).
- `command` exists only on kind 3: `string[]`, non-empty when present,
  each element non-blank, max 64 entries. It maps to Docker `Cmd`
  (Kubernetes `args`) and never to `Entrypoint`/`User`.
- Hardware (`K-1`) and network blocks are unchanged and apply to all three
  kinds. A profile's `image` base is used only by kind 1.
- **The write gate** (`processRuntimeSchema` + the revisions route)
  replaces `CONTAINER_RUNTIME_REFUSAL` with a DB-backed check, the
  `PROCESS_NETWORK_MAX` dual-enforcement pattern: the row named by
  `image.id` must exist, its `reference` and `digest` must equal the
  snapshot, its status must be `approved` and **not stale** (§4.3), and if
  it carries `registry_connection_id` that connection's group must be the
  process's group (the secret-ref rule). Failure is a 422 naming the
  reason (`image_not_approved`, `image_stale`, `image_group_mismatch`,
  `image_digest_mismatch`). The pipeline re-checks at launch (§8.4).

### 3.1 How inline code reaches a custom image (kind 2)

`services/process-runtime/entrypoint.py` is ~40 lines of stdlib. Today it
is COPY'd into the platform image; for kind 2 it travels the same way the
code does — **in the environment, no bind mount** (the executor's no-mount
rule stands):

- `STAC_HIGHER_RUNNER_B64` = base64 of `entrypoint.py`'s source (the
  pipeline image ships a copy; a hash test pins it equal to the runtime
  image's).
- The executor sets `Entrypoint: ["python3", "-c", BOOTSTRAP]` where
  `BOOTSTRAP` is one line: decode-and-exec `STAC_HIGHER_RUNNER_B64` after
  popping it from the environment. The runner then behaves exactly as on
  the platform image (pops `STAC_HIGHER_PROCESS_CODE_B64`, exit codes
  0/1/2).
- **Author requirement:** `python3` (≥ 3.10) on `PATH`. Nothing else —
  no platform package, no particular base. `docs/processes.md` says so.

### 3.2 What every user image gets, regardless of its own config (kinds 2, 3)

The executor's hardening is the platform's, not the image's: `CapDrop:
["ALL"]`, `no-new-privileges`, the revision's memory/CPU/timeout, the
run's network profile, **and `User: "10001:10001"` forced** (the platform
runtime's `runner` uid; Kubernetes `runAsUser/runAsNonRoot`). An image
whose files are unreadable by that uid fails at run time, not at scan
time; the scan records the image config's `User`, `Entrypoint` and `Cmd`
as information so the dashboard can warn ("image declares USER root; runs
as 10001"). Writable scratch is `/tmp` (tmpfs, sized with the memory
limit) (a tmpfs the executor adds for kinds 2–3) and the run's output prefix.
`docs/processes.md` "Bring your own image" states all of it.

## 4. Data model — migration 030

029 is reserved for K-3 (`TODO.md`); C-1 takes **030**. Two tables, both
`stac_higher.*`, DDL app-owned (ADR 0001); the pipeline reads and writes
rows.

### 4.1 `container_images` — the platform-wide registry (metadata only)

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `reference` | text | normalized `registry/namespace/name`; `docker.io/library/x` for bare names |
| `tag_at_add` | text | the tag the operator typed (`latest` allowed; it is resolved, never trusted) |
| `digest` | text null | manifest (or index) digest the tag resolved to at add time — **what runs**; null only while `pending`/`scanning` on admission; `UNIQUE (reference, digest)` (nulls distinct) |
| `platform_digest` | text | the platform-specific manifest that was scanned (equals `digest` for single-arch) |
| `platform` | jsonb | `{os, architecture}` from the policy's `platform` |
| `size_bytes` | bigint | sum of layer sizes from the manifest |
| `config` | jsonb | `{user, entrypoint, cmd}` from the image config (§3.2) |
| `status` | text CHECK | `pending \| scanning \| approved \| rejected \| flagged \| revoked \| scan_failed` (§4.3) |
| `verdict` | jsonb | the latest policy evaluation (§7.3): `{pass, reasons[], counts, fixed_counts, kev[], max_risk, evaluated_at, policy_version}` |
| `sbom_ref` | text | object key of the latest SBOM (§6.3) |
| `last_scan_id` | uuid | the latest scan row; no FK (it would be circular with `image_scans.image_id`) |
| `last_scanned_at` | timestamptz | drives staleness |
| `added_by` | text | audit identity |
| `registry_connection_id` | uuid null fk `connections` | the group-owned credential used to pull; null ⇒ anonymous/public |
| `exception_reason` / `exception_by` / `exception_at` / `exception_expires_at` | text / text / timestamptz / timestamptz | the live admin exception, if any (§4.4) |
| `tag_current_digest` / `tag_checked_at` | text / timestamptz | last drift check (§8.2) |
| `created_at` / `updated_at` | timestamptz | |

Indexes: the unique pair; `(status)`; `(last_scanned_at) WHERE status IN
('approved','flagged')` for the rescan tick. On `process_revisions`, an
expression index on `((runtime->'image'->>'id'))` so "in use by N
processes" and the flagged-alert fan-out are one join.

### 4.2 `image_scans` — the ADR 0004 request row

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `image_id` | uuid fk `container_images` ON DELETE CASCADE | |
| `kind` | text CHECK | `admission \| rescan` |
| `status` | text CHECK | `pending \| running \| done \| failed` — the `connection_checks` shape |
| `requested_by` | text | user id for admission / manual rescans; `pipeline` for the daily tick |
| `requested_at` / `started_at` / `finished_at` | timestamptz | |
| `executor_handle` / `log_ref` | text / text | the scanner run's container id and captured log object (§6.2, §8.1) — a scan is not a `process_runs` row |
| `result` | jsonb | the scanner summary (§6.4) + `verdict` + `diff` (§8.2); `{error}` on failure |
| `findings_ref` | text | object key of the full Grype JSON |

Partial index `(requested_at) WHERE status = 'pending'`, claimed with
`FOR UPDATE SKIP LOCKED` (`drain.py`'s pattern). Admission rows are
INSERTed by the app (`POST /api/images`, manual rescan); **rescan rows are
INSERTed by the pipeline's own daily tick** so the ledger is uniform — a
row, never a call, in both directions.

### 4.3 Status machine

```
pending ──(drain claims)──▶ scanning ──▶ approved     (policy pass)
                                     ├─▶ rejected     (policy fail, no exception)
                                     └─▶ scan_failed  (scanner error; retry by re-requesting)
approved ──(rescan newly fails)──▶ flagged ──(exception | passing rescan)──▶ approved
rejected ──(admin exception)──▶ approved
any ──(admin revoke)──▶ revoked            (terminal; explicit human decision)
```

- **Stale** is computed, never stored: `last_scanned_at < now() -
  policy.scan_window_days` (30, FedRAMP). Stale blocks **launches** as well
  as deploys (§8.4), unlike `flagged`, because staleness means the scanner
  itself has been broken for a month — the FedRAMP text is "not actively
  deployed". An exception does not cover staleness.
- `flagged` blocks deploys, permits launches (decision 4). `rejected`,
  `revoked`, `pending`, `scanning`, `scan_failed` block both.
- Exception expiry (daily tick): re-evaluate the latest verdict; pass ⇒
  stays `approved`, fail ⇒ `flagged` (never back to `rejected` — it may
  be in use).
- Status strings, scan kinds and scan statuses are pinned in a fixture
  (`image-status.json`, the `push-upload-status.json` style) consumed by
  both suites and by the UI's badge map (the `ALERT_KIND_LABEL` rule: a
  status cannot land unlabelled).

### 4.4 Exceptions

One live exception per image (columns, not a table — YAGNI; every grant is
an `audit_log` row with the reason, so the history is there). `expires_at`
is required and capped by `policy.exception_max_days` (90). Granting is
admin-only and audited (`image.exception`); the reason is free text
shown on the dashboard. Revoking an exception early = `image.revoke` or a
new grant.

No `DELETE` in v1: revoke is the terminal verb, and immutable revisions
may still snapshot the id. Rows of revoked images that nothing references
can be pruned by a later hygiene slice.

## 5. Registry credentials

- A new connection protocol **`registry`**: config `{host}` (e.g.
  `ghcr.io`, `123456789012.dkr.ecr.us-gov-west-1.amazonaws.com`),
  credentials `{username, password}` (a PAT, an ECR token, a robot
  account). It reuses the connections envelope, group ownership, the
  connection form and `connection_checks` (the check is a `GET /v2/` with
  basic/bearer auth — the drain's probe seam). Fixture:
  `registry-connection-config.json`. The `docs/processes.md` secret-ref
  table gains the row (`registry`: `username`, `password`).
- **Egress:** the registry host is a connection host like any other and
  goes through `resolve_pinned` when the pipeline touches it (drift
  HEADs, §8.2); the scanner and the Docker daemon do their own pulls
  (§6.2, §8.4).
- A **deployment-level Docker Hub credential** (`REGISTRY_DOCKERHUB_USER`
  / `_TOKEN`, optional) is used for `docker.io` references that carry no
  group credential, so scans and pulls do not spend the anonymous 100-
  pulls-per-6-hours-per-IP budget that a NAT'd cluster shares. Absent ⇒
  anonymous, and the ISSUES entry records the exposure.

## 6. The scanner

### 6.1 `services/image-scanner/`

A platform-built image, the third after `process-runtime` and
`-stactools`: `python:3.12-slim` + pinned **Syft** and **Grype** binaries
(release-asset download with checksum, versions in one `ARG` block), the
Grype DB **baked at build** (`grype db update` in the Dockerfile, so an
air-gapped deployment has a usable DB the day the image was built), a
non-root user, `scan.py` as entrypoint. Built by `containers.yml` and
`docker-bake.hcl` beside the runtime images, tagged by date so the baked
DB's age is visible. `GRYPE_DB_AUTO_UPDATE` is honoured at scan start
when the deployment allows it (`IMAGE_SCANNER_DB_UPDATE=true`, the dev
default) from `GRYPE_DB_UPDATE_URL` (default Anchore's listing; air-gap
points it at a mirror). The result records the DB build date so the
dashboard can show "DB 9 days old".

### 6.2 A scan is a run

The drain (§8.1) builds a `RunSpec` for the scanner image and launches it
through the **same `Executor`** a process uses: the policy's
`scan_limits` (4 096 MB, 900 s), `network = settings.scanner_network`
(§11), env:

| variable | value |
|---|---|
| `STAC_HIGHER_SCAN_ID`, `STAC_HIGHER_SCAN_KIND` | the row; `admission` or `rescan` |
| `STAC_HIGHER_IMAGE_REF` / `STAC_HIGHER_IMAGE_TAG` | the normalized reference and `tag_at_add` — admission resolves the tag; a rescan HEADs it for drift only |
| `STAC_HIGHER_PLATFORM` | `linux/amd64` from the policy |
| `STAC_HIGHER_MAX_IMAGE_BYTES` | from the policy |
| `STAC_HIGHER_SBOM_KEY` | rescan only: the stored SBOM to rescan |
| `REGISTRY_USERNAME` / `REGISTRY_PASSWORD` | the group credential or the deployment Docker Hub one, **resolved at launch into the scanner's env only** (the secret-ref path) |
| `AWS_*`, `STAC_HIGHER_OUTPUT_BUCKET`, `STAC_HIGHER_OUTPUT_PREFIX` | run-scoped STS credentials for `scans/{image_id}/{scan_id}/` (the ADR 0014 minting path with a scan prefix) |

The scanner never sees the DB, the master key or platform keys — it is
untrusted-adjacent code (it parses hostile image content), and it gets
exactly the process posture.

### 6.3 `scan.py`

Admission:
1. Parse the reference; refuse a registry host outside the policy's
   `allowed_registries` (defence in depth — the app already refused it).
2. Registry v2 **HEAD** on the tag (free on Docker Hub) → index/manifest
   digest; pick the policy platform's manifest; refuse if layer sizes sum
   above `max_image_bytes` (before any blob is fetched). Read the image
   config (`user`, `entrypoint`, `cmd`).
3. `syft registry:{reference}@{platform_digest} -o syft-json` (canonical,
   keeps distro + relationships) and `-o cyclonedx-json` (interop) →
   upload both.
4. `grype sbom:{syft.json} -o json` → upload; derive the summary (§6.4).
5. Write `result.json`; exit 0. Any failure ⇒ `result.json` with `error`
   and exit 1; the drain records `scan_failed` with the message and the
   run's log ref.

Rescan: fetch the stored syft-json, step 4–5 only (no pull), plus a
best-effort HEAD of `tag_at_add` for drift (`tag_current_digest`); a
registry failure on the HEAD is recorded, not fatal.

No secrets/misconfig scan in v1 (they need image bytes and are Trivy's
domain — §13). No `cosign verify` in v1 (§13).

### 6.4 `result.json` — fixture `image-scan-result.json`

```jsonc
{ "version": 1, "kind": "admission",
  "reference": "ghcr.io/org/img", "tag": "1.4.2",
  "digest": "sha256:…", "platform_digest": "sha256:…",
  "platform": { "os": "linux", "architecture": "amd64" },
  "size_bytes": 812345678,
  "config": { "user": "", "entrypoint": ["/entry.sh"], "cmd": [] },
  "scanner": { "syft": "1.x.y", "grype": "0.9x.y", "db_built_at": "2026-09-12T06:00:00Z" },
  "sbom_ref": "scans/{image_id}/{scan_id}/sbom.syft.json",
  "findings_ref": "scans/{image_id}/{scan_id}/findings.grype.json",
  "counts":       { "critical": 0, "high": 3, "medium": 12, "low": 40, "negligible": 5, "unknown": 1 },
  "fixed_counts": { "critical": 0, "high": 1, "medium": 4,  "low": 2,  "negligible": 0, "unknown": 0 },
  "kev": [],
  "max_risk": 0.42,
  "top": [ { "id": "CVE-2026-1234", "severity": "high", "package": "libxml2", "version": "2.12.7",
             "fixed_in": "2.12.9", "kev": false, "epss": 0.31, "risk": 0.42,
             "published_at": "2026-07-01" } /* ≤ 25, by risk desc */ ],
  "tag_drift": null,                        // rescan: { "current_digest": "sha256:…", "drifted": true }
  "error": null }
```

The pipeline is the only consumer; the fixture is consumed by pytest
(the parser) and vitest (the `result` jsonb the UI renders).

## 7. Policy — a deployment document

### 7.1 `PROCESS_IMAGE_POLICY_FILE` — fixture `image-policy.json`

The hardware-profiles pattern: one document read by both runtimes
(in-repo default `infra/image-policy/default.json`; a deployment ships
its own), served read-only to the UI.

```jsonc
{ "version": 1,
  "allowed_registries": ["docker.io", "ghcr.io", "public.ecr.aws", "*.dkr.ecr.*.amazonaws.com"],
  "platform": "linux/amd64",
  "max_image_size_mb": 4096,
  "block": {
    "kev": true,                              // any CISA KEV entry, any severity
    "critical_fixed": true,                   // a CRITICAL with a fix available
    "critical_unfixed_older_than_days": 30,   // an unfixed CRITICAL published more than N days ago (null ⇒ never)
    "high_fixed_epss_at_least": 0.1,          // a fixed HIGH whose EPSS ≥ this (null ⇒ never)
    "high_unfixed": false
  },
  "scan_window_days": 30,
  "rescan_interval_hours": 24,
  "exception_max_days": 90,
  "scan_limits": { "memory_mb": 4096, "timeout_seconds": 900 } }
```

### 7.2 Who enforces what

- **App**: `allowed_registries` on `POST /api/images` (a reference outside
  it is a 422 before any row is written); `exception_max_days` on the
  exception route; the write-gate checks in §3; the UI reads the policy to
  explain a verdict.
- **Pipeline**: `allowed_registries` again before launching a scan;
  `platform`, `max_image_size_mb`, `scan_limits` into the scanner's env
  and `RunSpec`; `block` in `pipeline/images/policy.py` (§7.3);
  `scan_window_days` at launch; `rescan_interval_hours` in the tick.
- Both loaders fail closed: a missing or invalid policy file means no
  image can be added or launched, and `/health` names the file.

### 7.3 `evaluate(result, policy) -> Verdict`

Pure, tested against the fixture's cases: `reasons[]` are stable strings
(`kev:CVE-…`, `critical_fixed:libxml2`, `high_fixed_epss:CVE-…:0.31`,
`critical_unfixed_age:CVE-…:41d`, `image_too_large`, `registry_not_allowed`).
`pass = reasons == []`. The verdict is stored on the image row and inside
`image_scans.result`; the dashboard renders reasons verbatim.

## 8. Pipeline

### 8.1 `pipeline.image_scan_drain` — `* * * * *`

`jobs/image_scans.py`, `drain.py`'s shape: claim pending rows (batch 1 —
scans are heavy), mark `running`, set the image `scanning` (admission
only; a rescan leaves the visible status alone), launch the scanner run,
`wait` with the policy timeout, read `result.json`, evaluate, write
`image_scans.result` + `findings_ref`, and transition the image (§4.3).
On `admission` the row's `reference`, `digest`, `platform_digest`,
`size_bytes`, `config`, `sbom_ref`, `last_scan*` are filled from the
result; on `rescan` only `verdict`, `last_scan*`, `tag_*` and the
`diff`. Scanner run logs go to `log_ref` like a process run
(`process_runs`-shaped capture into `scans/{image_id}/{scan_id}/log`).

**Blocking `wait` until K-4** lands the submit-then-reconcile executor.
A scan holds a worker slot for up to 15 minutes; at concurrency 1 that
stalls ingest — M3-D (concurrency 12, in flight) makes it a slot, not a
stall, and K-4 removes it. Logged as an ISSUES entry; not a reason to
build a second executor shape.

### 8.2 `pipeline.image_rescan_tick` — `15 3 * * *` (daily, after the DB refresh window)

For every `approved` or `flagged` image with `last_scanned_at` older than
`rescan_interval_hours`: INSERT a `rescan` row (`requested_by =
'pipeline'`) unless one is already pending/running. Also expires
exceptions (§4.3). The drain does the work. The **diff** against the
previous scan is computed by the drain when it records a rescan:
`{new: [ids], resolved: [ids], newly_fixed: [ids], verdict_changed}` —
the alert and the notification key off the diff, never off the raw counts
(alert-fatigue rule: new CRITICAL/HIGH, new KEV, or a verdict flip).
Drift (`tag_current_digest != digest`) is **informational**: shown on the
dashboard ("tag `1.4` now points elsewhere; you run the scanned digest"),
never an auto-upgrade — re-adding the reference is a new image row.

### 8.3 Retention

`scans/{image_id}/…` keeps the **latest SBOM pair** and the **last ten**
findings files + logs; the hourly `history_retention` sweep gains a leg
that prunes older scan objects **before** their rows, the process-run-log
precedent. `image_scans` rows older than `PROCESS_RUN_RETENTION_DAYS`
are pruned except each image's `last_scan_id`. Nothing goes through
`asset_gc` — these are not catalog assets (ADR 0011 is about bytes the
catalog references).

### 8.4 Launch path

`launch.py`'s `resolve_runtime_image` becomes `resolve_run_image(runtime,
settings, repo)`:

- kind 1: unchanged (alias → `PROCESS_RUNTIME_IMAGE*`).
- kinds 2, 3: load the `container_images` row by `runtime.image.id`;
  **dead run** (`ImageUnusable`, the `NetworkCapExceeded` outcome, reason
  in `process_runs.error`) when the row is missing, `reference`/`digest`
  differ from the snapshot, status ∉ {`approved`, `flagged`}, or the row
  is stale. Then **pull by digest** through the Engine API
  (`POST /images/create?fromImage={reference}&tag={digest}` with
  `X-Registry-Auth` from the group credential / deployment Docker Hub
  credential, resolved at launch), skipped when the daemon already has
  the digest. `RunSpec.image = "{reference}@{digest}"`; kind 2 sets the
  bootstrap entrypoint (§3.1); kind 3 sets `Cmd = command` when present.
  The executor forces `User: 10001:10001` for kinds 2 and 3 (§3.2).
- The pull is the daemon's egress, not the pipeline's: the host's Docker
  daemon fetches from the registry. On Kubernetes it is the kubelet's,
  with an `imagePullSecret` (§12).

Fixture `process-runtime.json` cases cover every dead-run reason on the
pipeline side (a `container` revision with a snapshot the reader accepts
but whose row is absent is a runtime error, not a parse error — the
fixture pins the parse, the pytest for `resolve_run_image` pins the rest).

### 8.5 Socket proxy

`docker-socket-proxy` gains **`IMAGES=1`** (with `POST=1` already on).
Residual risk, recorded in ADR 0021 and `docs/backend.md`: the pipeline
can now pull and delete arbitrary images on the daemon. Mitigations: the
proxy is reachable only from the pipeline (unchanged), the executor is
its only client, and every pull is by a digest that exists as an
`approved`/`flagged` row. No `NETWORKS`, `VOLUMES`, `EXEC` change.

## 9. App

### 9.1 Routes (`docs/backend.md` route table; gates in `lib/authz/permissions.ts`)

| route | access | audit | notes |
|---|---|---|---|
| `GET /api/images` | member | — | list with `status`, `q`, `in_use` filters; `in_use_by` count per row |
| `POST /api/images` | operator | `image.add` | `{reference, tag?, registry_connection_id?}`; normalizes the reference, checks `allowed_registries` and the connection's group, INSERTs a `pending` row with `digest` null (the app never touches a registry — resolution is the scanner's job, §6.3) and an `admission` scan, returns **202** `{image_id, scan_id}`. **Dedup by digest happens in the drain:** if the resolved `(reference, digest)` already exists, the drain re-points the scan row's `image_id` at the existing row, records the scan against it (as a rescan), then deletes the provisional row; the client follows the scan id from the 202, whose `image_id` is authoritative once the scan is `done` |
| `GET /api/images/[id]` | member | — | row + last ten scans + `in_use_by: [{process_id, name, group}]` |
| `GET /api/images/[id]/scans/[scanId]` | member | — | full `result` + a presigned/proxied read of `findings_ref` |
| `POST /api/images/[id]/rescan` | operator | `image.rescan` | INSERTs a `rescan` row; 409 if one is pending |
| `POST /api/images/[id]/exception` | admin | `image.exception` | `{reason, expires_at}`; `rejected`/`flagged` → `approved` |
| `POST /api/images/[id]/revoke` | admin | `image.revoke` | any → `revoked` |
| `GET /api/processes/image-policy` | member | — | the policy document, `scan_limits` stripped |
| `GET /api/internal/images/approved?digest=` | internal (network-gated, no session) | — | `{approved: bool}` for a cluster admission policy (§12); built in C-3 because it is ten lines, wired in K-6 |

Every mutation writes one `audit_log` row (RBAC invariant). Credentials
never leave the envelope: the response never carries the connection's
secret, only its id and label.

### 9.2 `/images` — the dashboard

`app/src/pages/images.astro` + `app/src/components/images/ImagesPage.tsx`
(new-page pattern; `SidebarNav` entry "Images" in the processes group).
A table: reference + tag, short digest with copy, status badge (fixture-
driven labels), severity counts as a compact stack with KEV called out,
"DB age"/last scanned, in-use-by count, exception expiry, drift marker.
Filters: status, in use, search. Row → a detail sheet: top findings
(risk-sorted, KEV first, with fixed-in), the policy reasons verbatim, scan
history with diffs ("+2 high, −1 medium since 2026-09-12"), the image
config warning (§3.2), the exception form (admin) and Rescan now
(operator). "Add image" opens the same dialog as the deploy form (§9.3)
and polls the scan (the test-run polling pattern).

Overview (`overview.ts`) gains one derivation: the count of `flagged` +
stale images in use, surfaced with the process-health verdicts so a
flagged image shows where its processes show.

### 9.3 Deploy form — the Lambda-style chooser

`CodeCard` gains a **Runtime** fieldset above the editor:

- **Platform image** (default) — surfaces the existing `runtime_image`
  alias as a select for the first time (`default`, `stactools`).
- **Custom image + your code** (kind 2) — an image picker (search over
  `approved` images the process's group may use; `flagged`/stale ones
  listed disabled with the reason) and the editor stays.
- **Container image** (kind 3) — the same picker; the editor hides; an
  optional Command field (space-separated, quoted, shown as the array it
  becomes).

The picker's "Add image…" dialog: reference, optional registry connection
(the group's `registry` connections), submit → 202 → live status until
`approved`/`rejected` (then selectable, or the reasons + "ask an admin for
an exception"). The current revision's kind, image and command sync into
the form (K-2 makes the same fix for hardware). Run rows show the pinned
image short digest. `docs/processes.md` gains "Bring your own image".

## 10. Alerts and notifications

- New kind **`process_image_flagged`**, anchored on `process_id` — one
  open alert per process whose CURRENT revision references an image that
  is `flagged`, `revoked` or stale, message naming the image, the reasons
  and the diff. Raised by the drain/tick (`pipeline/images/alerts.py`,
  the `sync_alerts` upsert against process anchors), auto-resolved when
  the image returns to `approved` or the process moves off it. An image
  nobody uses shows only on the dashboard.
- C-1 adds the kind to `alert-kinds.json` **`declared_kinds`** (the
  waiting room, empty today) and `ALERT_KIND_LABEL`; C-4 moves it to a
  writer set. Notification routing treats it like `process_failed`.
- Health: `health.ts`'s process verdict treats an open
  `process_image_flagged` as degraded (not failing) — the A-1 pattern of
  gating on alert-list completeness holds.

## 11. Networks and egress (compose)

- New network **`scanner-egress`** (bridge, NOT `internal`): the scanner
  container's `NetworkMode`; MinIO is attached to it as it is to
  `process-runs` so results can be uploaded. The socket proxy is not on
  it. `PROCESS_SCANNER_NETWORK` names it.
- Host-level filtering of that egress is not possible on a Docker
  network; the policy's `allowed_registries` is enforced twice in
  software (§7.2) and the scanner is the only thing on the network. On
  Kubernetes it is a NetworkPolicy with FQDN egress, the K8s spec §12
  caveat (CNI-dependent). ISSUES records the residual.
- The Docker daemon's pull (§8.4) uses the host's network; in cloud, the
  node's. Neither is pipeline egress.

## 12. Kubernetes addendum (K queue, not built here)

For K-5/K-6 when they reach the image branch: `image: reference@digest`,
`imagePullPolicy: IfNotPresent` (safe once the ref is a digest), a per-run
`kubernetes.io/dockerconfigjson` Secret from the group credential
(deleted at reap with the env Secret), `runAsUser: 10001`, and a Kyverno
`ImageValidatingPolicy` that calls `GET /api/internal/images/approved`
(`apiCall.service`) as defence in depth for FedRAMP's "mechanism to
restrict … from successfully deploying". In GovCloud, an **ECR pull-
through cache** for Docker Hub/GHCR/Quay with Inspector continuous
scanning as a second opinion is the recorded deployment shape; the
platform's registry stays the inventory of record (30-day window per
image), which neither ECR nor Harbor provides as such.

## 13. What this spec explicitly does NOT do

- Signature or provenance verification (`cosign verify`, SLSA
  attestations). Recorded as the next step once a platform-controlled
  registry exists; for third-party Docker Hub content the signer is
  usually absent, so gating on it would refuse most images.
- Secret and misconfiguration scanning of image contents (Trivy's
  domain; needs bytes on every scan). A second engine at admission is the
  documented extension.
- Mirroring image bytes into a platform registry (Harbor, ECR pull-
  through). Cloud deployment shape only (§12).
- Per-image resource hints, image build service, or "build from
  Dockerfile in the UI".
- Deleting image rows (§4.4). Network levels above `isolated` (ADR 0018
  unchanged). GPU images beyond what K-7 ships (a user CUDA image is
  just an image here; the profile decides the node).

## 14. Decisions taken by the agent (stand unless overturned)

1. Runs on a user image are forced to uid 10001 (§3.2) rather than
   honouring the image's `USER` — the Kubernetes shape already mandates
   non-root and one rule is easier to accredit than two.
2. Stale blocks launches; flagged does not (§4.3) — the FedRAMP window is
   the one hard line in the source material.
3. Exceptions are columns with an audit trail, not a table (§4.4).
4. `command` overrides `Cmd` only, never `Entrypoint` (§3) — an
   entrypoint override is how an image's own hardening gets bypassed.
5. The Grype DB is baked at build with optional refresh at scan start
   (§6.1) — no volume, no long-running scanner service, air-gap works by
   rebuilding the image.
6. Rescan rows are inserted by the pipeline itself (§4.2) so admission
   and rescan share one ledger and one drain.
7. Policy blocks: KEV, fixed CRITICAL, unfixed CRITICAL older than 30 d,
   fixed HIGH with EPSS ≥ 0.1 (§7.1) — BOD 26-04's exploitability-first
   shape rather than a flat severity threshold.
8. The Docker Hub deployment credential is optional (§5); anonymous is
   allowed and its exposure logged.
9. A new `registry` connection protocol rather than a new credential
   table (§5) — the envelope, group ownership, form and check probe are
   all already there.

## 15. Gate and slices — the C queue

**Gate (C-5, lead-only, live):** on a wiped stack, (1) add
`docker.io/library/python:3.12-slim` through the UI, watch it scan and
land `approved` or `rejected` with reasons; (2) grant an exception if
rejected; (3) add the platform runtime image itself by its GHCR reference
(`ghcr.io/…-process-runtime:<tag>`, what `containers.yml` pushes), and
redeploy the GOES GeoColor process as kind 2 on it with the code
unchanged — the same code, now on a digest-pinned user image — and see
it run to a published item; (4) add a deliberately vulnerable public image and see it
`rejected` with a KEV or CRITICAL reason; (5) force a rescan with a
policy tightened in the file and watch an in-use image go `flagged`, the
process alert fire, a new deploy be refused, and a triggered run still
launch; (6) `docker image ls` shows only digest-pinned pulls.

- **C-1 · Contracts + migration 030 + write gate.** §3, §4, §7.1, §10.
  Fixtures `process-runtime.json` (third variant, container cases flipped
  to accept/accept with the snapshot shape, `runtime_image: null`
  rule, `command` cases), `image-status.json`, `image-policy.json`,
  `image-scan-result.json`, `registry-connection-config.json`;
  `alert-kinds.json` `declared_kinds` + label. Zod: three kinds, image
  snapshot, command; `processRevisionCreateSchema` code rule; the DB-
  backed gate in the revisions route with its four 422 reasons. Python:
  `ProcessRuntime` gains `image_id/image_reference/image_digest/command`,
  the policy loader, `evaluate()`, status constants. Migration 030.
  `registry` protocol on both sides + the check probe. `docs/processes.md`
  "Bring your own image", `docs/backend.md` env. No executor change; no
  UI (the gate refuses everything until C-2 approves something).
- **C-2 · Scanner image + drain + launch path.** §6, §8.1, §8.4, §8.5,
  §11. `services/image-scanner/` (+ `containers.yml`, bake file),
  `jobs/image_scans.py` drain, `pipeline/images/{policy,repo,alerts}.py`,
  scan-prefix STS minting, `resolve_run_image`, Engine-API pull by
  digest, forced uid + bootstrap entrypoint in `DockerExecutor`,
  `IMAGES=1`, `scanner-egress` network, `PROCESS_SCANNER_NETWORK`,
  `REGISTRY_DOCKERHUB_*`, `/health` policy key. Depends on C-1.
- **C-3 · API + dashboard + deploy form.** §9. Routes, permissions,
  audit actions, `/images` page + island, `SidebarNav`, the Runtime
  chooser and image picker in `CodeCard`, run-row digest, overview
  derivation, `GET /api/internal/images/approved`. Depends on C-1 (can
  run parallel to C-2; the UI polls a scan that C-2 makes real).
- **C-4 · Rescans, drift, alerts, exceptions, retention.** §4.3, §4.4,
  §8.2, §8.3, §10. `image_rescan_tick`, diff, drift HEAD, exception
  expiry, the alert writer (kind moves out of `declared_kinds`),
  notification routing, the retention leg, the dashboard's history/diff
  and exception form wiring. Depends on C-2 and C-3.
- **C-5 · Live gate (LEAD ONLY).** The gate above; records measured scan
  durations and memory for `python:3.12-slim` and one ~1 GB image in this
  spec's addendum; closes or re-scopes the ISSUES entries. Depends on
  C-4.

Migration: **030** (029 is K-3's). ADR: **0021**. No new npm
dependency; the pipeline gains no Python dependency (Syft/Grype live in
the scanner image). Docs: `processes.md`, `backend.md`, `connections.md`
(the `registry` row), `monitoring.md` (the kind), `FEATURES.md` rows on
merge, `ISSUES.md` entries I-122…I-125 (written with this spec).
