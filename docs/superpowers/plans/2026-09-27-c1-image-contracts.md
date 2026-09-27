# C-1 · Image Contracts, Migration 030 and the Write Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the contract and the database ready for user-supplied process images: `runtime.kind` becomes `inline_python | inline_python_on_image | container` with an immutable `{id, reference, digest}` image snapshot, migration 030 lands `container_images` + `image_scans`, the revisions route refuses a user image unless a DB row says it is approved, fresh, digest-equal and group-usable, and both runtimes share the policy document, the scan-result shape, the status vocabulary and a new `registry` connection protocol. Nothing runs a user image yet.

**Architecture:** Every new shape is a cross-runtime contract pinned by a golden fixture in `tests/contract-fixtures/` and consumed by both suites (the Zod writer is strict, the Python reader lenient on unknown keys, the established direction). The app gets a new domain directory `app/src/lib/images/` (reference grammar, status vocabulary, policy loader, scan-result reader, gate) and the pipeline a new package `pipeline/images/` (reference grammar, status constants, policy loader + `evaluate()`, scan-result parser). The write gate is the `PROCESS_NETWORK_MAX` dual-enforcement pattern: the Zod shape accepts all three kinds, and the revisions route runs a DB-backed check (`checkImageGate`) that answers 422 with one of four reason codes. The gate refuses everything today because no scanner exists to approve a row (C-2). The pipeline launch path dies any kind 2/3 run naming ADR 0021 until C-2 replaces that guard with the real digest-pinned check. The `registry` protocol reuses the connections envelope, form, group ownership and `connection_checks` drain; its probe is `GET /v2/` with Basic auth plus the Bearer token exchange.

**Tech Stack:** Astro 7 API routes + Zod v4 + vitest (app); Python 3.12 stdlib + pytest + ruff (pipeline); migration in `app/src/lib/db/migrate.ts`; JSON golden fixtures.

**Spec:** `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` §3, §4, §5, §7.1–§7.3, §10 (C-1 slice text in §15); ADR `docs/decisions/0021-user-images-scan-then-approve.md`; epic #56; issue #50.

## Global Constraints

- **Worktree:** `.claude/worktrees/c1-image-contracts`, branch `feat/c1-image-contracts` (GitHub issue #50, epic #56). Already created off a fresh `main` with `npm install` done. Never work in the main checkout.
- **Gates:** every task ends with `npm run verify` from the worktree root. Any task that touches `services/pipeline/` also runs `cd services/pipeline && uv run pytest -q && uv run ruff check .`. Teammates never run e2e, the dev server, Docker (image builds included), or push.
- **Migration 030** (`030_container_images`). 029 is reserved for K-3 (#11) and is NOT on `main`. **Do not use 029.** Append 030 after the `028_builtin_processes` entry in `MIGRATIONS`. The pipeline runs no DDL (ADR 0001).
- **Fixture rule** (`backend-invariants`): a fixture changes in the same commit as BOTH runtimes' schemas, and both `app/src/__tests__/contract-fixtures.test.ts` and `services/pipeline/tests/test_contract_fixtures.py` consume every new file. Each new fixture gets a row or section in `tests/contract-fixtures/README.md` in the same commit.
- **Runtime kinds (spec §3), verbatim:** `inline_python | inline_python_on_image | container`. `image` is `{id, reference, digest}` for kinds 2 and 3 and `null`/absent for kind 1. `runtime_image` is required-absent (`null`) for kinds 2 and 3. `command` exists only on kind 3: `string[]`, non-empty when present, each element non-blank, max 64 entries. `code` is required for kinds 1 and 2 and refused for kind 3.
- **Gate reasons (spec §3), verbatim:** `image_not_approved`, `image_stale`, `image_group_mismatch`, `image_digest_mismatch`. The refusal is HTTP **422** with body `{ error, code }`, where `code` is the reason.
- **Image statuses (spec §4.1):** `pending | scanning | approved | rejected | flagged | revoked | scan_failed`. Scan kinds: `admission | rescan`. Scan statuses: `pending | running | done | failed`.
- **Policy document (spec §7.1)** is `infra/image-policy/default.json`, read through `PROCESS_IMAGE_POLICY_FILE` (unset ⇒ the checkout's default). Both loaders fail closed.
- **No executor change** (`docker_executor.py` is untouched). **No new UI screen.** The only UI edits are the connection form's typed protocol maps and a one-line filter in the two data-flow dialogs (Task 6). **No new npm or Python dependency.**
- **Structured logging (pipeline):** data goes in `extra={...}` and messages stay constant. Never log or echo a credential.
- **Commit trailer.** Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## Review Focus

These are the inputs a person will meet that the spec implies but no happy-path test exercises. Each line names the owning task, and that task carries the test.

1. **A kind-1 deploy on a deployment whose policy file is missing or broken must still work.** Existing processes must not start failing because the image policy is misconfigured. `checkImageGate(null, …)` never loads the policy (Task 4 test "inline revisions never read the policy").
2. **A snapshot naming an image that is still `pending` (its `digest` is NULL) must say "not approved", not "digest mismatch".** The operator needs to hear "wait for the scan", not "your revision is corrupt" (Task 4 test "a pending image with no digest yet is not approved").
3. **The staleness boundary and the never-scanned image.** `last_scanned_at` exactly `scan_window_days` ago is still fresh, one millisecond older is stale, and NULL is stale (Task 4 tests).
4. **An image whose registry credential was soft-deleted must be refused, not treated as public.** The LEFT JOIN filters deleted connections, so the group comes back NULL and the answer is `image_group_mismatch` (Task 4 test "a soft-deleted registry credential").
5. **A registry probe must never echo the password or token.** That holds for every failure branch, including an egress block and a rejected credential (Task 6 test "no probe message ever carries the secret").

---

### Task 0: Precondition (read-only)

- [ ] From the worktree root, run each command and check its result. If any check fails, STOP and report:
  - `git branch --show-current` prints `feat/c1-image-contracts`.
  - `grep -n 'name: "' app/src/lib/db/migrate.ts | tail -1` shows `028_builtin_processes`, and `grep -c '"029_' app/src/lib/db/migrate.ts` prints `0`.
  - `grep -rn "container_images" app/src services/pipeline/src` prints nothing.
  - `grep -n "CONTAINER_RUNTIME_REFUSAL" app/src/lib/processes/schemas.ts` shows the constant (it is replaced in Task 5).

---

### Task 1: Image vocabulary, reference grammar and migration 030

**Files:**
- Create: `tests/contract-fixtures/image-status.json`, `tests/contract-fixtures/image-reference.json`
- Create: `app/src/lib/images/status.ts`, `app/src/lib/images/reference.ts`
- Create: `services/pipeline/src/pipeline/images/__init__.py`, `services/pipeline/src/pipeline/images/status.py`, `services/pipeline/src/pipeline/images/reference.py`
- Modify: `app/src/lib/db/migrate.ts` (append `030_container_images` after the 028 entry)
- Modify: `app/src/__tests__/contract-fixtures.test.ts`, `services/pipeline/tests/test_contract_fixtures.py`, `tests/contract-fixtures/README.md`
- Test: `app/src/__tests__/images-migration.test.ts` (new)

**Interfaces:**
- Produces (TS, `@/lib/images/status`): `IMAGE_STATUSES`, `ImageStatus`, `DEPLOY_IMAGE_STATUSES`, `LAUNCH_IMAGE_STATUSES`, `IMAGE_SCAN_KINDS`, `ImageScanKind`, `IMAGE_SCAN_STATUSES`, `IMAGE_GATE_REASONS`, `ImageGateReason`, `IMAGE_STATUS_LABEL: Record<ImageStatus, string>`.
- Produces (TS, `@/lib/images/reference`): `IMAGE_REFERENCE_RE`, `IMAGE_DIGEST_RE`, `isImageReference(v: string): boolean`, `isImageDigest(v: string): boolean`, `registryHost(reference: string): string`, `imageReferenceSchema`, `imageDigestSchema`, `imageSnapshotSchema`, `type ImageSnapshot = { id: string; reference: string; digest: string }`.
- Produces (Python): `pipeline.images.status.{IMAGE_STATUSES, DEPLOY_STATUSES, LAUNCH_STATUSES, SCAN_KINDS, SCAN_STATUSES, GATE_REASONS}` (tuples), and `pipeline.images.reference.{is_image_reference, is_image_digest, registry_host, IMAGE_REFERENCE_MAX_LENGTH}`.
- Produces (DB): `stac_higher.container_images`, `stac_higher.image_scans`, `connections_protocol_check` admitting `registry`, and the expression index `process_revisions_image_id_idx`.

- [ ] **Step 1: The two fixtures**

`tests/contract-fixtures/image-status.json`:
```json
{
  "style": "pinned-enum",
  "$comment": "C-1 (container-images spec §4.3, ADR 0021): the closed vocabularies of stac_higher.container_images.status, image_scans.kind and image_scans.status, plus the four reasons the deploy gate refuses a user image with (HTTP 422, body {error, code}). Consumed by app/src/__tests__/contract-fixtures.test.ts (app/src/lib/images/status.ts, whose IMAGE_STATUS_LABEL must cover every status) and by services/pipeline/tests/test_contract_fixtures.py (pipeline/images/status.py); app/src/__tests__/images-migration.test.ts pins migration 030's CHECK lists against the same arrays. `deploy_statuses` are the statuses a NEW revision may snapshot. `launch_statuses` are the statuses a triggered run may still launch on: flagged blocks deploys, never runs (spec decision 4). Stale is computed from last_scanned_at and the policy's scan_window_days, never stored, so it is not a status.",
  "image_statuses": ["pending", "scanning", "approved", "rejected", "flagged", "revoked", "scan_failed"],
  "deploy_statuses": ["approved"],
  "launch_statuses": ["approved", "flagged"],
  "scan_kinds": ["admission", "rescan"],
  "scan_statuses": ["pending", "running", "done", "failed"],
  "gate_reasons": ["image_not_approved", "image_stale", "image_group_mismatch", "image_digest_mismatch"]
}
```

`tests/contract-fixtures/image-reference.json`:
```json
{
  "style": "grammar-cases",
  "$comment": "C-1 (container-images spec §3/§4.1): the grammar of a NORMALIZED image reference (registry/namespace/name: lowercase, an explicit registry host, no tag, no digest, at most 255 characters) and of a manifest digest (sha256 only, lowercase hex). Every snapshot, container_images row and scan result uses them. Normalizing what a person types (a bare `python:3.12` becoming `docker.io/library/python`) is C-3's POST /api/images; these cases pin only what may be STORED. Consumed by app/src/lib/images/reference.ts and pipeline/images/reference.py.",
  "cases": [
    { "name": "GHCR repository", "value": "ghcr.io/example/satpy-runtime", "reference": true },
    { "name": "Docker Hub official image, normalized", "value": "docker.io/library/python", "reference": true },
    { "name": "ECR private registry", "value": "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/tool", "reference": true },
    { "name": "ECR public", "value": "public.ecr.aws/docker/library/alpine", "reference": true },
    { "name": "registry with a port", "value": "registry.example.com:5000/team/tool", "reference": true },
    { "name": "localhost registry", "value": "localhost:5000/tool", "reference": true },
    { "name": "separators inside a component", "value": "ghcr.io/example/satpy_runtime-v2.x", "reference": true },
    { "name": "bare name (not normalized)", "value": "python", "reference": false },
    { "name": "namespace/name without a registry host", "value": "library/python", "reference": false },
    { "name": "a tag (a tag is resolved, never stored)", "value": "ghcr.io/example/proc:1.2.3", "reference": false },
    { "name": "a digest suffix (the digest is its own field)", "value": "ghcr.io/example/proc@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "reference": false },
    { "name": "uppercase", "value": "ghcr.io/Example/proc", "reference": false },
    { "name": "a scheme", "value": "https://ghcr.io/example/proc", "reference": false },
    { "name": "a trailing slash", "value": "ghcr.io/example/proc/", "reference": false },
    { "name": "empty", "value": "", "reference": false }
  ],
  "digest_cases": [
    { "name": "sha256, lowercase", "value": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "digest": true },
    { "name": "uppercase hex", "value": "sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "digest": false },
    { "name": "too short", "value": "sha256:abc", "digest": false },
    { "name": "sha512 (not accepted: every registry addresses manifests by sha256)", "value": "sha512:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "digest": false },
    { "name": "no algorithm", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "digest": false },
    { "name": "a tag", "value": "latest", "digest": false }
  ]
}
```

- [ ] **Step 2: Write the failing fixture consumers**

In `app/src/__tests__/contract-fixtures.test.ts`, add these imports beside the others:
```ts
import {
  DEPLOY_IMAGE_STATUSES,
  IMAGE_GATE_REASONS,
  IMAGE_SCAN_KINDS,
  IMAGE_SCAN_STATUSES,
  IMAGE_STATUSES,
  IMAGE_STATUS_LABEL,
  LAUNCH_IMAGE_STATUSES,
  type ImageStatus,
} from "@/lib/images/status";
import { isImageDigest, isImageReference } from "@/lib/images/reference";
```
Append at the end of the file:
```ts
// ---------------------------------------------------------------------------
// C queue, C-1 (container-images spec §3/§4): the image vocabularies and the
// reference/digest grammar.
// ---------------------------------------------------------------------------

describe("image status vocabularies (tests/contract-fixtures/image-status.json)", () => {
  const fixture = loadFixture("image-status.json") as unknown as {
    image_statuses: string[];
    deploy_statuses: string[];
    launch_statuses: string[];
    scan_kinds: string[];
    scan_statuses: string[];
    gate_reasons: string[];
  };

  it("pins every vocabulary verbatim, order included", () => {
    expect(fixture.image_statuses).toEqual([...IMAGE_STATUSES]);
    expect(fixture.deploy_statuses).toEqual([...DEPLOY_IMAGE_STATUSES]);
    expect(fixture.launch_statuses).toEqual([...LAUNCH_IMAGE_STATUSES]);
    expect(fixture.scan_kinds).toEqual([...IMAGE_SCAN_KINDS]);
    expect(fixture.scan_statuses).toEqual([...IMAGE_SCAN_STATUSES]);
    expect(fixture.gate_reasons).toEqual([...IMAGE_GATE_REASONS]);
  });

  it("deploy ⊆ launch ⊆ statuses: flagged launches but does not deploy", () => {
    for (const s of fixture.deploy_statuses) expect(fixture.launch_statuses).toContain(s);
    for (const s of fixture.launch_statuses) expect(fixture.image_statuses).toContain(s);
    expect(fixture.launch_statuses).toContain("flagged");
    expect(fixture.deploy_statuses).not.toContain("flagged");
  });

  it("labels every status, so a status cannot land unlabelled (the ALERT_KIND_LABEL rule)", () => {
    for (const status of fixture.image_statuses) {
      expect(IMAGE_STATUS_LABEL[status as ImageStatus], status).toBeTruthy();
    }
  });
});

describe("image reference grammar (tests/contract-fixtures/image-reference.json)", () => {
  const fixture = loadFixture("image-reference.json") as unknown as {
    cases: { name: string; value: string; reference: boolean }[];
    digest_cases: { name: string; value: string; digest: boolean }[];
  };
  it.each(fixture.cases)("reference — $name", ({ value, reference }) => {
    expect(isImageReference(value)).toBe(reference);
  });
  it.each(fixture.digest_cases)("digest — $name", ({ value, digest }) => {
    expect(isImageDigest(value)).toBe(digest);
  });
});
```

In `services/pipeline/tests/test_contract_fixtures.py`, add beside the other `_load` constants:
```python
IMAGE_STATUS = _load("image-status.json")
IMAGE_REFERENCE = _load("image-reference.json")
```
Append:
```python
# ---------------------------------------------------------------------------
# C queue, C-1: the image vocabularies and the reference/digest grammar.
# ---------------------------------------------------------------------------


def test_image_status_vocabularies_match_golden():
    from pipeline.images import status

    assert IMAGE_STATUS["image_statuses"] == list(status.IMAGE_STATUSES)
    assert IMAGE_STATUS["deploy_statuses"] == list(status.DEPLOY_STATUSES)
    assert IMAGE_STATUS["launch_statuses"] == list(status.LAUNCH_STATUSES)
    assert IMAGE_STATUS["scan_kinds"] == list(status.SCAN_KINDS)
    assert IMAGE_STATUS["scan_statuses"] == list(status.SCAN_STATUSES)
    assert IMAGE_STATUS["gate_reasons"] == list(status.GATE_REASONS)


@pytest.mark.parametrize("case", IMAGE_REFERENCE["cases"], ids=lambda c: c["name"])
def test_image_reference_grammar(case):
    from pipeline.images.reference import is_image_reference

    assert is_image_reference(case["value"]) is case["reference"]


@pytest.mark.parametrize("case", IMAGE_REFERENCE["digest_cases"], ids=lambda c: c["name"])
def test_image_digest_grammar(case):
    from pipeline.images.reference import is_image_digest

    assert is_image_digest(case["value"]) is case["digest"]
```

- [ ] **Step 3: Run both suites and watch them fail**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts`
Expected: FAIL (`Cannot find module '@/lib/images/status'`).
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py -q -k image`
Expected: FAIL (`ModuleNotFoundError: No module named 'pipeline.images'`).

- [ ] **Step 4: The TS modules**

`app/src/lib/images/status.ts`:
```ts
/**
 * The container-image vocabularies (C-1, container-images spec §4.3, ADR 0021).
 * A cross-runtime contract: `tests/contract-fixtures/image-status.json` pins
 * every list against `pipeline/images/status.py` and against migration 030's
 * CHECK constraints. Stale is not a status. It is computed from
 * `last_scanned_at` and the policy's `scan_window_days`, and never stored.
 */
export const IMAGE_STATUSES = [
  "pending",
  "scanning",
  "approved",
  "rejected",
  "flagged",
  "revoked",
  "scan_failed",
] as const;
export type ImageStatus = (typeof IMAGE_STATUSES)[number];

/** The statuses a NEW revision may snapshot (spec §4.3). */
export const DEPLOY_IMAGE_STATUSES = ["approved"] as const;
/** The statuses a triggered run may still launch on. Flagged blocks deploys,
 * never runs (spec decision 4). */
export const LAUNCH_IMAGE_STATUSES = ["approved", "flagged"] as const;

export const IMAGE_SCAN_KINDS = ["admission", "rescan"] as const;
export type ImageScanKind = (typeof IMAGE_SCAN_KINDS)[number];
/** The `connection_checks` shape (spec §4.2). */
export const IMAGE_SCAN_STATUSES = ["pending", "running", "done", "failed"] as const;

/** The deploy gate's four 422 reasons (spec §3). */
export const IMAGE_GATE_REASONS = [
  "image_not_approved",
  "image_stale",
  "image_group_mismatch",
  "image_digest_mismatch",
] as const;
export type ImageGateReason = (typeof IMAGE_GATE_REASONS)[number];

/** Badge labels for C-3's dashboard. The fixture consumer asserts this covers
 * every status, so a new status cannot land unlabelled. */
export const IMAGE_STATUS_LABEL: Record<ImageStatus, string> = {
  pending: "Waiting for scan",
  scanning: "Scanning",
  approved: "Approved",
  rejected: "Rejected",
  flagged: "Flagged",
  revoked: "Revoked",
  scan_failed: "Scan failed",
};
```

`app/src/lib/images/reference.ts`:
```ts
/**
 * The image reference + digest grammar (C-1, container-images spec §3/§4.1),
 * pinned by `tests/contract-fixtures/image-reference.json` against
 * `pipeline/images/reference.py`.
 *
 * A STORED reference is normalized. It is lowercase, carries an explicit
 * registry host (a dotted name or `localhost`, optional port), has at least
 * one path component, and has no tag and no digest, because the tag is
 * resolved once and the digest is its own field. Normalizing what a person
 * types is C-3's job. This module only says what may be stored.
 */
import { z } from "zod";

const LABEL = "[a-z0-9](?:[a-z0-9-]*[a-z0-9])?";
const HOST = `(?:localhost|${LABEL}(?:\\.${LABEL})+)(?::[0-9]{1,5})?`;
const COMPONENT = "[a-z0-9]+(?:(?:\\.|_|__|-+)[a-z0-9]+)*";

export const IMAGE_REFERENCE_RE = new RegExp(`^${HOST}(?:/${COMPONENT})+$`);
export const IMAGE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
export const IMAGE_REFERENCE_MAX_LENGTH = 255;

export function isImageReference(value: string): boolean {
  return value.length <= IMAGE_REFERENCE_MAX_LENGTH && IMAGE_REFERENCE_RE.test(value);
}

export function isImageDigest(value: string): boolean {
  return IMAGE_DIGEST_RE.test(value);
}

/** The registry host of a normalized reference (its first component). */
export function registryHost(reference: string): string {
  return reference.split("/", 1)[0];
}

export const imageReferenceSchema = z
  .string()
  .refine(
    isImageReference,
    "image.reference must be a normalized repository (registry/namespace/name: lowercase, no tag, no digest)",
  );

export const imageDigestSchema = z
  .string()
  .refine(isImageDigest, "image.digest must be a sha256 manifest digest (sha256:<64 lowercase hex>)");

/** The immutable snapshot a revision carries of a `container_images` row
 * (spec §3). A later rescan, revocation or deletion never rewrites it. */
export const imageSnapshotSchema = z
  .object({
    id: z.string().uuid("image.id must be a container image id"),
    reference: imageReferenceSchema,
    digest: imageDigestSchema,
  })
  .strict();

export type ImageSnapshot = z.infer<typeof imageSnapshotSchema>;
```

- [ ] **Step 5: The Python modules**

`services/pipeline/src/pipeline/images/__init__.py`:
```python
"""User-supplied process images (C queue, ADR 0021): the reference grammar,
the status vocabulary, the policy document and the scan-result contract. The
pipeline reads and writes ``container_images`` / ``image_scans`` rows and
never creates them (ADR 0001)."""
```

`services/pipeline/src/pipeline/images/status.py`:
```python
"""The container-image vocabularies (C-1, container-images spec §4.3).

Pinned by ``tests/contract-fixtures/image-status.json`` against
``app/src/lib/images/status.ts`` and migration 030's CHECK constraints. Stale is
not a status: it is computed from ``last_scanned_at`` and the policy's
``scan_window_days``.
"""

from __future__ import annotations

IMAGE_STATUSES = (
    "pending",
    "scanning",
    "approved",
    "rejected",
    "flagged",
    "revoked",
    "scan_failed",
)
#: A NEW revision may snapshot only these.
DEPLOY_STATUSES = ("approved",)
#: A triggered run may still launch on these (flagged blocks deploys, never runs).
LAUNCH_STATUSES = ("approved", "flagged")
SCAN_KINDS = ("admission", "rescan")
SCAN_STATUSES = ("pending", "running", "done", "failed")
#: The app's deploy-gate reasons. C-2's launch path reports the same strings.
GATE_REASONS = (
    "image_not_approved",
    "image_stale",
    "image_group_mismatch",
    "image_digest_mismatch",
)
```

`services/pipeline/src/pipeline/images/reference.py`:
```python
"""The image reference + digest grammar (C-1, container-images spec §3/§4.1).

Pinned by ``tests/contract-fixtures/image-reference.json`` against
``app/src/lib/images/reference.ts``. A STORED reference is normalized: it is
lowercase, has an explicit registry host and at least one path component, and
carries no tag and no digest.
"""

from __future__ import annotations

import re

_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_HOST = rf"(?:localhost|{_LABEL}(?:\.{_LABEL})+)(?::[0-9]{{1,5}})?"
_COMPONENT = r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*"

IMAGE_REFERENCE_RE = re.compile(rf"{_HOST}(?:/{_COMPONENT})+")
IMAGE_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
IMAGE_REFERENCE_MAX_LENGTH = 255


def is_image_reference(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= IMAGE_REFERENCE_MAX_LENGTH
        and IMAGE_REFERENCE_RE.fullmatch(value) is not None
    )


def is_image_digest(value: str) -> bool:
    return isinstance(value, str) and IMAGE_DIGEST_RE.fullmatch(value) is not None


def registry_host(reference: str) -> str:
    """The registry host of a normalized reference (its first component)."""
    return reference.split("/", 1)[0]
```

- [ ] **Step 6: Run the fixture consumers and watch them pass**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts`, which should PASS.
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py -q -k image`, which should PASS.

- [ ] **Step 7: Write the failing migration test**

`app/src/__tests__/images-migration.test.ts`:
```ts
// @vitest-environment node
/**
 * Migration 030 shape pins (C-1, container-images spec §4). Read as text, like
 * the 022/026/028 pins. The CHECK lists are compared against the
 * image-status.json fixture, so a status cannot drift between the DDL and the
 * two runtimes.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { migrationEntry, migrationSource } from "./helpers/migration-source";

const STATUS = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../tests/contract-fixtures/image-status.json", import.meta.url)),
    "utf8",
  ),
) as { image_statuses: string[]; scan_kinds: string[]; scan_statuses: string[] };

const sql = migrationEntry("030_container_images");

function checkList(constraint: string, column: string): string[] {
  const match = sql.match(
    new RegExp(`${constraint}\\s+CHECK \\(${column} IN \\(([^)]+)\\)\\)`),
  );
  expect(match, constraint).not.toBeNull();
  return match![1].split(",").map((s) => s.trim().replace(/'/g, ""));
}

describe("migration 030 (container images — C-1)", () => {
  it("runs after X-4's 028 (and after K-3's 029 whenever that lands)", () => {
    const at = migrationSource.indexOf('"030_container_images"');
    expect(at).toBeGreaterThan(migrationSource.indexOf('"028_builtin_processes"'));
    const k3 = migrationSource.indexOf('"029_');
    if (k3 !== -1) expect(at).toBeGreaterThan(k3);
  });

  it("creates both tables in stac_higher", () => {
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.container_images (");
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.image_scans (");
  });

  it("constrains every status/kind to exactly the fixture's vocabulary", () => {
    expect(checkList("container_images_status_check", "status")).toEqual(STATUS.image_statuses);
    expect(checkList("image_scans_kind_check", "kind")).toEqual(STATUS.scan_kinds);
    expect(checkList("image_scans_status_check", "status")).toEqual(STATUS.scan_statuses);
  });

  it("keys the registry on (reference, digest) with nulls distinct (spec §4.1)", () => {
    expect(sql).toContain("CONSTRAINT container_images_reference_digest_key UNIQUE (reference, digest)");
    expect(sql).not.toContain("NULLS NOT DISTINCT");
  });

  it("allows a NULL digest only while no digest can exist yet", () => {
    expect(sql).toMatch(
      /container_images_digest_required_check CHECK \(\s*digest IS NOT NULL OR status IN \('pending','scanning','scan_failed','revoked'\)\s*\)/,
    );
    expect(sql).toContain("digest ~ '^sha256:[a-f0-9]{64}$'");
  });

  it("keeps an exception all-or-nothing (spec §4.4: expires_at is required)", () => {
    expect(sql).toContain("container_images_exception_check");
    expect(sql).toContain("(exception_at IS NULL) = (exception_expires_at IS NULL)");
  });

  it("never lets a group credential vanish from under an image", () => {
    expect(sql).toMatch(
      /registry_connection_id uuid\s+REFERENCES stac_higher\.connections\(id\) ON DELETE RESTRICT/,
    );
  });

  it("cascades scans with their image and indexes the drain and the rescan tick", () => {
    expect(sql).toMatch(
      /image_id uuid NOT NULL\s+REFERENCES stac_higher\.container_images\(id\) ON DELETE CASCADE/,
    );
    expect(sql).toMatch(/image_scans_pending_idx\s+ON stac_higher\.image_scans \(requested_at\)\s+WHERE status = 'pending'/);
    expect(sql).toMatch(
      /container_images_rescan_idx\s+ON stac_higher\.container_images \(last_scanned_at\)\s+WHERE status IN \('approved','flagged'\)/,
    );
  });

  it("indexes the revision snapshot so 'in use by N processes' is one join", () => {
    expect(sql).toContain("process_revisions_image_id_idx");
    expect(sql).toContain("((runtime->'image'->>'id'))");
  });

  it("admits the registry protocol without dropping any existing one", () => {
    expect(sql).toContain(
      "CHECK (protocol IN ('ssh','sftp','ftp','ftps','s3','stac-api','registry'))",
    );
  });

  it("has no last_scan_id FK (it would be circular with image_scans.image_id)", () => {
    expect(sql).toMatch(/last_scan_id uuid,/);
    expect(sql).not.toMatch(/last_scan_id uuid[^,\n]*REFERENCES/);
  });
});
```

- [ ] **Step 8: Run it and watch it fail**

Run (from `app/`): `npx vitest run src/__tests__/images-migration.test.ts`
Expected: FAIL, because `migrationEntry` throws `migration 030_container_images not found`.

- [ ] **Step 9: The migration**

In `app/src/lib/db/migrate.ts`, add this entry after the `028_builtin_processes` entry and before the closing `];` of `MIGRATIONS`:
```ts
  {
    // C-1 (container-images spec §4, ADR 0021): the platform-wide image
    // registry (metadata only, never bytes) and its ADR 0004 scan ledger.
    // The app INSERTs admission rows (C-3); the pipeline claims them FOR
    // UPDATE SKIP LOCKED, INSERTs its own daily rescan rows, and moves the
    // image through the §4.3 status machine (C-2/C-4). Stale is computed from
    // last_scanned_at, never stored.
    //
    // digest is NULL only while the tag has not been resolved (pending,
    // scanning, or an admission that failed before resolving; a revoked
    // pending row keeps its NULL). UNIQUE (reference, digest) keeps the
    // default NULLS DISTINCT, so two pending rows for one reference coexist
    // until the drain dedups them by digest. last_scan_id carries no FK: it
    // would be circular with image_scans.image_id. registry_connection_id is
    // ON DELETE RESTRICT; connections are soft-deleted, and a soft-deleted
    // credential reads as "another group" at the deploy gate, never as a
    // public image.
    //
    // Also: the connections protocol CHECK admits `registry` (spec §5), and
    // process_revisions gains an expression index on the snapshot's image id.
    //
    // Numbering: 029 is K-3's (#11) and may merge after this. The two touch
    // disjoint objects, so either apply order is safe; K-3 inserts its entry
    // between 028 and this one (names, not positions, are what is recorded).
    name: "030_container_images",
    sql: `
      ALTER TABLE stac_higher.connections
        DROP CONSTRAINT IF EXISTS connections_protocol_check;
      ALTER TABLE stac_higher.connections
        ADD CONSTRAINT connections_protocol_check
        CHECK (protocol IN ('ssh','sftp','ftp','ftps','s3','stac-api','registry'));

      CREATE TABLE IF NOT EXISTS stac_higher.container_images (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        reference text NOT NULL,
        tag_at_add text NOT NULL,
        digest text,
        platform_digest text,
        platform jsonb,
        size_bytes bigint,
        config jsonb,
        status text NOT NULL DEFAULT 'pending'
          CONSTRAINT container_images_status_check
          CHECK (status IN ('pending','scanning','approved','rejected','flagged','revoked','scan_failed')),
        verdict jsonb,
        sbom_ref text,
        last_scan_id uuid,
        last_scanned_at timestamptz,
        added_by text NOT NULL,
        registry_connection_id uuid
          REFERENCES stac_higher.connections(id) ON DELETE RESTRICT,
        exception_reason text,
        exception_by text,
        exception_at timestamptz,
        exception_expires_at timestamptz,
        tag_current_digest text,
        tag_checked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT container_images_reference_digest_key UNIQUE (reference, digest),
        CONSTRAINT container_images_digest_format_check CHECK (
          (digest IS NULL OR digest ~ '^sha256:[a-f0-9]{64}$')
          AND (platform_digest IS NULL OR platform_digest ~ '^sha256:[a-f0-9]{64}$')
        ),
        CONSTRAINT container_images_digest_required_check CHECK (
          digest IS NOT NULL OR status IN ('pending','scanning','scan_failed','revoked')
        ),
        CONSTRAINT container_images_exception_check CHECK (
          (exception_at IS NULL) = (exception_expires_at IS NULL)
          AND (exception_at IS NULL) = (exception_reason IS NULL)
          AND (exception_at IS NULL) = (exception_by IS NULL)
        )
      );

      CREATE INDEX IF NOT EXISTS container_images_status_idx
        ON stac_higher.container_images (status);
      CREATE INDEX IF NOT EXISTS container_images_rescan_idx
        ON stac_higher.container_images (last_scanned_at)
        WHERE status IN ('approved','flagged');
      CREATE INDEX IF NOT EXISTS container_images_registry_connection_idx
        ON stac_higher.container_images (registry_connection_id)
        WHERE registry_connection_id IS NOT NULL;

      CREATE TABLE IF NOT EXISTS stac_higher.image_scans (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        image_id uuid NOT NULL
          REFERENCES stac_higher.container_images(id) ON DELETE CASCADE,
        kind text NOT NULL
          CONSTRAINT image_scans_kind_check
          CHECK (kind IN ('admission','rescan')),
        status text NOT NULL DEFAULT 'pending'
          CONSTRAINT image_scans_status_check
          CHECK (status IN ('pending','running','done','failed')),
        requested_by text NOT NULL,
        requested_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz,
        finished_at timestamptz,
        executor_handle text,
        log_ref text,
        result jsonb,
        findings_ref text
      );

      CREATE INDEX IF NOT EXISTS image_scans_image_idx
        ON stac_higher.image_scans (image_id, requested_at DESC);
      CREATE INDEX IF NOT EXISTS image_scans_pending_idx
        ON stac_higher.image_scans (requested_at)
        WHERE status = 'pending';

      CREATE INDEX IF NOT EXISTS process_revisions_image_id_idx
        ON stac_higher.process_revisions ((runtime->'image'->>'id'))
        WHERE runtime->'image'->>'id' IS NOT NULL;
    `,
  },
```

- [ ] **Step 10: Run it and watch it pass**

Run (from `app/`): `npx vitest run src/__tests__/images-migration.test.ts src/__tests__/processes-migration.test.ts`, which should PASS. The 028 pins must still pass, because the 030 comment block falls inside 028's slice and contains none of the strings 028's negative assertions look for.

- [ ] **Step 11: Fixture README**

Append to `tests/contract-fixtures/README.md`:
```markdown
## Additional fixture styles (C queue, C-1)

- `image-status.json` is style `pinned-enum`. It holds the `container_images.status`, `image_scans.kind`/`.status` vocabularies, the statuses a new revision may snapshot (`deploy_statuses`) or a run may launch on (`launch_statuses`), and the deploy gate's four 422 reasons. Three things consume it: `app/src/lib/images/status.ts` (whose `IMAGE_STATUS_LABEL` must cover every status), `pipeline/images/status.py`, and migration 030's CHECK constraints (`images-migration.test.ts`).
- `image-reference.json` is style `grammar-cases`, like `staged-asset-href.json`. It pins the grammar of a STORED image reference (normalized, lowercase, with an explicit registry host and no tag or digest) and of a manifest digest (sha256 only). Both sides must agree on each `reference`/`digest` boolean. Consumers: `app/src/lib/images/reference.ts` and `pipeline/images/reference.py`.
```

- [ ] **Step 12: Gates and commit**

Run: `npm run verify` (worktree root), then `cd services/pipeline && uv run pytest -q && uv run ruff check .`. All three should be green.
```bash
git add tests/contract-fixtures/image-status.json tests/contract-fixtures/image-reference.json tests/contract-fixtures/README.md \
  app/src/lib/images/status.ts app/src/lib/images/reference.ts app/src/lib/db/migrate.ts \
  app/src/__tests__/images-migration.test.ts app/src/__tests__/contract-fixtures.test.ts \
  services/pipeline/src/pipeline/images/ services/pipeline/tests/test_contract_fixtures.py
git commit -m "feat(images): status vocabulary, reference grammar and migration 030 (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The image policy document, its loaders and its packaging

**Files:**
- Create: `infra/image-policy/default.json`, `tests/contract-fixtures/image-policy.json`
- Create: `app/src/lib/images/policy.ts`, `services/pipeline/src/pipeline/images/policy.py`
- Modify: `app/Dockerfile`, `services/pipeline/Dockerfile`, `docker-compose.yml`, `.github/workflows/containers.yml`, `.env.example`
- Modify: `app/src/__tests__/contract-fixtures.test.ts`, `services/pipeline/tests/test_contract_fixtures.py`, `tests/contract-fixtures/README.md`, `docs/backend.md`, `services/pipeline/README.md`
- Test: `app/src/__tests__/images-policy.test.ts` (new), `services/pipeline/tests/test_image_policy.py` (new)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces (TS, `@/lib/images/policy`): `imagePolicySchema`, `type ImagePolicy`, `class ImagePolicyUnavailable extends Error`, `parseImagePolicy(doc: unknown): ImagePolicy` (throws `ImagePolicyUnavailable`), `imagePolicyPath(env?)`, `loadImagePolicy(env?): ImagePolicy` (cached per path, throws `ImagePolicyUnavailable`), `resetImagePolicyCache()`, `registryAllowed(host: string, patterns: readonly string[]): boolean`.
- Produces (Python, `pipeline.images.policy`): `POLICY_ENV_VAR = "PROCESS_IMAGE_POLICY_FILE"`, `ImagePolicyError(ValueError)`, `BlockRules`, `ImagePolicy` (with property `max_image_bytes`), `parse_image_policy(raw) -> ImagePolicy`, `image_policy_path(env=None) -> Path`, `load_image_policy(path=None) -> ImagePolicy`, `registry_allowed(host, patterns) -> bool`.

- [ ] **Step 1: The default policy (spec §7.1, verbatim values)**

`infra/image-policy/default.json`:
```json
{
  "version": 1,
  "allowed_registries": ["docker.io", "ghcr.io", "public.ecr.aws", "*.dkr.ecr.*.amazonaws.com"],
  "platform": "linux/amd64",
  "max_image_size_mb": 4096,
  "block": {
    "kev": true,
    "critical_fixed": true,
    "critical_unfixed_older_than_days": 30,
    "high_fixed_epss_at_least": 0.1,
    "high_unfixed": false
  },
  "scan_window_days": 30,
  "rescan_interval_hours": 24,
  "exception_max_days": 90,
  "scan_limits": { "memory_mb": 4096, "timeout_seconds": 900 }
}
```

- [ ] **Step 2: The fixture**

`tests/contract-fixtures/image-policy.json`. `document` is byte-for-byte the object above. A case is applied as follows: shallow-merge `patch` onto a copy of `document`, merge `block_patch` into its `block`, then delete the keys in `remove` (top level) and `block_remove` (inside `block`).
```json
{
  "style": "document",
  "$comment": "C-1 (container-images spec §7): the image policy, a per-deployment document both runtimes read through PROCESS_IMAGE_POLICY_FILE (in-repo default infra/image-policy/default.json, which must equal `document`). The app validates it STRICTLY (app/src/lib/images/policy.ts). The pipeline reader (pipeline/images/policy.py) ignores unknown keys but is otherwise strict, because a policy is a security document and both loaders fail closed. A case applies `patch` (top-level shallow merge), `block_patch` (merged into block), then deletes `remove` / `block_remove` keys. `registry_cases` run registryAllowed()/registry_allowed() against document.allowed_registries: `*` is exactly one DNS label, hosts are case-folded, and a port must match literally. `evaluate_cases` (pytest only; the pipeline is the only evaluator) are added by Task 3.",
  "document": {
    "version": 1,
    "allowed_registries": ["docker.io", "ghcr.io", "public.ecr.aws", "*.dkr.ecr.*.amazonaws.com"],
    "platform": "linux/amd64",
    "max_image_size_mb": 4096,
    "block": {
      "kev": true,
      "critical_fixed": true,
      "critical_unfixed_older_than_days": 30,
      "high_fixed_epss_at_least": 0.1,
      "high_unfixed": false
    },
    "scan_window_days": 30,
    "rescan_interval_hours": 24,
    "exception_max_days": 90,
    "scan_limits": { "memory_mb": 4096, "timeout_seconds": 900 }
  },
  "cases": [
    { "name": "the default policy", "app": "accept", "pipeline": "accept" },
    { "name": "version 2", "patch": { "version": 2 }, "app": "reject", "pipeline": "reject" },
    { "name": "no allowed registries (nothing could ever be added)", "patch": { "allowed_registries": [] }, "app": "reject", "pipeline": "reject" },
    { "name": "a registry pattern with a scheme", "patch": { "allowed_registries": ["https://ghcr.io"] }, "app": "reject", "pipeline": "reject" },
    { "name": "an uppercase registry pattern", "patch": { "allowed_registries": ["GHCR.IO"] }, "app": "reject", "pipeline": "reject" },
    { "name": "a platform without an architecture", "patch": { "platform": "amd64" }, "app": "reject", "pipeline": "reject" },
    { "name": "a zero size cap", "patch": { "max_image_size_mb": 0 }, "app": "reject", "pipeline": "reject" },
    { "name": "a boolean size cap", "patch": { "max_image_size_mb": true }, "app": "reject", "pipeline": "reject" },
    { "name": "block without kev", "block_remove": ["kev"], "app": "reject", "pipeline": "reject" },
    { "name": "block without the unfixed-critical key (null is how a rule is turned off, absence is a typo)", "block_remove": ["critical_unfixed_older_than_days"], "app": "reject", "pipeline": "reject" },
    { "name": "unfixed-critical rule off", "block_patch": { "critical_unfixed_older_than_days": null }, "app": "accept", "pipeline": "accept" },
    { "name": "EPSS rule off", "block_patch": { "high_fixed_epss_at_least": null }, "app": "accept", "pipeline": "accept" },
    { "name": "an EPSS threshold above 1", "block_patch": { "high_fixed_epss_at_least": 1.5 }, "app": "reject", "pipeline": "reject" },
    { "name": "a negative unfixed-critical age", "block_patch": { "critical_unfixed_older_than_days": -1 }, "app": "reject", "pipeline": "reject" },
    { "name": "a zero scan window", "patch": { "scan_window_days": 0 }, "app": "reject", "pipeline": "reject" },
    { "name": "a rescan interval longer than the scan window (every image would go stale between rescans)", "patch": { "rescan_interval_hours": 721 }, "app": "reject", "pipeline": "reject" },
    { "name": "a rescan interval exactly the scan window", "patch": { "rescan_interval_hours": 720 }, "app": "accept", "pipeline": "accept" },
    { "name": "a zero exception cap", "patch": { "exception_max_days": 0 }, "app": "reject", "pipeline": "reject" },
    { "name": "scanner memory below the floor", "patch": { "scan_limits": { "memory_mb": 64, "timeout_seconds": 900 } }, "app": "reject", "pipeline": "reject" },
    { "name": "no scan_limits", "remove": ["scan_limits"], "app": "reject", "pipeline": "reject" },
    { "name": "an unknown top-level key", "patch": { "cosign": true }, "app": "reject", "pipeline": "accept" },
    { "name": "an unknown block key", "block_patch": { "secrets": true }, "app": "reject", "pipeline": "accept" }
  ],
  "registry_cases": [
    { "name": "Docker Hub", "host": "docker.io", "allowed": true },
    { "name": "GHCR", "host": "ghcr.io", "allowed": true },
    { "name": "ECR public", "host": "public.ecr.aws", "allowed": true },
    { "name": "a private ECR host matches the two-wildcard pattern", "host": "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com", "allowed": true },
    { "name": "case is folded", "host": "GHCR.IO", "allowed": true },
    { "name": "a registry the policy does not name", "host": "quay.io", "allowed": false },
    { "name": "a wildcard matches exactly one label, not zero", "host": "dkr.ecr.us-east-1.amazonaws.com", "allowed": false },
    { "name": "a wildcard matches exactly one label, not two", "host": "evil.com.dkr.ecr.us-east-1.amazonaws.com", "allowed": false },
    { "name": "anchored at the end", "host": "123456789012.dkr.ecr.us-east-1.amazonaws.com.evil.com", "allowed": false },
    { "name": "a suffix is not a match", "host": "ghcr.io.evil.com", "allowed": false },
    { "name": "a port must match literally", "host": "ghcr.io:443", "allowed": false }
  ]
}
```

- [ ] **Step 3: Write the failing fixture consumers**

In `app/src/__tests__/contract-fixtures.test.ts` add the import `import { imagePolicySchema, registryAllowed } from "@/lib/images/policy";` and append:
```ts
interface PolicyCaseShape {
  patch?: Record<string, unknown>;
  block_patch?: Record<string, unknown>;
  remove?: string[];
  block_remove?: string[];
}

/** image-policy.json's case rule: patch, block_patch, then the removals. */
function policyDoc(document: Record<string, unknown>, c: PolicyCaseShape): Record<string, unknown> {
  const doc: Record<string, unknown> = { ...document, ...(c.patch ?? {}) };
  const block: Record<string, unknown> = {
    ...(document.block as Record<string, unknown>),
    ...(c.block_patch ?? {}),
  };
  for (const key of c.block_remove ?? []) delete block[key];
  doc.block = block;
  for (const key of c.remove ?? []) delete doc[key];
  return doc;
}

describe("image policy contract (tests/contract-fixtures/image-policy.json)", () => {
  const fixture = loadFixture("image-policy.json") as unknown as {
    document: Record<string, unknown> & { allowed_registries: string[] };
    cases: (PolicyCaseShape & { name: string; app: "accept" | "reject" })[];
    registry_cases: { name: string; host: string; allowed: boolean }[];
  };

  it("the in-repo default policy IS the fixture document", () => {
    const shipped = JSON.parse(
      readFileSync(
        fileURLToPath(new URL("../../../infra/image-policy/default.json", import.meta.url)),
        "utf8",
      ),
    );
    expect(shipped).toEqual(fixture.document);
  });

  it.each(fixture.cases)("$app: $name", (c) => {
    expect(imagePolicySchema.safeParse(policyDoc(fixture.document, c)).success).toBe(
      c.app === "accept",
    );
  });

  it.each(fixture.registry_cases)("registry — $name", ({ host, allowed }) => {
    expect(registryAllowed(host, fixture.document.allowed_registries)).toBe(allowed);
  });
});
```

In `services/pipeline/tests/test_contract_fixtures.py` add `IMAGE_POLICY = _load("image-policy.json")` beside the other constants and append:
```python
def _policy_doc(document: dict, case: dict) -> dict:
    """image-policy.json's case rule: patch, block_patch, then the removals."""
    doc = {**document, **case.get("patch", {})}
    block = {**document["block"], **case.get("block_patch", {})}
    for key in case.get("block_remove", []):
        block.pop(key, None)
    doc["block"] = block
    for key in case.get("remove", []):
        doc.pop(key, None)
    return doc


@pytest.mark.parametrize("case", IMAGE_POLICY["cases"], ids=lambda c: c["name"])
def test_image_policy_cases(case):
    from pipeline.images.policy import ImagePolicyError, parse_image_policy

    doc = _policy_doc(IMAGE_POLICY["document"], case)
    if case["pipeline"] == "accept":
        parse_image_policy(doc)
    else:
        with pytest.raises(ImagePolicyError):
            parse_image_policy(doc)


@pytest.mark.parametrize("case", IMAGE_POLICY["registry_cases"], ids=lambda c: c["name"])
def test_image_policy_registry_cases(case):
    from pipeline.images.policy import registry_allowed

    patterns = IMAGE_POLICY["document"]["allowed_registries"]
    assert registry_allowed(case["host"], patterns) is case["allowed"]
```

- [ ] **Step 4: Run both and watch them fail**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts`, which should FAIL (module not found).
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py -q -k policy`, which should FAIL (`ModuleNotFoundError`).

- [ ] **Step 5: The TS policy module**

`app/src/lib/images/policy.ts`:
```ts
/**
 * The image policy (C-1, container-images spec §7). It is a per-deployment
 * document both runtimes read (the hardware-profiles pattern), in-repo
 * default `infra/image-policy/default.json`, overridden by
 * `PROCESS_IMAGE_POLICY_FILE`. The app validates it STRICTLY and FAILS
 * CLOSED: when the document cannot be read or is invalid, no user image can
 * be deployed or added. That is a 503 at the gate, never a silent pass.
 * Inline revisions never read it.
 *
 * Pinned by `tests/contract-fixtures/image-policy.json` against
 * `pipeline/images/policy.py`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { z } from "zod";

const REGISTRY_PATTERN_RE = /^[a-z0-9*](?:[a-z0-9*.-]*[a-z0-9*])?(?::[0-9]{1,5})?$/;
const PLATFORM_RE = /^[a-z0-9]+\/[a-z0-9]+(?:\/[a-z0-9]+)?$/;
const LABEL_RE = /^[a-z0-9-]+$/;

export const imagePolicyBlockSchema = z
  .object({
    kev: z.boolean(),
    critical_fixed: z.boolean(),
    critical_unfixed_older_than_days: z.number().int().min(0).nullable(),
    high_fixed_epss_at_least: z.number().min(0).max(1).nullable(),
    high_unfixed: z.boolean(),
  })
  .strict();

export const imagePolicySchema = z
  .object({
    version: z.literal(1),
    allowed_registries: z
      .array(
        z
          .string()
          .regex(REGISTRY_PATTERN_RE, "allowed_registries entries are lowercase hostnames; `*` stands for one label"),
      )
      .min(1, "allowed_registries must name at least one registry"),
    platform: z.string().regex(PLATFORM_RE, "platform must be os/architecture, e.g. linux/amd64"),
    max_image_size_mb: z.number().int().min(1),
    block: imagePolicyBlockSchema,
    scan_window_days: z.number().int().min(1),
    rescan_interval_hours: z.number().int().min(1),
    exception_max_days: z.number().int().min(1),
    scan_limits: z
      .object({
        memory_mb: z.number().int().min(128),
        timeout_seconds: z.number().int().min(1).max(86_400),
      })
      .strict(),
  })
  .strict()
  .superRefine((policy, ctx) => {
    if (policy.rescan_interval_hours > policy.scan_window_days * 24) {
      ctx.addIssue({
        code: "custom",
        path: ["rescan_interval_hours"],
        message:
          "rescan_interval_hours must fit inside scan_window_days; otherwise every image goes stale between rescans",
      });
    }
  });

export type ImagePolicy = z.infer<typeof imagePolicySchema>;

/** The policy is missing or invalid. Callers answer 503 (fail closed). */
export class ImagePolicyUnavailable extends Error {}

export function parseImagePolicy(document: unknown): ImagePolicy {
  const parsed = imagePolicySchema.safeParse(document);
  if (!parsed.success) {
    throw new ImagePolicyUnavailable(
      `image policy: ${parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`,
    );
  }
  return parsed.data;
}

const CHECKOUT_DEFAULT_POLICY = fileURLToPath(
  new URL("../../../../infra/image-policy/default.json", import.meta.url),
);

export function imagePolicyPath(env: Record<string, string | undefined> = process.env): string {
  const override = env.PROCESS_IMAGE_POLICY_FILE?.trim();
  return override ? override : CHECKOUT_DEFAULT_POLICY;
}

let cache: { path: string; policy: ImagePolicy } | null = null;

/** Read and validate the deployment's policy, cached per path for the
 * process lifetime (deployment config, not operator data). */
export function loadImagePolicy(env: Record<string, string | undefined> = process.env): ImagePolicy {
  const path = imagePolicyPath(env);
  if (cache && cache.path === path) return cache.policy;
  let document: unknown;
  try {
    document = JSON.parse(readFileSync(path, "utf8"));
  } catch (err) {
    throw new ImagePolicyUnavailable(
      `could not read the image policy at ${path}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  const policy = parseImagePolicy(document);
  cache = { path, policy };
  return policy;
}

export function resetImagePolicyCache(): void {
  cache = null;
}

/** Is a registry HOST allowed by the policy's patterns? `*` is exactly one
 * DNS label; the host is case-folded; a port must match literally. The same
 * rule as `registry_allowed` in the pipeline (fixture `registry_cases`). */
export function registryAllowed(host: string, patterns: readonly string[]): boolean {
  const labels = host.toLowerCase().split(".");
  return patterns.some((pattern) => {
    const want = pattern.split(".");
    return (
      want.length === labels.length &&
      want.every((label, i) => (label === "*" ? LABEL_RE.test(labels[i]) : label === labels[i]))
    );
  });
}
```

- [ ] **Step 6: The Python policy module (parse, load, registry match — `evaluate` is Task 3)**

`services/pipeline/src/pipeline/images/policy.py`:
```python
"""The image policy (C-1, container-images spec §7): the Python half.

One per-deployment document both runtimes read, with the in-repo default at
``infra/image-policy/default.json`` and the override ``PROCESS_IMAGE_POLICY_FILE``.
Unknown keys are ignored (the established reader direction), and every other
defect raises: a policy is a security document, and a missing or invalid one
means no image can be added or launched (fail closed).

**Packaging:** the file reaches the image as ``COPY --from=imagepolicy`` out of
a named build context pointing at ``infra/image-policy`` (compose
``additional_contexts``, CI ``build-contexts``), and the image publishes the
copy's path in ``PROCESS_IMAGE_POLICY_FILE``. When the variable is unset (dev,
pytest), the reader uses the repo checkout.

Pinned by ``tests/contract-fixtures/image-policy.json`` against
``app/src/lib/images/policy.ts``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

POLICY_ENV_VAR = "PROCESS_IMAGE_POLICY_FILE"

_REGISTRY_PATTERN_RE = re.compile(r"[a-z0-9*](?:[a-z0-9*.-]*[a-z0-9*])?(?::[0-9]{1,5})?")
_PLATFORM_RE = re.compile(r"[a-z0-9]+/[a-z0-9]+(?:/[a-z0-9]+)?")
_LABEL_RE = re.compile(r"[a-z0-9-]+")


class ImagePolicyError(ValueError):
    """The policy document is missing or not a usable §7.1 shape."""


@dataclass(frozen=True)
class BlockRules:
    kev: bool
    critical_fixed: bool
    #: ``None`` turns the rule off.
    critical_unfixed_older_than_days: int | None
    #: ``None`` turns the rule off.
    high_fixed_epss_at_least: float | None
    high_unfixed: bool


@dataclass(frozen=True)
class ImagePolicy:
    version: int
    allowed_registries: tuple[str, ...]
    platform: str
    max_image_size_mb: int
    block: BlockRules
    scan_window_days: int
    rescan_interval_hours: int
    exception_max_days: int
    scan_memory_mb: int
    scan_timeout_seconds: int

    @property
    def max_image_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024


def _obj(raw: Any, what: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ImagePolicyError(f"{what} must be an object, got {raw!r}")
    return raw


def _int(raw: Any, what: str, *, minimum: int, maximum: int | None = None) -> int:
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or (isinstance(raw, float) and not raw.is_integer())
    ):
        raise ImagePolicyError(f"{what} must be an integer, got {raw!r}")
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        raise ImagePolicyError(f"{what} is out of range: {raw!r}")
    return value


def _bool(raw: Any, what: str) -> bool:
    if not isinstance(raw, bool):
        raise ImagePolicyError(f"{what} must be true or false, got {raw!r}")
    return raw


def _required(doc: dict[str, Any], key: str, what: str) -> Any:
    """Present, possibly null: a nullable rule is turned off with null, and an
    ABSENT key is a typo that must not read as 'off'."""
    if key not in doc:
        raise ImagePolicyError(f"{what}.{key} is required (null turns the rule off)")
    return doc[key]


def _parse_block(raw: Any) -> BlockRules:
    block = _obj(raw, "block")
    age = _required(block, "critical_unfixed_older_than_days", "block")
    epss = _required(block, "high_fixed_epss_at_least", "block")
    if epss is not None and (
        isinstance(epss, bool) or not isinstance(epss, (int, float)) or not 0 <= epss <= 1
    ):
        raise ImagePolicyError(f"block.high_fixed_epss_at_least must be in [0, 1], got {epss!r}")
    return BlockRules(
        kev=_bool(block.get("kev"), "block.kev"),
        critical_fixed=_bool(block.get("critical_fixed"), "block.critical_fixed"),
        critical_unfixed_older_than_days=(
            None
            if age is None
            else _int(age, "block.critical_unfixed_older_than_days", minimum=0)
        ),
        high_fixed_epss_at_least=None if epss is None else float(epss),
        high_unfixed=_bool(block.get("high_unfixed"), "block.high_unfixed"),
    )


def parse_image_policy(raw: Any) -> ImagePolicy:
    doc = _obj(raw, "image policy")
    version = doc.get("version")
    if type(version) is not int or version != 1:
        raise ImagePolicyError(f"version must be 1, got {version!r}")
    registries = doc.get("allowed_registries")
    if (
        not isinstance(registries, list)
        or not registries
        or not all(isinstance(r, str) and _REGISTRY_PATTERN_RE.fullmatch(r) for r in registries)
    ):
        raise ImagePolicyError(
            "allowed_registries must be a non-empty list of lowercase hostnames (`*` = one label)"
        )
    platform = doc.get("platform")
    if not isinstance(platform, str) or not _PLATFORM_RE.fullmatch(platform):
        raise ImagePolicyError(f"platform must be os/architecture, got {platform!r}")
    limits = _obj(doc.get("scan_limits"), "scan_limits")
    scan_window_days = _int(doc.get("scan_window_days"), "scan_window_days", minimum=1)
    rescan_interval_hours = _int(
        doc.get("rescan_interval_hours"), "rescan_interval_hours", minimum=1
    )
    if rescan_interval_hours > scan_window_days * 24:
        raise ImagePolicyError(
            "rescan_interval_hours must fit inside scan_window_days; otherwise every image "
            "goes stale between rescans"
        )
    return ImagePolicy(
        version=1,
        allowed_registries=tuple(registries),
        platform=platform,
        max_image_size_mb=_int(doc.get("max_image_size_mb"), "max_image_size_mb", minimum=1),
        block=_parse_block(doc.get("block")),
        scan_window_days=scan_window_days,
        rescan_interval_hours=rescan_interval_hours,
        exception_max_days=_int(doc.get("exception_max_days"), "exception_max_days", minimum=1),
        scan_memory_mb=_int(limits.get("memory_mb"), "scan_limits.memory_mb", minimum=128),
        scan_timeout_seconds=_int(
            limits.get("timeout_seconds"), "scan_limits.timeout_seconds", minimum=1, maximum=86_400
        ),
    )


def _checkout_policy() -> Path:
    """The repo checkout's default. It is resolved LAZILY: inside the image
    this module has fewer than six ancestors, which is the K-1 IndexError
    lesson (see ``process/hardware.py``)."""
    here = Path(__file__).resolve()
    root = here.parents[5] if len(here.parents) > 5 else here.parents[-1]
    return root / "infra" / "image-policy" / "default.json"


def image_policy_path(env: dict[str, str] | None = None) -> Path:
    override = (os.environ if env is None else env).get(POLICY_ENV_VAR)
    if override:
        return Path(override)
    return _checkout_policy()


def load_image_policy(path: Path | None = None) -> ImagePolicy:
    where = path or image_policy_path()
    try:
        document = json.loads(where.read_text())
    except (OSError, ValueError) as exc:
        raise ImagePolicyError(f"could not read the image policy at {where}: {exc}") from exc
    return parse_image_policy(document)


def registry_allowed(host: str, patterns: tuple[str, ...] | list[str]) -> bool:
    """``*`` is exactly one DNS label; the host is case-folded; a port must
    match literally. The same rule as ``registryAllowed`` in the app."""
    labels = host.lower().split(".")
    for pattern in patterns:
        want = pattern.split(".")
        if len(want) == len(labels) and all(
            (_LABEL_RE.fullmatch(have) is not None) if w == "*" else w == have
            for w, have in zip(want, labels, strict=True)
        ):
            return True
    return False
```

- [ ] **Step 7: Loader + packaging tests**

`app/src/__tests__/images-policy.test.ts`:
```ts
// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import {
  ImagePolicyUnavailable,
  imagePolicyPath,
  loadImagePolicy,
  resetImagePolicyCache,
} from "@/lib/images/policy";

afterEach(() => resetImagePolicyCache());

describe("loadImagePolicy", () => {
  it("reads the checkout default when PROCESS_IMAGE_POLICY_FILE is unset", () => {
    expect(imagePolicyPath({})).toMatch(/infra\/image-policy\/default\.json$/);
    expect(loadImagePolicy({}).scan_window_days).toBe(30);
  });

  it("fails closed on a missing file, naming the path", () => {
    expect(() => loadImagePolicy({ PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" })).toThrow(
      ImagePolicyUnavailable,
    );
    expect(() => loadImagePolicy({ PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" })).toThrow(
      /\/nonexistent\/policy\.json/,
    );
  });
});

describe("the app image ships the policy (the K-1 packaging pattern)", () => {
  const dockerfile = readFileSync(
    fileURLToPath(new URL("../../Dockerfile", import.meta.url)),
    "utf8",
  );
  it("copies it through the imagepolicy build context and publishes the path", () => {
    expect(dockerfile).toContain("COPY --from=imagepolicy default.json /app/share/image-policy/default.json");
    expect(dockerfile).toContain("PROCESS_IMAGE_POLICY_FILE=/app/share/image-policy/default.json");
  });
});
```

`services/pipeline/tests/test_image_policy.py`:
```python
"""The image policy loader and its packaging (C-1, container-images spec §7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.images.policy import (
    ImagePolicyError,
    image_policy_path,
    load_image_policy,
)

REPO = Path(__file__).resolve().parents[3]


def test_unset_env_reads_the_checkout_default():
    assert image_policy_path({}) == REPO / "infra" / "image-policy" / "default.json"
    policy = load_image_policy(image_policy_path({}))
    assert policy.scan_window_days == 30
    assert policy.max_image_bytes == 4096 * 1024 * 1024
    assert policy.block.high_fixed_epss_at_least == 0.1


def test_missing_file_fails_closed_naming_the_path(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(ImagePolicyError, match="nope.json"):
        load_image_policy(missing)


def test_the_pipeline_image_ships_the_policy():
    dockerfile = (REPO / "services" / "pipeline" / "Dockerfile").read_text()
    assert "COPY --from=imagepolicy default.json /app/share/image-policy/default.json" in dockerfile
    assert "PROCESS_IMAGE_POLICY_FILE=/app/share/image-policy/default.json" in dockerfile


def test_every_pipeline_build_carries_the_imagepolicy_context():
    # docker-compose.yml's header records why the build is an anchor: a copy
    # that drifted resolved `--from=hardware` as a Docker Hub image.
    compose = (REPO / "docker-compose.yml").read_text()
    assert "imagepolicy: ./infra/image-policy" in compose
    workflow = (REPO / ".github" / "workflows" / "containers.yml").read_text()
    assert workflow.count("imagepolicy=infra/image-policy") == 2  # app + pipeline
```

- [ ] **Step 8: Run and watch the packaging tests fail, the parse tests pass**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/images-policy.test.ts`. The fixture and loader cases should PASS. "the app image ships the policy" should FAIL.
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py tests/test_image_policy.py -q -k "policy"`. The two packaging tests should FAIL and the rest PASS.

- [ ] **Step 9: Packaging (mirror every `hardware` line)**

- `app/Dockerfile`:
  - In the header comment, extend the example build command with `--build-context imagepolicy=infra/image-policy` and add one line to the prose: "…and the `imagepolicy` named context that carries the deployment's image policy (C-1, the same reason)."
  - In the runtime stage's `ENV`, add `PROCESS_IMAGE_POLICY_FILE=/app/share/image-policy/default.json` after the hardware line. Keep the trailing ` \` continuation valid.
  - After `COPY --from=hardware local.json /app/share/hardware-profiles/local.json`, add:
    ```dockerfile
    # The deployment's image policy (C-1, container-images spec §7) — same
    # reason as the hardware set: the checkout fallback is not in this image.
    COPY --from=imagepolicy default.json /app/share/image-policy/default.json
    ```
- `services/pipeline/Dockerfile`:
  - Extend the header's example with `--build-context imagepolicy=infra/image-policy`.
  - After the `COPY --from=hardware …` line, add `COPY --from=imagepolicy default.json /app/share/image-policy/default.json` with a one-line comment naming C-1.
  - In `ENV`, add `PROCESS_IMAGE_POLICY_FILE=/app/share/image-policy/default.json` after the hardware line.
- `docker-compose.yml`:
  - In `x-pipeline-build.additional_contexts`, add `imagepolicy: ./infra/image-policy` with the comment `# The deployment's image policy (C-1) — same reason as hardware.`
  - In the `pipeline` service `environment`, directly after the `PROCESS_HARDWARE_PROFILES_FILE` line, add:
    ```yaml
      # C-1: where the image published the image policy the `imagepolicy`
      # build context copied in above. Unset means the repo checkout.
      - PROCESS_IMAGE_POLICY_FILE=${PROCESS_IMAGE_POLICY_FILE:-/app/share/image-policy/default.json}
    ```
- `.github/workflows/containers.yml`: in both the `app` and `pipeline` matrix entries, append `imagepolicy=infra/image-policy` as a new line of `build-contexts: |`, and extend each comment with "and the image policy (C-1)".
- `.env.example`: directly below `# PROCESS_HARDWARE_PROFILES_FILE=`, add:
  ```
  # The deployment's image policy for user-supplied process images (C-1).
  # Unset means the repo checkout's infra/image-policy/default.json; both
  # images copy that file to this path through the `imagepolicy` build context.
  # PROCESS_IMAGE_POLICY_FILE=
  ```

- [ ] **Step 10: Docs rows**

- `docs/backend.md`, "App environment" table: add a row after `PROCESS_HARDWARE_PROFILES_FILE`:
  `| \`PROCESS_IMAGE_POLICY_FILE\` | Path to the deployment's image policy (C-1, container-images spec §7): allowed registries, size cap, block rules, scan window. It is read only when a revision names a user image (\`inline_python_on_image\` / \`container\`). A missing or invalid file makes those deploys fail closed (503 \`image_policy_unavailable\`), and inline deploys never read it. Unset means the repo checkout's \`infra/image-policy/default.json\`. |`
- `services/pipeline/README.md`, env table: add after `PROCESS_HARDWARE_PROFILES_FILE`:
  `| \`PROCESS_IMAGE_POLICY_FILE\` | _(unset — the repo checkout's \`infra/image-policy/default.json\`)_ | The image policy (C-1, container-images spec §7). \`pipeline.images.policy.load_image_policy\` reads it and fails closed. The image copies the file here through the \`imagepolicy\` named build context. C-1 only parses and evaluates it; the scan drain and the launch path (C-2) are its first runtime readers. |`
- `tests/contract-fixtures/README.md`, in the C-1 section add:
  `- \`image-policy.json\` is style \`document\`. \`document\` must equal \`infra/image-policy/default.json\`. \`cases[]\` apply \`patch\` / \`block_patch\` / \`remove\` / \`block_remove\` to it and are run through \`imagePolicySchema\` (strict) and \`parse_image_policy\` (unknown keys ignored, otherwise strict). \`registry_cases[]\` pin the host-pattern rule on both sides: \`*\` is one DNS label, the host is case-folded, and a port matches literally. \`evaluate_cases[]\` are pytest-only (the pipeline is the only evaluator).`

- [ ] **Step 11: Gates and commit**

Run the full gates, which should all be green: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check .`. (CI's `containers.yml` build verifies the Dockerfiles on the PR. Teammates do not build images.)
```bash
git add infra/image-policy tests/contract-fixtures/image-policy.json tests/contract-fixtures/README.md \
  app/src/lib/images/policy.ts app/src/__tests__/images-policy.test.ts app/src/__tests__/contract-fixtures.test.ts \
  services/pipeline/src/pipeline/images/policy.py services/pipeline/tests/test_image_policy.py \
  services/pipeline/tests/test_contract_fixtures.py app/Dockerfile services/pipeline/Dockerfile \
  docker-compose.yml .github/workflows/containers.yml .env.example docs/backend.md services/pipeline/README.md
git commit -m "feat(images): the image policy document, both loaders, packaged into both images (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The scan-result contract and `evaluate()`

**Files:**
- Create: `tests/contract-fixtures/image-scan-result.json`
- Create: `app/src/lib/images/scan-result.ts`, `services/pipeline/src/pipeline/images/scan_result.py`
- Modify: `services/pipeline/src/pipeline/images/policy.py` (add `Verdict`, `evaluate`)
- Modify: `tests/contract-fixtures/image-policy.json` (add `evaluate_now`, `base_result`, `evaluate_cases`)
- Modify: `app/src/__tests__/contract-fixtures.test.ts`, `services/pipeline/tests/test_contract_fixtures.py`, `tests/contract-fixtures/README.md`
- Test: `services/pipeline/tests/test_image_evaluate.py` (new)

**Interfaces:**
- Consumes: `isImageReference`, `imageReferenceSchema`, `imageDigestSchema` (Task 1 TS); `IMAGE_SCAN_KINDS` (Task 1 TS); `pipeline.images.reference.{is_image_reference, is_image_digest, registry_host}`, `pipeline.images.status.SCAN_KINDS` (Task 1); `ImagePolicy`, `registry_allowed` (Task 2).
- Produces (TS, `@/lib/images/scan-result`): `SEVERITIES`, `imageScanResultSchema` (the LENIENT reader the UI will render in C-3), `type ImageScanResult`.
- Produces (Python): `pipeline.images.scan_result.{SEVERITIES, MAX_TOP_FINDINGS, ScanResultError, Finding, ScanResult, parse_scan_result}` and `pipeline.images.policy.{Verdict, evaluate}` with `evaluate(result: ScanResult, policy: ImagePolicy, *, now: datetime) -> Verdict`. `Verdict.as_json()` returns the spec §4.1 verdict: `{pass, reasons, counts, fixed_counts, kev, max_risk, evaluated_at, policy_version}`.

- [ ] **Step 1: The scan-result fixture**

`tests/contract-fixtures/image-scan-result.json`. A case is either `doc` (used as-is) or `patch` shallow-merged onto `document` followed by deleting `remove` keys.
```json
{
  "style": "document",
  "$comment": "C-1 (container-images spec §6.4): the scanner's result.json. The scanner image (C-2) is its writer. The pipeline parses it as UNTRUSTED data (pipeline/images/scan_result.py): unknown keys are ignored, but it is strict on version == 1, types, digests and top ≤ 25 findings. The app reads the same document out of image_scans.result for C-3's dashboard (app/src/lib/images/scan-result.ts): lenient, it survives a newer version. A failed scan carries only its identity plus `error`. `kev` is the COMPLETE list of KEV ids; `top` is the ≤ 25 findings by risk, descending; `fixed_counts` counts findings with a fix available.",
  "document": {
    "version": 1,
    "kind": "admission",
    "reference": "ghcr.io/example/satpy-runtime",
    "tag": "1.4.2",
    "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "platform_digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "platform": { "os": "linux", "architecture": "amd64" },
    "size_bytes": 812345678,
    "config": { "user": "", "entrypoint": ["/entry.sh"], "cmd": [] },
    "scanner": { "syft": "1.18.1", "grype": "0.92.0", "db_built_at": "2026-09-12T06:00:00Z" },
    "sbom_ref": "scans/7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f/0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a/sbom.syft.json",
    "findings_ref": "scans/7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f/0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a/findings.grype.json",
    "counts": { "critical": 0, "high": 3, "medium": 12, "low": 40, "negligible": 5, "unknown": 1 },
    "fixed_counts": { "critical": 0, "high": 1, "medium": 4, "low": 2, "negligible": 0, "unknown": 0 },
    "kev": [],
    "max_risk": 0.42,
    "top": [
      { "id": "CVE-2026-1234", "severity": "high", "package": "libxml2", "version": "2.12.7",
        "fixed_in": "2.12.9", "kev": false, "epss": 0.31, "risk": 0.42, "published_at": "2026-07-01" }
    ],
    "tag_drift": null,
    "error": null
  },
  "cases": [
    { "name": "the admission sample", "app": "accept", "pipeline": "accept" },
    { "name": "a failed scan carries only its identity and the error",
      "doc": { "version": 1, "kind": "admission", "reference": "docker.io/library/python", "tag": "3.12-slim", "error": "MANIFEST_UNKNOWN: manifest unknown" },
      "app": "accept", "pipeline": "accept" },
    { "name": "a rescan that saw the tag drift", "patch": { "kind": "rescan", "tag_drift": { "current_digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "drifted": true } }, "app": "accept", "pipeline": "accept" },
    { "name": "an unknown top-level key (both sides ignore it)", "patch": { "syft_warnings": [] }, "app": "accept", "pipeline": "accept" },
    { "name": "a newer result version (the reader survives it; the parser refuses a scanner it was not built for)", "patch": { "version": 2 }, "app": "accept", "pipeline": "reject" },
    { "name": "a successful scan without its digest", "remove": ["digest"], "app": "reject", "pipeline": "reject" },
    { "name": "a malformed digest", "patch": { "digest": "sha256:XYZ" }, "app": "reject", "pipeline": "reject" },
    { "name": "a reference carrying a tag", "patch": { "reference": "ghcr.io/example/satpy-runtime:1.4.2" }, "app": "reject", "pipeline": "reject" },
    { "name": "counts missing a severity", "patch": { "counts": { "critical": 0, "high": 3, "medium": 12, "low": 40, "negligible": 5 } }, "app": "reject", "pipeline": "reject" },
    { "name": "a negative count", "patch": { "counts": { "critical": -1, "high": 3, "medium": 12, "low": 40, "negligible": 5, "unknown": 1 } }, "app": "reject", "pipeline": "reject" },
    { "name": "a finding with an unknown severity",
      "patch": { "top": [ { "id": "CVE-2026-1234", "severity": "urgent", "package": "libxml2", "version": "2.12.7", "fixed_in": "2.12.9", "kev": false, "epss": 0.31, "risk": 0.42, "published_at": "2026-07-01" } ] },
      "app": "reject", "pipeline": "reject" },
    { "name": "an EPSS above 1",
      "patch": { "top": [ { "id": "CVE-2026-1234", "severity": "high", "package": "libxml2", "version": "2.12.7", "fixed_in": "2.12.9", "kev": false, "epss": 1.3, "risk": 0.42, "published_at": "2026-07-01" } ] },
      "app": "reject", "pipeline": "reject" },
    { "name": "kev as a string", "patch": { "kev": "CVE-2026-0001" }, "app": "reject", "pipeline": "reject" },
    { "name": "an unknown scan kind", "patch": { "kind": "manual" }, "app": "reject", "pipeline": "reject" },
    { "name": "max_risk as a string", "patch": { "max_risk": "0.42" }, "app": "reject", "pipeline": "reject" },
    { "name": "a negative size", "patch": { "size_bytes": -1 }, "app": "reject", "pipeline": "reject" }
  ]
}
```

- [ ] **Step 2: The evaluate cases (append three keys to `image-policy.json`)**

Add these top-level keys to `tests/contract-fixtures/image-policy.json`, after `registry_cases`. A case runs `evaluate(parse_scan_result({**base_result, **result_patch}), parse_image_policy(policy_doc(document, case)), now=evaluate_now)`. `block_patch` is the same rule as `cases[]`. `reasons` is the exact ordered list, and `pass` is `reasons == []`.
```json
  "evaluate_now": "2026-09-27T00:00:00+00:00",
  "base_result": {
    "version": 1, "kind": "admission", "reference": "ghcr.io/example/satpy-runtime", "tag": "1.4.2",
    "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "platform_digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "platform": { "os": "linux", "architecture": "amd64" }, "size_bytes": 812345678,
    "config": { "user": "", "entrypoint": null, "cmd": ["python3"] },
    "scanner": { "syft": "1.18.1", "grype": "0.92.0", "db_built_at": "2026-09-26T06:00:00Z" },
    "sbom_ref": "scans/i/s/sbom.syft.json", "findings_ref": "scans/i/s/findings.grype.json",
    "counts": { "critical": 0, "high": 0, "medium": 2, "low": 5, "negligible": 1, "unknown": 0 },
    "fixed_counts": { "critical": 0, "high": 0, "medium": 1, "low": 0, "negligible": 0, "unknown": 0 },
    "kev": [], "max_risk": 0.02,
    "top": [ { "id": "CVE-2026-0100", "severity": "medium", "package": "zlib", "version": "1.3", "fixed_in": "1.3.1", "kev": false, "epss": 0.02, "risk": 0.02, "published_at": "2026-06-01" } ],
    "tag_drift": null, "error": null
  },
  "evaluate_cases": [
    { "name": "a clean image passes", "reasons": [] },
    { "name": "any KEV blocks, whatever its severity",
      "result_patch": { "kev": ["CVE-2026-0001"], "top": [ { "id": "CVE-2026-0001", "severity": "high", "package": "curl", "version": "8.0", "fixed_in": "8.1", "kev": true, "epss": 0.05, "risk": 0.9, "published_at": "2026-09-01" } ] },
      "reasons": ["kev:CVE-2026-0001"] },
    { "name": "KEV rule off", "block_patch": { "kev": false },
      "result_patch": { "kev": ["CVE-2026-0001"], "top": [ { "id": "CVE-2026-0001", "severity": "high", "package": "curl", "version": "8.0", "fixed_in": "8.1", "kev": true, "epss": 0.05, "risk": 0.9, "published_at": "2026-09-01" } ] },
      "reasons": [] },
    { "name": "a fixed CRITICAL blocks, named by package",
      "result_patch": { "counts": { "critical": 1, "high": 0, "medium": 2, "low": 5, "negligible": 1, "unknown": 0 }, "fixed_counts": { "critical": 1, "high": 0, "medium": 1, "low": 0, "negligible": 0, "unknown": 0 },
        "top": [ { "id": "CVE-2026-0002", "severity": "critical", "package": "openssl", "version": "3.0.13", "fixed_in": "3.0.15", "kev": false, "epss": 0.01, "risk": 0.5, "published_at": "2026-09-20" } ] },
      "reasons": ["critical_fixed:openssl"] },
    { "name": "a fixed CRITICAL beyond the top 25 still blocks, unnamed",
      "result_patch": { "counts": { "critical": 2, "high": 0, "medium": 2, "low": 5, "negligible": 1, "unknown": 0 }, "fixed_counts": { "critical": 2, "high": 0, "medium": 1, "low": 0, "negligible": 0, "unknown": 0 }, "top": [] },
      "reasons": ["critical_fixed:*"] },
    { "name": "an unfixed CRITICAL published 41 days ago blocks",
      "result_patch": { "top": [ { "id": "CVE-2026-0003", "severity": "critical", "package": "glibc", "version": "2.36", "fixed_in": null, "kev": false, "epss": 0.01, "risk": 0.3, "published_at": "2026-08-17" } ] },
      "reasons": ["critical_unfixed_age:CVE-2026-0003:41d"] },
    { "name": "an unfixed CRITICAL exactly 30 days old is still in its grace period",
      "result_patch": { "top": [ { "id": "CVE-2026-0003", "severity": "critical", "package": "glibc", "version": "2.36", "fixed_in": null, "kev": false, "epss": 0.01, "risk": 0.3, "published_at": "2026-08-28" } ] },
      "reasons": [] },
    { "name": "an unfixed CRITICAL of unknown age blocks (no date, no grace)",
      "result_patch": { "top": [ { "id": "CVE-2026-0004", "severity": "critical", "package": "glibc", "version": "2.36", "fixed_in": null, "kev": false, "epss": null, "risk": 0.3, "published_at": null } ] },
      "reasons": ["critical_unfixed_age:CVE-2026-0004:unknown"] },
    { "name": "a fixed HIGH with EPSS 0.31 blocks",
      "result_patch": { "top": [ { "id": "CVE-2026-1234", "severity": "high", "package": "libxml2", "version": "2.12.7", "fixed_in": "2.12.9", "kev": false, "epss": 0.31, "risk": 0.42, "published_at": "2026-07-01" } ] },
      "reasons": ["high_fixed_epss:CVE-2026-1234:0.31"] },
    { "name": "a fixed HIGH exactly at the EPSS threshold blocks",
      "result_patch": { "top": [ { "id": "CVE-2026-0005", "severity": "high", "package": "expat", "version": "2.5", "fixed_in": "2.6", "kev": false, "epss": 0.1, "risk": 0.2, "published_at": "2026-07-01" } ] },
      "reasons": ["high_fixed_epss:CVE-2026-0005:0.1"] },
    { "name": "a fixed HIGH with no EPSS score never meets the threshold",
      "result_patch": { "top": [ { "id": "CVE-2026-0005", "severity": "high", "package": "expat", "version": "2.5", "fixed_in": "2.6", "kev": false, "epss": null, "risk": 0.2, "published_at": "2026-07-01" } ] },
      "reasons": [] },
    { "name": "an unfixed HIGH passes under the default policy",
      "result_patch": { "top": [ { "id": "CVE-2026-0006", "severity": "high", "package": "tar", "version": "1.34", "fixed_in": null, "kev": false, "epss": 0.5, "risk": 0.4, "published_at": "2026-01-01" } ] },
      "reasons": [] },
    { "name": "an unfixed HIGH blocks when the policy turns high_unfixed on", "block_patch": { "high_unfixed": true },
      "result_patch": { "top": [ { "id": "CVE-2026-0006", "severity": "high", "package": "tar", "version": "1.34", "fixed_in": null, "kev": false, "epss": 0.5, "risk": 0.4, "published_at": "2026-01-01" } ] },
      "reasons": ["high_unfixed:CVE-2026-0006"] },
    { "name": "one byte over the size cap", "result_patch": { "size_bytes": 4294967297 }, "reasons": ["image_too_large"] },
    { "name": "exactly at the size cap", "result_patch": { "size_bytes": 4294967296 }, "reasons": [] },
    { "name": "a registry outside the policy", "result_patch": { "reference": "quay.io/example/tool" }, "reasons": ["registry_not_allowed"] },
    { "name": "a private ECR registry matches the wildcard pattern", "result_patch": { "reference": "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/tool" }, "reasons": [] },
    { "name": "reasons come in rule order: registry, size, KEV, CRITICAL, HIGH",
      "result_patch": { "reference": "quay.io/example/tool", "kev": ["CVE-2026-0001"],
        "counts": { "critical": 1, "high": 1, "medium": 0, "low": 0, "negligible": 0, "unknown": 0 }, "fixed_counts": { "critical": 1, "high": 1, "medium": 0, "low": 0, "negligible": 0, "unknown": 0 },
        "top": [
          { "id": "CVE-2026-0001", "severity": "high", "package": "curl", "version": "8.0", "fixed_in": "8.1", "kev": true, "epss": 0.4, "risk": 0.9, "published_at": "2026-09-01" },
          { "id": "CVE-2026-0002", "severity": "critical", "package": "openssl", "version": "3.0.13", "fixed_in": "3.0.15", "kev": false, "epss": 0.01, "risk": 0.5, "published_at": "2026-09-20" } ] },
      "reasons": ["registry_not_allowed", "kev:CVE-2026-0001", "critical_fixed:openssl", "high_fixed_epss:CVE-2026-0001:0.4"] }
  ]
```

- [ ] **Step 3: Write the failing consumers**

In `app/src/__tests__/contract-fixtures.test.ts`, add `import { imageScanResultSchema } from "@/lib/images/scan-result";` and append:
```ts
describe("image scan result contract (tests/contract-fixtures/image-scan-result.json)", () => {
  const fixture = loadFixture("image-scan-result.json") as unknown as {
    document: Record<string, unknown>;
    cases: {
      name: string;
      doc?: unknown;
      patch?: Record<string, unknown>;
      remove?: string[];
      app: "accept" | "reject";
    }[];
  };

  function scanDoc(c: (typeof fixture.cases)[number]): unknown {
    if (c.doc !== undefined) return c.doc;
    const doc: Record<string, unknown> = { ...fixture.document, ...(c.patch ?? {}) };
    for (const key of c.remove ?? []) delete doc[key];
    return doc;
  }

  it.each(fixture.cases)("$app: $name", (c) => {
    expect(imageScanResultSchema.safeParse(scanDoc(c)).success).toBe(c.app === "accept");
  });
});
```

In `services/pipeline/tests/test_contract_fixtures.py`, add `import datetime as dt` to the imports and `IMAGE_SCAN_RESULT = _load("image-scan-result.json")` to the constants, then append:
```python
def _scan_doc(case: dict) -> dict:
    if "doc" in case:
        return case["doc"]
    doc = {**IMAGE_SCAN_RESULT["document"], **case.get("patch", {})}
    for key in case.get("remove", []):
        doc.pop(key, None)
    return doc


@pytest.mark.parametrize("case", IMAGE_SCAN_RESULT["cases"], ids=lambda c: c["name"])
def test_image_scan_result_cases(case):
    from pipeline.images.scan_result import ScanResultError, parse_scan_result

    if case["pipeline"] == "accept":
        parse_scan_result(_scan_doc(case))
    else:
        with pytest.raises(ScanResultError):
            parse_scan_result(_scan_doc(case))


@pytest.mark.parametrize("case", IMAGE_POLICY["evaluate_cases"], ids=lambda c: c["name"])
def test_image_policy_evaluate_cases(case):
    from pipeline.images.policy import evaluate, parse_image_policy
    from pipeline.images.scan_result import parse_scan_result

    policy = parse_image_policy(_policy_doc(IMAGE_POLICY["document"], case))
    result = parse_scan_result({**IMAGE_POLICY["base_result"], **case.get("result_patch", {})})
    now = dt.datetime.fromisoformat(IMAGE_POLICY["evaluate_now"])
    verdict = evaluate(result, policy, now=now)
    assert list(verdict.reasons) == case["reasons"]
    assert verdict.passed is (case["reasons"] == [])
```

- [ ] **Step 4: Run and watch them fail**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts`, which should FAIL (module not found).
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py -q -k "scan_result or evaluate"`, which should FAIL (`ModuleNotFoundError: pipeline.images.scan_result`).

- [ ] **Step 5: The TS reader**

`app/src/lib/images/scan-result.ts`:
```ts
/**
 * The scanner's `result.json` as the APP reads it back out of
 * `image_scans.result` (C-1, container-images spec §6.4). This is the
 * LENIENT reader: unknown keys are stripped and a newer `version` is
 * tolerated, so the C-3 dashboard survives a newer scanner. The pipeline's
 * parser (`pipeline/images/scan_result.py`) is the strict side. Pinned by
 * `tests/contract-fixtures/image-scan-result.json`.
 */
import { z } from "zod";
import { imageDigestSchema, imageReferenceSchema } from "./reference";
import { IMAGE_SCAN_KINDS } from "./status";

export const SEVERITIES = ["critical", "high", "medium", "low", "negligible", "unknown"] as const;

const count = z.number().int().min(0);
const severityCountsSchema = z.object({
  critical: count,
  high: count,
  medium: count,
  low: count,
  negligible: count,
  unknown: count,
});

const findingSchema = z.object({
  id: z.string().min(1),
  severity: z.enum(SEVERITIES),
  package: z.string().min(1),
  version: z.string(),
  fixed_in: z.string().nullable(),
  kev: z.boolean(),
  epss: z.number().min(0).max(1).nullable(),
  risk: z.number().min(0),
  published_at: z.string().nullable(),
});

const SUCCESS_KEYS = [
  "digest",
  "platform_digest",
  "platform",
  "size_bytes",
  "config",
  "scanner",
  "sbom_ref",
  "findings_ref",
  "counts",
  "fixed_counts",
  "kev",
  "max_risk",
  "top",
] as const;

export const imageScanResultSchema = z
  .object({
    version: z.number().int().min(1),
    kind: z.enum(IMAGE_SCAN_KINDS),
    reference: imageReferenceSchema,
    tag: z.string().min(1),
    error: z.string().min(1).nullable().default(null),
    digest: imageDigestSchema.optional(),
    platform_digest: imageDigestSchema.optional(),
    platform: z.object({ os: z.string().min(1), architecture: z.string().min(1) }).optional(),
    size_bytes: z.number().int().min(0).optional(),
    config: z
      .object({
        user: z.string(),
        entrypoint: z.array(z.string()).nullable(),
        cmd: z.array(z.string()).nullable(),
      })
      .optional(),
    scanner: z
      .object({ syft: z.string(), grype: z.string(), db_built_at: z.string() })
      .optional(),
    sbom_ref: z.string().min(1).optional(),
    findings_ref: z.string().min(1).optional(),
    counts: severityCountsSchema.optional(),
    fixed_counts: severityCountsSchema.optional(),
    kev: z.array(z.string().min(1)).optional(),
    max_risk: z.number().min(0).optional(),
    top: z.array(findingSchema).optional(),
    tag_drift: z
      .object({ current_digest: imageDigestSchema.nullable(), drifted: z.boolean() })
      .nullable()
      .optional(),
  })
  .superRefine((doc, ctx) => {
    if (doc.error !== null) return; // a failed scan carries only its identity
    for (const key of SUCCESS_KEYS) {
      if (doc[key] === undefined) {
        ctx.addIssue({ code: "custom", path: [key], message: `${key} is required on a successful scan` });
      }
    }
  });

export type ImageScanResult = z.infer<typeof imageScanResultSchema>;
```

- [ ] **Step 6: The Python parser**

`services/pipeline/src/pipeline/images/scan_result.py`:
```python
"""The scanner's ``result.json`` (C-1, container-images spec §6.4).

The pipeline is the only consumer, and it treats the document as UNTRUSTED:
the scanner parses hostile image content, so its summary is data, never
instructions (ADR 0021). Unknown keys are ignored. Types, digests, the
reference grammar, ``version == 1`` and ``len(top) <= 25`` are enforced.
A failed scan carries only ``version``, ``kind``, ``reference``, ``tag`` and a
non-blank ``error``.

Pinned by ``tests/contract-fixtures/image-scan-result.json`` against the app's
lenient reader ``app/src/lib/images/scan-result.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.images.reference import is_image_digest, is_image_reference
from pipeline.images.status import SCAN_KINDS

SEVERITIES = ("critical", "high", "medium", "low", "negligible", "unknown")
MAX_TOP_FINDINGS = 25
SCAN_RESULT_VERSION = 1


class ScanResultError(ValueError):
    """The scanner's result is not a usable §6.4 document."""


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    package: str
    version: str
    fixed_in: str | None
    kev: bool
    epss: float | None
    risk: float
    published_at: str | None


@dataclass(frozen=True)
class ScanResult:
    kind: str
    reference: str
    tag: str
    error: str | None
    digest: str | None = None
    platform_digest: str | None = None
    platform: dict[str, str] | None = None
    size_bytes: int | None = None
    config: dict[str, Any] | None = None
    scanner: dict[str, str] | None = None
    sbom_ref: str | None = None
    findings_ref: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    fixed_counts: dict[str, int] = field(default_factory=dict)
    kev: tuple[str, ...] = ()
    max_risk: float = 0.0
    top: tuple[Finding, ...] = ()
    tag_drift: dict[str, Any] | None = None


def _obj(raw: Any, what: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ScanResultError(f"{what} must be an object")
    return raw


def _text(raw: Any, what: str, *, blank_ok: bool = False) -> str:
    if not isinstance(raw, str) or (not blank_ok and not raw.strip()):
        raise ScanResultError(f"{what} must be a {'string' if blank_ok else 'non-empty string'}")
    return raw


def _number(raw: Any, what: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ScanResultError(f"{what} must be a number")
    if raw < minimum or (maximum is not None and raw > maximum):
        raise ScanResultError(f"{what} is out of range")
    return float(raw)


def _count(raw: Any, what: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ScanResultError(f"{what} must be a non-negative integer")
    return raw


def _counts(raw: Any, what: str) -> dict[str, int]:
    doc = _obj(raw, what)
    return {sev: _count(doc.get(sev), f"{what}.{sev}") for sev in SEVERITIES}


def _digest(raw: Any, what: str) -> str:
    if not isinstance(raw, str) or not is_image_digest(raw):
        raise ScanResultError(f"{what} must be a sha256 digest")
    return raw


def _string_list(raw: Any, what: str, *, nullable: bool = False) -> list[str] | None:
    if raw is None and nullable:
        return None
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise ScanResultError(f"{what} must be a list of strings")
    return raw


def _finding(raw: Any, index: int) -> Finding:
    what = f"top[{index}]"
    doc = _obj(raw, what)
    severity = doc.get("severity")
    if severity not in SEVERITIES:
        raise ScanResultError(f"{what}.severity must be one of {SEVERITIES}")
    fixed_in = doc.get("fixed_in")
    published_at = doc.get("published_at")
    epss = doc.get("epss")
    kev = doc.get("kev")
    if not isinstance(kev, bool):
        raise ScanResultError(f"{what}.kev must be true or false")
    return Finding(
        id=_text(doc.get("id"), f"{what}.id"),
        severity=severity,
        package=_text(doc.get("package"), f"{what}.package"),
        version=_text(doc.get("version"), f"{what}.version", blank_ok=True),
        fixed_in=None if fixed_in is None else _text(fixed_in, f"{what}.fixed_in"),
        kev=kev,
        epss=None if epss is None else _number(epss, f"{what}.epss", maximum=1.0),
        risk=_number(doc.get("risk"), f"{what}.risk"),
        published_at=None if published_at is None else _text(published_at, f"{what}.published_at"),
    )


def parse_scan_result(raw: Any) -> ScanResult:
    doc = _obj(raw, "scan result")
    version = doc.get("version")
    if type(version) is not int or version != SCAN_RESULT_VERSION:
        raise ScanResultError(f"unsupported scan result version {version!r}")
    kind = doc.get("kind")
    if kind not in SCAN_KINDS:
        raise ScanResultError(f"kind must be one of {SCAN_KINDS}")
    reference = doc.get("reference")
    if not isinstance(reference, str) or not is_image_reference(reference):
        raise ScanResultError("reference must be a normalized image reference")
    tag = _text(doc.get("tag"), "tag")
    error = doc.get("error")
    if error is not None:
        return ScanResult(kind=kind, reference=reference, tag=tag, error=_text(error, "error"))

    platform = _obj(doc.get("platform"), "platform")
    config = _obj(doc.get("config"), "config")
    scanner = _obj(doc.get("scanner"), "scanner")
    kev = doc.get("kev")
    if not isinstance(kev, list) or not all(isinstance(k, str) and k.strip() for k in kev):
        raise ScanResultError("kev must be a list of vulnerability ids")
    top = doc.get("top")
    if not isinstance(top, list):
        raise ScanResultError("top must be a list")
    if len(top) > MAX_TOP_FINDINGS:
        raise ScanResultError(f"top carries at most {MAX_TOP_FINDINGS} findings")
    drift = doc.get("tag_drift")
    if drift is not None:
        drift_doc = _obj(drift, "tag_drift")
        current = drift_doc.get("current_digest")
        if current is not None:
            _digest(current, "tag_drift.current_digest")
        if not isinstance(drift_doc.get("drifted"), bool):
            raise ScanResultError("tag_drift.drifted must be true or false")
    size = doc.get("size_bytes")
    return ScanResult(
        kind=kind,
        reference=reference,
        tag=tag,
        error=None,
        digest=_digest(doc.get("digest"), "digest"),
        platform_digest=_digest(doc.get("platform_digest"), "platform_digest"),
        platform={
            "os": _text(platform.get("os"), "platform.os"),
            "architecture": _text(platform.get("architecture"), "platform.architecture"),
        },
        size_bytes=_count(size, "size_bytes"),
        config={
            "user": _text(config.get("user"), "config.user", blank_ok=True),
            "entrypoint": _string_list(
                config.get("entrypoint"), "config.entrypoint", nullable=True
            ),
            "cmd": _string_list(config.get("cmd"), "config.cmd", nullable=True),
        },
        scanner={
            key: _text(scanner.get(key), f"scanner.{key}")
            for key in ("syft", "grype", "db_built_at")
        },
        sbom_ref=_text(doc.get("sbom_ref"), "sbom_ref"),
        findings_ref=_text(doc.get("findings_ref"), "findings_ref"),
        counts=_counts(doc.get("counts"), "counts"),
        fixed_counts=_counts(doc.get("fixed_counts"), "fixed_counts"),
        kev=tuple(kev),
        max_risk=_number(doc.get("max_risk"), "max_risk"),
        top=tuple(_finding(f, i) for i, f in enumerate(top)),
        tag_drift=drift,
    )
```

- [ ] **Step 7: `Verdict` and `evaluate()` (append to `pipeline/images/policy.py`)**

At the top of `policy.py`, add `import datetime as dt` to the imports and `from pipeline.images.reference import registry_host` plus `from pipeline.images.scan_result import ScanResult, ScanResultError` below `from typing import Any`. Then append:
```python
# ---------------------------------------------------------------------------
# evaluate (spec §7.3) — pure; reasons are stable strings the dashboard shows
# verbatim. It works over `kev` (complete), `top` (≤ 25 by risk) and
# `fixed_counts`: a fixed CRITICAL that fell outside `top` still blocks, as
# `critical_fixed:*`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasons: tuple[str, ...]
    counts: dict[str, int]
    fixed_counts: dict[str, int]
    kev: tuple[str, ...]
    max_risk: float
    evaluated_at: dt.datetime
    policy_version: int

    def as_json(self) -> dict[str, Any]:
        """The spec §4.1 ``verdict`` jsonb (``container_images.verdict`` and
        ``image_scans.result.verdict``)."""
        return {
            "pass": self.passed,
            "reasons": list(self.reasons),
            "counts": dict(self.counts),
            "fixed_counts": dict(self.fixed_counts),
            "kev": list(self.kev),
            "max_risk": self.max_risk,
            "evaluated_at": self.evaluated_at.isoformat(),
            "policy_version": self.policy_version,
        }


def _age_days(published_at: str | None, now: dt.datetime) -> int | None:
    if not published_at:
        return None
    try:
        published = dt.date.fromisoformat(published_at[:10])
    except ValueError:
        return None
    return (now.date() - published).days


def evaluate(result: ScanResult, policy: ImagePolicy, *, now: dt.datetime) -> Verdict:
    """Pure: the same result and policy always give the same verdict. Rule
    order (and so reason order): registry, size, KEV, fixed CRITICAL,
    unfixed-CRITICAL age, fixed-HIGH EPSS, unfixed HIGH."""
    if result.error is not None or result.size_bytes is None:
        raise ScanResultError("a failed scan has no verdict")
    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if not registry_allowed(registry_host(result.reference), policy.allowed_registries):
        add("registry_not_allowed")
    if result.size_bytes > policy.max_image_bytes:
        add("image_too_large")
    block = policy.block
    if block.kev:
        for cve in result.kev:
            add(f"kev:{cve}")
    if block.critical_fixed:
        named = False
        for f in result.top:
            if f.severity == "critical" and f.fixed_in:
                add(f"critical_fixed:{f.package}")
                named = True
        if not named and result.fixed_counts.get("critical", 0) > 0:
            add("critical_fixed:*")
    if block.critical_unfixed_older_than_days is not None:
        for f in result.top:
            if f.severity == "critical" and not f.fixed_in:
                age = _age_days(f.published_at, now)
                if age is None:
                    add(f"critical_unfixed_age:{f.id}:unknown")
                elif age > block.critical_unfixed_older_than_days:
                    add(f"critical_unfixed_age:{f.id}:{age}d")
    if block.high_fixed_epss_at_least is not None:
        for f in result.top:
            if (
                f.severity == "high"
                and f.fixed_in
                and f.epss is not None
                and f.epss >= block.high_fixed_epss_at_least
            ):
                add(f"high_fixed_epss:{f.id}:{format(f.epss, 'g')}")
    if block.high_unfixed:
        for f in result.top:
            if f.severity == "high" and not f.fixed_in:
                add(f"high_unfixed:{f.id}")
    return Verdict(
        passed=not reasons,
        reasons=tuple(reasons),
        counts=dict(result.counts),
        fixed_counts=dict(result.fixed_counts),
        kev=result.kev,
        max_risk=result.max_risk,
        evaluated_at=now,
        policy_version=policy.version,
    )
```
(`policy.py` imports `scan_result.py`, and `scan_result.py` imports only `reference` and `status`, so there is no cycle.)

- [ ] **Step 8: Direct pytest for what the fixture cannot express**

`services/pipeline/tests/test_image_evaluate.py`:
```python
"""evaluate() and parse_scan_result() edges the fixture cannot express
(C-1, container-images spec §6.4/§7.3)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from pipeline.images.policy import evaluate, load_image_policy
from pipeline.images.scan_result import MAX_TOP_FINDINGS, ScanResultError, parse_scan_result

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures"
POLICY_DOC = json.loads((FIXTURES / "image-policy.json").read_text())
BASE = POLICY_DOC["base_result"]
NOW = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
POLICY = load_image_policy(FIXTURES.parents[1] / "infra" / "image-policy" / "default.json")


def test_a_failed_scan_has_no_verdict():
    failed = parse_scan_result(
        {
            "version": 1,
            "kind": "admission",
            "reference": BASE["reference"],
            "tag": "x",
            "error": "boom",
        }
    )
    with pytest.raises(ScanResultError):
        evaluate(failed, POLICY, now=NOW)


def test_the_verdict_is_the_spec_shape():
    verdict = evaluate(parse_scan_result(BASE), POLICY, now=NOW).as_json()
    assert set(verdict) == {
        "pass", "reasons", "counts", "fixed_counts", "kev", "max_risk", "evaluated_at",
        "policy_version",
    }
    assert verdict["pass"] is True and verdict["policy_version"] == 1
    assert verdict["evaluated_at"] == "2026-09-27T00:00:00+00:00"


def test_top_is_capped_at_25_findings():
    finding = BASE["top"][0]
    with pytest.raises(ScanResultError, match="at most"):
        parse_scan_result({**BASE, "top": [finding] * (MAX_TOP_FINDINGS + 1)})
    parse_scan_result({**BASE, "top": [finding] * MAX_TOP_FINDINGS})


def test_a_blank_error_is_not_a_failure_marker():
    with pytest.raises(ScanResultError):
        parse_scan_result({**BASE, "error": "   "})


def test_evaluate_is_pure():
    result = parse_scan_result(BASE)
    assert evaluate(result, POLICY, now=NOW) == evaluate(result, POLICY, now=NOW)
```

- [ ] **Step 9: Run and watch everything pass**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts`, which should PASS.
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py tests/test_image_evaluate.py -q`, which should PASS.

- [ ] **Step 10: README and gates**

Add to the C-1 section of `tests/contract-fixtures/README.md`:
`- \`image-scan-result.json\` is style \`document\`. It is the scanner's \`result.json\` (spec §6.4). A case is \`doc\` (used as-is), or \`patch\` merged onto \`document\` minus the \`remove\` keys. The pipeline parser is the STRICT side (untrusted input: \`version == 1\`, \`top\` ≤ 25, digests and the reference grammar). The app reader is lenient (a newer \`version\` and unknown keys pass). A failed scan is \`{version, kind, reference, tag, error}\`.`

Run the gates, which should all be green: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add tests/contract-fixtures/image-scan-result.json tests/contract-fixtures/image-policy.json tests/contract-fixtures/README.md \
  app/src/lib/images/scan-result.ts app/src/__tests__/contract-fixtures.test.ts \
  services/pipeline/src/pipeline/images/scan_result.py services/pipeline/src/pipeline/images/policy.py \
  services/pipeline/tests/test_contract_fixtures.py services/pipeline/tests/test_image_evaluate.py
git commit -m "feat(images): scan-result contract and the pure policy evaluate() (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The DB-backed image gate (app, not yet wired)

**Files:**
- Create: `app/src/lib/images/storage.ts`, `app/src/lib/images/gate.ts`
- Test: `app/src/__tests__/images-gate.test.ts` (new), `app/src/__tests__/images-storage.test.ts` (new)

**Interfaces:**
- Consumes: `ImageSnapshot` (Task 1), `ImageStatus`/`ImageGateReason` (Task 1), `loadImagePolicy`/`ImagePolicyUnavailable` (Task 2).
- Produces:
  ```ts
  // @/lib/images/storage
  export interface ImageGateRow {
    id: string; reference: string; digest: string | null; status: ImageStatus;
    last_scanned_at: Date | null; registry_connection_id: string | null;
    registry_connection_group_id: string | null; // NULL when the connection is soft-deleted
  }
  export async function getImageForGate(id: string): Promise<ImageGateRow | null>;
  // @/lib/images/gate
  export interface ImageGateRefusal { reason: ImageGateReason; message: string }
  export function evaluateImageGate(input: { row: ImageGateRow | null; snapshot: ImageSnapshot; processGroupId: string; now: Date; scanWindowDays: number }): ImageGateRefusal | null;
  export async function checkImageGate(snapshot: ImageSnapshot | null, processGroupId: string, options?: { now?: Date; env?: Record<string, string | undefined> }): Promise<ImageGateRefusal | null>; // throws ImagePolicyUnavailable
  export function imageGateRefused(refusal: ImageGateRefusal): Response; // 422 {error, code}
  export function imagePolicyUnavailable(err: Error): Response;          // 503 {error, code: "image_policy_unavailable"}
  ```
  `checkImageGate` takes the SNAPSHOT (or `null` for kind 1), not the runtime, so this task does not depend on Task 5's union.

- [ ] **Step 1: Write the failing tests**

`app/src/__tests__/images-gate.test.ts`:
```ts
// @vitest-environment node
/**
 * The C-1 deploy gate (container-images spec §3): the row named by the
 * snapshot must exist, be approved, match reference+digest, be fresh, and,
 * when it carries a registry credential, that credential must be the
 * process's group's.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({ getImageForGate: vi.fn() }));

import { checkImageGate, evaluateImageGate, imageGateRefused } from "@/lib/images/gate";
import { ImagePolicyUnavailable, resetImagePolicyCache } from "@/lib/images/policy";
import { getImageForGate, type ImageGateRow } from "@/lib/images/storage";

const DIGEST = "sha256:" + "a".repeat(64);
const OTHER_DIGEST = "sha256:" + "b".repeat(64);
const SNAP = { id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", reference: "ghcr.io/example/satpy-runtime", digest: DIGEST };
const NOW = new Date("2026-09-27T12:00:00.000Z");
const DAY = 86_400_000;
const GROUP = "earth-observation";

function row(overrides: Partial<ImageGateRow> = {}): ImageGateRow {
  return {
    id: SNAP.id,
    reference: SNAP.reference,
    digest: DIGEST,
    status: "approved",
    last_scanned_at: new Date(NOW.getTime() - DAY),
    registry_connection_id: null,
    registry_connection_group_id: null,
    ...overrides,
  };
}

function gate(r: ImageGateRow | null) {
  return evaluateImageGate({ row: r, snapshot: SNAP, processGroupId: GROUP, now: NOW, scanWindowDays: 30 });
}

beforeEach(() => vi.mocked(getImageForGate).mockReset());
afterEach(() => resetImagePolicyCache());

describe("evaluateImageGate", () => {
  it("passes an approved, fresh, public image whose digest matches", () => {
    expect(gate(row())).toBeNull();
  });

  it("refuses an image the registry has never seen", () => {
    expect(gate(null)?.reason).toBe("image_not_approved");
  });

  it("a pending image with no digest yet is not approved (not a digest mismatch)", () => {
    expect(gate(row({ status: "pending", digest: null, last_scanned_at: null }))?.reason).toBe(
      "image_not_approved",
    );
  });

  it.each(["scanning", "rejected", "flagged", "revoked", "scan_failed"] as const)(
    "refuses a %s image (flagged blocks deploys even though it still launches)",
    (status) => {
      const refusal = gate(row({ status }));
      expect(refusal?.reason).toBe("image_not_approved");
      expect(refusal?.message).toContain(status);
    },
  );

  it("refuses a snapshot whose digest is not the row's", () => {
    expect(gate(row({ digest: OTHER_DIGEST }))?.reason).toBe("image_digest_mismatch");
  });

  it("refuses a snapshot whose reference is not the row's", () => {
    expect(gate(row({ reference: "ghcr.io/example/other" }))?.reason).toBe("image_digest_mismatch");
  });

  it("refuses a never-scanned approved row as stale", () => {
    expect(gate(row({ last_scanned_at: null }))?.reason).toBe("image_stale");
  });

  it("is still fresh at exactly scan_window_days, stale one millisecond later", () => {
    expect(gate(row({ last_scanned_at: new Date(NOW.getTime() - 30 * DAY) }))).toBeNull();
    expect(gate(row({ last_scanned_at: new Date(NOW.getTime() - 30 * DAY - 1) }))?.reason).toBe(
      "image_stale",
    );
  });

  it("refuses an image pulled with another group's credential", () => {
    expect(
      gate(row({ registry_connection_id: "c-1", registry_connection_group_id: "weather" }))?.reason,
    ).toBe("image_group_mismatch");
  });

  it("a soft-deleted registry credential is 'another group', never 'public'", () => {
    expect(
      gate(row({ registry_connection_id: "c-1", registry_connection_group_id: null }))?.reason,
    ).toBe("image_group_mismatch");
  });

  it("passes an image pulled with the process's own group's credential", () => {
    expect(
      gate(row({ registry_connection_id: "c-1", registry_connection_group_id: GROUP })),
    ).toBeNull();
  });
});

describe("checkImageGate", () => {
  it("inline revisions never read the policy or the registry", async () => {
    // A deployment whose policy file is broken must still deploy inline code.
    const refusal = await checkImageGate(null, GROUP, {
      env: { PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" },
    });
    expect(refusal).toBeNull();
    expect(getImageForGate).not.toHaveBeenCalled();
  });

  it("fails closed when the policy cannot be read, before touching the DB", async () => {
    await expect(
      checkImageGate(SNAP, GROUP, { env: { PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" } }),
    ).rejects.toBeInstanceOf(ImagePolicyUnavailable);
    expect(getImageForGate).not.toHaveBeenCalled();
  });

  it("uses the policy's scan window (30 days in the default)", async () => {
    vi.mocked(getImageForGate).mockResolvedValue(
      row({ last_scanned_at: new Date(NOW.getTime() - 31 * DAY) }),
    );
    const refusal = await checkImageGate(SNAP, GROUP, { now: NOW, env: {} });
    expect(refusal?.reason).toBe("image_stale");
    expect(refusal?.message).toContain("30-day");
    expect(getImageForGate).toHaveBeenCalledWith(SNAP.id);
  });

  it("refuses everything today: no scanner has approved a row yet", async () => {
    vi.mocked(getImageForGate).mockResolvedValue(null);
    expect((await checkImageGate(SNAP, GROUP, { now: NOW, env: {} }))?.reason).toBe(
      "image_not_approved",
    );
  });
});

describe("imageGateRefused", () => {
  it("is a 422 whose code is the reason", async () => {
    const res = imageGateRefused({ reason: "image_stale", message: "m" });
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual({ error: "m", code: "image_stale" });
  });
});
```

`app/src/__tests__/images-storage.test.ts`:
```ts
// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { query } from "@/lib/db/connection";
import { getImageForGate } from "@/lib/images/storage";

beforeEach(() => vi.mocked(query).mockReset());

describe("getImageForGate", () => {
  it("joins only LIVE connections, so a soft-deleted credential reads as no group", async () => {
    vi.mocked(query).mockResolvedValue({ rows: [] } as never);
    expect(await getImageForGate("img-1")).toBeNull();
    const [sql, params] = vi.mocked(query).mock.calls[0];
    expect(sql).toContain("FROM stac_higher.container_images i");
    expect(sql).toMatch(/LEFT JOIN stac_higher\.connections c\s+ON c\.id = i\.registry_connection_id AND c\.deleted_at IS NULL/);
    expect(params).toEqual(["img-1"]);
  });

  it("normalizes last_scanned_at to a Date", async () => {
    vi.mocked(query).mockResolvedValue({
      rows: [
        {
          id: "img-1", reference: "ghcr.io/x/y", digest: null, status: "pending",
          last_scanned_at: "2026-09-01T00:00:00.000Z", registry_connection_id: null,
          registry_connection_group_id: null,
        },
      ],
    } as never);
    const row = await getImageForGate("img-1");
    expect(row?.last_scanned_at).toEqual(new Date("2026-09-01T00:00:00.000Z"));
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run (from `app/`): `npx vitest run src/__tests__/images-gate.test.ts src/__tests__/images-storage.test.ts`
Expected: FAIL (modules not found).

- [ ] **Step 3: Storage**

`app/src/lib/images/storage.ts`:
```ts
/**
 * `container_images` reads for the deploy gate (C-1). C-3 grows this module
 * with the registry CRUD. The app owns the DDL (migration 030); the
 * pipeline writes statuses, verdicts and scan times.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import type { ImageStatus } from "./status";

export interface ImageGateRow {
  id: string;
  reference: string;
  digest: string | null;
  status: ImageStatus;
  last_scanned_at: Date | null;
  registry_connection_id: string | null;
  /** NULL when the row carries no credential OR its connection is soft-deleted. */
  registry_connection_group_id: string | null;
}

interface GateQueryRow extends Omit<ImageGateRow, "last_scanned_at"> {
  last_scanned_at: Date | string | null;
}

export async function getImageForGate(id: string): Promise<ImageGateRow | null> {
  await runMigrations();
  const result = await query<GateQueryRow>(
    `SELECT i.id, i.reference, i.digest, i.status, i.last_scanned_at,
            i.registry_connection_id, c.group_id AS registry_connection_group_id
       FROM stac_higher.container_images i
       LEFT JOIN stac_higher.connections c
         ON c.id = i.registry_connection_id AND c.deleted_at IS NULL
      WHERE i.id = $1`,
    [id],
  );
  const row = result.rows[0];
  if (!row) return null;
  return {
    ...row,
    last_scanned_at: row.last_scanned_at === null ? null : new Date(row.last_scanned_at),
  };
}
```

- [ ] **Step 4: The gate**

`app/src/lib/images/gate.ts`:
```ts
/**
 * The deploy gate for user images (C-1, container-images spec §3). This is
 * the `PROCESS_NETWORK_MAX` dual-enforcement pattern: the Zod shape accepts
 * all three runtime kinds, this DB-backed check refuses a kind 2/3 revision
 * unless its snapshot names an APPROVED, FRESH `container_images` row with
 * the same reference and digest that is usable by the process's group, and
 * the pipeline re-checks at launch (C-2, spec §8.4).
 *
 * Order: exists → approved → reference/digest → fresh → group. A pending
 * image therefore says "not approved" rather than "digest mismatch" (its
 * digest is still NULL). The policy is read only for a user image, so a
 * broken policy file never blocks an inline deploy, and a user-image deploy
 * fails CLOSED (503) when the policy cannot be read.
 */
import { jsonResponse } from "@/lib/http/response";
import { loadImagePolicy } from "./policy";
import type { ImageSnapshot } from "./reference";
import { getImageForGate, type ImageGateRow } from "./storage";
import type { ImageGateReason } from "./status";

const DAY_MS = 86_400_000;

export interface ImageGateRefusal {
  reason: ImageGateReason;
  message: string;
}

export function evaluateImageGate(input: {
  row: ImageGateRow | null;
  snapshot: ImageSnapshot;
  processGroupId: string;
  now: Date;
  scanWindowDays: number;
}): ImageGateRefusal | null {
  const { row, snapshot, processGroupId, now, scanWindowDays } = input;
  // "sha256:" + 12 hex characters: enough to tell digests apart in a message.
  const named = `${snapshot.reference}@${snapshot.digest.slice(0, 19)}`;
  if (!row) {
    return {
      reason: "image_not_approved",
      message: `image ${named} is not in the platform's image registry; add it and let its scan pass before deploying`,
    };
  }
  if (row.status !== "approved") {
    return {
      reason: "image_not_approved",
      message: `image ${named} is ${row.status}; only an approved image can be deployed`,
    };
  }
  if (row.reference !== snapshot.reference || row.digest !== snapshot.digest) {
    return {
      reason: "image_digest_mismatch",
      message: `image ${snapshot.id} is ${row.reference}@${row.digest ?? "(unresolved)"}, not the ${snapshot.reference}@${snapshot.digest} this revision names`,
    };
  }
  const cutoff = now.getTime() - scanWindowDays * DAY_MS;
  if (row.last_scanned_at === null || row.last_scanned_at.getTime() < cutoff) {
    const when = row.last_scanned_at
      ? `last scanned ${row.last_scanned_at.toISOString()}`
      : "never scanned";
    return {
      reason: "image_stale",
      message: `image ${named} was ${when}, outside the ${scanWindowDays}-day scan window; rescan it before deploying`,
    };
  }
  if (row.registry_connection_id !== null && row.registry_connection_group_id !== processGroupId) {
    return {
      reason: "image_group_mismatch",
      message: `image ${named} is pulled with a registry credential that belongs to another group; only that group's processes can use it`,
    };
  }
  return null;
}

/** `snapshot` is `runtime.image` for kinds 2/3 and `null` for `inline_python`.
 * Throws `ImagePolicyUnavailable` when a user image is named and the policy
 * cannot be read (the caller answers 503). */
export async function checkImageGate(
  snapshot: ImageSnapshot | null,
  processGroupId: string,
  options: { now?: Date; env?: Record<string, string | undefined> } = {},
): Promise<ImageGateRefusal | null> {
  if (snapshot === null) return null;
  const policy = loadImagePolicy(options.env);
  const row = await getImageForGate(snapshot.id);
  return evaluateImageGate({
    row,
    snapshot,
    processGroupId,
    now: options.now ?? new Date(),
    scanWindowDays: policy.scan_window_days,
  });
}

export function imageGateRefused(refusal: ImageGateRefusal): Response {
  return jsonResponse(422, { error: refusal.message, code: refusal.reason });
}

export function imagePolicyUnavailable(err: Error): Response {
  return jsonResponse(503, { error: err.message, code: "image_policy_unavailable" });
}
```

- [ ] **Step 5: Run and watch them pass**

Run (from `app/`): `npx vitest run src/__tests__/images-gate.test.ts src/__tests__/images-storage.test.ts`, which should PASS.

- [ ] **Step 6: Gate and commit**

Run `npm run verify`, which should be green.
```bash
git add app/src/lib/images/storage.ts app/src/lib/images/gate.ts \
  app/src/__tests__/images-gate.test.ts app/src/__tests__/images-storage.test.ts
git commit -m "feat(images): the DB-backed image deploy gate and its four 422 reasons (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Three runtime kinds on both sides, wired to the gate

**Files:**
- Modify: `tests/contract-fixtures/process-runtime.json`, `tests/contract-fixtures/README.md`
- Modify: `app/src/lib/processes/schemas.ts:73-250,400-431`
- Modify: `app/src/pages/api/processes/[id]/revisions.ts`
- Modify: `services/pipeline/src/pipeline/process/config.py:1-40,164-292`
- Modify: `services/pipeline/src/pipeline/process/launch.py` (the `ImageUnusable` guard and a `resolve_runtime_image` branch)
- Modify: `services/pipeline/src/pipeline/process/runner.py:95-110`
- Modify tests: `app/src/__tests__/contract-fixtures.test.ts` (runtime describe), `app/src/__tests__/processes-runtime-image.test.ts`, `app/src/__tests__/api-processes.test.ts`, `services/pipeline/tests/test_contract_fixtures.py` (runtime defaults test), `services/pipeline/tests/test_process_executor.py` (lines ~278-290, ~713), `services/pipeline/tests/test_process_triggers.py` (new runner test)
- Create test: `app/src/__tests__/processes-runtime-kinds.test.ts`

**Interfaces:**
- Consumes: `imageSnapshotSchema` (Task 1), `pipeline.images.reference.{is_image_reference, is_image_digest}` (Task 1), `checkImageGate`, `imageGateRefused`, `imagePolicyUnavailable` (Task 4), `ImagePolicyUnavailable` (Task 2).
- Produces (TS, `@/lib/processes/schemas`): `PROCESS_RUNTIME_KINDS = ["inline_python", "inline_python_on_image", "container"]`, `USER_IMAGE_RUNTIME_KINDS`, `MAX_COMMAND_ENTRIES = 64`, a three-arm `processRuntimeReadSchema` / `processRuntimeSchema` / `ProcessRuntime`. `CONTAINER_RUNTIME_REFUSAL` is DELETED.
- Produces (Python, `pipeline.process.config`): `RUNTIME_KINDS` (three), `USER_IMAGE_KINDS: frozenset[str]`, `MAX_COMMAND_ENTRIES = 64`. `ProcessRuntime` loses `image: str | None` and gains `image_id`, `image_reference`, `image_digest: str | None` and `command: tuple[str, ...] | None`. `runtime_image` becomes `str | None` (None for kinds 2/3).
- Produces (Python, `pipeline.process.launch`): `class ImageUnusable(Exception)`, `check_user_image_launchable(runtime: ProcessRuntime) -> None` (raises for kinds 2/3 in C-1; C-2 replaces the body with the DB-backed digest check).

- [ ] **Step 1: Rewrite the runtime fixture**

In `tests/contract-fixtures/process-runtime.json`:

(a) Replace the `$comment` value with:
`"Golden fixture for the process runtime shape (Phase 9 §5.6; C-1, container-images spec §3), stored in stac_higher.process_revisions.runtime. Consumed by app/src/__tests__/contract-fixtures.test.ts (processRuntimeSchema, the write gate; defaults via processRuntimeReadSchema) and services/pipeline/tests/test_contract_fixtures.py (parse_process_runtime). THREE KINDS: inline_python (a platform image chosen by the runtime_image alias, code injected), inline_python_on_image (your scanned image as the dependency bundle, our runner and your code injected), and container (the image is the process; optional `command` overrides its Cmd, never its Entrypoint or User). `image` is the immutable snapshot {id, reference, digest} of a container_images row: required for kinds 2 and 3, null/absent for kind 1 (reject/reject otherwise). `runtime_image` is required-absent (null) for kinds 2 and 3, since an alias names a PLATFORM image and combining the two means nothing (reject/reject). `command` is kind 3 only: a non-empty list of non-blank strings, at most 64 (on kinds 1 and 2 it is an unknown key: app reject / pipeline accept). The shape accepts every kind. Whether a snapshot's image may be deployed is a DB-backed check in the revisions route (422 image_not_approved | image_stale | image_group_mismatch | image_digest_mismatch), not a parse outcome, so no case here encodes it. `memory_mb`/`timeout_seconds` are the executor's per-run limits (ADR 0013); `retry` is the §5.1 RetrySpec. `network` (GOES spec §4, ADR 0018) keeps its slice-1 asymmetry on every kind: the reader accepts every level, and the write gate stores only `isolated` until the egress proxy exists. `hardware` (K-1) applies to all three kinds; a profile's `image` base is used only by kind 1. `runtime_image` (X-3) is `default` | `stactools`, absent ⇒ `default` on kind 1 for both sides."`

(b) Replace the `container` variant and add the `inline_python_on_image` variant (keep `inline_python` unchanged):
```json
    "inline_python_on_image": {
      "minimal": { "kind": "inline_python_on_image", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } },
      "defaults": {
        "kind": "inline_python_on_image",
        "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
        "runtime_image": null,
        "memory_mb": 512,
        "timeout_seconds": 900,
        "retry": { "max_attempts": 3, "backoff": "exponential" },
        "network": { "level": "isolated", "hosts": [] },
        "hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }
      }
    },
    "container": {
      "minimal": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } },
      "defaults": {
        "kind": "container",
        "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
        "command": null,
        "runtime_image": null,
        "memory_mb": 512,
        "timeout_seconds": 900,
        "retry": { "max_attempts": 3, "backoff": "exponential" },
        "network": { "level": "isolated", "hosts": [] },
        "hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }
      }
    }
```

(c) Change these existing cases (match them by `name`):
- `"inline_python carrying an image (nothing to run it in)"`: rename it to `"inline_python carrying an image (use inline_python_on_image to run code on your own image)"` and set `"pipeline": "reject"`.
- `"container with limits — SLICE-1 ASYMMETRY: shape valid, app gate refuses"`: rename it to `"container with limits (C-1: the shape is open; whether the image may run is the revisions route's DB check)"`, set `"image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }`, and set both `"app": "accept"` and `"pipeline": "accept"`.
- Leave `"container without an image"` and `"container with a blank image"` as reject/reject.

(d) Append these cases to `cases[]`:
```json
    { "name": "inline_python_on_image, minimal", "config": { "kind": "inline_python_on_image", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }, "app": "accept", "pipeline": "accept" },
    { "name": "inline_python_on_image with limits and hardware", "config": { "kind": "inline_python_on_image", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "memory_mb": 2048, "hardware": { "profile": "standard", "cpu": 2, "gpu_count": 0 } }, "app": "accept", "pipeline": "accept" },
    { "name": "inline_python_on_image without an image", "config": { "kind": "inline_python_on_image" }, "app": "reject", "pipeline": "reject" },
    { "name": "inline_python_on_image with a tag reference instead of a snapshot", "config": { "kind": "inline_python_on_image", "image": "ghcr.io/example/satpy-runtime:1.4.2" }, "app": "reject", "pipeline": "reject" },
    { "name": "inline_python_on_image with a platform alias (meaningless beside your image)", "config": { "kind": "inline_python_on_image", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "runtime_image": "default" }, "app": "reject", "pipeline": "reject" },
    { "name": "inline_python_on_image with runtime_image null explicitly", "config": { "kind": "inline_python_on_image", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "runtime_image": null }, "app": "accept", "pipeline": "accept" },
    { "name": "inline_python_on_image carrying a command (kind 3 only)", "config": { "kind": "inline_python_on_image", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "command": ["python3", "x.py"] }, "app": "reject", "pipeline": "accept" },
    { "name": "container with a command", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "command": ["tool", "--run"] }, "app": "accept", "pipeline": "accept" },
    { "name": "container with a null command (the image's own CMD)", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "command": null }, "app": "accept", "pipeline": "accept" },
    { "name": "container with an empty command", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "command": [] }, "app": "reject", "pipeline": "reject" },
    { "name": "container with a blank command entry", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "command": ["tool", "  "] }, "app": "reject", "pipeline": "reject" },
    { "name": "container with 65 command entries", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "command": ["x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x"] }, "app": "reject", "pipeline": "reject" },
    { "name": "container with a command string instead of a list", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "command": "tool --run" }, "app": "reject", "pipeline": "reject" },
    { "name": "container with a platform alias", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "runtime_image": "stactools" }, "app": "reject", "pipeline": "reject" },
    { "name": "container, snapshot id not a uuid", "config": { "kind": "container", "image": { "id": "img-1", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }, "app": "reject", "pipeline": "reject" },
    { "name": "container, snapshot digest not sha256", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha1:abc" } }, "app": "reject", "pipeline": "reject" },
    { "name": "container, snapshot digest in uppercase hex", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" } }, "app": "reject", "pipeline": "reject" },
    { "name": "container, snapshot reference carries a tag", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime:1.4.2", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }, "app": "reject", "pipeline": "reject" },
    { "name": "container, snapshot reference not normalized (bare name)", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "python", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }, "app": "reject", "pipeline": "reject" },
    { "name": "container, snapshot without its digest", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime" } }, "app": "reject", "pipeline": "reject" },
    { "name": "container, snapshot with an unknown key", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "tag": "1.4.2" } }, "app": "reject", "pipeline": "accept" },
    { "name": "container asking for open network (the slice-1 network rule applies to every kind)", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "network": { "level": "open" } }, "app": "reject", "pipeline": "accept" },
    { "name": "container with a hardware block", "config": { "kind": "container", "image": { "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", "reference": "ghcr.io/example/satpy-runtime", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }, "hardware": { "profile": "standard", "cpu": 4, "gpu_count": 0 } }, "app": "accept", "pipeline": "accept" }
```

In `tests/contract-fixtures/README.md`, replace the paragraph that starts `**\`process-runtime.json\` carries the M5 slice-1 asymmetry**` (through `broken is not the same as gated.`) with:
```markdown
**`process-runtime.json` carries three kinds since C-1** (container-images spec §3): `inline_python`, `inline_python_on_image` and `container`, one `{minimal, defaults}` pair each. The `container` arm is no longer refused by the shape. Whether a snapshot's image may be deployed is decided by the DB-backed check in the revisions route (422 with `image_not_approved` / `image_stale` / `image_group_mismatch` / `image_digest_mismatch`), which a fixture cannot express, so every well-formed kind 2/3 case is accept/accept. `image` is the snapshot `{id, reference, digest}` (grammar pinned by `image-reference.json`). `runtime_image` is null on kinds 2 and 3 and `command` exists only on kind 3. Both of those are reject/reject when violated, because a lenient reader silently ignoring them would run something other than what the revision says.
```

- [ ] **Step 2: Update the fixture consumers (these will fail)**

`app/src/__tests__/contract-fixtures.test.ts`, in the `process runtime contract` describe: delete the test `"the container arm is refused by the write gate, not by the shape"` and replace it with:
```ts
  it("the write gate accepts every kind's minimal document (C-1: the image check is the route's)", () => {
    const fixture = loadFixture("process-runtime.json") as unknown as UnionFixture;
    for (const [kind, variant] of Object.entries(fixture.variants)) {
      expect(processRuntimeSchema.safeParse(variant.minimal).success, kind).toBe(true);
    }
    expect(Object.keys(fixture.variants)).toEqual([
      "inline_python",
      "inline_python_on_image",
      "container",
    ]);
  });
```
Update the comment above `describeUnion("process-runtime.json", …)` to: `// The fixture's \`app\` column is the WRITE gate (network rule); defaults round-trip through the read schema.` (For the key-order assertion to hold, the fixture's `variants` object must list `inline_python`, then `inline_python_on_image`, then `container`.)

`services/pipeline/tests/test_contract_fixtures.py`, in `test_process_runtime_defaults_match_golden`, replace the line `assert runtime.image == golden.get("image")` with:
```python
        snapshot = golden.get("image")
        if snapshot is None:
            assert (runtime.image_id, runtime.image_reference, runtime.image_digest) == (
                None,
                None,
                None,
            )
        else:
            assert (runtime.image_id, runtime.image_reference, runtime.image_digest) == (
                snapshot["id"],
                snapshot["reference"],
                snapshot["digest"],
            )
        command = golden.get("command")
        assert runtime.command == (None if command is None else tuple(command))
```
Also update the docstring of `test_process_runtime_cases` to: `"""Three kinds (C-1). Whether a snapshot's image may run is a DB check in the app's revisions route and, from C-2, at launch, so it is never a parse outcome."""`.

- [ ] **Step 3: New and changed app tests**

`app/src/__tests__/processes-runtime-kinds.test.ts`:
```ts
// @vitest-environment node
/**
 * `processRevisionCreateSchema` (C-1, container-images spec §3): code is
 * required for inline_python and inline_python_on_image, and refused for
 * container, where the image is the process.
 */
import { describe, expect, it } from "vitest";
import { processRevisionCreateSchema } from "@/lib/processes/schemas";

const SNAP = {
  id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
  reference: "ghcr.io/example/satpy-runtime",
  digest: "sha256:" + "a".repeat(64),
};

function revision(runtime: Record<string, unknown>, code: string | null) {
  return processRevisionCreateSchema.safeParse({ runtime, code, env: [] });
}

describe("processRevisionCreateSchema — code by kind", () => {
  it("kind 2 carries code, like kind 1", () => {
    expect(revision({ kind: "inline_python_on_image", image: SNAP }, "print(1)").success).toBe(true);
    const missing = revision({ kind: "inline_python_on_image", image: SNAP }, null);
    expect(missing.success).toBe(false);
    expect(JSON.stringify(missing.error?.issues)).toContain("inline_python_on_image revisions need code");
  });

  it("kind 3 refuses code: the image is the process", () => {
    expect(revision({ kind: "container", image: SNAP }, null).success).toBe(true);
    const withCode = revision({ kind: "container", image: SNAP }, "print(1)");
    expect(withCode.success).toBe(false);
    expect(JSON.stringify(withCode.error?.issues)).toContain("container revisions carry no code");
  });

  it("kind 3 refuses an empty-string code too (only null means 'no code')", () => {
    expect(revision({ kind: "container", image: SNAP }, "").success).toBe(false);
  });

  it("kind 1 is unchanged", () => {
    expect(revision({ kind: "inline_python" }, "print(1)").success).toBe(true);
    expect(revision({ kind: "inline_python" }, "   ").success).toBe(false);
  });
});
```

`app/src/__tests__/processes-runtime-image.test.ts`: replace the last test (`"does not open the container arm: …"`) with:
```ts
  it("an alias beside a user image is refused on both schemas (spec §3: runtime_image is null for kinds 2–3)", () => {
    const image = {
      id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
      reference: "ghcr.io/x/y",
      digest: "sha256:" + "a".repeat(64),
    };
    for (const kind of ["inline_python_on_image", "container"]) {
      const doc = { kind, image, runtime_image: "stactools" };
      expect(processRuntimeReadSchema.safeParse(doc).success, kind).toBe(false);
      expect(processRuntimeSchema.safeParse(doc).success, kind).toBe(false);
      expect(processRuntimeSchema.parse({ kind, image }).runtime_image, kind).toBeNull();
    }
  });
```
Also update that file's header comment: replace "It is not `runtime.image` — that is a user-supplied reference the write gate refuses (ADR 0013) —" with "It is not `runtime.image`, which is the scanned user-image snapshot of kinds 2–3 (C-1, ADR 0021),".

`app/src/__tests__/api-processes.test.ts`:
- Add `vi.mock("@/lib/images/storage", () => ({ getImageForGate: vi.fn() }));` beside the other `vi.mock` calls, and `import { getImageForGate } from "@/lib/images/storage";` plus `import { resetImagePolicyCache } from "@/lib/images/policy";` to the imports.
- Add after the `INLINE` constant:
  ```ts
  const SNAP = {
    id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
    reference: "ghcr.io/example/satpy-runtime",
    digest: "sha256:" + "a".repeat(64),
  };
  function approvedImage(overrides: Record<string, unknown> = {}) {
    return {
      id: SNAP.id,
      reference: SNAP.reference,
      digest: SNAP.digest,
      status: "approved",
      last_scanned_at: new Date(Date.now() - 86_400_000),
      registry_connection_id: null,
      registry_connection_group_id: null,
      ...overrides,
    } as never;
  }
  ```
- In `beforeEach`, add `vi.mocked(getImageForGate).mockResolvedValue(null);` and `resetImagePolicyCache();`.
- Replace the test `"refuses the container runtime this slice (spec §4, ADR 0013)"` with:
  ```ts
  it("refuses a user image no scan has approved (C-1: 422 image_not_approved)", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: { kind: "container", image: SNAP }, code: null },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("image_not_approved");
    expect(getImageForGate).toHaveBeenCalledWith(SNAP.id);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it.each([
    ["image_stale", { last_scanned_at: new Date(Date.now() - 31 * 86_400_000) }],
    ["image_digest_mismatch", { digest: "sha256:" + "b".repeat(64) }],
    ["image_group_mismatch", { registry_connection_id: "c-1", registry_connection_group_id: "weather" }],
    ["image_not_approved", { status: "flagged" }],
  ])("answers 422 %s", async (code, overrides) => {
    vi.mocked(getImageForGate).mockResolvedValue(approvedImage(overrides));
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: { kind: "inline_python_on_image", image: SNAP }, code: "print(1)" },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe(code);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("deploys kind 2 on an approved, fresh image, snapshot stored verbatim", async () => {
    vi.mocked(getImageForGate).mockResolvedValue(approvedImage());
    vi.mocked(deployRevision).mockResolvedValue({ id: REVISION_ID } as never);
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: { kind: "inline_python_on_image", image: SNAP }, code: "print(1)" },
    });
    expect(res.status).toBe(201);
    const input = vi.mocked(deployRevision).mock.calls[0][0];
    expect(input.runtime).toMatchObject({ kind: "inline_python_on_image", image: SNAP, runtime_image: null });
  });

  it("fails closed with 503 when the policy cannot be read, and still deploys inline code", async () => {
    vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/policy.json");
    try {
      const refused = await call(deployRoute, operator, {
        method: "POST",
        body: { runtime: { kind: "container", image: SNAP }, code: null },
      });
      expect(refused.status).toBe(503);
      expect((await refused.json()).code).toBe("image_policy_unavailable");

      vi.mocked(deployRevision).mockResolvedValue({ id: REVISION_ID } as never);
      const inline = await call(deployRoute, operator, {
        method: "POST",
        body: { runtime: INLINE, code: "print(1)" },
      });
      expect(inline.status).toBe(201);
    } finally {
      vi.unstubAllEnvs();
      resetImagePolicyCache();
    }
  });

  it("400s a container revision that carries code (schema, before any DB read)", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: { kind: "container", image: SNAP }, code: "print(1)" },
    });
    expect(res.status).toBe(400);
    expect(getImageForGate).not.toHaveBeenCalled();
  });

  it("never reads the image registry for an inline revision", async () => {
    vi.mocked(deployRevision).mockResolvedValue({ id: REVISION_ID } as never);
    await call(deployRoute, operator, { method: "POST", body: { runtime: INLINE, code: "print(1)" } });
    expect(getImageForGate).not.toHaveBeenCalled();
  });
  ```

- [ ] **Step 4: Pipeline tests (these will fail)**

`services/pipeline/tests/test_process_executor.py`:
- Replace `test_slice_1_always_runs_the_platform_image` with:
  ```python
  def test_a_user_image_is_never_resolved_by_alias():
      """Kinds 2–3 run by digest (C-2), never through the platform alias map.
      build_run_spec must refuse rather than quietly run the platform image."""
      runtime = parse_process_runtime(
          {
              "kind": "container",
              "image": {
                  "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
                  "reference": "ghcr.io/example/tool",
                  "digest": "sha256:" + "a" * 64,
              },
          }
      )
      assert runtime.runtime_image is None
      with pytest.raises(RuntimeImageUnavailable, match="by digest"):
          build_run_spec(
              settings(),
              run_id=RUN,
              process_id=PROC,
              runtime=runtime,
              code="print(1)",
              env={},
              credentials=RunCredentials(
                  "AK", "SK", "TOK", "b", run_staging_prefix(RUN), None, "r"
              ),
          )
  ```
- In `test_runtime_image_alias_defaults_and_is_an_enum`, change `assert stactools.runtime_image == "stactools" and stactools.image is None` to `assert stactools.runtime_image == "stactools" and stactools.image_id is None`.
- Append:
  ```python
  def test_three_runtime_kinds_parse_their_snapshot_and_command():
      from pipeline.process.config import MAX_COMMAND_ENTRIES, RUNTIME_KINDS, USER_IMAGE_KINDS

      assert RUNTIME_KINDS == ("inline_python", "inline_python_on_image", "container")
      assert frozenset({"inline_python_on_image", "container"}) == USER_IMAGE_KINDS
      assert MAX_COMMAND_ENTRIES == 64
      snap = {
          "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
          "reference": "ghcr.io/example/tool",
          "digest": "sha256:" + "a" * 64,
      }
      rt = parse_process_runtime({"kind": "container", "image": snap, "command": ["tool", "--run"]})
      assert (rt.image_id, rt.image_reference, rt.image_digest) == (
          snap["id"],
          snap["reference"],
          snap["digest"],
      )
      assert rt.command == ("tool", "--run")
      # command is kind-3 only: on kind 2 it is an unknown key, ignored.
      assert parse_process_runtime(
          {"kind": "inline_python_on_image", "image": snap, "command": ["x"]}
      ).command is None
      with pytest.raises(ProcessConfigError, match=r"runtime\.image must be null"):
          parse_process_runtime({"kind": "inline_python", "image": snap})
      with pytest.raises(ProcessConfigError, match=r"runtime\.runtime_image must be null"):
          parse_process_runtime({"kind": "container", "image": snap, "runtime_image": "default"})
  ```

`services/pipeline/tests/test_process_triggers.py`: append after `test_a_revision_with_no_code_dies_rather_than_running_nothing`:
```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "code"), [("inline_python_on_image", "print(1)"), ("container", None)]
)
async def test_a_user_image_revision_dies_before_anything_launches(kind, code):
    """C-1: the contract admits kinds 2–3 but this pipeline cannot launch them
    yet (C-2 lands the digest-pinned path). The run dies naming ADR 0021; it
    must never fall through to the platform image, and a kind-3 run must not
    be reported as 'no code'."""
    repo = FakeProcessRepo()
    executor = MemoryExecutor()
    runtime = {
        "kind": kind,
        "image": {
            "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
            "reference": "ghcr.io/example/tool",
            "digest": "sha256:" + "a" * 64,
        },
    }
    result = await _run(queued(runtime=runtime, code=code), executor, repo)
    assert result.status == "dead"
    assert "ADR 0021" in repo.finished[0]["error"]
    assert executor.list_launched() == []
```

- [ ] **Step 5: Run everything touched and watch it fail**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/processes-runtime-kinds.test.ts src/__tests__/processes-runtime-image.test.ts src/__tests__/api-processes.test.ts`, which should FAIL.
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py tests/test_process_executor.py tests/test_process_triggers.py -q`, which should FAIL.

- [ ] **Step 6: Zod: three kinds and the code rule**

In `app/src/lib/processes/schemas.ts`:
- Add `import { imageSnapshotSchema } from "@/lib/images/reference";` below the existing imports.
- Replace `export const PROCESS_RUNTIME_KINDS = ["inline_python", "container"] as const;` with:
  ```ts
  export const PROCESS_RUNTIME_KINDS = [
    "inline_python",
    "inline_python_on_image",
    "container",
  ] as const;
  /** The kinds that run on a scanned USER image (C-1, container-images spec §3). */
  export const USER_IMAGE_RUNTIME_KINDS = ["inline_python_on_image", "container"] as const;
  /** `command` (kind 3) maps to Docker `Cmd` / Kubernetes `args`, never `Entrypoint` or `User`. */
  export const MAX_COMMAND_ENTRIES = 64;
  ```
- In `runtimeLimits`, DELETE the `runtime_image` entry (and its two comment lines). It moves into each arm.
- Replace `inlinePythonRuntimeSchema` and `containerRuntimeSchema` with:
  ```ts
  const inlinePythonRuntimeSchema = z
    .object({
      kind: z.literal("inline_python"),
      // Present and null-only: inline code runs on a PLATFORM image picked by
      // `runtime_image`; code on your own image is `inline_python_on_image`.
      image: z.null().default(null),
      // Every stored revision predates the alias; absent reads as `default`
      // here and in the Python reader.
      runtime_image: z.enum(PROCESS_RUNTIME_IMAGE_ALIASES).default("default"),
      ...runtimeLimits,
    })
    .strict();

  /** Kinds 2–3 name a scanned image by snapshot; an alias beside it would
   * name a second image, so it is null-only (spec §3). */
  const userImageFields = {
    image: imageSnapshotSchema,
    runtime_image: z.null().default(null),
  };

  const inlinePythonOnImageRuntimeSchema = z
    .object({
      kind: z.literal("inline_python_on_image"),
      ...userImageFields,
      ...runtimeLimits,
    })
    .strict();

  const commandSchema = z
    .array(z.string().refine((s) => s.trim().length > 0, "command entries must be non-blank"))
    .min(1, "command must be non-empty when present (omit it to use the image's own CMD)")
    .max(MAX_COMMAND_ENTRIES, `command carries at most ${MAX_COMMAND_ENTRIES} entries`);

  const containerRuntimeSchema = z
    .object({
      kind: z.literal("container"),
      ...userImageFields,
      // Overrides the image's Cmd, never its Entrypoint or User (spec §14.4).
      command: commandSchema.nullable().default(null),
      ...runtimeLimits,
    })
    .strict();
  ```
- Replace the `processRuntimeReadSchema` doc comment and definition with:
  ```ts
  /**
   * The full runtime shape, all three arms, for READING a stored runtime.
   * `processRuntimeSchema` below is the write gate. The two differ only in the
   * slice-1 network rule. Whether a kind 2/3 snapshot's image may be deployed
   * is NOT a shape question: the revisions route runs the DB-backed
   * `checkImageGate` (C-1, container-images spec §3).
   */
  export const processRuntimeReadSchema = z.discriminatedUnion("kind", [
    inlinePythonRuntimeSchema,
    inlinePythonOnImageRuntimeSchema,
    containerRuntimeSchema,
  ]);
  ```
- DELETE `CONTAINER_RUNTIME_REFUSAL` and its comment.
- Replace the `processRuntimeSchema` doc comment and body with:
  ```ts
  /**
   * The WRITE gate and the default name. The shape carries every kind. The
   * image check for kinds 2–3 lives in the revisions route (it needs the DB),
   * and the pipeline re-checks at launch. The network asymmetry stays here
   * (GOES spec §4): the reader carries every level, but the write gate stores
   * only `isolated` until the egress proxy exists.
   */
  export const processRuntimeSchema = processRuntimeReadSchema.superRefine(
    (runtime, ctx) => {
      if (runtime.network.level !== "isolated") {
        ctx.addIssue({
          code: "custom",
          path: ["network", "level"],
          message: NETWORK_LEVEL_NOT_YET_AVAILABLE,
        });
      }
    },
  );
  ```
- Replace the `processRevisionCreateSchema` doc comment and `.superRefine` body with:
  ```ts
  /**
   * A deploy: an immutable revision snapshot, which the route then makes
   * current. `code` is required for `inline_python` and
   * `inline_python_on_image` and refused for `container`, where the image is
   * the process (spec §3). Carrying both would leave two sources of truth
   * for what executes.
   */
  export const processRevisionCreateSchema = z
    .object({
      runtime: processRuntimeSchema,
      code: z.string().nullable().default(null),
      env: processEnvSchema,
    })
    .strict()
    .superRefine((revision, ctx) => {
      const kind = revision.runtime.kind;
      const carriesCode = kind !== "container";
      if (carriesCode && (revision.code === null || revision.code.trim().length === 0)) {
        ctx.addIssue({ code: "custom", path: ["code"], message: `${kind} revisions need code` });
      }
      if (!carriesCode && revision.code !== null) {
        ctx.addIssue({
          code: "custom",
          path: ["code"],
          message: "container revisions carry no code: the image is the process",
        });
      }
    });
  ```
- Update the `PROCESS_RUNTIME_IMAGE_ALIASES` doc comment: replace "never a user-supplied reference, which is what `runtime.image` would be and what ADR 0013 refuses —" with "never a user image (that is `runtime.image`, the scanned snapshot of kinds 2–3, ADR 0021) —".

- [ ] **Step 7: Wire the gate into the revisions route**

In `app/src/pages/api/processes/[id]/revisions.ts`:
- Add imports:
  ```ts
  import { checkImageGate, imageGateRefused, imagePolicyUnavailable } from "@/lib/images/gate";
  import { ImagePolicyUnavailable } from "@/lib/images/policy";
  ```
- In the header comment, replace the sentence block from "`runtime` is validated by the WRITE gate (`processRuntimeSchema`), which refuses the `container` arm this slice …" through "… accreditation scope (spec §4, ADR 0013)." with:
  ```
   * `runtime` is validated by the WRITE gate (`processRuntimeSchema`). A
   * kind 2/3 runtime (`inline_python_on_image`, `container`) names a scanned
   * user image by snapshot. `checkImageGate` then requires that snapshot's
   * `container_images` row to be approved, fresh, digest-equal and usable by
   * the process's group, answering 422 with the reason otherwise and 503 when
   * the image policy cannot be read (C-1, container-images spec §3, ADR 0021).
  ```
- After the `if (unresolvable) return secretRefOutOfScope(unresolvable);` line, add:
  ```ts
    // C-1 (container-images spec §3): a user image must be an approved,
    // fresh, digest-equal registry row this group may use. The pipeline
    // re-checks at launch. Inline revisions skip it without reading the policy.
    const snapshot = data.runtime.kind === "inline_python" ? null : data.runtime.image;
    let imageRefusal;
    try {
      imageRefusal = await checkImageGate(snapshot, loaded.process.group_id);
    } catch (err) {
      if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
      throw err;
    }
    if (imageRefusal) return imageGateRefused(imageRefusal);
  ```

- [ ] **Step 8: Python: three kinds**

In `services/pipeline/src/pipeline/process/config.py`:
- In the module docstring, replace the paragraph starting `**The slice-1 asymmetry is deliberate**` with:
  ```
  **Three runtime kinds since C-1** (container-images spec §3, ADR 0021):
  ``inline_python``, ``inline_python_on_image`` and ``container``. The
  snapshot ``image`` of kinds 2–3 is parsed here. Whether that image may
  deploy is the app's DB-backed gate, and whether it may LAUNCH is the launch
  path's (``launch.check_user_image_launchable``; C-2 makes that a digest check).
  ```
- Add `from pipeline.images.reference import is_image_digest, is_image_reference` to the imports.
- Replace `RUNTIME_KINDS = ("inline_python", "container")` and its comment with:
  ```python
  #: Runtime kinds the shape admits (C-1, container-images spec §3).
  RUNTIME_KINDS = ("inline_python", "inline_python_on_image", "container")
  #: Kinds that run on a scanned USER image, named by an immutable snapshot.
  USER_IMAGE_KINDS = frozenset({"inline_python_on_image", "container"})
  #: ``command`` (kind 3) overrides the image's Cmd; never Entrypoint or User.
  MAX_COMMAND_ENTRIES = 64
  ```
- In the `RUNTIME_IMAGE_ALIASES` comment, replace "never a user-supplied reference (that is ``runtime.image``, refused by the app's write gate under ADR 0013)" with "never a user image (that is ``runtime.image``, the scanned snapshot of kinds 2–3)".
- Replace the `ProcessRuntime` dataclass's `image` field (and its 3-line comment) with:
  ```python
      #: Kinds 2–3 only: the immutable snapshot of a ``container_images`` row.
      #: ``None`` for inline_python.
      image_id: str | None = None
      image_reference: str | None = None
      image_digest: str | None = None
      #: Kind 3 only: overrides the image's Cmd. ``None`` means the image's own.
      command: tuple[str, ...] | None = None
  ```
  and change the `runtime_image` field to:
  ```python
      #: X-queue spec §8: one of RUNTIME_IMAGE_ALIASES for inline_python;
      #: ``None`` for kinds 2–3, which run by digest.
      runtime_image: str | None = DEFAULT_RUNTIME_IMAGE_ALIAS
  ```
- Add these helpers just above `def parse_process_runtime`:
  ```python
  def _parse_image_snapshot(raw: Any) -> tuple[str, str, str]:
      doc = _obj(raw, "runtime.image")
      image_id = doc.get("id")
      if not isinstance(image_id, str) or not _UUID_RE.match(image_id):
          raise ProcessConfigError(
              f"runtime.image.id must be a container image id, got {image_id!r}"
          )
      reference = doc.get("reference")
      if not isinstance(reference, str) or not is_image_reference(reference):
          raise ProcessConfigError(
              f"runtime.image.reference must be a normalized repository, got {reference!r}"
          )
      digest = doc.get("digest")
      if not isinstance(digest, str) or not is_image_digest(digest):
          raise ProcessConfigError(
              f"runtime.image.digest must be a sha256 digest, got {digest!r}"
          )
      return image_id, reference, digest


  def _parse_command(raw: Any) -> tuple[str, ...] | None:
      if raw is None:
          return None
      if not isinstance(raw, list) or not all(isinstance(arg, str) for arg in raw):
          raise ProcessConfigError("runtime.command must be a list of strings")
      if not raw:
          raise ProcessConfigError("runtime.command must be non-empty when present")
      if len(raw) > MAX_COMMAND_ENTRIES:
          raise ProcessConfigError(
              f"runtime.command carries at most {MAX_COMMAND_ENTRIES} entries"
          )
      if any(not arg.strip() for arg in raw):
          raise ProcessConfigError("runtime.command entries must be non-blank")
      return tuple(raw)
  ```
  (`_UUID_RE` is defined later in the module. It is resolved at call time, so the order is fine.)
- In `parse_process_runtime`, replace
  ```python
      image: str | None = None
      if kind == "container":
          image = _non_blank(doc.get("image"), "runtime.image")
  ```
  with:
  ```python
      image_id = image_reference = image_digest = None
      command: tuple[str, ...] | None = None
      runtime_image: str | None
      if kind == "inline_python":
          if doc.get("image") is not None:
              raise ProcessConfigError(
                  "runtime.image must be null for inline_python; use inline_python_on_image "
                  "to run code on your own image"
              )
          runtime_image = _enum(
              doc.get("runtime_image"),
              RUNTIME_IMAGE_ALIASES,
              "runtime.runtime_image",
              default=DEFAULT_RUNTIME_IMAGE_ALIAS,
          )
      else:
          image_id, image_reference, image_digest = _parse_image_snapshot(doc.get("image"))
          if doc.get("runtime_image") is not None:
              raise ProcessConfigError(
                  f"runtime.runtime_image must be null for {kind}: a platform alias and a user "
                  "image cannot both name what runs"
              )
          runtime_image = None
          if kind == "container":
              command = _parse_command(doc.get("command"))
  ```
  In the `return ProcessRuntime(...)` call, replace `image=image,` with `image_id=image_id, image_reference=image_reference, image_digest=image_digest, command=command,`, and replace the whole `runtime_image=_enum(...)` argument with `runtime_image=runtime_image,`.

- [ ] **Step 9: Python: the launch guard**

In `services/pipeline/src/pipeline/process/launch.py`:
- Change the import from `pipeline.process.config` to also bring `USER_IMAGE_KINDS` (add it to the existing import list).
- Add below `class RuntimeImageUnavailable`:
  ```python
  class ImageUnusable(Exception):
      """A revision on a user-supplied image (kinds 2–3) that must not launch.
      C-1: the contract and the deploy gate exist but no scanner does, so no
      user image can launch yet. C-2 replaces
      :func:`check_user_image_launchable`'s body with the spec §8.4 check (row
      exists, digest equal, status approved|flagged, not stale). The run dies
      with the reason; it never falls back to the platform image."""


  USER_IMAGE_LAUNCH_UNAVAILABLE = (
      "revision runs on a user-supplied image ({kind} {reference}); this pipeline "
      "cannot launch user images yet (ADR 0021: scan, approve, run by digest)"
  )


  def check_user_image_launchable(runtime: ProcessRuntime) -> None:
      """Raise :class:`ImageUnusable` for a kind 2/3 runtime (C-1)."""
      if runtime.kind in USER_IMAGE_KINDS:
          raise ImageUnusable(
              USER_IMAGE_LAUNCH_UNAVAILABLE.format(
                  kind=runtime.kind, reference=runtime.image_reference
              )
          )
  ```
- In `resolve_runtime_image`, insert at the top of the body (after the docstring):
  ```python
      if runtime.kind in USER_IMAGE_KINDS:
          # Defence in depth: run_one refuses kinds 2–3 before this is reached.
          raise RuntimeImageUnavailable(
              f"{runtime.kind} revisions run their own image by digest, never a platform alias"
          )
  ```
- In `build_run_spec`, replace the 4-line comment above `image=resolve_runtime_image(runtime, settings),` with `# Always a PLATFORM image chosen by the alias (X-queue spec §8). Kinds 2–3` / `# are refused before this point (C-1) and pulled by digest from C-2 on.`

In `services/pipeline/src/pipeline/process/runner.py`:
- Add `ImageUnusable` and `check_user_image_launchable` to the `from pipeline.process.launch import (...)` list (keep it sorted).
- Directly after the `except ProcessConfigError` block (before `if run.code is None:`), insert:
  ```python
      # C-1 (ADR 0021): the contract admits user-image kinds but nothing can
      # launch one yet. This runs before the code check, so a kind-3 run
      # (legitimately code-less) dies for the real reason.
      try:
          check_user_image_launchable(runtime)
      except ImageUnusable as err:
          await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
          return RunResult(run.id, "dead", error=str(err))
  ```

- [ ] **Step 10: Run everything and watch it pass**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/processes-runtime-kinds.test.ts src/__tests__/processes-runtime-image.test.ts src/__tests__/api-processes.test.ts src/__tests__/api-processes-builtin.test.ts src/__tests__/builtin-processes.test.ts src/__tests__/processes-network.test.ts`, which should PASS.
Run (from `services/pipeline/`): `uv run pytest -q && uv run ruff check .`, which should PASS. The demo/loadgen fixtures are all `inline_python` and are unaffected.
Then run `grep -rn "CONTAINER_RUNTIME_REFUSAL" app services packages`, which should print nothing.

- [ ] **Step 11: Gates and commit**

Run the full gates, which should be green: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add tests/contract-fixtures/process-runtime.json tests/contract-fixtures/README.md \
  app/src/lib/processes/schemas.ts "app/src/pages/api/processes/[id]/revisions.ts" \
  app/src/__tests__/contract-fixtures.test.ts app/src/__tests__/processes-runtime-kinds.test.ts \
  app/src/__tests__/processes-runtime-image.test.ts app/src/__tests__/api-processes.test.ts \
  services/pipeline/src/pipeline/process/config.py services/pipeline/src/pipeline/process/launch.py \
  services/pipeline/src/pipeline/process/runner.py services/pipeline/tests/test_contract_fixtures.py \
  services/pipeline/tests/test_process_executor.py services/pipeline/tests/test_process_triggers.py
git commit -m "feat(processes): three runtime kinds with an image snapshot; the revisions route's DB-backed image gate replaces CONTAINER_RUNTIME_REFUSAL (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The `registry` connection protocol on both sides

**Files:**
- Create: `tests/contract-fixtures/registry-connection-config.json`
- Create: `services/pipeline/src/pipeline/connections/registry.py`
- Modify: `app/src/lib/connections/schemas.ts`, `app/src/components/connections/ConnectionForm.tsx`, `app/src/lib/associations/access.ts:118-138`, `app/src/components/collections/IngestFormDialog.tsx:336`, `app/src/components/collections/DeliveryFormDialog.tsx:226`
- Modify: `services/pipeline/src/pipeline/connections/build.py`, `services/pipeline/src/pipeline/connections/probe.py`, `services/pipeline/src/pipeline/connections/adapters/factory.py`
- Modify tests: `app/src/__tests__/contract-fixtures.test.ts`, `app/src/__tests__/connections-schemas.test.ts`, `app/src/__tests__/connections-form.test.tsx`, `app/src/__tests__/api-associations.test.ts`, `services/pipeline/tests/test_contract_fixtures.py`, `services/pipeline/tests/test_probe.py`, `services/pipeline/tests/test_build_adapter.py`
- Create test: `services/pipeline/tests/test_registry_connection.py`
- Docs: `docs/connections.md`, `docs/processes.md` (secret-ref table row), `tests/contract-fixtures/README.md`

**Interfaces:**
- Consumes: migration 030's `connections_protocol_check` (Task 1).
- Produces (TS): `registryConfigSchema`, `registryCredentialsSchema`, `"registry"` in `CONNECTION_PROTOCOLS` and `WRITABLE_PROTOCOLS`, `CREDENTIAL_KEYS.registry = ["username", "password"]`, and `REGISTRY_NOT_A_FLOW_MESSAGE`.
- Produces (Python, `pipeline.connections.registry`): `REGISTRY_PROTOCOL`, `RegistryConfigError(ValueError)`, `RegistryConfig`, `parse_registry_config(raw) -> RegistryConfig`, `registry_api_host(host) -> str`, `parse_bearer_challenge(header) -> dict[str, str] | None`, `HttpResponse`, `check_registry(config, credentials, allow_hosts, *, http_get=...) -> TestResult`, and `async test_registry_connection(connection, master_key, allow_hosts, *, http_get=...) -> TestResult`. Also `pipeline.connections.build.decrypt_credentials(connection, master_key) -> dict` (factored out of `build_adapter`).

- [ ] **Step 1: The fixture**

`tests/contract-fixtures/registry-connection-config.json`:
```json
{
  "$comment": "C-1 (container-images spec §5): the `registry` connection's config: {host}, a bare registry hostname with an optional port. Credentials are {username, password} (a PAT, an ECR token, a robot account) sealed in the existing envelope. Validated by registryConfigSchema (app/src/lib/connections/schemas.ts, strict: lowercase only) and parse_registry_config (pipeline/connections/registry.py, which strips and case-folds). Docker Hub's short host `docker.io` is probed at registry-1.docker.io.",
  "minimal": { "host": "ghcr.io" },
  "defaults": { "host": "ghcr.io" },
  "cases": [
    { "name": "GHCR", "config": { "host": "ghcr.io" }, "app": "accept", "pipeline": "accept" },
    { "name": "a private ECR host", "config": { "host": "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com" }, "app": "accept", "pipeline": "accept" },
    { "name": "Docker Hub by its short name", "config": { "host": "docker.io" }, "app": "accept", "pipeline": "accept" },
    { "name": "a host with a port", "config": { "host": "registry.example.com:5000" }, "app": "accept", "pipeline": "accept" },
    { "name": "a host with a scheme", "config": { "host": "https://ghcr.io" }, "app": "reject", "pipeline": "reject" },
    { "name": "a host with a path", "config": { "host": "ghcr.io/example" }, "app": "reject", "pipeline": "reject" },
    { "name": "a blank host", "config": { "host": "   " }, "app": "reject", "pipeline": "reject" },
    { "name": "no host", "config": {}, "app": "reject", "pipeline": "reject" },
    { "name": "a numeric host", "config": { "host": 5 }, "app": "reject", "pipeline": "reject" },
    { "name": "a label with a leading hyphen", "config": { "host": "-bad.example.com" }, "app": "reject", "pipeline": "reject" },
    { "name": "a port with too many digits", "config": { "host": "ghcr.io:123456" }, "app": "reject", "pipeline": "reject" },
    { "name": "uppercase (the writer stores lowercase; the reader folds)", "config": { "host": "GHCR.IO" }, "app": "reject", "pipeline": "accept" },
    { "name": "an unknown key", "config": { "host": "ghcr.io", "insecure": true }, "app": "reject", "pipeline": "accept" }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`app/src/__tests__/contract-fixtures.test.ts`: change the connections import to `import { registryConfigSchema, s3ConfigSchema } from "@/lib/connections/schemas";` and add, after the s3 describe:
```ts
describe("registry connection config contract (tests/contract-fixtures/registry-connection-config.json)", () => {
  describeDirection("registry-connection-config.json", registryConfigSchema);
});
```

`app/src/__tests__/connections-schemas.test.ts`, append:
```ts
describe("parseConnectionCreate — registry (C-1)", () => {
  it("accepts a registry with username + password", () => {
    const result = create({
      protocol: "registry",
      config: { host: "ghcr.io" },
      credentials: { username: "bot", password: "ghp_x" },
    });
    expect(result.success).toBe(true);
  });

  it("requires both credentials: a registry connection exists to pull privately", () => {
    expect(
      create({ protocol: "registry", config: { host: "ghcr.io" }, credentials: { username: "bot" } })
        .success,
    ).toBe(false);
    expect(create({ protocol: "registry", config: { host: "ghcr.io" } }).success).toBe(false);
  });

  it("exposes exactly username/password to secret_ref", async () => {
    const { CREDENTIAL_KEYS } = await import("@/lib/connections/schemas");
    expect(CREDENTIAL_KEYS.registry).toEqual(["username", "password"]);
  });
});
```

`app/src/__tests__/connections-form.test.tsx`, append:
```tsx
describe("ConnectionForm — registry (C-1)", () => {
  it("creates a registry connection from its type card", async () => {
    render(<ConnectionForm open onOpenChange={() => {}} groups={["g1"]} />);
    fireEvent.click(screen.getByRole("radio", { name: "registry" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "GHCR bot" } });
    fireEvent.change(screen.getByLabelText("Registry host"), { target: { value: "ghcr.io" } });
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "bot" } });
    fireEvent.change(screen.getByLabelText("Password or token"), { target: { value: "ghp_x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(createMock).toHaveBeenCalled());
    const payload = createMock.mock.calls[0][0];
    expect(payload.protocol).toBe("registry");
    expect(payload.config).toEqual({ host: "ghcr.io" });
    expect(payload.credentials).toEqual({ username: "bot", password: "ghp_x" });
  });
});
```

`app/src/__tests__/api-associations.test.ts`, inside the create describe (after `"rejects reference mode for a non-s3 connection (400)"`):
```ts
  it("refuses a registry connection as a data flow (C-1: it holds pull credentials, not files)", async () => {
    vi.mocked(getConnection).mockResolvedValue({ ...s3Connection, protocol: "registry" } as ApiConnection);
    const res = await call(createRoute, authed(["operator"]), { body: validCreateBody });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatch(/registry connection/);
    expect(createAssociation).not.toHaveBeenCalled();
  });
```

`services/pipeline/tests/test_contract_fixtures.py`: add `REGISTRY_CONFIG = _load("registry-connection-config.json")` and append:
```python
@pytest.mark.parametrize("case", REGISTRY_CONFIG["cases"], ids=lambda c: c["name"])
def test_registry_config_cases(case):
    from pipeline.connections.registry import parse_registry_config

    _check(parse_registry_config, case)


def test_registry_config_minimal_parses_to_defaults():
    from pipeline.connections.registry import parse_registry_config

    parsed = parse_registry_config(REGISTRY_CONFIG["minimal"])
    assert parsed.host == REGISTRY_CONFIG["defaults"]["host"]
```

`services/pipeline/tests/test_registry_connection.py`:
```python
"""The `registry` connection protocol's check probe (C-1, container-images spec §5).

The transport is injected: no test touches the network, and every host is
allow-listed so ``resolve_pinned`` never consults DNS.
"""

from __future__ import annotations

import json

import pytest

from pipeline.connections import registry
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.registry import (
    HttpResponse,
    RegistryConfig,
    check_registry,
    parse_bearer_challenge,
    registry_api_host,
)
from pipeline.connections.registry import (
    test_registry_connection as probe_registry_connection,
)
from pipeline.connections.repo import ConnectionRow

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
CREDS = {"username": "bot", "password": "s3cr3t-token"}
ALLOW = frozenset({"ghcr.io", "registry-1.docker.io", "auth.docker.io"})


class FakeHttp:
    def __init__(self, *responses: HttpResponse):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        return self.responses.pop(0)


def _ok(status=200, headers=None):
    return HttpResponse(status=status, headers=headers or {}, body=b"")


def test_docker_hub_short_names_probe_the_api_host():
    assert registry_api_host("docker.io") == "registry-1.docker.io"
    assert registry_api_host("index.docker.io") == "registry-1.docker.io"
    assert registry_api_host("ghcr.io") == "ghcr.io"


def test_bearer_challenge_parsing():
    assert parse_bearer_challenge(
        'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
    ) == {"realm": "https://auth.docker.io/token", "service": "registry.docker.io"}
    assert parse_bearer_challenge('Basic realm="x"') is None
    assert parse_bearer_challenge(None) is None


def test_v2_answering_200_with_basic_auth_is_ok():
    http = FakeHttp(_ok(200))
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is True
    url, headers = http.calls[0]
    assert url == "https://ghcr.io/v2/"
    assert headers["Authorization"].startswith("Basic ")


def test_bearer_registries_are_ok_when_the_token_endpoint_accepts_the_credentials():
    challenge = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
    http = FakeHttp(_ok(401, {"www-authenticate": challenge}), _ok(200))
    result = check_registry(RegistryConfig("docker.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is True
    assert http.calls[0][0] == "https://registry-1.docker.io/v2/"
    assert http.calls[1][0] == "https://auth.docker.io/token?service=registry.docker.io"


def test_a_rejected_token_request_fails():
    http = FakeHttp(
        _ok(401, {"www-authenticate": 'Bearer realm="https://auth.docker.io/token",service="x"'}),
        _ok(401),
    )
    result = check_registry(RegistryConfig("docker.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is False
    assert "rejected the credentials" in result["message"]


def test_a_401_without_a_bearer_challenge_is_a_rejection():
    http = FakeHttp(_ok(401, {"www-authenticate": 'Basic realm="x"'}))
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is False
    assert "rejected the credentials" in result["message"]


def test_an_http_token_realm_is_refused():
    http = FakeHttp(_ok(401, {"www-authenticate": 'Bearer realm="http://ghcr.io/token"'}))
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is False
    assert len(http.calls) == 1


def test_an_unexpected_status_fails_naming_it():
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=FakeHttp(_ok(503)))
    assert result["ok"] is False and "503" in result["message"]


def test_egress_is_checked_before_any_request():
    http = FakeHttp()
    result = check_registry(RegistryConfig("127.0.0.1:5000"), CREDS, frozenset(), http_get=http)
    assert result["ok"] is False
    assert http.calls == []


def test_missing_credentials_fail_without_a_request():
    http = FakeHttp()
    result = check_registry(RegistryConfig("ghcr.io"), {"username": "bot"}, ALLOW, http_get=http)
    assert result["ok"] is False
    assert http.calls == []


def test_a_transport_error_is_a_failure_not_a_crash():
    def boom(url, headers):
        raise OSError("connection refused")

    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=boom)
    assert result["ok"] is False and "unreachable" in result["message"]


@pytest.mark.parametrize(
    "responses",
    [
        (_ok(401, {"www-authenticate": 'Basic realm="x"'}),),
        (_ok(401, {"www-authenticate": 'Bearer realm="https://ghcr.io/token"'}), _ok(403)),
        (_ok(500),),
    ],
)
def test_no_probe_message_ever_carries_the_secret(responses):
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=FakeHttp(*responses))
    assert CREDS["password"] not in json.dumps(result)
    blocked = check_registry(RegistryConfig("10.0.0.1"), CREDS, frozenset(), http_get=FakeHttp())
    assert CREDS["password"] not in json.dumps(blocked)


@pytest.mark.asyncio
async def test_the_connection_probe_decrypts_and_checks(monkeypatch):
    seen = {}

    def fake_check(config, credentials, allow_hosts, *, http_get=None):
        seen.update(host=config.host, username=credentials["username"])
        return {"ok": True, "message": "ok"}

    monkeypatch.setattr(registry, "check_registry", fake_check)
    row = ConnectionRow(
        id="c1", name="n", protocol="registry", config={"host": "GHCR.IO"},
        credentials=seal(json.dumps(CREDS), KEY), host_key=None,
    )
    assert (await probe_registry_connection(row, KEY, ALLOW))["ok"] is True
    assert seen == {"host": "ghcr.io", "username": "bot"}


@pytest.mark.asyncio
async def test_a_bad_config_or_envelope_is_a_failed_check():
    bad_config = ConnectionRow(
        id="c1", name="n", protocol="registry", config={"host": "https://x"},
        credentials=seal(json.dumps(CREDS), KEY), host_key=None,
    )
    assert (await probe_registry_connection(bad_config, KEY, ALLOW))["ok"] is False
    no_creds = ConnectionRow(
        id="c1", name="n", protocol="registry", config={"host": "ghcr.io"},
        credentials=None, host_key=None,
    )
    assert (await probe_registry_connection(no_creds, KEY, ALLOW))["ok"] is False
```
(The probe is imported under an alias because its real name starts with `test_`. `registry.py` also sets `test_registry_connection.__test__ = False`, so no test module that imports it by its own name collects it as a test.)

`services/pipeline/tests/test_probe.py`, append:
```python
import pytest

from pipeline.connections import probe as probe_mod


@pytest.mark.asyncio
async def test_registry_connections_take_the_registry_probe(monkeypatch):
    async def fake(connection, master_key, allow_hosts):
        return {"ok": True, "message": "registry ok"}

    monkeypatch.setattr(probe_mod, "test_registry_connection", fake)
    connection = _conn(protocol="registry", config={"host": "ghcr.io"})
    result = await run_adapter_test(connection, KEY, frozenset())
    assert result == {"ok": True, "message": "registry ok"}
    assert evaluate_test_outcome("registry", None, result).connection_status == "ok"
```
(If `pytest` is already imported at the top of `test_probe.py`, do not re-import it. Put the new imports at the top of the file with the others so ruff's isort rule passes.)

`services/pipeline/tests/test_build_adapter.py`, append:
```python
def test_a_registry_connection_is_not_a_storage_adapter():
    with pytest.raises(AdapterBuildError, match="image pull credentials"):
        connection = _conn(
            protocol="registry",
            config={"host": "ghcr.io"},
            creds={"username": "u", "password": "p"},
        )
        build_adapter(connection, KEY, ALLOW)
```

- [ ] **Step 3: Run and watch them fail**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/connections-schemas.test.ts src/__tests__/connections-form.test.tsx src/__tests__/api-associations.test.ts`, which should FAIL.
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py tests/test_registry_connection.py tests/test_probe.py tests/test_build_adapter.py -q`, which should FAIL.

- [ ] **Step 4: App schemas**

In `app/src/lib/connections/schemas.ts`:
- In the header comment's config/credentials table, add the lines `registry  {host}` (config) and `registry  {username, password}` (credentials).
- `CONNECTION_PROTOCOLS`: append `"registry"`. `WRITABLE_PROTOCOLS`: append `"registry"`.
- After `ftpsConfigSchema`, add:
  ```ts
  /** A registry host (C-1, container-images spec §5): a bare hostname with an
   * optional port. No scheme or path, lowercase. Pinned by
   * registry-connection-config.json. */
  const REGISTRY_HOST_RE =
    /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*(?::[0-9]{1,5})?$/;

  export const registryConfigSchema = z
    .object({
      host: z
        .string()
        .regex(
          REGISTRY_HOST_RE,
          "host must be a bare lowercase registry hostname (e.g. ghcr.io), no scheme or path",
        ),
    })
    .strict();
  ```
- After `ftpCredentialsSchema`, add:
  ```ts
  /** Pull credentials: a PAT, an ECR token or a robot account. Both are
   * required, because a registry connection exists to pull privately. */
  export const registryCredentialsSchema = z
    .object({
      username: z.string().min(1, "username is required"),
      password: z.string().min(1, "password or token is required"),
    })
    .strict();
  ```
- Add `registry: registryConfigSchema,` to `CONFIG_SCHEMAS`, `registry: registryCredentialsSchema,` to `CREDENTIALS_SCHEMAS`, and `registry: Object.keys(registryCredentialsSchema.shape),` to `CREDENTIAL_KEYS`.
- Add to `connectionCreateUnion`'s array:
  ```ts
    z.object({
      protocol: z.literal("registry"),
      ...baseCreateFields,
      config: registryConfigSchema,
      credentials: registryCredentialsSchema,
    }),
  ```
- Add near the other message constants:
  ```ts
  export const REGISTRY_NOT_A_FLOW_MESSAGE =
    "connection_id names a registry connection. Registry connections hold image pull " +
    "credentials for processes and cannot carry a data flow";
  ```

- [ ] **Step 5: The connection form's typed maps**

In `app/src/components/connections/ConnectionForm.tsx`:
- `CONFIG_FIELDS`: add
  ```ts
  registry: [
    {
      name: "host",
      label: "Registry host",
      type: "text",
      placeholder: "ghcr.io",
      help: "Bare hostname, no scheme: docker.io, ghcr.io, or an ECR host such as 123456789012.dkr.ecr.us-gov-west-1.amazonaws.com.",
    },
  ],
  ```
- `CRED_FIELDS`: add
  ```ts
  registry: [
    { name: "username", label: "Username", type: "text" },
    { name: "password", label: "Password or token", type: "password" },
  ],
  ```
- `defaultConfig`: add `case "registry": return { host: "" };`.
- `PROTOCOL_ORDER`: append `"registry"`. `PROTOCOL_LABEL`: add `registry: "Container registry"`. `PROTOCOL_HINT`: add `registry: "Image pull credentials"`.

- [ ] **Step 6: Refuse registry connections as data flows**

In `app/src/lib/associations/access.ts`, import `REGISTRY_NOT_A_FLOW_MESSAGE` from `@/lib/connections/schemas`. In `resolveUsableConnection`, after the not-found/group check and before the `return`, add:
```ts
  // C-1 (container-images spec §5): a registry connection is image pull
  // credentials for processes. No adapter can list or move files through it.
  if (connection.protocol === "registry") {
    return { response: jsonResponse(400, { error: REGISTRY_NOT_A_FLOW_MESSAGE }) };
  }
```
In `IngestFormDialog.tsx` (line ~336) and `DeliveryFormDialog.tsx` (line ~226), change `{connections.map((c) => (` to `{connections.filter((c) => c.protocol !== "registry").map((c) => (`.

- [ ] **Step 7: Pipeline: `decrypt_credentials`, the registry module, probe wiring, factory refusal**

`services/pipeline/src/pipeline/connections/build.py`: factor the decrypt half out of `build_adapter`:
```python
def decrypt_credentials(connection: ConnectionRow, master_key: bytes) -> dict:
    """Decrypt a connection's credential envelope. Raises
    :class:`AdapterBuildError` with a caller-safe message (never the secret)."""
    if connection.credentials is None:
        raise AdapterBuildError("connection has no stored credentials")
    try:
        credentials = json.loads(decrypt(connection.credentials, master_key))
    except EnvelopeError as exc:
        raise AdapterBuildError(f"credential decryption failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AdapterBuildError("credential payload is not valid JSON") from exc
    if not isinstance(credentials, dict):
        raise AdapterBuildError("credential payload is not an object")
    return credentials
```
and make `build_adapter` call `credentials = decrypt_credentials(connection, master_key)` in place of its inline decrypt block (the messages are unchanged, so `test_build_adapter.py` keeps passing).

`services/pipeline/src/pipeline/connections/adapters/factory.py`: before the `stac-api` branch, add:
```python
    if protocol == "registry":
        raise NotImplementedError(
            "registry connections hold image pull credentials; they carry no files"
        )
```
and add `` ``registry`` holds pull credentials and raises too.`` to the module docstring.

`services/pipeline/src/pipeline/connections/registry.py`:
```python
"""The ``registry`` connection protocol (C-1, container-images spec §5, ADR 0021).

A registry connection is group-owned image pull credentials: config ``{host}``,
credentials ``{username, password}`` sealed in the existing envelope. It
reuses the connections form, group ownership and the ``connection_checks``
drain. Its check probe is the registry v2 handshake: ``GET /v2/`` with Basic
auth. A Bearer challenge is followed to its token endpoint with the same
Basic credentials, and a token (200) means the credentials work.

Egress: every host dialled (the registry and a token realm) goes through
``resolve_pinned`` first, and connections are HTTPS only. Like
``http_fetch.py``, the request goes by HOSTNAME after validation so TLS can
verify the certificate. Redirects are not followed. Neither the password nor
the token ever appears in a result message or a log line.
"""

from __future__ import annotations

import asyncio
import base64
import http.client
import re
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from pipeline.connections.adapters.base import TestResult
from pipeline.connections.build import AdapterBuildError, decrypt_credentials
from pipeline.connections.egress import EgressBlocked, resolve_pinned
from pipeline.connections.repo import ConnectionRow

REGISTRY_PROTOCOL = "registry"
DOCKER_HUB_HOSTS = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})
DOCKER_HUB_API_HOST = "registry-1.docker.io"
PROBE_TIMEOUT_SECONDS = 15.0
_MAX_BODY = 64 * 1024

_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*(?::[0-9]{1,5})?"
)
_CHALLENGE_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')


class RegistryConfigError(ValueError):
    """A registry connection's config is not a usable ``{host}``."""


@dataclass(frozen=True)
class RegistryConfig:
    host: str


def parse_registry_config(raw: Any) -> RegistryConfig:
    if not isinstance(raw, dict):
        raise RegistryConfigError("registry config must be an object")
    host = raw.get("host")
    if not isinstance(host, str) or not host.strip():
        raise RegistryConfigError("registry config needs a host")
    host = host.strip().lower()
    if not _HOST_RE.fullmatch(host):
        raise RegistryConfigError(
            "registry host must be a bare hostname with an optional port, no scheme or path"
        )
    return RegistryConfig(host=host)


def registry_api_host(host: str) -> str:
    """Docker Hub's short names are served by registry-1.docker.io."""
    return DOCKER_HUB_API_HOST if host in DOCKER_HUB_HOSTS else host


def parse_bearer_challenge(header: str | None) -> dict[str, str] | None:
    if not header:
        return None
    scheme, _, params = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return {key.lower(): value for key, value in _CHALLENGE_PARAM_RE.findall(params)}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    #: lower-cased header names
    headers: Mapping[str, str]
    body: bytes


HttpGet = Callable[[str, Mapping[str, str]], HttpResponse]


def _https_get(url: str, headers: Mapping[str, str]) -> HttpResponse:  # pragma: no cover - network
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(
        parts.hostname or "",
        parts.port or 443,
        timeout=PROBE_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        conn.request("GET", path, headers={"User-Agent": "stac-higher-pipeline", **headers})
        resp = conn.getresponse()
        body = resp.read(_MAX_BODY)
        return HttpResponse(resp.status, {k.lower(): v for k, v in resp.getheaders()}, body)
    finally:
        conn.close()


def _host_only(host: str) -> str:
    return host.rsplit(":", 1)[0] if ":" in host else host


def check_registry(
    config: RegistryConfig,
    credentials: Mapping[str, Any],
    allow_hosts: frozenset[str],
    *,
    http_get: HttpGet = _https_get,
) -> TestResult:
    """Blocking. Never raises for an expected failure; never echoes a secret."""
    username = credentials.get("username")
    password = credentials.get("password")
    if not (isinstance(username, str) and username and isinstance(password, str) and password):
        return {"ok": False, "message": "registry credentials need a username and a password"}
    api_host = registry_api_host(config.host)
    started = time.monotonic()
    basic = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    rejected: TestResult = {"ok": False, "message": f"{config.host} rejected the credentials"}
    try:
        resolve_pinned(_host_only(api_host), allow_hosts)
        first = http_get(f"https://{api_host}/v2/", {"Authorization": basic})
        if first.status == 200:
            return _ok(config, started)
        if first.status != 401:
            return {"ok": False, "message": f"GET /v2/ on {api_host} returned {first.status}"}
        challenge = parse_bearer_challenge(first.headers.get("www-authenticate"))
        if not challenge or not challenge.get("realm"):
            return rejected
        realm = urlsplit(challenge["realm"])
        if realm.scheme != "https" or not realm.hostname:
            return {
                "ok": False,
                "message": f"{config.host} names a token endpoint that is not https",
            }
        resolve_pinned(realm.hostname, allow_hosts)
        query = {"service": challenge["service"]} if challenge.get("service") else {}
        token_url = challenge["realm"]
        if query:
            token_url = f"{token_url}{'&' if realm.query else '?'}{urlencode(query)}"
        token = http_get(token_url, {"Authorization": basic})
        if token.status == 200:
            return _ok(config, started)
        if token.status in (401, 403):
            return rejected
        return {
            "ok": False,
            "message": f"the token endpoint of {config.host} returned {token.status}",
        }
    except EgressBlocked as exc:
        return {"ok": False, "message": str(exc)}
    except (OSError, http.client.HTTPException) as exc:
        return {"ok": False, "message": f"{config.host} unreachable: {type(exc).__name__}"}


def _ok(config: RegistryConfig, started: float) -> TestResult:
    return {
        "ok": True,
        "message": f"authenticated to {config.host}",
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


async def test_registry_connection(
    connection: ConnectionRow,
    master_key: bytes,
    allow_hosts: frozenset[str],
    *,
    http_get: HttpGet = _https_get,
) -> TestResult:
    """The probe seam for ``protocol == "registry"`` (see ``probe.run_adapter_test``)."""
    try:
        config = parse_registry_config(connection.config)
        credentials = decrypt_credentials(connection, master_key)
    except (RegistryConfigError, AdapterBuildError) as exc:
        return {"ok": False, "message": str(exc)}
    return await asyncio.to_thread(
        check_registry, config, credentials, allow_hosts, http_get=http_get
    )


test_registry_connection.__test__ = False  # not a pytest test, despite its name
```

`services/pipeline/src/pipeline/connections/probe.py`: import `from pipeline.connections.registry import REGISTRY_PROTOCOL, test_registry_connection` and, at the top of `run_adapter_test`'s body (before `try: adapter = build_adapter(...)`), add:
```python
    if connection.protocol == REGISTRY_PROTOCOL:
        # C-1: a registry is not a storage adapter; its check is the v2 handshake.
        return await test_registry_connection(connection, master_key, allow_hosts)
```
Also extend the module docstring's first bullet with "(or the registry v2 handshake for ``registry`` connections)".

Note: `probe.py` imports `registry.py`, which imports `build.py`, which imports the adapters. `build.py` does not import `probe` or `registry`, so there is no cycle.

- [ ] **Step 8: Run and watch them pass**

Run (from `app/`): the four vitest files from Step 3, which should PASS.
Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py tests/test_registry_connection.py tests/test_probe.py tests/test_build_adapter.py tests/test_drain.py tests/test_health_sweep.py -q`, which should PASS.

- [ ] **Step 9: Docs**

- `docs/connections.md`:
  - In "Data model", change the protocol list to `(\`ssh|sftp|ftp|ftps|s3|stac-api|registry\`)`, noting that migration 030 widened the CHECK.
  - In "Per-protocol `config`", add the row `| \`registry\` | \`{host}\`: a bare registry hostname with an optional port (\`docker.io\`, \`ghcr.io\`, \`123456789012.dkr.ecr.us-gov-west-1.amazonaws.com\`). Image pull credentials for user-supplied process images (C-1, ADR 0021). A registry connection cannot carry a data flow (400). |`
  - In "Per-protocol `credentials`", add `| \`registry\` | \`{username, password}\` (a PAT, an ECR token, a robot account) |`.
  - Under "Pipeline side", add one paragraph: the registry check is `GET https://{host}/v2/` with Basic auth, following a Bearer challenge to its HTTPS token endpoint with the same credentials (`docker.io` is probed at `registry-1.docker.io`). Every host goes through `resolve_pinned`, and nothing is logged but the host and the status.
- `docs/processes.md`, "What a secret reference can reach" table: add `| \`registry\` | \`username\`, \`password\` |`.
- `tests/contract-fixtures/README.md`, C-1 section: `- \`registry-connection-config.json\` uses the ordinary \`minimal\`/\`defaults\`/\`cases[]\` format. It is the \`registry\` connection's \`{host}\`: strict and lowercase-only in \`registryConfigSchema\`, stripped and case-folded in \`parse_registry_config\`.`

- [ ] **Step 10: Gates and commit**

Run the full gates, which should all be green: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add tests/contract-fixtures/registry-connection-config.json tests/contract-fixtures/README.md \
  app/src/lib/connections/schemas.ts app/src/components/connections/ConnectionForm.tsx \
  app/src/lib/associations/access.ts app/src/components/collections/IngestFormDialog.tsx \
  app/src/components/collections/DeliveryFormDialog.tsx app/src/__tests__/contract-fixtures.test.ts \
  app/src/__tests__/connections-schemas.test.ts app/src/__tests__/connections-form.test.tsx \
  app/src/__tests__/api-associations.test.ts services/pipeline/src/pipeline/connections/ \
  services/pipeline/tests/test_contract_fixtures.py services/pipeline/tests/test_registry_connection.py \
  services/pipeline/tests/test_probe.py services/pipeline/tests/test_build_adapter.py \
  docs/connections.md docs/processes.md
git commit -m "feat(connections): the registry protocol, with its v2 check probe on both sides (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Declare the `process_image_flagged` alert kind

**Files:**
- Modify: `tests/contract-fixtures/alert-kinds.json`, `app/src/components/monitoring/shared.ts:50-62`, `app/src/__tests__/contract-fixtures.test.ts`, `services/pipeline/tests/test_contract_fixtures.py`, `docs/monitoring.md`

**Interfaces:**
- Produces: `"process_image_flagged"` in `kinds` (between `process_rate_limited` and `webhook_failed`) and in `declared_kinds`, plus `ALERT_KIND_LABEL.process_image_flagged`. C-4 moves it to a writer list.

- [ ] **Step 1: Failing tests**

`app/src/__tests__/contract-fixtures.test.ts`, inside the `alert kind enum` describe:
```ts
  it("process_image_flagged waits in declared_kinds until C-4 names its writer", () => {
    expect(fixture.kinds).toContain("process_image_flagged");
    expect(fixture.declared_kinds).toEqual(["process_image_flagged"]);
    expect(fixture.monitor_kinds).not.toContain("process_image_flagged");
  });
```
`services/pipeline/tests/test_contract_fixtures.py`:
```python
def test_process_image_flagged_is_declared_not_written():
    """C-1 declares the kind (container-images spec §10); no pipeline writer
    may claim it until C-4 lands pipeline/images/alerts.py."""
    from pipeline.flow.monitor import MONITOR_KINDS

    assert "process_image_flagged" in ALERT_KINDS["kinds"]
    assert ALERT_KINDS["declared_kinds"] == ["process_image_flagged"]
    assert "process_image_flagged" not in MONITOR_KINDS
```
Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts -t "alert"`, which should FAIL. Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py -q -k "alert or flagged"`, which should FAIL.

- [ ] **Step 2: Fixture + label**

`tests/contract-fixtures/alert-kinds.json`:
- In `kinds`, insert `"process_image_flagged"` after `"process_rate_limited"`. The order must stay monitor, then declared, then notify, because the partition test concatenates them.
- Set `"declared_kinds": ["process_image_flagged"]`.
- Append to `description`: ` C-1 (container-images spec §10) declared \`process_image_flagged\` (one open alert per process whose CURRENT revision references an image that is flagged, revoked or stale); C-4 moves it to its writer (pipeline/images/alerts.py).`

`app/src/components/monitoring/shared.ts`, in `ALERT_KIND_LABEL`, after `process_rate_limited`, add `process_image_flagged: "process image flagged",`.

- [ ] **Step 3: Docs**

In `docs/monitoring.md` "Alerts (M2-B)", after the table, add:
```markdown
Declared, no writer yet (`alert-kinds.json` `declared_kinds`): `process_image_flagged` (C-1, container-images spec §10). It will be one open alert per process whose current revision references a user image that is `flagged`, `revoked` or stale, anchored on `process_id` and auto-resolved when the image is approved again or the process moves off it. C-4 lands its writer. Routing will treat it like `process_failed`, and the process health verdict will treat it as degraded, not failing.
```

- [ ] **Step 4: Gates and commit**

Run the full gates, which should all be green: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add tests/contract-fixtures/alert-kinds.json app/src/components/monitoring/shared.ts \
  app/src/__tests__/contract-fixtures.test.ts services/pipeline/tests/test_contract_fixtures.py docs/monitoring.md
git commit -m "feat(alerts): declare process_image_flagged (writer arrives with C-4) (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Docs: "Bring your own image", the route row, FEATURES

**Files:**
- Modify: `docs/processes.md`, `docs/backend.md:107`, `docs/FEATURES.md`

- [ ] **Step 1: `docs/processes.md`**

(a) In `## Runtime image`, replace the first paragraph ("Every run executes on a **platform-built** image — a user-supplied image (`runtime.image`) is refused (ADR 0013). A revision picks WHICH platform image with an alias:") with:
```markdown
An `inline_python` revision runs on a **platform-built** image and picks WHICH one with an alias. (To run on your own image, see [Bring your own image](#bring-your-own-image).)
```

(b) Insert a new section directly before `## Network access`:
```markdown
## Bring your own image

A process can also run on an image you supply (ADR 0021). The runtime `kind` picks one of three shapes. The Lambda console's choice between "code" and "container image" is the model.

| `kind` | What runs | `code` | `image` | `runtime_image` | `command` |
|---|---|---|---|---|---|
| `inline_python` | your code on a platform image | required | `null` | `default` \| `stactools` | — |
| `inline_python_on_image` | your code on **your** image (the image is the dependency bundle) | required | snapshot | `null` | — |
| `container` | **your image's own entrypoint** (the image is the process) | refused | snapshot | `null` | optional |

`image` is an immutable **snapshot** of a registry row, taken at deploy time:

```json
"image": { "id": "…uuid…", "reference": "ghcr.io/org/satpy-runtime", "digest": "sha256:…" }
```

`reference` is the normalized repository: lowercase, with an explicit registry host and no tag (`docker.io/library/python` for a bare `python`). `digest` is the manifest digest that runs. **Only that digest ever runs.** A tag is resolved once, when the image is added, and is never followed afterwards. A later rescan, revocation or deletion never rewrites a stored revision.

`command` (kind 3 only) replaces the image's `CMD`: a non-empty list of up to 64 non-blank strings. It never replaces `ENTRYPOINT` or `USER`.

### What your image must provide

- **Kind 2:** `python3` (≥ 3.10) on `PATH`, and nothing else. The platform injects its runner and your code through the environment, not a mount, so no platform package or particular base image is needed.
- **Kind 3:** an entrypoint that speaks the run contract in this document: it reads its inputs from the environment and exits 0/1/2.

### What every user image gets, whatever its own config says

The platform's hardening applies, not the image's: all capabilities dropped, `no-new-privileges`, the revision's memory/CPU/timeout, the run's network profile, and **user `10001:10001`** whatever the image's `USER` says. An image whose files that uid cannot read fails at run time. Writable scratch is `/tmp` (a tmpfs sized with the memory limit) plus the run's output prefix.

### When a deploy is refused

The deploy answers **422** with a `code` when the snapshot's image is not usable:

| `code` | Meaning |
|---|---|
| `image_not_approved` | The image is not in the registry, or is `pending`, `scanning`, `rejected`, `flagged`, `revoked` or `scan_failed`. Only `approved` deploys. |
| `image_digest_mismatch` | The registry row with that id has a different reference or digest than the snapshot. |
| `image_stale` | The image has not been scanned within the policy's window (30 days by default, the FedRAMP rule). An exception does not cover staleness. |
| `image_group_mismatch` | The image is pulled with a `registry` connection that belongs to another group. |

A **503** `image_policy_unavailable` means the deployment's image policy (`PROCESS_IMAGE_POLICY_FILE`) is missing or invalid. User images fail closed, and inline revisions are unaffected.

An image is approved by a **scan**: an SBOM (Syft) and a vulnerability match (Grype, with CISA KEV and EPSS) evaluated against the deployment's policy. The default policy blocks any KEV entry, a fixed CRITICAL, an unfixed CRITICAL published more than 30 days ago, and a fixed HIGH with EPSS ≥ 0.1. A failing image needs an admin's expiring, audited exception. The scanner, the image registry page and rescans are being built now. Until the scanner exists nothing is approved, so every kind 2/3 deploy is refused with `image_not_approved`, and a kind 2/3 run that reached the pipeline by any other route dies naming ADR 0021.

### Private registries

Pull credentials are a group-owned `registry` connection (`{host}` plus `{username, password}`; [`connections.md`](connections.md)). An image added with a group's credential can be used only by that group's processes.
```

- [ ] **Step 2: `docs/backend.md`**

Replace the `/api/processes/[id]/revisions` row's text "`runtime.kind: container` is refused this slice (ADR 0013)" with "`runtime.kind` is `inline_python` \| `inline_python_on_image` \| `container`. A kind 2/3 snapshot must name an approved, fresh, digest-equal image the process's group may use: otherwise **422** with `code` `image_not_approved` \| `image_stale` \| `image_group_mismatch` \| `image_digest_mismatch`, or **503** `image_policy_unavailable` when the policy cannot be read (C-1, ADR 0021)".

- [ ] **Step 3: `docs/FEATURES.md`**

Insert a section directly before `## Pipeline graph views (P queue, 2026-09-04) ✅`:
```markdown
### Bring-your-own container images + scanning (C queue) 🔄

Spec: `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` (approved 2026-09-27), ADR 0021; epic #56.

| Slice | Status | Notes |
|---|---|---|
| C-1 · Contracts, migration 030, write gate | ✅ | `runtime.kind` is `inline_python \| inline_python_on_image \| container`. Kinds 2–3 carry an immutable `image` snapshot `{id, reference, digest}` with `runtime_image: null`; `command` is kind 3 only; `code` is required for kinds 1–2 and refused for kind 3 (Zod `processes/schemas.ts`, Python `process/config.py`). Migration **030** adds `container_images` (the platform-wide registry: metadata only, status machine, verdict, exception columns, drift columns) and `image_scans` (the ADR 0004 ledger), widens the connections protocol CHECK to `registry`, and indexes `process_revisions` by snapshot image id. The revisions route's DB-backed `checkImageGate` (`lib/images/gate.ts`) replaces `CONTAINER_RUNTIME_REFUSAL`: 422 `image_not_approved` / `image_stale` / `image_group_mismatch` / `image_digest_mismatch`, or 503 `image_policy_unavailable`. It refuses everything until C-2's scanner approves something. The pipeline dies any kind 2/3 run naming ADR 0021 (`launch.check_user_image_launchable`, C-2's seam). Both runtimes read the image policy (`infra/image-policy/default.json`, `PROCESS_IMAGE_POLICY_FILE`, packaged into both images through an `imagepolicy` build context) and the Python side has the pure `evaluate()` (KEV → fixed CRITICAL → aged unfixed CRITICAL → fixed HIGH by EPSS). The `registry` connection protocol has a v2 check probe (`connections/registry.py`) and is refused as a data-flow connection. `process_image_flagged` is declared. Fixtures: `image-status`, `image-reference`, `image-policy`, `image-scan-result`, `registry-connection-config`, plus the three-kind `process-runtime` and `alert-kinds`. No executor change, no new screen |
| C-2 · Scanner image, drain, digest-pinned launch | ⬜ | #51 |
| C-3 · API, `/images` dashboard, deploy-form chooser | ⬜ | #52 |
| C-4 · Rescans, drift, flagged/stale, exceptions, retention | ⬜ | #53 |
| C-5 · Live gate (lead) | ⬜ | #54 |
```

- [ ] **Step 4: Gate and commit**

Run `npm run verify`, which should be green.
```bash
git add docs/processes.md docs/backend.md docs/FEATURES.md
git commit -m "docs: Bring your own image, the revisions route's image gate, C queue in FEATURES (C-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Rebase, full gates, PR (lead only)

- [ ] `git fetch origin main && git rebase origin/main`. If K-3 (#11) has merged, resolve `migrate.ts` so that `029_process_runs_executor_phase` sits between 028 and `030_container_images`. Nothing is reordered: both names are appended entries. Re-run `images-migration.test.ts` (its "after 029" branch now runs) and K-3's `processes-migration.test.ts`. For a `package-lock.json` conflict: `git checkout --theirs package-lock.json && npm install && git add package-lock.json`.
- [ ] Gates: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check .`. All must be green.
- [ ] `git push -u origin feat/c1-image-contracts`, then `gh pr create --base main --title "C-1: image contracts, migration 030 and the image write gate"`. The body starts `Closes #50`, lists the gates run, and names the lead-only steps:
  - CI's `containers.yml` must build the `app` and `pipeline` images with the new `imagepolicy` context. This is the only verification the Dockerfile edits get before merge.
  - There is no live check. C-1 changes no executor and adds no screen. After merge, under the session's Docker policy (`smoke`), the lead MAY `docker compose build pipeline && docker compose up -d pipeline` and hit any app API route to apply migration 030 (`psql`: `\d stac_higher.container_images`), but nothing depends on it.
  - e2e is not required: no UI flow changed. The connection form gained a type card and the flow dialogs a filter, both covered by vitest.
  - The body also copies the "Decisions made in this plan" list below as deviations and choices, and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] After CI is green, squash-merge. `git worktree remove .claude/worktrees/c1-image-contracts`.
- [ ] Follow-ups to file or record: (a) comment on K-3 #11 and K-4's issue that migration **030 is taken**, so K-4's `cancelled` status (which the K-3 plan names as "migration 030") needs **031**; (b) C-2 owns the pipeline `/health` policy key and replaces `check_user_image_launchable`'s body; (c) C-3 owns normalizing typed references and `allowed_registries` on `POST /api/images` (it can reuse `registryAllowed` and `registryHost`).

---

## Decisions made in this plan

Each resolves a C-1 question the spec leaves open, choosing the option most consistent with spec §14. They go into the PR body verbatim.

1. **Missing or invalid image policy ⇒ 503 `image_policy_unavailable`, only for kinds 2–3.** Spec §7.2 says the loaders fail closed. §3's four reasons describe an image, not the deployment, so a fifth code with a distinct status keeps them honest. An inline deploy never reads the policy (Review Focus 1).
2. **Gate order: exists → approved → reference/digest → fresh → group.** Any non-approved status, `flagged` included, is `image_not_approved` with the status in the message. A reference mismatch is reported as `image_digest_mismatch` (spec §3: "reference and digest must equal the snapshot"). A pending row's NULL digest therefore reads as not approved (Review Focus 2).
3. **`runtime_image` on kinds 2–3 is reject/reject, not ignored by the reader.** The spec says "required-absent". A lenient reader silently ignoring an alias beside a user image would be the only place two images could name what runs.
4. **`image` on kind 1 flips to reject/reject** (spec §3, "reject/reject otherwise"). The app never stored one, so no row is affected.
5. **`command` on kinds 1–2 is app reject / pipeline accept.** It is an unknown key for those arms, the established lenient-reader direction. It is never read for them.
6. **A fifth fixture, `image-reference.json`** (grammar-cases), pins the reference and digest grammar that three shapes share (snapshot, scan result, migration CHECK). The grammar: lowercase, an explicit registry host (dotted or `localhost`, optional port), at least one path component, no tag, no digest, ≤ 255 characters. Digests are `sha256:` plus 64 lowercase hex characters only. Normalizing typed input is C-3's job.
7. **Python `ProcessRuntime.image: str` is replaced** by `image_id`/`image_reference`/`image_digest` plus `command`, and `runtime_image` becomes `str | None`. Spec §15 names those four fields. No code outside `config.py` read `.image`.
8. **The pipeline gets a C-1 launch guard.** `run_one` dies a kind 2/3 run naming ADR 0021 before the code check and before anything is staged or minted. This is outside the executor, which is unchanged. Without it a hand-inserted kind-2 revision would silently run on the platform image, and a kind-3 run would die as "no code". C-2 replaces the guard's body (`check_user_image_launchable`) with the §8.4 digest check. `resolve_runtime_image` also refuses kinds 2–3 as defence in depth.
9. **The policy file is packaged into both images in C-1** (`imagepolicy` build context, the K-1 pattern), because the app's gate reads it now. The pipeline's `/health` policy key stays C-2 (spec §15).
10. **Cross-field rule: `rescan_interval_hours ≤ 24 × scan_window_days`**, rejected on both sides. A policy that rescans less often than its own window guarantees every image goes stale.
11. **How `evaluate()` reads the result.** It works over `kev` (defined as the complete KEV list), `top` (≤ 25 by risk) and `fixed_counts`. A fixed CRITICAL outside `top` still blocks as `critical_fixed:*`. An unfixed CRITICAL with no `published_at` blocks as `critical_unfixed_age:<id>:unknown` (no date, no grace). A null EPSS never meets the EPSS rule. `high_unfixed` yields `high_unfixed:<id>`. Reasons are ordered registry → size → KEV → fixed CRITICAL → aged CRITICAL → EPSS HIGH → unfixed HIGH, and deduplicated. Residual risk: an aged unfixed CRITICAL or a high-EPSS fixed HIGH that ranks below 25 higher-risk findings is not named. C-2's scanner should sort so the ranking favours those rules. This is noted for C-2.
12. **Registry host patterns:** `*` is exactly one DNS label, hosts are case-folded, a port must match literally, and matching is anchored at both ends. `registryAllowed` exists in the app now so C-3 inherits the fixture-pinned rule.
13. **The `registry` connection.** Config `{host}` is lowercase-only for the writer, and the reader strips and folds case. Both credentials are required. `docker.io`/`index.docker.io` are probed at `registry-1.docker.io`. The probe is HTTPS-only and follows no redirects: `GET /v2/` with Basic auth, where 200 means ok; a 401 Bearer challenge is followed to the HTTPS realm with the same Basic credentials, where 200 means ok and 401/403 means rejected. Every dialled host goes through `resolve_pinned` first.
14. **A registry connection cannot back a data flow.** `resolveUsableConnection` answers 400, and the two flow dialogs filter registry connections out. The connection form gains the "Container registry" type card, because its protocol maps are typed `Record<WritableProtocol, …>` and C-3's image picker needs these connections to exist. This is the only UI in C-1.
15. **`registry_connection_id` is `ON DELETE RESTRICT`.** Connections are soft-deleted, and the gate's join excludes deleted ones, so a deleted credential reads as another group, never as a public image (Review Focus 4).
16. **Extra DB CHECKs** beyond spec §4.1: digest format (both digest columns); digest required unless status ∈ {`pending`, `scanning`, `scan_failed`, `revoked`}; and the four exception columns all-or-nothing (spec §4.4: `expires_at` is required).
17. **Scan result strictness:** the pipeline refuses `version != 1` and `top` > 25 (untrusted scanner output). The app reader tolerates a newer version and strips unknown keys, so C-3's dashboard survives a newer scanner.
18. **`IMAGE_STATUS_LABEL` lives in `lib/images/status.ts`, not a component.** The fixture test enforces full coverage now, so C-3's badge map cannot land a status unlabelled.
19. **Migration numbering.** 030 is appended after 028. K-3's 029 is inserted before it when K-3 lands, and they touch disjoint objects, so the apply order is irrelevant. The K-3 plan's line "`cancelled` … K-4, migration 030" is now stale, and K-4 needs 031. This is flagged in Task 9.

## Self-review

- **Spec coverage.**
  - §3: three kinds, snapshot, `runtime_image` null, `command`, code rule, and the write gate with four reasons (Tasks 1, 4, 5).
  - §3.1 and §3.2: documented (Task 8). The executor mechanics are C-2.
  - §4.1 and §4.2: both tables with every listed column and index, plus the `process_revisions` expression index (Task 1).
  - §4.3: statuses and fixture, with stale computed in the gate (Tasks 1, 4).
  - §4.4: exception columns (Task 1). The routes are C-3/C-4.
  - §5: `registry` protocol, fixture, check probe, secret-ref row (Task 6). `REGISTRY_DOCKERHUB_*` is C-2 per §15.
  - §6.4: the result fixture (Task 3).
  - §7.1: fixture and default file on both sides (Task 2).
  - §7.2: app gate reads `scan_window_days`, both loaders fail closed (Tasks 2, 4).
  - §7.3: `evaluate()` (Task 3).
  - §10: kind declared, labelled and documented (Task 7).
  - §15 docs list: `processes.md`, `backend.md`, `connections.md`, `monitoring.md`, `FEATURES.md` (Tasks 2, 6, 7, 8). `ISSUES.md` I-122…I-125 already exist.
- **Placeholders.** None remain. Every fixture literal (digests, the snapshot, the 65-entry command) is written out in full. Python snippets are wrapped to ruff's 100-column limit. If ruff's E501 flags a line after an edit, wrap it without changing its content.
- **Type consistency.**
  - `checkImageGate(snapshot: ImageSnapshot | null, groupId, options)` is defined in Task 4 and called that way in Task 5's route.
  - `ImageGateRow.registry_connection_group_id` is the same in storage, gate and tests.
  - `USER_IMAGE_KINDS` (Python) and `USER_IMAGE_RUNTIME_KINDS` (TS) are distinct names, one per runtime.
  - `parse_scan_result`/`ScanResult`/`Finding` are defined in Task 3 and consumed by `evaluate`.
  - `_policy_doc` is defined in Task 2 and reused by Task 3's evaluate harness (`block_patch` only; the result side uses `result_patch`).
  - `decrypt_credentials` is defined in Task 6's `build.py` edit and consumed by `registry.py`.
  - `check_user_image_launchable`/`ImageUnusable` are defined in `launch.py` and imported by `runner.py`.
