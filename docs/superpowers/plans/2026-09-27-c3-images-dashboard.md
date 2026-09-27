# C-3 · Images API, `/images` Dashboard and the Runtime Chooser — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give operators the surface for user-supplied images. This covers the `/api/images` routes (add, list, detail, scan poll, findings, rescan, exception, revoke), the policy read route, the internal `approved?digest=` probe and the connection-impact count. It adds a platform-wide `/images` dashboard with a detail sheet and an "Add image" dialog that polls the scan. It also puts a Lambda-style **Runtime** chooser into the deploy form (platform image / custom image + your code / container image) with an image picker, and shows the pinned digest on run rows. The overview gains a count of flagged or stale images in use. Without C-2 merged, every added image stays `Waiting for scan` and nothing can be picked. That is correct behaviour.

**Architecture:** C-1 left `app/src/lib/images/` with the vocabulary, grammar, policy loader, scan-result reader and the deploy gate. C-3 adds four things. First, pure helpers: typed-reference normalization, staleness, a lenient verdict reader and request schemas. Second, additive registry reads and writes in `lib/images/storage.ts`. Third, route handlers under `app/src/pages/api/images/`, registered in the gated-route table with new audit actions. Fourth, a client layer (`lib/images/{api,queries,types}.ts` plus `imageKeys`) consumed by `components/images/*` and the deploy card. The app never touches a registry: `POST /api/images` INSERTs a `pending` row plus an `admission` scan request (ADR 0004), and C-2's drain makes it real. There is no DDL: migration 030 already has every column C-3 reads.

**Tech Stack:** Astro 7 API routes, Zod v4, TanStack Query v5, React 19, React Hook Form + `@hookform/resolvers/zod`, shadcn primitives (shared `Badge`/`Button`/`Card`/`Input`/`Label`, app `Dialog`/`Sheet`/`Table`), lucide-react, vitest + Testing Library, Playwright (lead only).

**Spec:** `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` §9 (routes, dashboard, deploy form) and the C-3 text in §15; §2 and §14 are settled and not reopened. ADR `docs/decisions/0021-user-images-scan-then-approve.md`. Epic #56, issue #52. The merged C-1 plan `docs/superpowers/plans/2026-09-27-c1-image-contracts.md` ("Decisions made in this plan" binds the grammar, gate order and module layout used here).

## Global Constraints

- **Worktree:** `.claude/worktrees/c3-images-dashboard`, branch `feat/c3-images-dashboard` (GitHub issue #52, epic #56). It is already created off `main` at `af82cab` (C-1 merged) with `npm install` done. Never work in the main checkout or in another worktree.
- **Gates:** every task ends with `npm run verify` from the worktree root. It must be green before the task's commit. No task in this plan touches `services/pipeline/`. If one ever does, also run `cd services/pipeline && uv run pytest -q && uv run ruff check .`. Teammates never run e2e, the dev server, Docker or `git push`.
- **No DDL, no migration.** Migration 029 is reserved for K-3, 030 is C-1's, and 031 is reserved for K-4. If any step appears to need a schema change, STOP and report to the lead. Do not pick a number.
- **No new npm or Python dependency.** Never hand-edit `components/ui/` in the app or in `packages/shared` (a hook blocks it). Everything this plan needs already exists: `dialog`, `sheet`, `table` (app), `badge`/`button`/`card`/`input`/`label` (shared).
- **Reuse C-1, additively.** Reuse `registryAllowed` and `loadImagePolicy` (`lib/images/policy.ts`), `registryHost`, `isImageReference`, `isImageDigest` and `imageSnapshotSchema` (`lib/images/reference.ts`), `IMAGE_STATUS_LABEL` and `IMAGE_STATUSES` (`lib/images/status.ts`), `imageScanResultSchema` (`lib/images/scan-result.ts`), and `imagePolicyUnavailable` (`lib/images/gate.ts`). Do not change existing exports of those files. C-2 (#51) is being built in parallel. `lib/images/storage.ts` is the only C-1 module this plan edits, and only by appending.
- **Stored reference grammar (C-1, verbatim):** a stored reference is lowercase, has an explicit registry host (dotted or `localhost`, optional port), at least one path component, no tag and no digest, and is at most 255 characters. Digests are `sha256:` plus 64 lowercase hex characters.
- **Image statuses (spec §4.1):** `pending | scanning | approved | rejected | flagged | revoked | scan_failed`. Scan kinds: `admission | rescan`. Scan statuses: `pending | running | done | failed`. Every status badge text comes from `IMAGE_STATUS_LABEL`.
- **RBAC (spec §9.1):** reads are member+ (authenticated). `POST /api/images` and `POST /api/images/[id]/rescan` are operator+. `exception` and `revoke` are admin, checked in-route because the guard's gate is operator-level. Every mutation route is registered in `lib/authz/permissions.ts`, so the guard writes exactly one `audit_log` row. Authz failures use `{ error, code }` with 401 `unauthenticated` or 403 `forbidden`. Credentials never leave the envelope: image responses carry a registry connection's `id`, `name`, `group_id` and a `deleted` flag, never its config secrets or credentials.
- **UI conventions:** each Astro page is a thin shell with one React island. TanStack Query holds server state, with keys only from `lib/query/keys.ts`. The Add-image dialog uses RHF + Zod. The deploy card keeps its existing `useState` pattern (see Decisions). Mutating verbs render only for operator/admin (`useAuthMe().identity.roles`). Icons come from lucide.
- **Copy rules:** UI text says "image", "scan", "digest", "registry credential". The chooser's three labels are exactly `Platform image`, `Custom image + your code` and `Container image`. The dashboard's h1 is `Images`, and the nav label is `Images`.
- **Commit trailer.** Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## Review Focus

These are the inputs a person will meet that the spec implies but no happy-path test exercises. Each line names the owning task, and that task carries the test.

1. **Typed references that look like something else.** `localhost:5000/team/img` has a port, not a tag. A bare `python` must become `docker.io/library/python`. `Org/Tool:V1` keeps the tag's case but lowercases the repository. `python@sha256:…` must be refused with a reason, never half-parsed (Task 1 tests).
2. **An image whose registry credential was soft-deleted.** The dashboard, the detail sheet and the picker must say "credential deleted" and must not present the image as a public, usable one. The gate already answers `image_group_mismatch` "was deleted" (Task 2 test "keeps soft-deleted connections and flags them", Task 8 picker test, Task 9 page test).
3. **The image policy file is missing or invalid.** `GET /api/images` still lists, with `stale: null` and `scan_window_days: null`, and the dashboard says why. `POST /api/images` answers 503 `image_policy_unavailable` before writing a row (Task 4 tests, Task 9 test).
4. **A second "Add" of the same reference while its first scan is still open** (double-click, a second operator) must not create a second provisional row. It answers 202 with the open `{image_id, scan_id}` and `deduplicated: true` (Task 4 test).
5. **Polling a scan after C-2's drain de-duplicated it.** The provisional image row is deleted and the scan is re-pointed at the existing image. The poll by the 202's ids must still find the scan and return the authoritative image, not 404 (Task 2 SQL test, Task 4 route test).

## File map

Created:

| Path | Responsibility |
|---|---|
| `app/src/lib/images/normalize.ts` | typed reference + tag → stored `{reference, tag}`; Docker Hub host aliases |
| `app/src/lib/images/stale.ts` | computed staleness (the gate's boundary) |
| `app/src/lib/images/verdict.ts` | lenient reader for `container_images.verdict` (client-safe) |
| `app/src/lib/images/schemas.ts` | request bodies: add, exception |
| `app/src/lib/images/access.ts` | route preamble (member/operator/admin), 404 helper, policy-derived view |
| `app/src/lib/images/api.ts` | browser client for `/api/images*` and the policy route |
| `app/src/lib/images/queries.ts` | TanStack hooks |
| `app/src/lib/images/types.ts` | type-only re-exports of the API shapes |
| `app/src/lib/processes/command.ts` | `command` text ⇄ `string[]` (quotes, escapes) |
| `app/src/pages/api/images/index.ts` | `GET` list, `POST` add |
| `app/src/pages/api/images/[id].ts` | `GET` detail |
| `app/src/pages/api/images/[id]/scans/[scanId].ts` | `GET` scan poll |
| `app/src/pages/api/images/[id]/scans/[scanId]/findings.ts` | `GET` 302 to the presigned findings |
| `app/src/pages/api/images/[id]/rescan.ts` | `POST` rescan request |
| `app/src/pages/api/images/[id]/exception.ts` | `POST` admin exception |
| `app/src/pages/api/images/[id]/revoke.ts` | `POST` admin revoke |
| `app/src/pages/api/processes/image-policy.ts` | `GET` the policy minus `scan_limits` |
| `app/src/pages/api/internal/images/approved.ts` | `GET ?digest=` → `{approved}` (K-6 seam) |
| `app/src/pages/images.astro` | the page shell |
| `app/src/components/images/format.ts` | short digest, config warning, findings order, DB age, scan error |
| `app/src/components/images/picker.ts` | image options for the picker, usability reason |
| `app/src/components/images/ImageStatusBadge.tsx` | fixture-labelled status + stale + credential-deleted |
| `app/src/components/images/SeverityStack.tsx` | compact counts with KEV called out |
| `app/src/components/images/AddImageDialog.tsx` | add → 202 → live scan status |
| `app/src/components/images/ImagePicker.tsx` | the deploy form's picker |
| `app/src/components/images/ImageDetailSheet.tsx` | row detail |
| `app/src/components/images/ImagesPage.tsx` | the island |
| `app/src/components/processes/runtime-form.ts` | chooser state ⇄ runtime payload |
| tests: `images-normalize`, `images-registry-storage`, `authz-images-gate`, `api-images`, `api-images-verbs`, `api-images-internal`, `images-client`, `images-components`, `images-page`, `process-runtime-form` | one per task (paths in each task) |

Modified: `app/src/lib/images/storage.ts` (append), `app/src/lib/authz/permissions.ts`, `app/src/lib/authz/guard.ts`, `app/src/env.d.ts`, `app/src/lib/connections/deletion.ts`, `app/src/lib/connections/api.ts`, `app/src/components/connections/ConnectionsPage.tsx`, `app/src/lib/query/keys.ts`, `app/src/components/layout/SidebarNav.tsx`, `app/src/components/processes/ProcessDetailPage.tsx`, `app/src/components/layout/overview.ts`, `app/src/components/layout/DashboardPage.tsx`, `app/e2e/processes.spec.ts`, `docs/backend.md`, `docs/processes.md`, `docs/FEATURES.md`, and the existing tests `authz-guard.test.ts`, `connections-deletion.test.ts`, `process-code-card.test.tsx`, `overview.test.ts`.

**Files C-2 is likely to touch too** (whichever PR merges second rebases): `app/src/lib/images/storage.ts` (only if C-2 adds app-side reads; C-3 only appends), `docs/backend.md` (C-2 adds env rows, C-3 adds route rows), `docs/processes.md` ("Bring your own image": C-2 edits the launch text, C-3 the UI text), `docs/FEATURES.md` (each flips its own row), and possibly `app/src/lib/images/status.ts` or `scan-result.ts` if C-2 finds a contract gap. C-3 does not edit those two.

---

### Task 0: Precondition (read-only)

- [ ] From the worktree root, run each command and check its result. If any check fails, STOP and report:
  - `git branch --show-current` prints `feat/c3-images-dashboard`.
  - `git merge-base --is-ancestor af82cab HEAD && echo ok` prints `ok` (C-1 is in the branch).
  - `grep -c '"030_container_images"' app/src/lib/db/migrate.ts` prints `1`, and `grep -c '"031_' app/src/lib/db/migrate.ts` prints `0`.
  - `ls app/src/lib/images/` lists exactly `gate.ts policy.ts reference.ts scan-result.ts status.ts storage.ts`.
  - `ls app/src/pages/api/images 2>/dev/null` prints nothing (no images routes yet).
  - `npm run verify` is green before any change.

---

### Task 1: Pure helpers — reference normalization, staleness, verdict reader, request schemas

**Files:**
- Create: `app/src/lib/images/normalize.ts`, `app/src/lib/images/stale.ts`, `app/src/lib/images/verdict.ts`, `app/src/lib/images/schemas.ts`
- Test: `app/src/__tests__/images-normalize.test.ts`

**Interfaces:**
- Consumes: `isImageReference` (`lib/images/reference.ts`), `ImageStatus` (`lib/images/status.ts`).
- Produces:
  - `normalizeImageInput(typed: string, explicitTag?: string | null): NormalizedImage` where `NormalizedImage = { ok: true; reference: string; tag: string } | { ok: false; error: string }`
  - `canonicalRegistryHost(host: string): string`, `IMAGE_TAG_RE`, `DEFAULT_IMAGE_TAG = "latest"`
  - `isImageStale(status: ImageStatus, lastScannedAt: Date | string | null, scanWindowDays: number | null, now: Date): boolean | null`
  - `imageVerdictSchema`, `type ImageVerdict`, `readVerdict(raw: unknown): ImageVerdict | null`
  - `imageAddSchema` / `type ImageAdd` (`{reference, tag?, registry_connection_id?}`), `imageExceptionSchema` / `type ImageException` (`{reason, expires_at}`), `IMAGE_EXCEPTION_REASON_MIN = 10`

- [ ] **Step 1: Write the failing test** — `app/src/__tests__/images-normalize.test.ts`

```ts
import { describe, expect, it } from "vitest";
import {
  DEFAULT_IMAGE_TAG,
  canonicalRegistryHost,
  normalizeImageInput,
} from "@/lib/images/normalize";
import { isImageStale } from "@/lib/images/stale";
import { readVerdict } from "@/lib/images/verdict";
import { imageAddSchema, imageExceptionSchema } from "@/lib/images/schemas";
import { isImageReference } from "@/lib/images/reference";

function ok(typed: string, tag?: string | null) {
  const result = normalizeImageInput(typed, tag);
  if (!result.ok) throw new Error(`expected ok for ${typed}: ${result.error}`);
  return { reference: result.reference, tag: result.tag };
}

function refused(typed: string, tag?: string | null): string {
  const result = normalizeImageInput(typed, tag);
  if (result.ok) throw new Error(`expected a refusal for ${typed}`);
  return result.error;
}

describe("normalizeImageInput (C-3: what a person types -> C-1's stored grammar)", () => {
  it.each([
    ["python", { reference: "docker.io/library/python", tag: DEFAULT_IMAGE_TAG }],
    ["python:3.12-slim", { reference: "docker.io/library/python", tag: "3.12-slim" }],
    ["docker.io/python", { reference: "docker.io/library/python", tag: "latest" }],
    ["index.docker.io/library/python:3", { reference: "docker.io/library/python", tag: "3" }],
    ["registry-1.docker.io/org/tool:1", { reference: "docker.io/org/tool", tag: "1" }],
    ["Org/Tool:V1", { reference: "docker.io/org/tool", tag: "V1" }],
    ["GHCR.IO/Org/Img", { reference: "ghcr.io/org/img", tag: "latest" }],
    ["ghcr.io/org/satpy-runtime:1.4.2", { reference: "ghcr.io/org/satpy-runtime", tag: "1.4.2" }],
    ["localhost:5000/team/img", { reference: "localhost:5000/team/img", tag: "latest" }],
    ["localhost:5000/team/img:dev", { reference: "localhost:5000/team/img", tag: "dev" }],
    [
      "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/app:prod",
      { reference: "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/app", tag: "prod" },
    ],
    ["  ghcr.io/org/img  ", { reference: "ghcr.io/org/img", tag: "latest" }],
  ])("normalizes %s", (typed, expected) => {
    const result = ok(typed);
    expect(result).toEqual(expected);
    expect(isImageReference(result.reference)).toBe(true);
  });

  it("takes the tag field when the reference carries none, and agrees when both match", () => {
    expect(ok("ghcr.io/org/img", "2.0")).toEqual({ reference: "ghcr.io/org/img", tag: "2.0" });
    expect(ok("ghcr.io/org/img:2.0", "2.0")).toEqual({ reference: "ghcr.io/org/img", tag: "2.0" });
    expect(ok("ghcr.io/org/img", "  ")).toEqual({ reference: "ghcr.io/org/img", tag: "latest" });
  });

  it("refuses what it cannot store, with a reason a person can act on", () => {
    expect(refused("")).toMatch(/Enter an image reference/);
    expect(refused("https://ghcr.io/org/img")).toMatch(/scheme/);
    expect(refused("python@sha256:" + "a".repeat(64))).toMatch(/by tag, not by digest/);
    expect(refused("ghcr.io/org/img:1", "2")).toMatch(/"1".*"2"/);
    expect(refused("ghcr.io/org/img:")).toMatch(/not a valid tag/);
    expect(refused("ghcr.io/org/my img")).toMatch(/no spaces/);
    expect(refused("ghcr.io/org/-bad")).toMatch(/not an image reference/);
  });

  it("folds the Docker Hub host aliases to docker.io", () => {
    expect(canonicalRegistryHost("Index.Docker.IO")).toBe("docker.io");
    expect(canonicalRegistryHost("registry-1.docker.io")).toBe("docker.io");
    expect(canonicalRegistryHost("GHCR.io")).toBe("ghcr.io");
  });
});

describe("isImageStale (spec §4.3: computed, never stored)", () => {
  const now = new Date("2026-09-27T12:00:00.000Z");
  const DAY = 86_400_000;

  it("uses the gate's boundary: exactly the window ago is fresh, a millisecond older is stale", () => {
    const edge = new Date(now.getTime() - 30 * DAY);
    expect(isImageStale("approved", edge, 30, now)).toBe(false);
    expect(isImageStale("approved", new Date(edge.getTime() - 1), 30, now)).toBe(true);
    expect(isImageStale("flagged", edge.toISOString(), 30, now)).toBe(false);
  });

  it("calls a scanned-status image with no scan time stale", () => {
    expect(isImageStale("approved", null, 30, now)).toBe(true);
  });

  it("is never stale for statuses that have not passed a scan", () => {
    for (const status of ["pending", "scanning", "rejected", "revoked", "scan_failed"] as const) {
      expect(isImageStale(status, null, 30, now)).toBe(false);
    }
  });

  it("is unknown (null) when the policy window is unknown", () => {
    expect(isImageStale("approved", now, null, now)).toBeNull();
  });
});

describe("readVerdict (lenient: the pipeline owns the shape)", () => {
  it("reads the spec §4.1 verdict and keeps defaults for missing lists", () => {
    expect(
      readVerdict({ pass: false, reasons: ["kev:CVE-2026-1"], counts: { critical: 1 }, extra: 1 }),
    ).toEqual({ pass: false, reasons: ["kev:CVE-2026-1"], counts: { critical: 1 }, kev: [] });
  });

  it("answers null for anything that is not a verdict", () => {
    expect(readVerdict(null)).toBeNull();
    expect(readVerdict({ reasons: [] })).toBeNull();
    expect(readVerdict("pass")).toBeNull();
  });
});

describe("request schemas", () => {
  it("accepts an add body and refuses unknown keys", () => {
    expect(imageAddSchema.safeParse({ reference: "python" }).success).toBe(true);
    expect(
      imageAddSchema.safeParse({
        reference: "ghcr.io/org/img",
        tag: "1.0",
        registry_connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
      }).success,
    ).toBe(true);
    expect(imageAddSchema.safeParse({ reference: "python", digest: "x" }).success).toBe(false);
    expect(imageAddSchema.safeParse({ reference: "   " }).success).toBe(false);
  });

  it("requires a real reason and an offset timestamp for an exception", () => {
    expect(
      imageExceptionSchema.safeParse({
        reason: "Vendor fix lands next sprint; tracked in TT-12",
        expires_at: "2026-10-27T00:00:00Z",
      }).success,
    ).toBe(true);
    expect(
      imageExceptionSchema.safeParse({ reason: "ok", expires_at: "2026-10-27T00:00:00Z" }).success,
    ).toBe(false);
    expect(
      imageExceptionSchema.safeParse({ reason: "A long enough reason", expires_at: "next week" }).success,
    ).toBe(false);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/images-normalize.test.ts`
Expected: FAIL (`Cannot find module '@/lib/images/normalize'`).

- [ ] **Step 3: Write the implementation**

`app/src/lib/images/normalize.ts`:

```ts
/**
 * Normalizing what a person TYPES into C-1's stored reference grammar
 * (C-3, container-images spec §9.1: `POST /api/images` "normalizes the
 * reference"). The stored form is `lib/images/reference.ts`'s. This module
 * only produces it.
 *
 * Rules (Docker's, the ones people already know):
 *   - The tag follows the last `:` AFTER the last `/`. An earlier `:` is a
 *     registry port (`localhost:5000/team/img`).
 *   - The first component is a registry host when it has a `.` or `:` or is
 *     `localhost`. Otherwise the host is Docker Hub, and a single-component
 *     Hub name lives under `library/` (`python` -> `docker.io/library/python`).
 *   - Host and repository are lowercased. Tags are case-sensitive and kept
 *     as typed.
 *   - A digest (`@sha256:…`) is refused: the scanner resolves a TAG and pins
 *     the digest it scanned (spec §6.3). Adding by digest is not in v1.
 */
import { isImageReference } from "./reference";

/** An OCI tag: 1–128 characters, first one a word character. */
export const IMAGE_TAG_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
export const DEFAULT_IMAGE_TAG = "latest";

const DOCKER_HUB_ALIASES = new Set([
  "docker.io",
  "index.docker.io",
  "registry-1.docker.io",
  "registry.hub.docker.com",
]);

/** A registry host as the platform stores it: lowercase, Docker Hub's
 * aliases folded to `docker.io`. Used for references AND for a `registry`
 * connection's `config.host`, so the two compare equal. */
export function canonicalRegistryHost(host: string): string {
  const lower = host.trim().toLowerCase();
  return DOCKER_HUB_ALIASES.has(lower) ? "docker.io" : lower;
}

export type NormalizedImage =
  | { ok: true; reference: string; tag: string }
  | { ok: false; error: string };

export function normalizeImageInput(
  typed: string,
  explicitTag?: string | null,
): NormalizedImage {
  const raw = typed.trim();
  if (raw.length === 0) {
    return { ok: false, error: "Enter an image reference, e.g. ghcr.io/org/tool:1.2" };
  }
  if (/\s/.test(raw)) return { ok: false, error: "An image reference has no spaces" };
  if (raw.includes("://")) {
    return {
      ok: false,
      error: "Leave out the scheme: ghcr.io/org/tool, not https://ghcr.io/org/tool",
    };
  }
  if (raw.includes("@")) {
    return {
      ok: false,
      error:
        "Add an image by tag, not by digest: the scan resolves the tag and pins the digest it scanned",
    };
  }

  const lastSlash = raw.lastIndexOf("/");
  const lastColon = raw.lastIndexOf(":");
  let name = raw;
  let typedTag: string | null = null;
  if (lastColon > lastSlash) {
    name = raw.slice(0, lastColon);
    typedTag = raw.slice(lastColon + 1);
  }
  const wanted = explicitTag?.trim() ? explicitTag.trim() : null;
  if (typedTag !== null && wanted !== null && typedTag !== wanted) {
    return {
      ok: false,
      error: `The reference names tag "${typedTag}" but the tag field says "${wanted}"; give one`,
    };
  }
  const tag = typedTag ?? wanted ?? DEFAULT_IMAGE_TAG;
  if (!IMAGE_TAG_RE.test(tag)) {
    return {
      ok: false,
      error: `"${tag}" is not a valid tag (letters, digits, _ . - and at most 128 characters)`,
    };
  }

  const parts = name.split("/");
  const first = parts[0];
  const hasHost =
    parts.length > 1 &&
    (first.includes(".") || first.includes(":") || first.toLowerCase() === "localhost");
  const host = hasHost ? canonicalRegistryHost(first) : "docker.io";
  let path = (hasHost ? parts.slice(1) : parts).map((part) => part.toLowerCase());
  if (host === "docker.io" && path.length === 1) path = ["library", ...path];
  const reference = [host, ...path].join("/");
  if (!isImageReference(reference)) {
    return {
      ok: false,
      error: `"${raw}" is not an image reference this platform can store (read as ${reference})`,
    };
  }
  return { ok: true, reference, tag };
}
```

`app/src/lib/images/stale.ts`:

```ts
/**
 * Staleness (container-images spec §4.3) is computed, never stored. It only
 * means something for an image that has passed a scan (`approved`,
 * `flagged`). Every other status is already blocked for its own reason, so
 * it reads as not stale. The boundary is the deploy gate's
 * (`evaluateImageGate`): a scan exactly `scanWindowDays` ago is still fresh.
 * `null` means "unknown" because the policy (and so the window) could not
 * be read.
 */
import type { ImageStatus } from "./status";

const DAY_MS = 86_400_000;

export function isImageStale(
  status: ImageStatus,
  lastScannedAt: Date | string | null,
  scanWindowDays: number | null,
  now: Date,
): boolean | null {
  if (status !== "approved" && status !== "flagged") return false;
  if (scanWindowDays === null) return null;
  if (lastScannedAt === null) return true;
  const scanned = new Date(lastScannedAt).getTime();
  return scanned < now.getTime() - scanWindowDays * DAY_MS;
}
```

`app/src/lib/images/verdict.ts`:

```ts
/**
 * `container_images.verdict` as the UI reads it (spec §4.1:
 * `{pass, reasons[], counts, fixed_counts, kev[], max_risk, evaluated_at,
 * policy_version}`). The pipeline writes it (C-2). This reader is lenient on
 * purpose, like the scan-result reader: unknown keys are dropped and missing
 * lists default, so the dashboard survives a newer writer. Client-safe (zod
 * only).
 */
import { z } from "zod";

export const imageVerdictSchema = z.object({
  pass: z.boolean(),
  reasons: z.array(z.string()).default([]),
  counts: z.record(z.string(), z.number()).optional(),
  fixed_counts: z.record(z.string(), z.number()).optional(),
  kev: z.array(z.string()).default([]),
  max_risk: z.number().nullable().optional(),
  evaluated_at: z.string().nullable().optional(),
  policy_version: z.number().nullable().optional(),
});

export type ImageVerdict = z.infer<typeof imageVerdictSchema>;

export function readVerdict(raw: unknown): ImageVerdict | null {
  const parsed = imageVerdictSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}
```

`app/src/lib/images/schemas.ts`:

```ts
/**
 * Request bodies for the `/api/images` routes (C-3, container-images spec
 * §9.1). `reference` is what the person typed. The route normalizes it
 * (`normalize.ts`) and checks the policy's `allowed_registries` before any
 * row is written.
 */
import { z } from "zod";

export const imageAddSchema = z
  .object({
    reference: z.string().trim().min(1, "reference is required").max(512),
    tag: z.string().trim().min(1).max(128).optional(),
    registry_connection_id: z.string().uuid().nullable().optional(),
  })
  .strict();
export type ImageAdd = z.infer<typeof imageAddSchema>;

/** Spec §4.4: the reason is free text shown on the dashboard and recorded in
 * the audit row. Ten characters keeps "ok" out without dictating prose. */
export const IMAGE_EXCEPTION_REASON_MIN = 10;

export const imageExceptionSchema = z
  .object({
    reason: z
      .string()
      .trim()
      .min(IMAGE_EXCEPTION_REASON_MIN, `reason must be at least ${IMAGE_EXCEPTION_REASON_MIN} characters`)
      .max(2000),
    expires_at: z.iso.datetime({ offset: true }),
  })
  .strict();
export type ImageException = z.infer<typeof imageExceptionSchema>;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/images-normalize.test.ts`
Expected: PASS. If `z.iso.datetime` is flagged by the type checker, the installed Zod is older than 4.0: STOP and report. Do not swap in the deprecated `z.string().datetime()` silently.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify` (worktree root). Expected: green.

```bash
git add app/src/lib/images/normalize.ts app/src/lib/images/stale.ts app/src/lib/images/verdict.ts app/src/lib/images/schemas.ts app/src/__tests__/images-normalize.test.ts
git commit -m "feat(images): typed-reference normalization, staleness, verdict reader, request schemas (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: Registry reads and writes in `lib/images/storage.ts`

**Files:**
- Modify: `app/src/lib/images/storage.ts` (imports at the top; everything else is appended after `getImageForGate`)
- Test: `app/src/__tests__/images-registry-storage.test.ts` (new; C-1's `images-storage.test.ts` stays untouched)

**Interfaces:**
- Consumes: `query`, `getClient` (`@/lib/db/connection`), `runMigrations`, `isImageStale` (Task 1), `ImageStatus`, `ImageScanKind`.
- Produces (all exported from `@/lib/images/storage`):
  - types `ApiImage`, `ApiImageRegistryConnection`, `ApiImageException`, `ApiImageScan`, `ApiImageUser`, `ImageView` (`{ now: Date; scanWindowDays: number | null }`), `ImageListFilters` (`{ status?: ImageStatus; q?: string; inUse?: boolean }`), `AddImageInput`, `AddedImage` (`{ image_id; scan_id; deduplicated }`), `ScanRequestOutcome`, `ExceptionOutcome`, `RevokeOutcome`
  - `IMAGE_LIST_LIMIT = 500`
  - `listImages(filters: ImageListFilters, view: ImageView): Promise<ApiImage[]>`
  - `getImage(id: string, view: ImageView): Promise<ApiImage | null>`
  - `listImageScans(imageId: string, limit?: number): Promise<ApiImageScan[]>`
  - `getImageScan(imageId: string, scanId: string): Promise<ApiImageScan | null>`
  - `listImageUsers(imageId: string): Promise<ApiImageUser[]>`
  - `findOpenAdmission(input: Omit<AddImageInput, "addedBy">): Promise<AddedImage | null>`
  - `insertImageWithAdmission(input: AddImageInput): Promise<AddedImage>`
  - `requestImageScan(imageId: string, requestedBy: string): Promise<ScanRequestOutcome>`
  - `grantImageException(input: { imageId: string; reason: string; by: string; expiresAt: Date }): Promise<ExceptionOutcome>`
  - `revokeImage(imageId: string): Promise<RevokeOutcome>`
  - `isDigestLaunchable(digest: string, scanWindowDays: number): Promise<boolean>`

- [ ] **Step 1: Write the failing test** — `app/src/__tests__/images-registry-storage.test.ts`

```ts
// @vitest-environment node
/**
 * C-3 registry storage: pins the SQL shapes that carry a rule (the
 * connection join keeps soft-deleted rows, in-use counts CURRENT revisions
 * of LIVE processes, the scan poll survives the drain's dedup), and the
 * row -> API mapping. The DB is mocked; this is not an integration test.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { getClient, query } from "@/lib/db/connection";
import {
  IMAGE_LIST_LIMIT,
  findOpenAdmission,
  getImage,
  getImageScan,
  grantImageException,
  insertImageWithAdmission,
  isDigestLaunchable,
  listImageUsers,
  listImages,
  requestImageScan,
  revokeImage,
} from "@/lib/images/storage";

const mockQuery = vi.mocked(query);
const mockGetClient = vi.mocked(getClient);
const NOW = new Date("2026-09-27T12:00:00.000Z");
const VIEW = { now: NOW, scanWindowDays: 30 };
const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";
const DIGEST = "sha256:" + "a".repeat(64);

function imageRow(overrides: Record<string, unknown> = {}) {
  return {
    id: IMG,
    reference: "ghcr.io/org/img",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    verdict: { pass: true, reasons: [] },
    size_bytes: "812345678",
    config: { user: "", entrypoint: ["/entry.sh"], cmd: [] },
    last_scan_id: SCAN,
    last_scanned_at: new Date(NOW.getTime() - 86_400_000),
    added_by: "user-1",
    created_at: new Date("2026-09-20T00:00:00.000Z"),
    updated_at: new Date("2026-09-21T00:00:00.000Z"),
    exception_reason: null,
    exception_by: null,
    exception_at: null,
    exception_expires_at: null,
    tag_current_digest: null,
    tag_checked_at: null,
    registry_connection_id: null,
    registry_connection_name: null,
    registry_connection_group_id: null,
    registry_connection_deleted: null,
    db_built_at: "2026-09-20T06:00:00Z",
    in_use_by: 2,
    ...overrides,
  };
}

/** A pooled client whose `query` answers by SQL prefix. */
function mockClient(answer: (sql: string, params: unknown[]) => { rows: unknown[] }) {
  const client = {
    query: vi.fn(async (sql: string, params: unknown[] = []) => ({
      rowCount: 0,
      ...answer(sql, params),
    })),
    release: vi.fn(),
  };
  mockGetClient.mockResolvedValue(client as never);
  return client;
}

beforeEach(() => {
  mockQuery.mockReset();
  mockGetClient.mockReset();
});

describe("listImages / getImage", () => {
  it("maps a row: bigint size, computed staleness, drift, exception, in-use count", async () => {
    mockQuery.mockResolvedValue({
      rows: [
        imageRow({
          tag_current_digest: "sha256:" + "b".repeat(64),
          exception_reason: "vendor fix pending",
          exception_by: "admin-1",
          exception_at: new Date("2026-09-22T00:00:00.000Z"),
          exception_expires_at: new Date("2026-10-22T00:00:00.000Z"),
        }),
      ],
    } as never);
    const [image] = await listImages({}, VIEW);
    expect(image).toMatchObject({
      id: IMG,
      size_bytes: 812345678,
      stale: false,
      drifted: true,
      in_use_by: 2,
      db_built_at: "2026-09-20T06:00:00Z",
      registry_connection: null,
      exception: {
        reason: "vendor fix pending",
        by: "admin-1",
        at: "2026-09-22T00:00:00.000Z",
        expires_at: "2026-10-22T00:00:00.000Z",
      },
      created_at: "2026-09-20T00:00:00.000Z",
    });
  });

  it("keeps soft-deleted connections and flags them, so the UI never shows a public image", async () => {
    mockQuery.mockResolvedValue({
      rows: [
        imageRow({
          registry_connection_id: "c-1",
          registry_connection_name: "ghcr robot",
          registry_connection_group_id: "earth-observation",
          registry_connection_deleted: true,
        }),
      ],
    } as never);
    const [image] = await listImages({}, VIEW);
    expect(image.registry_connection).toEqual({
      id: "c-1",
      name: "ghcr robot",
      group_id: "earth-observation",
      deleted: true,
    });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/LEFT JOIN stac_higher\.connections c ON c\.id = i\.registry_connection_id\s/);
    expect(sql).not.toMatch(/c\.deleted_at IS NULL/);
  });

  it("counts only CURRENT revisions of LIVE processes as in use", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await listImages({}, VIEW);
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/ON p\.current_revision = r\.id AND p\.deleted_at IS NULL/);
    expect(sql).toMatch(/WHERE r\.runtime->'image'->>'id' = i\.id::text/);
  });

  it("applies filters as parameters and escapes LIKE wildcards in the search", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await listImages({ status: "flagged", q: "50%_off\\x", inUse: true }, VIEW);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status = $1");
    expect(sql).toContain("ILIKE $2 ESCAPE '\\'");
    expect(sql).toContain("in_use_by > 0");
    expect(sql).toContain("LIMIT $3");
    expect(params).toEqual(["flagged", "%50\\%\\_off\\\\x%", IMAGE_LIST_LIMIT]);
  });

  it("filters to images nobody uses", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await listImages({ inUse: false }, VIEW);
    expect(mockQuery.mock.calls[0][0]).toContain("in_use_by = 0");
  });

  it("reports staleness as unknown when the policy window is unknown", async () => {
    mockQuery.mockResolvedValue({ rows: [imageRow()] } as never);
    const image = await getImage(IMG, { now: NOW, scanWindowDays: null });
    expect(image?.stale).toBeNull();
    expect(mockQuery.mock.calls[0][1]).toEqual([IMG]);
  });

  it("answers null for a missing image", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    expect(await getImage(IMG, VIEW)).toBeNull();
  });
});

describe("scans and users", () => {
  it("finds a scan by id even after the drain de-duplicated its provisional image", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await getImageScan(IMG, SCAN);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/s\.id = \$2/);
    expect(sql).toMatch(
      /s\.image_id = \$1\s+OR NOT EXISTS \(SELECT 1 FROM stac_higher\.container_images WHERE id = \$1\)/,
    );
    expect(params).toEqual([IMG, SCAN]);
  });

  it("lists the live processes whose current revision snapshots the image", async () => {
    mockQuery.mockResolvedValue({
      rows: [{ process_id: "p-1", name: "geocolor", group_id: "earth-observation" }],
    } as never);
    expect(await listImageUsers(IMG)).toEqual([
      { process_id: "p-1", name: "geocolor", group_id: "earth-observation" },
    ]);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/JOIN stac_higher\.process_revisions r ON r\.id = p\.current_revision/);
    expect(sql).toMatch(/p\.deleted_at IS NULL/);
    expect(params).toEqual([IMG]);
  });
});

describe("adding an image (ADR 0004: rows, never a call)", () => {
  it("inserts a pending image and its admission scan in one transaction", async () => {
    const client = mockClient((sql) => {
      if (sql.includes("INSERT INTO stac_higher.container_images")) return { rows: [{ id: IMG }] };
      if (sql.includes("INSERT INTO stac_higher.image_scans")) return { rows: [{ id: SCAN }] };
      return { rows: [] };
    });
    const added = await insertImageWithAdmission({
      reference: "docker.io/library/python",
      tag: "3.12-slim",
      registryConnectionId: null,
      addedBy: "user-1",
    });
    expect(added).toEqual({ image_id: IMG, scan_id: SCAN, deduplicated: false });
    const sqls = client.query.mock.calls.map(([sql]) => String(sql).trim().split(/\s+/)[0]);
    expect(sqls).toEqual(["BEGIN", "INSERT", "INSERT", "COMMIT"]);
    expect(client.query.mock.calls[1][1]).toEqual([
      "docker.io/library/python",
      "3.12-slim",
      null,
      "user-1",
    ]);
    expect(String(client.query.mock.calls[2][0])).toContain("'admission'");
    expect(client.release).toHaveBeenCalled();
  });

  it("rolls back and rethrows when the scan insert fails", async () => {
    const client = mockClient((sql) => {
      if (sql.includes("INSERT INTO stac_higher.container_images")) return { rows: [{ id: IMG }] };
      if (sql.includes("INSERT INTO stac_higher.image_scans")) throw new Error("boom");
      return { rows: [] };
    });
    await expect(
      insertImageWithAdmission({
        reference: "ghcr.io/org/img",
        tag: "1",
        registryConnectionId: null,
        addedBy: "user-1",
      }),
    ).rejects.toThrow("boom");
    expect(client.query.mock.calls.map(([sql]) => sql)).toContain("ROLLBACK");
    expect(client.release).toHaveBeenCalled();
  });

  it("finds an open admission for the same reference, tag and credential", async () => {
    mockQuery.mockResolvedValue({ rows: [{ image_id: IMG, scan_id: SCAN }] } as never);
    expect(
      await findOpenAdmission({ reference: "ghcr.io/org/img", tag: "1", registryConnectionId: null }),
    ).toEqual({ image_id: IMG, scan_id: SCAN, deduplicated: true });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("IS NOT DISTINCT FROM $3::uuid");
    expect(sql).toContain("s.status IN ('pending','running')");
    expect(params).toEqual(["ghcr.io/org/img", "1", null]);
  });
});

describe("requestImageScan", () => {
  function imageState(row: { status: string; sbom_ref: string | null } | null, openScan: string | null) {
    return mockClient((sql) => {
      if (sql.startsWith("SELECT status, sbom_ref")) return { rows: row ? [row] : [] };
      if (sql.startsWith("SELECT id FROM stac_higher.image_scans")) {
        return { rows: openScan ? [{ id: openScan }] : [] };
      }
      if (sql.startsWith("INSERT INTO stac_higher.image_scans")) return { rows: [{ id: SCAN }] };
      return { rows: [] };
    });
  }

  it("is not_found for a missing image", async () => {
    imageState(null, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({ outcome: "not_found" });
  });

  it("refuses a revoked image", async () => {
    imageState({ status: "revoked", sbom_ref: "scans/x/sbom.syft.json" }, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({ outcome: "revoked" });
  });

  it("reports the open scan instead of queueing a second one", async () => {
    imageState({ status: "approved", sbom_ref: "scans/x/sbom.syft.json" }, "open-1");
    expect(await requestImageScan(IMG, "user-1")).toEqual({
      outcome: "already_pending",
      scan_id: "open-1",
    });
  });

  it("queues a rescan when an SBOM is stored", async () => {
    const client = imageState({ status: "flagged", sbom_ref: "scans/x/sbom.syft.json" }, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({
      outcome: "requested",
      scan_id: SCAN,
      kind: "rescan",
    });
    const insert = client.query.mock.calls.find(([sql]) =>
      String(sql).startsWith("INSERT INTO stac_higher.image_scans"),
    );
    expect(insert?.[1]).toEqual([IMG, "rescan", "user-1"]);
    expect(client.query.mock.calls.some(([sql]) => String(sql).includes("SET status = 'pending'"))).toBe(false);
  });

  it("re-requests an ADMISSION (and resets scan_failed to pending) when no scan ever succeeded", async () => {
    const client = imageState({ status: "scan_failed", sbom_ref: null }, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({
      outcome: "requested",
      scan_id: SCAN,
      kind: "admission",
    });
    expect(
      client.query.mock.calls.some(([sql]) =>
        String(sql).includes("SET status = 'pending', updated_at = now()"),
      ),
    ).toBe(true);
  });
});

describe("human verdicts", () => {
  const EXPIRES = new Date("2026-10-27T00:00:00.000Z");

  it("grants an exception only from rejected or flagged", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ id: IMG }] } as never);
    expect(
      await grantImageException({ imageId: IMG, reason: "vendor fix pending", by: "admin-1", expiresAt: EXPIRES }),
    ).toEqual({ outcome: "granted" });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status = 'approved'");
    expect(sql).toContain("status IN ('rejected','flagged')");
    expect(params).toEqual([IMG, "vendor fix pending", "admin-1", EXPIRES.toISOString()]);
  });

  it("says why an exception did not apply", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [{ status: "approved" }] } as never);
    expect(
      await grantImageException({ imageId: IMG, reason: "vendor fix pending", by: "admin-1", expiresAt: EXPIRES }),
    ).toEqual({ outcome: "wrong_status", status: "approved" });

    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [] } as never);
    expect(
      await grantImageException({ imageId: IMG, reason: "vendor fix pending", by: "admin-1", expiresAt: EXPIRES }),
    ).toEqual({ outcome: "not_found" });
  });

  it("revokes from any status except revoked, clearing a live exception", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ id: IMG }] } as never);
    expect(await revokeImage(IMG)).toEqual({ outcome: "revoked" });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toContain("status = 'revoked'");
    expect(sql).toContain("exception_expires_at = NULL");
    expect(sql).toContain("status <> 'revoked'");

    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [{ status: "revoked" }] } as never);
    expect(await revokeImage(IMG)).toEqual({ outcome: "already_revoked" });

    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [] } as never);
    expect(await revokeImage(IMG)).toEqual({ outcome: "not_found" });
  });
});

describe("isDigestLaunchable (the K-6 admission probe)", () => {
  it("asks for an approved-or-flagged, fresh row with that digest", async () => {
    mockQuery.mockResolvedValue({ rows: [{ ok: true }] } as never);
    expect(await isDigestLaunchable(DIGEST, 30)).toBe(true);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status IN ('approved','flagged')");
    expect(sql).toContain("last_scanned_at >= now() - make_interval(days => $2::int)");
    expect(params).toEqual([DIGEST, 30]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/images-registry-storage.test.ts`
Expected: FAIL (`listImages` and the other functions are not exported).

- [ ] **Step 3: Write the implementation**

In `app/src/lib/images/storage.ts`, replace the three import lines at the top:

```ts
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import type { ImageStatus } from "./status";
```

with:

```ts
import { getClient, query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { isImageStale } from "./stale";
import type { ImageScanKind, ImageStatus } from "./status";
```

Then append, after `getImageForGate`:

```ts
// ---------------------------------------------------------------------------
// C-3: the registry surface (container-images spec §9.1). The app INSERTs
// `pending` rows with their `image_scans` admission requests (ADR 0004), and
// applies the two human verdicts (exception, revoke). Everything a scan
// decides (digest, verdict, status transitions, last_scan_*) is the
// pipeline's to write (C-2). The app never sets `last_scan_id`.
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;

function iso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : String(value);
}

export interface ApiImageRegistryConnection {
  id: string;
  /** The connection's label. Never its config secrets or credentials. */
  name: string;
  group_id: string;
  /** Soft-deleted: the image can no longer be deployed (`image_group_mismatch`). */
  deleted: boolean;
}

export interface ApiImageException {
  reason: string;
  by: string;
  at: string;
  expires_at: string;
}

export interface ApiImage {
  id: string;
  reference: string;
  tag_at_add: string;
  digest: string | null;
  status: ImageStatus;
  /** The latest policy evaluation as the pipeline stored it (`verdict.ts` reads it). */
  verdict: Json | null;
  size_bytes: number | null;
  config: Json | null;
  last_scan_id: string | null;
  last_scanned_at: string | null;
  /** Computed, never stored (spec §4.3); null when the policy is unreadable. */
  stale: boolean | null;
  /** The Grype DB build date of the latest completed scan (`result.scanner`). */
  db_built_at: string | null;
  added_by: string;
  created_at: string;
  updated_at: string;
  exception: ApiImageException | null;
  tag_current_digest: string | null;
  tag_checked_at: string | null;
  /** The tag now points at another digest than the one that runs (§8.2, informational). */
  drifted: boolean;
  registry_connection: ApiImageRegistryConnection | null;
  /** Live processes whose CURRENT revision snapshots this image. */
  in_use_by: number;
}

interface ImageRow {
  id: string;
  reference: string;
  tag_at_add: string;
  digest: string | null;
  status: ImageStatus;
  verdict: Json | null;
  size_bytes: string | number | null;
  config: Json | null;
  last_scan_id: string | null;
  last_scanned_at: Date | string | null;
  added_by: string;
  created_at: Date | string;
  updated_at: Date | string;
  exception_reason: string | null;
  exception_by: string | null;
  exception_at: Date | string | null;
  exception_expires_at: Date | string | null;
  tag_current_digest: string | null;
  tag_checked_at: Date | string | null;
  registry_connection_id: string | null;
  registry_connection_name: string | null;
  registry_connection_group_id: string | null;
  registry_connection_deleted: boolean | null;
  db_built_at: string | null;
  in_use_by: number | string;
}

/**
 * One row per image, everything the dashboard shows. The connection join
 * deliberately INCLUDES soft-deleted connections (the gate's join excludes
 * them): the dashboard must be able to say "credential deleted" instead of
 * presenting the image as public. The FK is ON DELETE RESTRICT and
 * connections are only soft-deleted, so the joined row always exists.
 * The in-use count rides `process_revisions_image_id_idx` (migration 030).
 */
const IMAGE_ROWS_SQL = `
  SELECT i.id, i.reference, i.tag_at_add, i.digest, i.status, i.verdict,
         i.size_bytes, i.config, i.last_scan_id, i.last_scanned_at,
         i.added_by, i.created_at, i.updated_at,
         i.exception_reason, i.exception_by, i.exception_at, i.exception_expires_at,
         i.tag_current_digest, i.tag_checked_at,
         i.registry_connection_id,
         c.name AS registry_connection_name,
         c.group_id AS registry_connection_group_id,
         (c.deleted_at IS NOT NULL) AS registry_connection_deleted,
         s.result->'scanner'->>'db_built_at' AS db_built_at,
         (SELECT count(*)
            FROM stac_higher.process_revisions r
            JOIN stac_higher.processes p
              ON p.current_revision = r.id AND p.deleted_at IS NULL
           WHERE r.runtime->'image'->>'id' = i.id::text)::int AS in_use_by
    FROM stac_higher.container_images i
    LEFT JOIN stac_higher.connections c ON c.id = i.registry_connection_id
    LEFT JOIN stac_higher.image_scans s ON s.id = i.last_scan_id
`;

/** What the reader needs to derive computed fields (staleness). */
export interface ImageView {
  now: Date;
  scanWindowDays: number | null;
}

function toApiImage(row: ImageRow, view: ImageView): ApiImage {
  return {
    id: row.id,
    reference: row.reference,
    tag_at_add: row.tag_at_add,
    digest: row.digest,
    status: row.status,
    verdict: row.verdict,
    size_bytes: row.size_bytes === null ? null : Number(row.size_bytes),
    config: row.config,
    last_scan_id: row.last_scan_id,
    last_scanned_at: iso(row.last_scanned_at),
    stale: isImageStale(row.status, row.last_scanned_at, view.scanWindowDays, view.now),
    db_built_at: row.db_built_at,
    added_by: row.added_by,
    created_at: iso(row.created_at) as string,
    updated_at: iso(row.updated_at) as string,
    exception:
      row.exception_at === null
        ? null
        : {
            reason: row.exception_reason ?? "",
            by: row.exception_by ?? "",
            at: iso(row.exception_at) as string,
            expires_at: iso(row.exception_expires_at) as string,
          },
    tag_current_digest: row.tag_current_digest,
    tag_checked_at: iso(row.tag_checked_at),
    drifted:
      row.tag_current_digest !== null &&
      row.digest !== null &&
      row.tag_current_digest !== row.digest,
    registry_connection:
      row.registry_connection_id === null
        ? null
        : {
            id: row.registry_connection_id,
            name: row.registry_connection_name ?? "",
            group_id: row.registry_connection_group_id ?? "",
            deleted: row.registry_connection_deleted === true,
          },
    in_use_by: Number(row.in_use_by),
  };
}

export interface ImageListFilters {
  status?: ImageStatus;
  q?: string;
  inUse?: boolean;
}

/** The registry is small (one row per scanned digest); a hard cap keeps a
 * runaway list bounded until the dashboard needs paging. */
export const IMAGE_LIST_LIMIT = 500;

function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, (c) => `\\${c}`);
}

export async function listImages(filters: ImageListFilters, view: ImageView): Promise<ApiImage[]> {
  await runMigrations();
  const where: string[] = [];
  const params: unknown[] = [];
  if (filters.status) {
    params.push(filters.status);
    where.push(`status = $${params.length}`);
  }
  if (filters.q) {
    params.push(`%${escapeLike(filters.q)}%`);
    where.push(`(reference || ':' || tag_at_add) ILIKE $${params.length} ESCAPE '\\'`);
  }
  if (filters.inUse === true) where.push("in_use_by > 0");
  if (filters.inUse === false) where.push("in_use_by = 0");
  params.push(IMAGE_LIST_LIMIT);
  const result = await query<ImageRow>(
    `WITH rows AS (${IMAGE_ROWS_SQL})
     SELECT * FROM rows
     ${where.length > 0 ? `WHERE ${where.join(" AND ")}` : ""}
     ORDER BY created_at DESC
     LIMIT $${params.length}`,
    params,
  );
  return result.rows.map((row) => toApiImage(row, view));
}

export async function getImage(id: string, view: ImageView): Promise<ApiImage | null> {
  await runMigrations();
  const result = await query<ImageRow>(
    `WITH rows AS (${IMAGE_ROWS_SQL}) SELECT * FROM rows WHERE id = $1`,
    [id],
  );
  return result.rows[0] ? toApiImage(result.rows[0], view) : null;
}

// --- image_scans -----------------------------------------------------------

export interface ApiImageScan {
  id: string;
  image_id: string;
  kind: ImageScanKind;
  status: "pending" | "running" | "done" | "failed";
  requested_by: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** The §6.4 document with `verdict` and `diff` beside it (C-2 writes it), or `{error}`. */
  result: Json | null;
  findings_ref: string | null;
  log_ref: string | null;
}

interface ScanRow {
  id: string;
  image_id: string;
  kind: ImageScanKind;
  status: ApiImageScan["status"];
  requested_by: string;
  requested_at: Date | string;
  started_at: Date | string | null;
  finished_at: Date | string | null;
  result: Json | null;
  findings_ref: string | null;
  log_ref: string | null;
}

const SCAN_COLUMNS = `
  s.id, s.image_id, s.kind, s.status, s.requested_by, s.requested_at,
  s.started_at, s.finished_at, s.result, s.findings_ref, s.log_ref
`;

function toApiScan(row: ScanRow): ApiImageScan {
  return {
    id: row.id,
    image_id: row.image_id,
    kind: row.kind,
    status: row.status,
    requested_by: row.requested_by,
    requested_at: iso(row.requested_at) as string,
    started_at: iso(row.started_at),
    finished_at: iso(row.finished_at),
    result: row.result,
    findings_ref: row.findings_ref,
    log_ref: row.log_ref,
  };
}

export async function listImageScans(imageId: string, limit = 10): Promise<ApiImageScan[]> {
  await runMigrations();
  const result = await query<ScanRow>(
    `SELECT ${SCAN_COLUMNS} FROM stac_higher.image_scans s
      WHERE s.image_id = $1
      ORDER BY s.requested_at DESC
      LIMIT $2`,
    [imageId, Math.min(Math.max(limit, 1), 50)],
  );
  return result.rows.map(toApiScan);
}

/**
 * A scan by the ids the 202 handed out. The drain may DE-DUPLICATE (spec
 * §9.1): it re-points the scan at the existing image with the same digest
 * and deletes the provisional row. So the scan is also reachable through its
 * old image id once that image no longer exists. It stays unreachable
 * through any OTHER live image's id.
 */
export async function getImageScan(imageId: string, scanId: string): Promise<ApiImageScan | null> {
  await runMigrations();
  const result = await query<ScanRow>(
    `SELECT ${SCAN_COLUMNS} FROM stac_higher.image_scans s
      WHERE s.id = $2
        AND (s.image_id = $1
             OR NOT EXISTS (SELECT 1 FROM stac_higher.container_images WHERE id = $1))`,
    [imageId, scanId],
  );
  return result.rows[0] ? toApiScan(result.rows[0]) : null;
}

// --- who uses an image -----------------------------------------------------

export interface ApiImageUser {
  process_id: string;
  name: string;
  group_id: string;
}

export async function listImageUsers(imageId: string): Promise<ApiImageUser[]> {
  await runMigrations();
  const result = await query<ApiImageUser>(
    `SELECT p.id AS process_id, p.name, p.group_id
       FROM stac_higher.processes p
       JOIN stac_higher.process_revisions r ON r.id = p.current_revision
      WHERE p.deleted_at IS NULL
        AND r.runtime->'image'->>'id' = $1
      ORDER BY p.name`,
    [imageId],
  );
  return result.rows;
}

// --- add (admission) -------------------------------------------------------

export interface AddImageInput {
  reference: string;
  tag: string;
  registryConnectionId: string | null;
  addedBy: string;
}

export interface AddedImage {
  image_id: string;
  scan_id: string;
  /** True when an open admission for the same reference/tag/credential was reused. */
  deduplicated: boolean;
}

/** An admission still in flight for the same reference, tag and credential.
 * A second "Add" (a double-click, a second operator) reuses it instead of
 * queueing a second provisional row the drain would only merge again. */
export async function findOpenAdmission(
  input: Omit<AddImageInput, "addedBy">,
): Promise<AddedImage | null> {
  await runMigrations();
  const result = await query<{ image_id: string; scan_id: string }>(
    `SELECT i.id AS image_id, s.id AS scan_id
       FROM stac_higher.container_images i
       JOIN stac_higher.image_scans s
         ON s.image_id = i.id AND s.kind = 'admission' AND s.status IN ('pending','running')
      WHERE i.reference = $1
        AND i.tag_at_add = $2
        AND i.registry_connection_id IS NOT DISTINCT FROM $3::uuid
        AND i.status IN ('pending','scanning')
      ORDER BY s.requested_at DESC
      LIMIT 1`,
    [input.reference, input.tag, input.registryConnectionId],
  );
  const row = result.rows[0];
  return row ? { ...row, deduplicated: true } : null;
}

/** A `pending` row (digest NULL: the app never touches a registry) and its
 * `admission` scan request, in ONE transaction, so there is never an image
 * nobody will scan. */
export async function insertImageWithAdmission(input: AddImageInput): Promise<AddedImage> {
  await runMigrations();
  const client = await getClient();
  try {
    await client.query("BEGIN");
    const image = await client.query<{ id: string }>(
      `INSERT INTO stac_higher.container_images
         (reference, tag_at_add, registry_connection_id, added_by)
       VALUES ($1, $2, $3, $4)
       RETURNING id`,
      [input.reference, input.tag, input.registryConnectionId, input.addedBy],
    );
    const imageId = image.rows[0].id;
    const scan = await client.query<{ id: string }>(
      `INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)
       VALUES ($1, 'admission', $2)
       RETURNING id`,
      [imageId, input.addedBy],
    );
    await client.query("COMMIT");
    return { image_id: imageId, scan_id: scan.rows[0].id, deduplicated: false };
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

// --- rescan ----------------------------------------------------------------

export type ScanRequestOutcome =
  | { outcome: "requested"; scan_id: string; kind: ImageScanKind }
  | { outcome: "not_found" }
  | { outcome: "revoked" }
  | { outcome: "already_pending"; scan_id: string };

/**
 * "Rescan now" (spec §9.1). A rescan matches the STORED SBOM (spec §6.3), so
 * an image that never scanned successfully (`sbom_ref` NULL: `pending` with
 * a lost request, or `scan_failed`) gets a new ADMISSION instead, which is
 * spec §4.3's "retry by re-requesting". `scan_failed` goes back to
 * `pending` so the dashboard stops saying "failed" while the retry waits.
 * One open scan per image; revoked is terminal.
 */
export async function requestImageScan(
  imageId: string,
  requestedBy: string,
): Promise<ScanRequestOutcome> {
  await runMigrations();
  const client = await getClient();
  try {
    await client.query("BEGIN");
    const image = await client.query<{ status: ImageStatus; sbom_ref: string | null }>(
      `SELECT status, sbom_ref FROM stac_higher.container_images WHERE id = $1 FOR UPDATE`,
      [imageId],
    );
    const row = image.rows[0];
    if (!row) {
      await client.query("ROLLBACK");
      return { outcome: "not_found" };
    }
    if (row.status === "revoked") {
      await client.query("ROLLBACK");
      return { outcome: "revoked" };
    }
    const open = await client.query<{ id: string }>(
      `SELECT id FROM stac_higher.image_scans
        WHERE image_id = $1 AND status IN ('pending','running')
        LIMIT 1`,
      [imageId],
    );
    if (open.rows[0]) {
      await client.query("ROLLBACK");
      return { outcome: "already_pending", scan_id: open.rows[0].id };
    }
    const kind: ImageScanKind = row.sbom_ref === null ? "admission" : "rescan";
    if (kind === "admission") {
      await client.query(
        `UPDATE stac_higher.container_images
            SET status = 'pending', updated_at = now()
          WHERE id = $1 AND status = 'scan_failed'`,
        [imageId],
      );
    }
    const scan = await client.query<{ id: string }>(
      `INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)
       VALUES ($1, $2, $3)
       RETURNING id`,
      [imageId, kind, requestedBy],
    );
    await client.query("COMMIT");
    return { outcome: "requested", scan_id: scan.rows[0].id, kind };
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

// --- exception / revoke (admin verdicts, spec §4.4) ------------------------

export type ExceptionOutcome =
  | { outcome: "granted" }
  | { outcome: "not_found" }
  | { outcome: "wrong_status"; status: ImageStatus };

/** `rejected`/`flagged` -> `approved` with the one live exception (spec §4.4).
 * Conditional UPDATE, so a concurrent drain transition cannot be overwritten
 * by a stale read. */
export async function grantImageException(input: {
  imageId: string;
  reason: string;
  by: string;
  expiresAt: Date;
}): Promise<ExceptionOutcome> {
  await runMigrations();
  const updated = await query<{ id: string }>(
    `UPDATE stac_higher.container_images
        SET status = 'approved',
            exception_reason = $2,
            exception_by = $3,
            exception_at = now(),
            exception_expires_at = $4,
            updated_at = now()
      WHERE id = $1 AND status IN ('rejected','flagged')
      RETURNING id`,
    [input.imageId, input.reason, input.by, input.expiresAt.toISOString()],
  );
  if (updated.rows[0]) return { outcome: "granted" };
  const current = await query<{ status: ImageStatus }>(
    `SELECT status FROM stac_higher.container_images WHERE id = $1`,
    [input.imageId],
  );
  return current.rows[0]
    ? { outcome: "wrong_status", status: current.rows[0].status }
    : { outcome: "not_found" };
}

export type RevokeOutcome =
  | { outcome: "revoked" }
  | { outcome: "not_found" }
  | { outcome: "already_revoked" };

/** Any status -> `revoked` (terminal, spec §4.3). Revoking also ends a live
 * exception (spec §4.4: "revoking an exception early = image.revoke"); the
 * grant stays in the audit log. */
export async function revokeImage(imageId: string): Promise<RevokeOutcome> {
  await runMigrations();
  const updated = await query<{ id: string }>(
    `UPDATE stac_higher.container_images
        SET status = 'revoked',
            exception_reason = NULL,
            exception_by = NULL,
            exception_at = NULL,
            exception_expires_at = NULL,
            updated_at = now()
      WHERE id = $1 AND status <> 'revoked'
      RETURNING id`,
    [imageId],
  );
  if (updated.rows[0]) return { outcome: "revoked" };
  const current = await query<{ status: ImageStatus }>(
    `SELECT status FROM stac_higher.container_images WHERE id = $1`,
    [imageId],
  );
  return current.rows[0] ? { outcome: "already_revoked" } : { outcome: "not_found" };
}

// --- the cluster admission probe (spec §12, wired in K-6) -------------------

/** Could a run on this digest LAUNCH (spec §4.3: approved or flagged, and
 * scanned inside the window)? The same boundary as the gate: exactly the
 * window ago is fresh. */
export async function isDigestLaunchable(digest: string, scanWindowDays: number): Promise<boolean> {
  await runMigrations();
  const result = await query<{ ok: boolean }>(
    `SELECT EXISTS (
       SELECT 1 FROM stac_higher.container_images
        WHERE digest = $1
          AND status IN ('approved','flagged')
          AND last_scanned_at >= now() - make_interval(days => $2::int)
     ) AS ok`,
    [digest, scanWindowDays],
  );
  return result.rows[0]?.ok === true;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/images-registry-storage.test.ts src/__tests__/images-storage.test.ts src/__tests__/api-processes.test.ts`
Expected: PASS. C-1's storage test and the revisions-route test still pass: they mock or use only `getImageForGate`.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green.

```bash
git add app/src/lib/images/storage.ts app/src/__tests__/images-registry-storage.test.ts
git commit -m "feat(images): registry reads, admission insert, rescan request, exception and revoke storage (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Gated routes, audit actions, and route-supplied audit detail

**Files:**
- Modify: `app/src/lib/authz/permissions.ts` (the `GatedAction` union, `SUB_ACTION_ROUTES`, `matchGatedRoute`)
- Modify: `app/src/lib/authz/guard.ts` (`GuardContext.locals`, the allowed-row detail)
- Modify: `app/src/env.d.ts` (`App.Locals.auditDetail`)
- Test: `app/src/__tests__/authz-images-gate.test.ts` (new), `app/src/__tests__/authz-guard.test.ts` (one added test)

**Interfaces:**
- Produces:
  - `GatedAction` gains `"rescan" | "exception" | "revoke"`.
  - `matchGatedRoute("POST", "/api/images")` → `{ action: "create", resourceType: "container_image", resourceId: null }`.
  - `matchGatedRoute("POST", "/api/images/<id>/rescan" | "/exception" | "/revoke")` → `{ action: "rescan" | "exception" | "revoke", resourceType: "container_image", resourceId: <id> }`.
  - `locals.auditDetail?: Record<string, unknown>`. A gated route MAY set it. The guard merges it into the ALLOWED audit row's `detail`, beneath `outcome`/`status` (which it can never overwrite). `writeAudit` still redacts it.

The spec names the audit actions `image.add`, `image.rescan`, `image.exception` and `image.revoke`. An `audit_log` row is `(action, resource_type)`, so they are recorded as `create`/`rescan`/`exception`/`revoke` on `container_image` (see Decisions).

- [ ] **Step 1: Write the failing tests**

`app/src/__tests__/authz-images-gate.test.ts`:

```ts
// @vitest-environment node
/**
 * C-3: image mutations are gated (operator+ at the guard; exception and
 * revoke are admin, enforced in-route) and audited as their own actions on
 * `container_image`. Reads, the policy route and the internal probe stay
 * ungated.
 */
import { describe, expect, it } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";

describe("matchGatedRoute — /api/images (C-3)", () => {
  it("audits adding an image as a create of container_image", () => {
    expect(matchGatedRoute("POST", "/api/images")).toEqual({
      action: "create",
      resourceType: "container_image",
      resourceId: null,
    });
    expect(matchGatedRoute("POST", "/api/images/")).toEqual({
      action: "create",
      resourceType: "container_image",
      resourceId: null,
    });
  });

  it.each(["rescan", "exception", "revoke"] as const)(
    "audits %s as its own action against the image id",
    (verb) => {
      expect(matchGatedRoute("POST", `/api/images/${IMG}/${verb}`)).toEqual({
        action: verb,
        resourceType: "container_image",
        resourceId: IMG,
      });
    },
  );

  it("leaves every read ungated", () => {
    expect(matchGatedRoute("GET", "/api/images")).toBeNull();
    expect(matchGatedRoute("GET", `/api/images/${IMG}`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/images/${IMG}/scans/${SCAN}`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/images/${IMG}/scans/${SCAN}/findings`)).toBeNull();
    expect(matchGatedRoute("GET", "/api/processes/image-policy")).toBeNull();
    expect(matchGatedRoute("GET", "/api/internal/images/approved")).toBeNull();
  });

  it("does not treat an unknown image sub-path as a verb", () => {
    expect(matchGatedRoute("POST", `/api/images/${IMG}/delete`)).toBeNull();
  });
});
```

Append to `app/src/__tests__/authz-guard.test.ts` (a new `describe` at the end of the file; it uses the file's existing `makeContext`, `authed` and `mockWriteAudit`):

```ts
describe("route-supplied audit detail (C-3)", () => {
  it("merges locals.auditDetail into the allowed row, never over outcome or status", async () => {
    const ctx = makeContext("POST", "/api/images", authed(["operator"]));
    const next = vi.fn(async () => {
      (ctx.locals as { auditDetail?: Record<string, unknown> }).auditDetail = {
        reference: "docker.io/library/python",
        outcome: "spoofed",
        status: 999,
      };
      return new Response(JSON.stringify({ id: "img-1" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    });

    await applyApiGuard(ctx, next);

    expect(mockWriteAudit).toHaveBeenCalledTimes(1);
    const entry = mockWriteAudit.mock.calls[0][0];
    expect(entry).toMatchObject({
      action: "create",
      resourceType: "container_image",
      resourceId: "img-1",
    });
    expect(entry.detail).toMatchObject({
      method: "POST",
      path: "/api/images",
      reference: "docker.io/library/python",
      outcome: "allowed",
      status: 202,
    });
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app && npx vitest run src/__tests__/authz-images-gate.test.ts src/__tests__/authz-guard.test.ts`
Expected: FAIL. The image routes are not matched, and the detail lacks `reference`.

- [ ] **Step 3: Write the implementation**

`app/src/lib/authz/permissions.ts`. Replace the `GatedAction` union and its doc comment:

```ts
/** `test` = test-connection, `backfill` = deliver-association backfill,
 * `redeliver` = dead-letter recovery, `ack`/`resolve` = alert lifecycle
 * transitions (ROADMAP §5 audit action enum; M2-B adds `resolve`). C-3 adds
 * the image verbs (container-images spec §9.1: `image.rescan`,
 * `image.exception`, `image.revoke`; `image.add` is a `create`). */
export type GatedAction =
  | "create"
  | "update"
  | "delete"
  | "test"
  | "backfill"
  | "redeliver"
  | "ack"
  | "resolve"
  | "deploy"
  | "rerun"
  | "rescan"
  | "exception"
  | "revoke";
```

Append three entries at the END of the `SUB_ACTION_ROUTES` array (after the `host-key/reset` entry):

```ts
  // C-3 (container-images spec §9.1): the image verbs. Rescan is operator+
  // (the guard's gate); exception and revoke are ADMIN, re-checked in-route
  // because the guard gates by operator. Audited against the image id.
  {
    pattern: /^\/api\/images\/([^/]+)\/rescan$/,
    action: "rescan",
    resourceType: "container_image",
  },
  {
    pattern: /^\/api\/images\/([^/]+)\/exception$/,
    action: "exception",
    resourceType: "container_image",
  },
  {
    pattern: /^\/api\/images\/([^/]+)\/revoke$/,
    action: "revoke",
    resourceType: "container_image",
  },
```

In `matchGatedRoute`, directly after the `POST /api/connections` branch, add:

```ts
  // C-3: adding an image to the platform-wide registry (container-images
  // spec §9.1, `image.add`). The 202 body carries `id` (the new image), so
  // the guard's created-id extraction audits it.
  if (m === "POST" && path === "/api/images") {
    return { action: "create", resourceType: "container_image", resourceId: null };
  }
```

`app/src/lib/authz/guard.ts`. Widen the structural context:

```ts
/** Structural subset of Astro's APIContext that the guard needs. */
export interface GuardContext {
  request: Request;
  url: URL;
  /** `auditDetail` is set by a gated ROUTE that has something worth recording
   * beyond the request line (C-3: an image exception's reason). */
  locals: { auth: AuthContext; auditDetail?: Record<string, unknown> };
}
```

Replace the final `writeAudit` call (the allowed row) with:

```ts
  await writeAudit({
    actor: identity.sub,
    actorGroups: identity.groups,
    action: gate.action,
    resourceType: gate.resourceType,
    resourceId,
    // Route-supplied detail sits BENEATH the guard's own keys, so a route
    // can never rewrite the outcome or status of its own audit row.
    detail: {
      ...requestDetail,
      ...(context.locals.auditDetail ?? {}),
      outcome: "allowed",
      status: response.status,
    },
  });
```

`app/src/env.d.ts`. Add the field to `App.Locals`:

```ts
declare namespace App {
  interface Locals {
    /** Canonical auth context for the request — set by `src/middleware.ts`.
     * Future RBAC/permission middleware consumes this and nothing else. */
    auth: import("./lib/auth/types").AuthContext;
    /** Optional detail a gated route adds to its audit row (C-3). Merged by
     * the guard beneath `outcome`/`status`; redacted by `writeAudit`. Never
     * put a secret here. */
    auditDetail?: Record<string, unknown>;
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/authz-images-gate.test.ts src/__tests__/authz-guard.test.ts src/__tests__/authz-processes-gate.test.ts src/__tests__/authz-connections-gate.test.ts`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green.

```bash
git add app/src/lib/authz/permissions.ts app/src/lib/authz/guard.ts app/src/env.d.ts app/src/__tests__/authz-images-gate.test.ts app/src/__tests__/authz-guard.test.ts
git commit -m "feat(authz): gate and audit the image routes; routes may add audit detail (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: Read routes and `POST /api/images`

**Files:**
- Create: `app/src/lib/images/access.ts`
- Create: `app/src/pages/api/images/index.ts`, `app/src/pages/api/images/[id].ts`, `app/src/pages/api/images/[id]/scans/[scanId].ts`, `app/src/pages/api/images/[id]/scans/[scanId]/findings.ts`
- Test: `app/src/__tests__/api-images.test.ts`

**Interfaces:**
- Consumes: Task 1 (`normalizeImageInput`, `canonicalRegistryHost`, `imageAddSchema`), Task 2 storage functions, C-1's `loadImagePolicy`, `ImagePolicyUnavailable`, `registryAllowed`, `registryHost`, `imagePolicyUnavailable`, `IMAGE_STATUSES`, and `getConnection`, `canAccessGroup`, `isUuid` (connections), `presignGetUrl` (`@/lib/storage/presign`).
- Produces:
  - `requireImageRole(auth: AuthContext | undefined, role: "member" | "operator" | "admin"): { identity: CanonicalIdentity } | { response: Response }`
  - `imageNotFound(): Response`
  - `currentImageView(now?: Date): ImageView` (the policy's `scan_window_days`, or `null` when the policy is unreadable)
  - HTTP:
    - `GET /api/images?status&q&in_use` → 200 `{ images: ApiImage[], scan_window_days: number | null }`.
    - `POST /api/images` → 202 `{ id, image_id, scan_id, deduplicated, reference, tag }`, or 400 `invalid_reference`, 422 `registry_not_allowed` / `registry_connection_host_mismatch`, 404 `registry_connection_not_found`, 400 `not_a_registry_connection`, 503 `image_policy_unavailable`.
    - `GET /api/images/[id]` → 200 `{ image, scans, in_use_by: ApiImageUser[], in_use_elsewhere: number }`.
    - `GET /api/images/[id]/scans/[scanId]` → 200 `{ scan, image: ApiImage | null }`.
    - `GET …/findings` → 302 to the presigned object.

- [ ] **Step 1: Write the failing test** — `app/src/__tests__/api-images.test.ts`

```ts
// @vitest-environment node
/**
 * /api/images read routes and the add verb (C-3, container-images spec
 * §9.1): auth, normalization, the allowed_registries check, the credential's
 * group and host, dedup of an open admission, the fail-closed policy, the
 * group-scoped "in use by" list, and the scan poll across the drain's dedup.
 * Storage is mocked: this pins ROUTE behaviour, not SQL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({
  listImages: vi.fn(),
  getImage: vi.fn(),
  listImageScans: vi.fn(),
  listImageUsers: vi.fn(),
  getImageScan: vi.fn(),
  findOpenAdmission: vi.fn(),
  insertImageWithAdmission: vi.fn(),
  requestImageScan: vi.fn(),
  grantImageException: vi.fn(),
  revokeImage: vi.fn(),
  isDigestLaunchable: vi.fn(),
  getImageForGate: vi.fn(),
}));
vi.mock("@/lib/connections/storage", () => ({ getConnection: vi.fn() }));
vi.mock("@/lib/storage/presign", () => ({
  presignGetUrl: vi.fn(async (key: string) => `https://objects.example/${key}?sig=1`),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { getConnection } from "@/lib/connections/storage";
import { resetImagePolicyCache } from "@/lib/images/policy";
import {
  findOpenAdmission,
  getImage,
  getImageScan,
  insertImageWithAdmission,
  listImageScans,
  listImageUsers,
  listImages,
  type ApiImage,
  type ApiImageScan,
} from "@/lib/images/storage";
import { GET as listRoute, POST as addRoute } from "@/pages/api/images/index";
import { GET as detailRoute } from "@/pages/api/images/[id]";
import { GET as scanRoute } from "@/pages/api/images/[id]/scans/[scanId]";
import { GET as findingsRoute } from "@/pages/api/images/[id]/scans/[scanId]/findings";

const EO = "earth-observation";
const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const OTHER_IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e71";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";
const CONN = "3a9f1c2e-0000-4000-8000-000000000001";

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };
const member = authed(["member"]);
const operator = authed(["operator"]);
const admin = authed(["admin"], []);

type RouteHandler = (ctx: never) => Promise<Response> | Response;

async function call(
  handler: RouteHandler,
  auth: AuthContext,
  {
    method = "GET",
    body,
    params = {},
    search = "",
  }: { method?: string; body?: unknown; params?: Record<string, string>; search?: string } = {},
) {
  const url = new URL(`http://localhost:4321/api/images${search}`);
  const locals: { auth: AuthContext; auditDetail?: Record<string, unknown> } = { auth };
  const res = await handler({
    url,
    locals,
    params,
    request: new Request(url, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  } as never);
  return { res, locals };
}

function image(overrides: Partial<ApiImage> = {}): ApiImage {
  return {
    id: IMG,
    reference: "docker.io/library/python",
    tag_at_add: "3.12-slim",
    digest: null,
    status: "pending",
    verdict: null,
    size_bytes: null,
    config: null,
    last_scan_id: null,
    last_scanned_at: null,
    stale: false,
    db_built_at: null,
    added_by: "user-1",
    created_at: "2026-09-27T00:00:00.000Z",
    updated_at: "2026-09-27T00:00:00.000Z",
    exception: null,
    tag_current_digest: null,
    tag_checked_at: null,
    drifted: false,
    registry_connection: null,
    in_use_by: 0,
    ...overrides,
  };
}

function scan(overrides: Partial<ApiImageScan> = {}): ApiImageScan {
  return {
    id: SCAN,
    image_id: IMG,
    kind: "admission",
    status: "pending",
    requested_by: "user-1",
    requested_at: "2026-09-27T00:00:00.000Z",
    started_at: null,
    finished_at: null,
    result: null,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

function registryConnection(overrides: Record<string, unknown> = {}) {
  return {
    id: CONN,
    name: "ghcr robot",
    description: "",
    protocol: "registry",
    config: { host: "ghcr.io" },
    credentials_set: true,
    host_key: null,
    group_id: EO,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00.000Z",
    updated_at: "2026-09-01T00:00:00.000Z",
    enabled: true,
    status: "ok",
    last_checked_at: null,
    last_error: null,
    ...overrides,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  resetImagePolicyCache();
  vi.mocked(findOpenAdmission).mockResolvedValue(null);
  vi.mocked(insertImageWithAdmission).mockResolvedValue({
    image_id: IMG,
    scan_id: SCAN,
    deduplicated: false,
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
  resetImagePolicyCache();
});

function breakPolicy() {
  vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
  resetImagePolicyCache();
}

describe("GET /api/images", () => {
  it("is member+ and 401 for anonymous", async () => {
    expect((await call(listRoute, anon)).res.status).toBe(401);
    vi.mocked(listImages).mockResolvedValue([image()]);
    const { res } = await call(listRoute, member);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ images: [image()], scan_window_days: 30 });
  });

  it("passes the filters through", async () => {
    vi.mocked(listImages).mockResolvedValue([]);
    await call(listRoute, member, { search: "?status=flagged&q=%20satpy%20&in_use=true" });
    expect(vi.mocked(listImages).mock.calls[0][0]).toEqual({
      status: "flagged",
      q: "satpy",
      inUse: true,
    });
    await call(listRoute, member, { search: "?in_use=false" });
    expect(vi.mocked(listImages).mock.calls[1][0]).toEqual({
      status: undefined,
      q: undefined,
      inUse: false,
    });
  });

  it("refuses an unknown status or in_use value", async () => {
    expect((await call(listRoute, member, { search: "?status=stale" })).res.status).toBe(400);
    expect((await call(listRoute, member, { search: "?in_use=yes" })).res.status).toBe(400);
    expect(listImages).not.toHaveBeenCalled();
  });

  it("still lists when the policy is unreadable, with the window unknown", async () => {
    breakPolicy();
    vi.mocked(listImages).mockResolvedValue([]);
    const { res } = await call(listRoute, member);
    expect(res.status).toBe(200);
    expect((await res.json()).scan_window_days).toBeNull();
    expect(vi.mocked(listImages).mock.calls[0][1].scanWindowDays).toBeNull();
  });
});

describe("POST /api/images", () => {
  it("is operator+", async () => {
    expect((await call(addRoute, anon, { method: "POST", body: { reference: "python" } })).res.status).toBe(401);
    const { res } = await call(addRoute, member, { method: "POST", body: { reference: "python" } });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
  });

  it("normalizes the reference, writes a pending row + admission scan, and answers 202", async () => {
    const { res, locals } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python:3.12-slim" },
    });
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({
      id: IMG,
      image_id: IMG,
      scan_id: SCAN,
      deduplicated: false,
      reference: "docker.io/library/python",
      tag: "3.12-slim",
    });
    expect(insertImageWithAdmission).toHaveBeenCalledWith({
      reference: "docker.io/library/python",
      tag: "3.12-slim",
      registryConnectionId: null,
      addedBy: "user-1",
    });
    expect(locals.auditDetail).toEqual({
      reference: "docker.io/library/python",
      tag: "3.12-slim",
      registry_connection_id: null,
      scan_id: SCAN,
      deduplicated: false,
    });
  });

  it("refuses a body it cannot read and a reference it cannot store", async () => {
    expect((await call(addRoute, operator, { method: "POST", body: { ref: "python" } })).res.status).toBe(400);
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python@sha256:" + "a".repeat(64) },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("invalid_reference");
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("refuses a registry outside the policy before writing anything", async () => {
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "quay.io/org/tool:1" },
    });
    expect(res.status).toBe(422);
    const body = await res.json();
    expect(body.code).toBe("registry_not_allowed");
    expect(body.error).toMatch(/quay\.io/);
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("allows a wildcard-matched ECR host", async () => {
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/app:prod" },
    });
    expect(res.status).toBe(202);
  });

  it("fails closed when the policy is unreadable", async () => {
    breakPolicy();
    const { res } = await call(addRoute, operator, { method: "POST", body: { reference: "python" } });
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("image_policy_unavailable");
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("accepts a registry credential of the caller's group for the same host", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection());
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/satpy-runtime:1.4.2", registry_connection_id: CONN },
    });
    expect(res.status).toBe(202);
    expect(vi.mocked(insertImageWithAdmission).mock.calls[0][0].registryConnectionId).toBe(CONN);
  });

  it("matches Docker Hub credentials through the host aliases", async () => {
    vi.mocked(getConnection).mockResolvedValue(
      registryConnection({ config: { host: "index.docker.io" } }),
    );
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "org/private-tool:2", registry_connection_id: CONN },
    });
    expect(res.status).toBe(202);
  });

  it("hides a credential outside the caller's groups (404) and refuses a non-registry one", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection({ group_id: "weather" }));
    let { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    });
    expect(res.status).toBe(404);
    expect((await res.json()).code).toBe("registry_connection_not_found");

    vi.mocked(getConnection).mockResolvedValue(null);
    ({ res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    }));
    expect(res.status).toBe(404);

    vi.mocked(getConnection).mockResolvedValue(registryConnection({ protocol: "s3", config: { bucket: "b" } }));
    ({ res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    }));
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("not_a_registry_connection");
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("an admin may use any group's credential", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection({ group_id: "weather" }));
    const { res } = await call(addRoute, admin, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    });
    expect(res.status).toBe(202);
  });

  it("refuses a credential for another registry host", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection({ config: { host: "ghcr.io" } }));
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python", registry_connection_id: CONN },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("registry_connection_host_mismatch");
  });

  it("reuses an open admission instead of queueing a second provisional row", async () => {
    vi.mocked(findOpenAdmission).mockResolvedValue({
      image_id: IMG,
      scan_id: SCAN,
      deduplicated: true,
    });
    const { res } = await call(addRoute, operator, { method: "POST", body: { reference: "python" } });
    expect(res.status).toBe(202);
    expect((await res.json()).deduplicated).toBe(true);
    expect(findOpenAdmission).toHaveBeenCalledWith({
      reference: "docker.io/library/python",
      tag: "latest",
      registryConnectionId: null,
    });
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });
});

describe("GET /api/images/[id]", () => {
  it("404s a malformed or missing id", async () => {
    expect((await call(detailRoute, member, { params: { id: "nope" } })).res.status).toBe(404);
    vi.mocked(getImage).mockResolvedValue(null);
    expect((await call(detailRoute, member, { params: { id: IMG } })).res.status).toBe(404);
  });

  it("names only the processes in the caller's groups and counts the rest", async () => {
    vi.mocked(getImage).mockResolvedValue(image({ status: "approved", in_use_by: 2 }));
    vi.mocked(listImageScans).mockResolvedValue([scan({ status: "done" })]);
    vi.mocked(listImageUsers).mockResolvedValue([
      { process_id: "p-1", name: "geocolor", group_id: EO },
      { process_id: "p-2", name: "radar", group_id: "weather" },
    ]);
    const { res } = await call(detailRoute, member, { params: { id: IMG } });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.in_use_by).toEqual([{ process_id: "p-1", name: "geocolor", group_id: EO }]);
    expect(body.in_use_elsewhere).toBe(1);
    expect(body.scans).toHaveLength(1);
    expect(vi.mocked(listImageScans).mock.calls[0]).toEqual([IMG, 10]);

    const asAdmin = await call(detailRoute, admin, { params: { id: IMG } });
    const adminBody = await asAdmin.res.json();
    expect(adminBody.in_use_by).toHaveLength(2);
    expect(adminBody.in_use_elsewhere).toBe(0);
  });
});

describe("GET /api/images/[id]/scans/[scanId]", () => {
  it("returns the scan with the AUTHORITATIVE image (the drain may have re-pointed it)", async () => {
    vi.mocked(getImageScan).mockResolvedValue(scan({ image_id: OTHER_IMG, status: "done" }));
    vi.mocked(getImage).mockResolvedValue(image({ id: OTHER_IMG, status: "approved" }));
    const { res } = await call(scanRoute, member, { params: { id: IMG, scanId: SCAN } });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.scan.image_id).toBe(OTHER_IMG);
    expect(body.image.id).toBe(OTHER_IMG);
    expect(vi.mocked(getImageScan)).toHaveBeenCalledWith(IMG, SCAN);
    expect(vi.mocked(getImage).mock.calls[0][0]).toBe(OTHER_IMG);
  });

  it("404s an unknown scan and 401s anonymous", async () => {
    vi.mocked(getImageScan).mockResolvedValue(null);
    expect((await call(scanRoute, member, { params: { id: IMG, scanId: SCAN } })).res.status).toBe(404);
    expect((await call(scanRoute, member, { params: { id: IMG, scanId: "x" } })).res.status).toBe(404);
    expect((await call(scanRoute, anon, { params: { id: IMG, scanId: SCAN } })).res.status).toBe(401);
  });
});

describe("GET /api/images/[id]/scans/[scanId]/findings", () => {
  it("redirects to a short-lived presigned URL, never cached", async () => {
    vi.mocked(getImageScan).mockResolvedValue(
      scan({ status: "done", findings_ref: "scans/i/s/findings.grype.json" }),
    );
    const { res } = await call(findingsRoute, member, { params: { id: IMG, scanId: SCAN } });
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe(
      "https://objects.example/scans/i/s/findings.grype.json?sig=1",
    );
    expect(res.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it("404s a scan with no stored findings", async () => {
    vi.mocked(getImageScan).mockResolvedValue(scan());
    const { res } = await call(findingsRoute, member, { params: { id: IMG, scanId: SCAN } });
    expect(res.status).toBe(404);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/api-images.test.ts`
Expected: FAIL (the route modules do not exist).

- [ ] **Step 3: Write the implementation**

`app/src/lib/images/access.ts`:

```ts
/**
 * The /api/images route preamble (C-3, container-images spec §9.1).
 *
 * The registry is PLATFORM-WIDE (spec decision 7): every authenticated
 * member sees every image, so there is no group-scoped 404 on an image the
 * way there is on a process or a connection. Group ownership applies to the
 * registry CREDENTIAL (checked on add, and by the deploy gate) and to the
 * processes named on the detail page. The guard enforces operator+ on the
 * mutation routes and writes their audit rows. These helpers repeat the
 * role check so a route is safe on its own (and unit-testable), and add the
 * ADMIN check the guard cannot express.
 */
import type { AuthContext, CanonicalIdentity } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/http/response";
import { loadImagePolicy } from "./policy";
import type { ImageView } from "./storage";

export type ImageRole = "member" | "operator" | "admin";

export function requireImageRole(
  auth: AuthContext | undefined,
  role: ImageRole,
): { identity: CanonicalIdentity } | { response: Response } {
  if (!auth?.authenticated) {
    return {
      response: authzError(401, "unauthenticated", "Authentication required for this action"),
    };
  }
  if (role === "operator" && !canMutate(auth.identity)) {
    return {
      response: authzError(403, "forbidden", "This action requires the operator or admin role"),
    };
  }
  if (role === "admin" && !isAdmin(auth.identity)) {
    return { response: authzError(403, "forbidden", "This action requires the admin role") };
  }
  return { identity: auth.identity };
}

export function imageNotFound(): Response {
  return jsonResponse(404, { error: "Image not found" });
}

/** The policy-derived view a read needs. A broken policy must not take the
 * dashboard down: staleness becomes unknown (`null`) and the page says why,
 * while every WRITE that needs the policy still fails closed (503). */
export function currentImageView(now: Date = new Date()): ImageView {
  try {
    return { now, scanWindowDays: loadImagePolicy().scan_window_days };
  } catch {
    return { now, scanWindowDays: null };
  }
}
```

`app/src/pages/api/images/index.ts`:

```ts
/**
 * /api/images — the platform-wide image registry (C-3, container-images spec
 * §9.1, ADR 0021).
 *
 * GET  — member+. `?status=<image status>`, `?q=<text>` (reference:tag
 *        substring), `?in_use=true|false`. Every row carries its computed
 *        `stale` flag and `in_use_by` count.
 * POST — operator+, audited `create` on `container_image` (`image.add`).
 *        `{reference, tag?, registry_connection_id?}`. The typed reference is
 *        normalized to C-1's stored grammar, its registry host must be in the
 *        policy's `allowed_registries`, and a credential must be a `registry`
 *        connection of one of the caller's groups for that same host. The
 *        app never touches a registry: it INSERTs a `pending` row (digest
 *        NULL) and an `admission` scan request, and answers 202 with both
 *        ids. The scanner (C-2) resolves the tag and the drain may
 *        de-duplicate by digest. The client follows the scan id.
 */
import type { APIRoute } from "astro";
import { canAccessGroup } from "@/lib/connections/access";
import { getConnection } from "@/lib/connections/storage";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, requireImageRole } from "@/lib/images/access";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { canonicalRegistryHost, normalizeImageInput } from "@/lib/images/normalize";
import {
  ImagePolicyUnavailable,
  loadImagePolicy,
  registryAllowed,
  type ImagePolicy,
} from "@/lib/images/policy";
import { registryHost } from "@/lib/images/reference";
import { imageAddSchema } from "@/lib/images/schemas";
import { IMAGE_STATUSES, type ImageStatus } from "@/lib/images/status";
import {
  findOpenAdmission,
  insertImageWithAdmission,
  listImages,
} from "@/lib/images/storage";

const Q_MAX = 200;

export const GET: APIRoute = async ({ url, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;

  const status = url.searchParams.get("status");
  if (status !== null && !(IMAGE_STATUSES as readonly string[]).includes(status)) {
    return jsonResponse(400, { error: `status must be one of ${IMAGE_STATUSES.join(", ")}` });
  }
  const inUse = url.searchParams.get("in_use");
  if (inUse !== null && inUse !== "true" && inUse !== "false") {
    return jsonResponse(400, { error: "in_use must be true or false" });
  }
  const q = url.searchParams.get("q")?.trim() ?? "";
  if (q.length > Q_MAX) {
    return jsonResponse(400, { error: `q is at most ${Q_MAX} characters` });
  }

  try {
    const view = currentImageView();
    const images = await listImages(
      {
        status: (status as ImageStatus | null) ?? undefined,
        q: q || undefined,
        inUse: inUse === null ? undefined : inUse === "true",
      },
      view,
    );
    return jsonResponse(200, { images, scan_window_days: view.scanWindowDays });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const POST: APIRoute = async ({ request, locals }) => {
  const allowed = requireImageRole(locals.auth, "operator");
  if ("response" in allowed) return allowed.response;
  const { identity } = allowed;

  const parsed = imageAddSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
  }
  const normalized = normalizeImageInput(parsed.data.reference, parsed.data.tag ?? null);
  if (!normalized.ok) {
    return jsonResponse(400, { error: normalized.error, code: "invalid_reference" });
  }

  try {
    let policy: ImagePolicy;
    try {
      policy = loadImagePolicy();
    } catch (err) {
      if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
      throw err;
    }

    const host = registryHost(normalized.reference);
    if (!registryAllowed(host, policy.allowed_registries)) {
      return jsonResponse(422, {
        error: `${host} is not an allowed registry on this deployment (allowed: ${policy.allowed_registries.join(", ")})`,
        code: "registry_not_allowed",
      });
    }

    const connectionId = parsed.data.registry_connection_id ?? null;
    if (connectionId !== null) {
      const connection = await getConnection(connectionId);
      // A credential outside the caller's groups is indistinguishable from a
      // missing one (the connections rule: group ownership scopes existence).
      if (!connection || !canAccessGroup(identity, connection.group_id)) {
        return jsonResponse(404, {
          error: "Registry connection not found",
          code: "registry_connection_not_found",
        });
      }
      if (connection.protocol !== "registry") {
        return jsonResponse(400, {
          error: `${connection.name} is a ${connection.protocol} connection; image pulls need a registry connection`,
          code: "not_a_registry_connection",
        });
      }
      const credentialHost = canonicalRegistryHost(String(connection.config.host ?? ""));
      if (credentialHost !== host) {
        return jsonResponse(422, {
          error: `${connection.name} holds credentials for ${credentialHost}, not ${host}`,
          code: "registry_connection_host_mismatch",
        });
      }
    }

    const target = {
      reference: normalized.reference,
      tag: normalized.tag,
      registryConnectionId: connectionId,
    };
    const added =
      (await findOpenAdmission(target)) ??
      (await insertImageWithAdmission({ ...target, addedBy: identity.sub }));

    locals.auditDetail = {
      reference: normalized.reference,
      tag: normalized.tag,
      registry_connection_id: connectionId,
      scan_id: added.scan_id,
      deduplicated: added.deduplicated,
    };
    // `id` is the new image, so the guard's created-id extraction audits it.
    return jsonResponse(202, {
      id: added.image_id,
      ...added,
      reference: normalized.reference,
      tag: normalized.tag,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

`app/src/pages/api/images/[id].ts`:

```ts
/**
 * GET /api/images/[id] — one image with its last ten scans and who uses it
 * (C-3, container-images spec §9.1/§9.2). member+.
 *
 * The registry is platform-wide, but PROCESSES are group-owned, and a
 * process outside your groups is a 404 everywhere else. So `in_use_by`
 * names only the processes in the caller's groups (all of them for an
 * admin), and `in_use_elsewhere` counts the rest.
 */
import type { APIRoute } from "astro";
import { canAccessGroup, isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, imageNotFound, requireImageRole } from "@/lib/images/access";
import { getImage, listImageScans, listImageUsers } from "@/lib/images/storage";

const SCAN_HISTORY = 10;

export const GET: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return imageNotFound();

  try {
    const image = await getImage(params.id, currentImageView());
    if (!image) return imageNotFound();
    const [scans, users] = await Promise.all([
      listImageScans(image.id, SCAN_HISTORY),
      listImageUsers(image.id),
    ]);
    const visible = users.filter((user) => canAccessGroup(allowed.identity, user.group_id));
    return jsonResponse(200, {
      image,
      scans,
      in_use_by: visible,
      in_use_elsewhere: users.length - visible.length,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

`app/src/pages/api/images/[id]/scans/[scanId].ts`:

```ts
/**
 * GET /api/images/[id]/scans/[scanId] — poll a scan (C-3, the test-run
 * polling pattern). member+.
 *
 * The client polls the ids the 202 handed out. The drain may de-duplicate
 * by digest (spec §9.1), re-pointing the scan at an existing image and
 * deleting the provisional one. `getImageScan` still finds the scan through
 * the old id, and the `image` returned is the one the scan now belongs to,
 * which is authoritative.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, requireImageRole } from "@/lib/images/access";
import { getImage, getImageScan } from "@/lib/images/storage";

function scanNotFound(): Response {
  return jsonResponse(404, { error: "Scan not found" });
}

export const GET: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id) || !isUuid(params.scanId)) return scanNotFound();

  try {
    const scan = await getImageScan(params.id, params.scanId);
    if (!scan) return scanNotFound();
    const image = await getImage(scan.image_id, currentImageView());
    return jsonResponse(200, { scan, image });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

`app/src/pages/api/images/[id]/scans/[scanId]/findings.ts`:

```ts
/**
 * GET /api/images/[id]/scans/[scanId]/findings — the full Grype JSON of a
 * scan (C-3, container-images spec §9.1 "a presigned/proxied read of
 * findings_ref"). member+: the registry and its findings are platform-wide.
 *
 * Authorize, then 302 to a short-lived presigned URL: the run-log
 * precedent (`runs/[runId]/log`). Findings can be megabytes and the app
 * never streams them.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { requireImageRole } from "@/lib/images/access";
import { getImageScan } from "@/lib/images/storage";
import { presignGetUrl } from "@/lib/storage/presign";

export const GET: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id) || !isUuid(params.scanId)) {
    return jsonResponse(404, { error: "Scan not found" });
  }

  try {
    const scan = await getImageScan(params.id, params.scanId);
    if (!scan) return jsonResponse(404, { error: "Scan not found" });
    if (!scan.findings_ref) {
      return jsonResponse(404, { error: "This scan has no stored findings" });
    }
    const url = await presignGetUrl(scan.findings_ref);
    return new Response(null, {
      status: 302,
      headers: { Location: url, "Cache-Control": "private, no-store" },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/api-images.test.ts`
Expected: PASS. The policy tests read the real `infra/image-policy/default.json`. `allowed_registries` there includes `docker.io`, `ghcr.io` and `*.dkr.ecr.*.amazonaws.com`, and excludes `quay.io`.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green. Astro accepts `[id].ts` beside an `[id]/` directory (the processes routes already do this).

```bash
git add app/src/lib/images/access.ts app/src/pages/api/images app/src/__tests__/api-images.test.ts
git commit -m "feat(images): list, detail, scan poll, findings and add routes (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: The verbs — rescan, exception, revoke

**Files:**
- Create: `app/src/pages/api/images/[id]/rescan.ts`, `app/src/pages/api/images/[id]/exception.ts`, `app/src/pages/api/images/[id]/revoke.ts`
- Test: `app/src/__tests__/api-images-verbs.test.ts`

**Interfaces:**
- Consumes: `requireImageRole`, `imageNotFound`, `currentImageView` (Task 4); `requestImageScan`, `grantImageException`, `revokeImage`, `getImage` (Task 2); `imageExceptionSchema` (Task 1); `loadImagePolicy`, `ImagePolicyUnavailable`, `imagePolicyUnavailable` (C-1).
- Produces:
  - `POST /api/images/[id]/rescan` (operator+) → 202 `{ image_id, scan_id, kind }`, or 404, 409 `image_revoked`, or 409 `scan_pending` with `scan_id`.
  - `POST /api/images/[id]/exception` (admin) with `{reason, expires_at}` → 200 `{ image }`, or 400 validation / `exception_expiry_invalid`, 422 `exception_too_long`, 404, 409 `image_not_exceptionable`, 503 `image_policy_unavailable`. It sets `locals.auditDetail = { reason, expires_at }`.
  - `POST /api/images/[id]/revoke` (admin) → 200 `{ image }`, or 404, or 409 `image_already_revoked`.

The exception FORM on the dashboard is C-4 (spec §15: "the dashboard's history/diff and exception form wiring"). C-3 ships the route and shows a live exception read-only. The dashboard gets no revoke button in C-3 (spec §9.2 names none); C-4 adds it beside the exception form.

- [ ] **Step 1: Write the failing test** — `app/src/__tests__/api-images-verbs.test.ts`

```ts
// @vitest-environment node
/**
 * The image verbs (C-3, container-images spec §9.1, §4.3, §4.4). Rescan is
 * operator+. Exception and revoke are ADMIN, which the guard (operator-level)
 * cannot express, so the routes enforce it. The exception's reason reaches
 * the audit row through `locals.auditDetail`: exceptions are columns and
 * their history IS the audit log (spec §4.4).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({
  getImage: vi.fn(),
  requestImageScan: vi.fn(),
  grantImageException: vi.fn(),
  revokeImage: vi.fn(),
  getImageForGate: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { resetImagePolicyCache } from "@/lib/images/policy";
import {
  getImage,
  grantImageException,
  requestImageScan,
  revokeImage,
} from "@/lib/images/storage";
import { POST as rescanRoute } from "@/pages/api/images/[id]/rescan";
import { POST as exceptionRoute } from "@/pages/api/images/[id]/exception";
import { POST as revokeRoute } from "@/pages/api/images/[id]/revoke";

const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";
const DAY = 86_400_000;

function authed(roles: CanonicalRole[], groups = ["earth-observation"]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "admin-1", email: null, name: null, groups, roles },
  };
}
const member = authed(["member"]);
const operator = authed(["operator"]);
const admin = authed(["admin"], []);

type RouteHandler = (ctx: never) => Promise<Response> | Response;

async function call(handler: RouteHandler, auth: AuthContext, body?: unknown, id = IMG) {
  const url = new URL(`http://localhost:4321/api/images/${id}/verb`);
  const locals: { auth: AuthContext; auditDetail?: Record<string, unknown> } = { auth };
  const res = await handler({
    url,
    locals,
    params: { id },
    request: new Request(url, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  } as never);
  return { res, locals };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetImagePolicyCache();
  vi.mocked(getImage).mockResolvedValue({ id: IMG, status: "approved" } as never);
});
afterEach(() => {
  vi.unstubAllEnvs();
  resetImagePolicyCache();
});

describe("POST /api/images/[id]/rescan", () => {
  it("is operator+", async () => {
    expect((await call(rescanRoute, member)).res.status).toBe(403);
    expect(requestImageScan).not.toHaveBeenCalled();
  });

  it("queues a scan and answers 202 with its id and kind", async () => {
    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "requested", scan_id: SCAN, kind: "rescan" });
    const { res, locals } = await call(rescanRoute, operator);
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ image_id: IMG, scan_id: SCAN, kind: "rescan" });
    expect(requestImageScan).toHaveBeenCalledWith(IMG, "admin-1");
    expect(locals.auditDetail).toEqual({ scan_id: SCAN, kind: "rescan" });
  });

  it("maps each refusal to its status and code", async () => {
    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "not_found" });
    expect((await call(rescanRoute, operator)).res.status).toBe(404);

    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "revoked" });
    let { res } = await call(rescanRoute, operator);
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("image_revoked");

    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "already_pending", scan_id: SCAN });
    ({ res } = await call(rescanRoute, operator));
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ code: "scan_pending", scan_id: SCAN });

    expect((await call(rescanRoute, operator, undefined, "nope")).res.status).toBe(404);
  });
});

describe("POST /api/images/[id]/exception", () => {
  const inDays = (days: number) => new Date(Date.now() + days * DAY).toISOString();
  const REASON = "Upstream fix lands in 2.13; tracked in TT-12";

  it("is admin only: an operator is 403 even though the guard let them through", async () => {
    const { res } = await call(exceptionRoute, operator, { reason: REASON, expires_at: inDays(30) });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(grantImageException).not.toHaveBeenCalled();
  });

  it("grants, returns the image, and records the reason in the audit row", async () => {
    vi.mocked(grantImageException).mockResolvedValue({ outcome: "granted" });
    const expires = inDays(30);
    const { res, locals } = await call(exceptionRoute, admin, { reason: REASON, expires_at: expires });
    expect(res.status).toBe(200);
    expect((await res.json()).image.id).toBe(IMG);
    const input = vi.mocked(grantImageException).mock.calls[0][0];
    expect(input).toMatchObject({ imageId: IMG, reason: REASON, by: "admin-1" });
    expect(input.expiresAt.toISOString()).toBe(new Date(expires).toISOString());
    expect(locals.auditDetail).toEqual({ reason: REASON, expires_at: new Date(expires).toISOString() });
  });

  it("refuses an expiry in the past or beyond the policy's exception_max_days", async () => {
    let { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(-1) });
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("exception_expiry_invalid");

    ({ res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(91) }));
    expect(res.status).toBe(422);
    const body = await res.json();
    expect(body.code).toBe("exception_too_long");
    expect(body.error).toMatch(/90 days/);
    expect(grantImageException).not.toHaveBeenCalled();
  });

  it("refuses a thin reason", async () => {
    const { res } = await call(exceptionRoute, admin, { reason: "ok", expires_at: inDays(5) });
    expect(res.status).toBe(400);
  });

  it("says why the image cannot take an exception", async () => {
    vi.mocked(grantImageException).mockResolvedValue({ outcome: "wrong_status", status: "pending" });
    const { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(5) });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.code).toBe("image_not_exceptionable");
    expect(body.error).toMatch(/pending/);

    vi.mocked(grantImageException).mockResolvedValue({ outcome: "not_found" });
    expect((await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(5) })).res.status).toBe(404);
  });

  it("fails closed when the policy is unreadable", async () => {
    vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
    resetImagePolicyCache();
    const { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(5) });
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("image_policy_unavailable");
  });
});

describe("POST /api/images/[id]/revoke", () => {
  it("is admin only", async () => {
    expect((await call(revokeRoute, operator)).res.status).toBe(403);
    expect(revokeImage).not.toHaveBeenCalled();
  });

  it("revokes and returns the image", async () => {
    vi.mocked(revokeImage).mockResolvedValue({ outcome: "revoked" });
    vi.mocked(getImage).mockResolvedValue({ id: IMG, status: "revoked" } as never);
    const { res } = await call(revokeRoute, admin);
    expect(res.status).toBe(200);
    expect((await res.json()).image.status).toBe("revoked");
  });

  it("maps not_found and already_revoked", async () => {
    vi.mocked(revokeImage).mockResolvedValue({ outcome: "not_found" });
    expect((await call(revokeRoute, admin)).res.status).toBe(404);
    vi.mocked(revokeImage).mockResolvedValue({ outcome: "already_revoked" });
    const { res } = await call(revokeRoute, admin);
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("image_already_revoked");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/api-images-verbs.test.ts`
Expected: FAIL (the route modules do not exist).

- [ ] **Step 3: Write the implementation**

`app/src/pages/api/images/[id]/rescan.ts`:

```ts
/**
 * POST /api/images/[id]/rescan — "Rescan now" (C-3, container-images spec
 * §9.1). operator+, audited `rescan` on `container_image`.
 *
 * INSERTs an `image_scans` request row for the pipeline to drain (ADR 0004).
 * An image that never scanned successfully gets an ADMISSION retry instead
 * of a rescan (a rescan re-matches the stored SBOM, and there is none). One
 * open scan per image (409 `scan_pending`, naming it); revoked is terminal
 * (409 `image_revoked`).
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { imageNotFound, requireImageRole } from "@/lib/images/access";
import { requestImageScan } from "@/lib/images/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "operator");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return imageNotFound();

  try {
    const outcome = await requestImageScan(params.id, allowed.identity.sub);
    switch (outcome.outcome) {
      case "not_found":
        return imageNotFound();
      case "revoked":
        return jsonResponse(409, {
          error: "This image is revoked; add the reference again to scan it as a new image",
          code: "image_revoked",
        });
      case "already_pending":
        return jsonResponse(409, {
          error: "A scan of this image is already queued or running",
          code: "scan_pending",
          scan_id: outcome.scan_id,
        });
      case "requested":
        locals.auditDetail = { scan_id: outcome.scan_id, kind: outcome.kind };
        return jsonResponse(202, {
          image_id: params.id,
          scan_id: outcome.scan_id,
          kind: outcome.kind,
        });
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

`app/src/pages/api/images/[id]/exception.ts`:

```ts
/**
 * POST /api/images/[id]/exception — an admin's expiring exception (C-3,
 * container-images spec §4.4, §9.1). ADMIN only (checked here; the guard
 * gates operator+ and writes the audit row `exception` on
 * `container_image`, carrying the reason and expiry via `locals.auditDetail`).
 *
 * `{reason, expires_at}`: `expires_at` must be in the future and at most the
 * policy's `exception_max_days` away. Only a `rejected` or `flagged` image
 * takes an exception (-> `approved`). An exception never covers staleness
 * (spec §4.3); the gate still refuses a stale image.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { imageNotFound, requireImageRole } from "@/lib/images/access";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { ImagePolicyUnavailable, loadImagePolicy, type ImagePolicy } from "@/lib/images/policy";
import { imageExceptionSchema } from "@/lib/images/schemas";
import { getImage, grantImageException } from "@/lib/images/storage";

const DAY_MS = 86_400_000;

export const POST: APIRoute = async ({ params, request, locals }) => {
  const allowed = requireImageRole(locals.auth, "admin");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return imageNotFound();

  const parsed = imageExceptionSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
  }

  try {
    let policy: ImagePolicy;
    try {
      policy = loadImagePolicy();
    } catch (err) {
      if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
      throw err;
    }

    const now = new Date();
    const expiresAt = new Date(parsed.data.expires_at);
    if (expiresAt.getTime() <= now.getTime()) {
      return jsonResponse(400, {
        error: "expires_at must be in the future",
        code: "exception_expiry_invalid",
      });
    }
    if (expiresAt.getTime() - now.getTime() > policy.exception_max_days * DAY_MS) {
      return jsonResponse(422, {
        error: `An exception lasts at most ${policy.exception_max_days} days on this deployment (policy exception_max_days)`,
        code: "exception_too_long",
      });
    }

    const outcome = await grantImageException({
      imageId: params.id,
      reason: parsed.data.reason,
      by: allowed.identity.sub,
      expiresAt,
    });
    if (outcome.outcome === "not_found") return imageNotFound();
    if (outcome.outcome === "wrong_status") {
      return jsonResponse(409, {
        error: `An exception applies to a rejected or flagged image; this one is ${outcome.status}`,
        code: "image_not_exceptionable",
      });
    }
    locals.auditDetail = { reason: parsed.data.reason, expires_at: expiresAt.toISOString() };
    const image = await getImage(params.id, { now, scanWindowDays: policy.scan_window_days });
    return jsonResponse(200, { image });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

`app/src/pages/api/images/[id]/revoke.ts`:

```ts
/**
 * POST /api/images/[id]/revoke — the terminal verb (C-3, container-images
 * spec §4.3/§4.4, §9.1). ADMIN only (checked here; the guard audits
 * `revoke` on `container_image`). Any status -> `revoked`, ending a live
 * exception. There is no DELETE in v1: immutable revisions may still
 * snapshot the id, and the pipeline dies their runs at launch (C-2).
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, imageNotFound, requireImageRole } from "@/lib/images/access";
import { getImage, revokeImage } from "@/lib/images/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "admin");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return imageNotFound();

  try {
    const outcome = await revokeImage(params.id);
    if (outcome.outcome === "not_found") return imageNotFound();
    if (outcome.outcome === "already_revoked") {
      return jsonResponse(409, { error: "This image is already revoked", code: "image_already_revoked" });
    }
    return jsonResponse(200, { image: await getImage(params.id, currentImageView()) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/api-images-verbs.test.ts`
Expected: PASS. If the type checker rejects `rescan.ts`'s `switch` for a missing return, it means the union is not exhaustive. Add `default: return imageNotFound();` rather than widening the union.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green.

```bash
git add "app/src/pages/api/images/[id]/rescan.ts" "app/src/pages/api/images/[id]/exception.ts" "app/src/pages/api/images/[id]/revoke.ts" app/src/__tests__/api-images-verbs.test.ts
git commit -m "feat(images): rescan (operator), exception and revoke (admin) routes (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: The policy route, the internal admission probe, and the connection-impact count

**Files:**
- Create: `app/src/pages/api/processes/image-policy.ts`, `app/src/pages/api/internal/images/approved.ts`
- Modify: `app/src/lib/connections/deletion.ts` (`ConnectionDeleteImpact`, `connectionDeleteImpact`)
- Modify: `app/src/lib/connections/api.ts` (the client `ConnectionDeleteImpact` interface)
- Modify: `app/src/components/connections/ConnectionsPage.tsx` (one line in the delete dialog)
- Test: `app/src/__tests__/api-images-internal.test.ts` (new), `app/src/__tests__/connections-deletion.test.ts` (updated)

**Interfaces:**
- Consumes: `loadImagePolicy`, `ImagePolicyUnavailable`, `imagePolicyUnavailable`, `isImageDigest`, `isDigestLaunchable` (Task 2), `requireImageRole` (Task 4).
- Produces:
  - `GET /api/processes/image-policy` (member+) → 200, the policy document without `scan_limits` (type `PublicImagePolicy = Omit<ImagePolicy, "scan_limits">`; Task 7 declares the same alias for the client in `lib/images/types.ts`), or 503 `image_policy_unavailable`.
  - `GET /api/internal/images/approved?digest=sha256:…` → 200 `{ approved: boolean }` (true when a row with that digest could LAUNCH: `approved` or `flagged`, scanned inside the window), 400 for a malformed digest, 503 without a policy. This route has no session. It is network-gated (never routed by a public ingress), and when `INTERNAL_API_TOKEN` is set, the `X-Internal-Token` header must equal it (401 otherwise).
  - `ConnectionDeleteImpact.images: number` (server and client).

- [ ] **Step 1: Write the failing tests**

`app/src/__tests__/api-images-internal.test.ts`:

```ts
// @vitest-environment node
/**
 * C-3: the read-only policy route and the internal admission probe
 * (container-images spec §9.1, §12). The probe is the seam K-6's Kyverno
 * policy calls; it answers "could this digest LAUNCH", the same statuses
 * and window the pipeline enforces (spec §4.3, §8.4).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({
  isDigestLaunchable: vi.fn(),
  getImageForGate: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext } from "@/lib/auth/types";
import { resetImagePolicyCache } from "@/lib/images/policy";
import { isDigestLaunchable } from "@/lib/images/storage";
import { GET as policyRoute } from "@/pages/api/processes/image-policy";
import { GET as approvedRoute } from "@/pages/api/internal/images/approved";

const DIGEST = "sha256:" + "c".repeat(64);
const member: AuthContext = {
  authenticated: true,
  mode: "bypass",
  identity: { sub: "user-1", email: null, name: null, groups: ["earth-observation"], roles: ["member"] },
};
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  { auth = anon, search = "", headers = {} }: { auth?: AuthContext; search?: string; headers?: Record<string, string> } = {},
) {
  const url = new URL(`http://localhost:4321/api/x${search}`);
  return handler({ url, locals: { auth }, params: {}, request: new Request(url, { headers }) } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  resetImagePolicyCache();
});
afterEach(() => {
  vi.unstubAllEnvs();
  resetImagePolicyCache();
});

describe("GET /api/processes/image-policy", () => {
  it("serves the policy to members without the scanner's resource limits", async () => {
    const res = await call(policyRoute, { auth: member });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.allowed_registries).toContain("ghcr.io");
    expect(body.exception_max_days).toBe(90);
    expect(body.scan_window_days).toBe(30);
    expect(body).not.toHaveProperty("scan_limits");
  });

  it("is 401 for anonymous and 503 when the policy is unreadable", async () => {
    expect((await call(policyRoute)).status).toBe(401);
    vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
    resetImagePolicyCache();
    const res = await call(policyRoute, { auth: member });
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("image_policy_unavailable");
  });
});

describe("GET /api/internal/images/approved", () => {
  it("answers whether a digest could launch, with the policy's window", async () => {
    vi.mocked(isDigestLaunchable).mockResolvedValue(true);
    const res = await call(approvedRoute, { search: `?digest=${DIGEST}` });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ approved: true });
    expect(isDigestLaunchable).toHaveBeenCalledWith(DIGEST, 30);
  });

  it("needs no session (it is network-gated), and refuses a malformed digest", async () => {
    vi.mocked(isDigestLaunchable).mockResolvedValue(false);
    expect(await (await call(approvedRoute, { search: `?digest=${DIGEST}` })).json()).toEqual({ approved: false });
    expect((await call(approvedRoute, { search: "?digest=sha256:ABC" })).status).toBe(400);
    expect((await call(approvedRoute)).status).toBe(400);
    expect(isDigestLaunchable).toHaveBeenCalledTimes(1);
  });

  it("requires the shared token when the deployment sets one", async () => {
    vi.stubEnv("INTERNAL_API_TOKEN", "s3cret-token");
    vi.mocked(isDigestLaunchable).mockResolvedValue(true);
    expect((await call(approvedRoute, { search: `?digest=${DIGEST}` })).status).toBe(401);
    expect(
      (await call(approvedRoute, { search: `?digest=${DIGEST}`, headers: { "X-Internal-Token": "wrong" } })).status,
    ).toBe(401);
    const ok = await call(approvedRoute, {
      search: `?digest=${DIGEST}`,
      headers: { "X-Internal-Token": "s3cret-token" },
    });
    expect(ok.status).toBe(200);
  });

  it("fails closed (503) without a policy", async () => {
    vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
    resetImagePolicyCache();
    const res = await call(approvedRoute, { search: `?digest=${DIGEST}` });
    expect(res.status).toBe(503);
    expect(isDigestLaunchable).not.toHaveBeenCalled();
  });
});
```

In `app/src/__tests__/connections-deletion.test.ts`, inside `describe("connectionDeleteImpact")`, change the existing expectation of the first test from

```ts
    expect(impact).toEqual({
      associations: { ingest: 2, deliver: 0 },
      reference_items: [{ collection_id: "goes-west", items: 3 }],
      history: { ingest_files: 12, delivery_log: 4, connection_checks: 2 },
    });
```

to

```ts
    expect(impact).toEqual({
      associations: { ingest: 2, deliver: 0 },
      reference_items: [{ collection_id: "goes-west", items: 3 }],
      history: { ingest_files: 12, delivery_log: 4, connection_checks: 2 },
      images: 0,
    });
```

and add a second test to the same `describe`:

```ts
  it("counts the container images pulled with this credential (C-3)", async () => {
    mockQuery.mockImplementation(async (sql: string) => {
      if (sql.includes("GROUP BY direction") || sql.includes("GROUP BY cc.collection_id")) {
        return { rows: [], rowCount: 0 } as never;
      }
      return {
        rows: [
          { ingest_files: "0", delivery_log: "0", connection_checks: "1", container_images: "3" },
        ],
        rowCount: 1,
      } as never;
    });
    const impact = await connectionDeleteImpact(CONN_ID);
    expect(impact.images).toBe(3);
    const historySql = mockQuery.mock.calls
      .map(([sql]) => String(sql))
      .find((sql) => sql.includes("connection_checks"));
    expect(historySql).toContain(
      "FROM stac_higher.container_images WHERE registry_connection_id = $1",
    );
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app && npx vitest run src/__tests__/api-images-internal.test.ts src/__tests__/connections-deletion.test.ts`
Expected: FAIL. The route modules are missing, and `images` is absent from the impact.

- [ ] **Step 3: Write the implementation**

`app/src/pages/api/processes/image-policy.ts`:

```ts
/**
 * GET /api/processes/image-policy — the deployment's image policy for the
 * UI (C-3, container-images spec §9.1). member+. The UI reads it to explain
 * a verdict and to say which registries an image may come from. The
 * scanner's `scan_limits` are pipeline-only and are left out.
 * Missing/invalid policy -> 503 (the loaders fail closed).
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { jsonResponse } from "@/lib/http/response";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { ImagePolicyUnavailable, loadImagePolicy, type ImagePolicy } from "@/lib/images/policy";

export type PublicImagePolicy = Omit<ImagePolicy, "scan_limits">;

export const GET: APIRoute = async ({ locals }) => {
  if (!locals.auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required to read the image policy");
  }
  try {
    const policy = loadImagePolicy();
    const visible: PublicImagePolicy = {
      version: policy.version,
      allowed_registries: policy.allowed_registries,
      platform: policy.platform,
      max_image_size_mb: policy.max_image_size_mb,
      block: policy.block,
      scan_window_days: policy.scan_window_days,
      rescan_interval_hours: policy.rescan_interval_hours,
      exception_max_days: policy.exception_max_days,
    };
    return jsonResponse(200, visible);
  } catch (err) {
    if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

`app/src/pages/api/internal/images/approved.ts`:

```ts
/**
 * GET /api/internal/images/approved?digest=sha256:… — `{approved: bool}` for
 * a cluster admission policy (C-3 builds it, K-6 wires it: container-images
 * spec §9.1, §12). Defence in depth behind the deploy gate and the
 * pipeline's launch check.
 *
 * "Approved" here means "could LAUNCH": a row with that digest is `approved`
 * or `flagged` and was scanned inside the policy window (spec §4.3). Flagged
 * blocks deploys, not runs, and the admission policy guards runs.
 *
 * No session: the caller is the cluster, not a person. The route is
 * NETWORK-GATED (a deployment never routes `/api/internal/*` from a public
 * ingress), and when `INTERNAL_API_TOKEN` is set it additionally requires
 * `X-Internal-Token` to equal it (constant-time compare). Missing/invalid
 * policy -> 503, never a silent "approved".
 */
import { timingSafeEqual } from "node:crypto";
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { ImagePolicyUnavailable, loadImagePolicy } from "@/lib/images/policy";
import { isImageDigest } from "@/lib/images/reference";
import { isDigestLaunchable } from "@/lib/images/storage";

function tokenMatches(given: string, expected: string): boolean {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export const GET: APIRoute = async ({ url, request }) => {
  const expected = process.env.INTERNAL_API_TOKEN?.trim();
  if (expected && !tokenMatches(request.headers.get("x-internal-token") ?? "", expected)) {
    return jsonResponse(401, { error: "internal token required", code: "unauthenticated" });
  }
  const digest = url.searchParams.get("digest") ?? "";
  if (!isImageDigest(digest)) {
    return jsonResponse(400, { error: "digest must be sha256:<64 lowercase hex>" });
  }
  try {
    const policy = loadImagePolicy();
    return jsonResponse(200, {
      approved: await isDigestLaunchable(digest, policy.scan_window_days),
    });
  } catch (err) {
    if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```

`app/src/lib/connections/deletion.ts`. Add the field to `ConnectionDeleteImpact` (after `history`):

```ts
  /** C-3: container images pulled with this credential. They stay in the
   * registry, but once the connection is deleted the deploy gate refuses
   * them (`image_group_mismatch`, "was deleted"). */
  images: number;
```

In `connectionDeleteImpact`, widen the third query's row type and add a subquery:

```ts
    query<{
      ingest_files: string;
      delivery_log: string;
      connection_checks: string;
      container_images: string;
    }>(
      `SELECT
         (SELECT count(*) FROM stac_higher.ingest_files f
            JOIN stac_higher.collection_connections cc ON cc.id = f.association_id
           WHERE cc.connection_id = $1)::text AS ingest_files,
         (SELECT count(*) FROM stac_higher.delivery_log d
            JOIN stac_higher.collection_connections cc ON cc.id = d.association_id
           WHERE cc.connection_id = $1)::text AS delivery_log,
         (SELECT count(*) FROM stac_higher.connection_checks
           WHERE connection_id = $1)::text AS connection_checks,
         (SELECT count(*) FROM stac_higher.container_images WHERE registry_connection_id = $1)::text AS container_images`,
      [connectionId],
    ),
```

and add to the returned object, after `history`:

```ts
    images: Number(historyRow?.container_images ?? 0),
```

`app/src/lib/connections/api.ts`. Add to the client `ConnectionDeleteImpact` interface, after `history`:

```ts
  /** Container images pulled with this credential (C-3). */
  images: number;
```

`app/src/components/connections/ConnectionsPage.tsx`. In the delete dialog, directly after the `deleteImpact.data.reference_items.map(...)` block, add:

```tsx
                  {deleteImpact.data.images > 0 && (
                    <p className="text-destructive">
                      {deleteImpact.data.images} container image(s) pull with
                      this credential. They stay in the image registry, but no
                      process can deploy them until they are added again with a
                      live credential.
                    </p>
                  )}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/api-images-internal.test.ts src/__tests__/connections-deletion.test.ts src/__tests__/api-connections.test.ts src/__tests__/connections-page.test.tsx`
Expected: PASS. `api-connections.test.ts` mocks the deletion module wholesale, so its `IMPACT` constant is unaffected.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green.

```bash
git add app/src/pages/api/processes/image-policy.ts app/src/pages/api/internal/images/approved.ts app/src/lib/connections/deletion.ts app/src/lib/connections/api.ts app/src/components/connections/ConnectionsPage.tsx app/src/__tests__/api-images-internal.test.ts app/src/__tests__/connections-deletion.test.ts
git commit -m "feat(images): policy read route, internal approved probe, images in the connection delete impact (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: Client layer — types, API client, query keys, hooks

**Files:**
- Create: `app/src/lib/images/types.ts`, `app/src/lib/images/api.ts`, `app/src/lib/images/queries.ts`
- Modify: `app/src/lib/query/keys.ts` (add `imageKeys`)
- Test: `app/src/__tests__/images-client.test.ts`

**Interfaces:**
- Consumes: the Task 2 API types (type-only), `ImageAdd` (Task 1), `ImageStatus`, `ImagePolicy` (type-only).
- Produces:
  - types: `Image`, `ImageScan`, `ImageUser`, `ImageRegistryConnection`, `PublicImagePolicy` (from `@/lib/images/types`)
  - `ImageApiError` (`.status`, `.code`)
  - `ImageListQuery = { status?: ImageStatus; q?: string; in_use?: boolean }`, `ImageList = { images: Image[]; scan_window_days: number | null }`, `ImageDetail = { image; scans; in_use_by: ImageUser[]; in_use_elsewhere: number }`, `AddedImage`, `ImageScanPoll = { scan: ImageScan; image: Image | null }`, `RescanRequested`
  - `imageListSearch(filters): string`, `listImages(filters?)`, `getImage(id)`, `addImage(input)`, `getImageScan(imageId, scanId)`, `requestRescan(id)`, `getImagePolicy()`
  - `imageKeys.{all, list(filters), detail(id), scan(imageId, scanId), policy()}`
  - hooks: `useImages(filters?)`, `useImage(id | null)`, `useImageScan(imageId | null, scanId | null)`, `useImagePolicy()`, `useAddImage()`, `useRescanImage()`
  - `isScanTerminal(status)`, `hasScanInFlight(images)`

- [ ] **Step 1: Write the failing test** — `app/src/__tests__/images-client.test.ts`

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ImageApiError,
  addImage,
  getImagePolicy,
  getImageScan,
  imageListSearch,
  listImages,
  requestRescan,
} from "@/lib/images/api";
import { hasScanInFlight, isScanTerminal } from "@/lib/images/queries";
import { imageKeys } from "@/lib/query/keys";
import type { Image } from "@/lib/images/types";

const fetchMock = vi.fn();

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("images client", () => {
  it("builds the list query string from the filters it was given", async () => {
    expect(imageListSearch({})).toBe("");
    expect(imageListSearch({ status: "flagged", q: "satpy", in_use: true })).toBe(
      "?status=flagged&q=satpy&in_use=true",
    );
    fetchMock.mockResolvedValue(reply(200, { images: [], scan_window_days: 30 }));
    expect(await listImages({ in_use: false })).toEqual({ images: [], scan_window_days: 30 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/images?in_use=false");
  });

  it("POSTs an add and returns the 202 body", async () => {
    const body = {
      id: "img-1",
      image_id: "img-1",
      scan_id: "scan-1",
      deduplicated: false,
      reference: "docker.io/library/python",
      tag: "latest",
    };
    fetchMock.mockResolvedValue(reply(202, body));
    expect(await addImage({ reference: "python" })).toEqual(body);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/images");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ reference: "python" });
    expect(init.credentials).toBe("same-origin");
  });

  it("surfaces the server's error and code", async () => {
    fetchMock.mockResolvedValue(
      reply(422, { error: "quay.io is not an allowed registry", code: "registry_not_allowed" }),
    );
    const err = await addImage({ reference: "quay.io/x/y" }).catch((e) => e);
    expect(err).toBeInstanceOf(ImageApiError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("registry_not_allowed");
    expect(err.message).toBe("quay.io is not an allowed registry");
  });

  it("encodes ids into the scan-poll, rescan and policy paths", async () => {
    fetchMock.mockResolvedValue(reply(200, {}));
    await getImageScan("a/b", "c d");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/images/a%2Fb/scans/c%20d");
    fetchMock.mockResolvedValue(reply(202, {}));
    await requestRescan("img-1");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/images/img-1/rescan");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
    fetchMock.mockResolvedValue(reply(200, {}));
    await getImagePolicy();
    expect(fetchMock.mock.calls[2][0]).toBe("/api/processes/image-policy");
  });
});

describe("image query helpers and keys", () => {
  it("stops polling at a terminal scan status", () => {
    expect(isScanTerminal("done")).toBe(true);
    expect(isScanTerminal("failed")).toBe(true);
    expect(isScanTerminal("pending")).toBe(false);
    expect(isScanTerminal("running")).toBe(false);
    expect(isScanTerminal(undefined)).toBe(false);
  });

  it("keeps the list polling while any image is waiting for or in a scan", () => {
    const row = (status: Image["status"]) => ({ status }) as Image;
    expect(hasScanInFlight([row("approved"), row("pending")])).toBe(true);
    expect(hasScanInFlight([row("approved"), row("scanning")])).toBe(true);
    expect(hasScanInFlight([row("approved"), row("rejected")])).toBe(false);
    expect(hasScanInFlight(undefined)).toBe(false);
  });

  it("keys every image query under one prefix, catalog-agnostic", () => {
    expect(imageKeys.all()).toEqual(["images"]);
    expect(imageKeys.list({ status: "flagged" })).toEqual(["images", "list", { status: "flagged" }]);
    expect(imageKeys.detail("img-1")).toEqual(["images", "detail", "img-1"]);
    expect(imageKeys.scan("img-1", "scan-1")).toEqual(["images", "scan", "img-1", "scan-1"]);
    expect(imageKeys.policy()).toEqual(["images", "policy"]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/images-client.test.ts`
Expected: FAIL (the modules do not exist).

- [ ] **Step 3: Write the implementation**

`app/src/lib/images/types.ts`:

```ts
/**
 * Client-facing image types (C-3). Type-only re-exports from the server
 * storage module, so nothing from `storage.ts` (the pg client) reaches the
 * browser bundle: the connections/processes `types.ts` pattern. A registry
 * connection appears only as `{id, name, group_id, deleted}`, never with its
 * credentials.
 */
import type { ImagePolicy } from "./policy";

export type {
  ApiImage as Image,
  ApiImageScan as ImageScan,
  ApiImageUser as ImageUser,
  ApiImageRegistryConnection as ImageRegistryConnection,
} from "./storage";

/** `GET /api/processes/image-policy`: the policy minus the scanner's limits. */
export type PublicImagePolicy = Omit<ImagePolicy, "scan_limits">;
```

`app/src/lib/images/api.ts`:

```ts
/**
 * Client functions for `/api/images` and the image policy (C-3,
 * container-images spec §9). Same-origin JSON. Errors surface the server's
 * `{error, code}` as an `ImageApiError` carrying `.status` and `.code` (the
 * processes/connections `api.ts` contract).
 *
 * Nothing here touches a registry or a scanner. Adding an image writes a
 * request row and returns ids to poll (ADR 0004); the pipeline makes the
 * scan real (C-2).
 */
import type { ImageAdd } from "./schemas";
import type { ImageScanKind, ImageStatus } from "./status";
import type { Image, ImageScan, ImageUser, PublicImagePolicy } from "./types";

export class ImageApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ImageApiError";
    this.status = status;
    this.code = code;
  }
}

async function apiFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      (typeof body.error === "string" && body.error) || `Request failed: ${res.status}`;
    const code = typeof body.code === "string" ? body.code : undefined;
    throw new ImageApiError(message, res.status, code);
  }
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;

export interface ImageListQuery {
  status?: ImageStatus;
  q?: string;
  in_use?: boolean;
}

export interface ImageList {
  images: Image[];
  /** null when the deployment's policy cannot be read (staleness unknown). */
  scan_window_days: number | null;
}

export function imageListSearch(filters: ImageListQuery): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  if (filters.in_use !== undefined) params.set("in_use", String(filters.in_use));
  const search = params.toString();
  return search ? `?${search}` : "";
}

export async function listImages(filters: ImageListQuery = {}): Promise<ImageList> {
  return apiFetch<ImageList>(`/api/images${imageListSearch(filters)}`);
}

export interface ImageDetail {
  image: Image;
  scans: ImageScan[];
  /** Processes in the caller's groups whose current revision uses the image. */
  in_use_by: ImageUser[];
  /** Processes in other groups using it (named only to their own members). */
  in_use_elsewhere: number;
}

export async function getImage(id: string): Promise<ImageDetail> {
  return apiFetch<ImageDetail>(`/api/images/${enc(id)}`);
}

export interface AddedImage {
  id: string;
  image_id: string;
  scan_id: string;
  deduplicated: boolean;
  reference: string;
  tag: string;
}

export async function addImage(input: ImageAdd): Promise<AddedImage> {
  return apiFetch<AddedImage>("/api/images", { method: "POST", body: JSON.stringify(input) });
}

export interface ImageScanPoll {
  scan: ImageScan;
  /** The image the scan belongs to NOW (authoritative after the drain's dedup). */
  image: Image | null;
}

export async function getImageScan(imageId: string, scanId: string): Promise<ImageScanPoll> {
  return apiFetch<ImageScanPoll>(`/api/images/${enc(imageId)}/scans/${enc(scanId)}`);
}

export interface RescanRequested {
  image_id: string;
  scan_id: string;
  kind: ImageScanKind;
}

export async function requestRescan(id: string): Promise<RescanRequested> {
  return apiFetch<RescanRequested>(`/api/images/${enc(id)}/rescan`, { method: "POST" });
}

export async function getImagePolicy(): Promise<PublicImagePolicy> {
  return apiFetch<PublicImagePolicy>("/api/processes/image-policy");
}
```

`app/src/lib/query/keys.ts`. Add the import at the top (beside the existing `StacSearchBody` import):

```ts
import type { ImageListQuery } from "@/lib/images/api";
```

and add, after `processKeys`:

```ts
/** The platform-wide image registry (C-3). Catalog-agnostic like every
 * platform factory; mutations invalidate by the `all()` prefix. */
export const imageKeys = {
  all: () => ["images"] as const,
  list: (filters: ImageListQuery) => [...imageKeys.all(), "list", filters] as const,
  detail: (id: string) => [...imageKeys.all(), "detail", id] as const,
  scan: (imageId: string, scanId: string) =>
    [...imageKeys.all(), "scan", imageId, scanId] as const,
  policy: () => [...imageKeys.all(), "policy"] as const,
} as const;
```

`app/src/lib/images/queries.ts`:

```ts
/**
 * TanStack Query hooks for the image registry (C-3). The app never learns a
 * scan result directly (ADR 0004), so both the scan poll and the list POLL
 * while something is in flight, and stop when nothing is.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { imageKeys } from "@/lib/query/keys";
import {
  addImage,
  getImage,
  getImagePolicy,
  getImageScan,
  listImages,
  requestRescan,
  type ImageListQuery,
} from "./api";
import type { ImageAdd } from "./schemas";
import type { Image, ImageScan } from "./types";

const SCAN_POLL_MS = 2_000;
const LIST_POLL_MS = 10_000;

export function isScanTerminal(status: ImageScan["status"] | undefined): boolean {
  return status === "done" || status === "failed";
}

export function hasScanInFlight(images: readonly Image[] | undefined): boolean {
  return (images ?? []).some((image) => image.status === "pending" || image.status === "scanning");
}

export function useImages(filters: ImageListQuery = {}) {
  return useQuery({
    queryKey: imageKeys.list(filters),
    queryFn: () => listImages(filters),
    refetchInterval: (query) => (hasScanInFlight(query.state.data?.images) ? LIST_POLL_MS : false),
  });
}

export function useImage(id: string | null) {
  return useQuery({
    queryKey: imageKeys.detail(id ?? "none"),
    queryFn: () => getImage(id as string),
    enabled: !!id,
  });
}

/** Poll one scan by the ids the 202 handed out, until it is done or failed. */
export function useImageScan(imageId: string | null, scanId: string | null) {
  return useQuery({
    queryKey: imageKeys.scan(imageId ?? "none", scanId ?? "none"),
    queryFn: () => getImageScan(imageId as string, scanId as string),
    enabled: !!imageId && !!scanId,
    refetchInterval: (query) => (isScanTerminal(query.state.data?.scan.status) ? false : SCAN_POLL_MS),
  });
}

export function useImagePolicy() {
  return useQuery({
    queryKey: imageKeys.policy(),
    queryFn: getImagePolicy,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

function useImageMutation<TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => qc.invalidateQueries({ queryKey: imageKeys.all() }),
  });
}

export function useAddImage() {
  return useImageMutation((input: ImageAdd) => addImage(input));
}

export function useRescanImage() {
  return useImageMutation((id: string) => requestRescan(id));
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/images-client.test.ts`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green. The build must not pull `pg` into the client bundle. `types.ts` is type-only and `api.ts` imports only types.

```bash
git add app/src/lib/images/types.ts app/src/lib/images/api.ts app/src/lib/images/queries.ts app/src/lib/query/keys.ts app/src/__tests__/images-client.test.ts
git commit -m "feat(images): client API, query keys and hooks for the image registry (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Image components — format helpers, picker options, status badge, severity stack, Add-image dialog

**Files:**
- Create: `app/src/components/images/format.ts`, `app/src/components/images/picker.ts`, `app/src/components/images/ImageStatusBadge.tsx`, `app/src/components/images/SeverityStack.tsx`, `app/src/components/images/AddImageDialog.tsx`
- Test: `app/src/__tests__/images-components.test.tsx`

**Interfaces:**
- Consumes: Task 1 (`normalizeImageInput`, `readVerdict`, `ImageVerdict`), Task 7 (`useAddImage`, `useImageScan`, `useImagePolicy`, types), `useConnections` (`@/lib/connections/queries`), `IMAGE_STATUS_LABEL`, `imageScanResultSchema`, `ImageSnapshot`.
- Produces:
  - `format.ts`: `shortDigest(digest)`, `configWarning(config)`, `type Finding`, `sortFindings(findings)`, `latestScanResult(scans)`, `scanError(scan)`, `dbAgeDays(dbBuiltAt, now)`
  - `picker.ts`: `type ImageOption = { id; label; snapshot: ImageSnapshot | null; disabledReason: string | null }`, `imageUsableReason(image, groupId): string | null`, `imagePickerOptions(images, groupId, search?): ImageOption[]`
  - `<ImageStatusBadge status stale? credentialDeleted? />`
  - `<SeverityStack verdict />`
  - `<AddImageDialog open onOpenChange groupId? onUse? />`. `groupId` limits the credential list to that group's `registry` connections. `onUse(image)` is offered once the image is approved.

- [ ] **Step 1: Write the failing test** — `app/src/__tests__/images-components.test.tsx`

```tsx
/**
 * C-3 image components: the pure helpers (digest, config warning, findings
 * order, picker usability) and the Add-image dialog (normalized preview,
 * credential filtering, submit -> live scan status). Query hooks are
 * mocked: what's under test is the render logic.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Image, ImageScan } from "@/lib/images/types";

const { addMutate, scanState } = vi.hoisted(() => ({
  addMutate: vi.fn(),
  scanState: { data: undefined as unknown },
}));

vi.mock("@/lib/images/queries", () => ({
  useAddImage: () => ({ mutateAsync: addMutate, isPending: false }),
  useImageScan: (imageId: string | null) => ({ data: imageId ? scanState.data : undefined }),
  useImagePolicy: () => ({ data: { allowed_registries: ["docker.io", "ghcr.io"] } }),
}));
vi.mock("@/lib/connections/queries", () => ({
  useConnections: () => ({
    data: [
      { id: "c-eo", name: "ghcr robot", protocol: "registry", config: { host: "ghcr.io" }, group_id: "earth-observation" },
      { id: "c-wx", name: "weather hub", protocol: "registry", config: { host: "docker.io" }, group_id: "weather" },
      { id: "c-s3", name: "bucket", protocol: "s3", config: { bucket: "b" }, group_id: "earth-observation" },
    ],
  }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  configWarning,
  dbAgeDays,
  latestScanResult,
  shortDigest,
  sortFindings,
} from "@/components/images/format";
import { imagePickerOptions, imageUsableReason } from "@/components/images/picker";
import { ImageStatusBadge } from "@/components/images/ImageStatusBadge";
import { AddImageDialog } from "@/components/images/AddImageDialog";

const GROUP = "earth-observation";
const DIGEST = "sha256:" + "a".repeat(64);

function image(overrides: Partial<Image> = {}): Image {
  return {
    id: "img-1",
    reference: "ghcr.io/org/satpy-runtime",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    verdict: { pass: true, reasons: [] },
    size_bytes: 1,
    config: { user: "10001" },
    last_scan_id: "s-1",
    last_scanned_at: "2026-09-26T00:00:00.000Z",
    stale: false,
    db_built_at: null,
    added_by: "user-1",
    created_at: "2026-09-20T00:00:00.000Z",
    updated_at: "2026-09-20T00:00:00.000Z",
    exception: null,
    tag_current_digest: null,
    tag_checked_at: null,
    drifted: false,
    registry_connection: null,
    in_use_by: 0,
    ...overrides,
  };
}

const SCAN_DOC = {
  version: 1,
  kind: "admission",
  reference: "ghcr.io/org/satpy-runtime",
  tag: "1.4.2",
  error: null,
  digest: DIGEST,
  platform_digest: "sha256:" + "b".repeat(64),
  platform: { os: "linux", architecture: "amd64" },
  size_bytes: 1,
  config: { user: "", entrypoint: null, cmd: null },
  scanner: { syft: "1.0.0", grype: "0.90.0", db_built_at: "2026-09-20T06:00:00Z" },
  sbom_ref: "scans/i/s/sbom.syft.json",
  findings_ref: "scans/i/s/findings.grype.json",
  counts: { critical: 0, high: 2, medium: 0, low: 0, negligible: 0, unknown: 0 },
  fixed_counts: { critical: 0, high: 1, medium: 0, low: 0, negligible: 0, unknown: 0 },
  kev: ["CVE-2026-2"],
  max_risk: 0.9,
  top: [
    { id: "CVE-2026-1", severity: "high", package: "libxml2", version: "2.12.7", fixed_in: "2.12.9", kev: false, epss: 0.3, risk: 0.9, published_at: "2026-07-01" },
    { id: "CVE-2026-2", severity: "high", package: "openssl", version: "3.0.1", fixed_in: null, kev: true, epss: 0.1, risk: 0.2, published_at: "2026-06-01" },
  ],
  verdict: { pass: false, reasons: ["kev:CVE-2026-2"] },
  diff: null,
};

function scan(overrides: Partial<ImageScan> = {}): ImageScan {
  return {
    id: "s-1",
    image_id: "img-1",
    kind: "admission",
    status: "done",
    requested_by: "user-1",
    requested_at: "2026-09-26T00:00:00.000Z",
    started_at: null,
    finished_at: null,
    result: SCAN_DOC,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

beforeEach(() => {
  addMutate.mockReset();
  scanState.data = undefined;
});

describe("format helpers", () => {
  it("shortens a digest to sha256 plus 12 hex characters", () => {
    expect(shortDigest(DIGEST)).toBe("sha256:aaaaaaaaaaaa");
    expect(shortDigest(null)).toBe("unresolved");
  });

  it("warns when the image's USER is not the platform's uid (spec §3.2)", () => {
    expect(configWarning({ user: "" })).toBe("image declares no USER (runs as root by default); runs as 10001");
    expect(configWarning({ user: "root" })).toBe("image declares USER root; runs as 10001");
    expect(configWarning({ user: "0:0" })).toBe("image declares USER 0:0; runs as 10001");
    expect(configWarning({ user: "app" })).toMatch(/USER app; runs as 10001, so the files it needs/);
    expect(configWarning({ user: "10001:10001" })).toBeNull();
    expect(configWarning(null)).toBeNull();
  });

  it("orders findings KEV first, then by risk", () => {
    expect(sortFindings(SCAN_DOC.top as never).map((f) => f.id)).toEqual(["CVE-2026-2", "CVE-2026-1"]);
  });

  it("reads the latest DONE scan's §6.4 document and skips failed or unreadable ones", () => {
    const result = latestScanResult([
      scan({ id: "s-3", status: "pending", result: null }),
      scan({ id: "s-2", status: "failed", result: { error: "pull denied" } }),
      scan({ id: "s-1" }),
    ]);
    expect(result?.top).toHaveLength(2);
    expect(latestScanResult([scan({ result: { nonsense: true } })])).toBeNull();
  });

  it("computes the scanner DB's age in whole days", () => {
    expect(dbAgeDays("2026-09-18T06:00:00Z", new Date("2026-09-27T12:00:00Z"))).toBe(9);
    expect(dbAgeDays(null, new Date())).toBeNull();
  });
});

describe("picker options", () => {
  it("offers only approved, fresh images this group may use", () => {
    expect(imageUsableReason(image(), GROUP)).toBeNull();
    expect(imageUsableReason(image({ status: "flagged" }), GROUP)).toBe("Flagged");
    expect(imageUsableReason(image({ status: "pending", digest: null }), GROUP)).toBe("Waiting for scan");
    expect(imageUsableReason(image({ stale: true }), GROUP)).toBe("Stale: rescan before deploying");
    expect(imageUsableReason(image({ stale: null }), GROUP)).toBe("Image policy unavailable");
    expect(
      imageUsableReason(
        image({ registry_connection: { id: "c", name: "robot", group_id: "weather", deleted: false } }),
        GROUP,
      ),
    ).toBe("Pulled with another group's registry credential");
    expect(
      imageUsableReason(
        image({ registry_connection: { id: "c", name: "robot", group_id: GROUP, deleted: true } }),
        GROUP,
      ),
    ).toBe("Registry credential was deleted");
  });

  it("hides revoked images, filters by search, and lists usable ones first", () => {
    const options = imagePickerOptions(
      [
        image({ id: "a", reference: "ghcr.io/org/zeta", status: "flagged" }),
        image({ id: "b", reference: "ghcr.io/org/alpha" }),
        image({ id: "c", reference: "ghcr.io/org/gone", status: "revoked" }),
      ],
      GROUP,
    );
    expect(options.map((o) => o.id)).toEqual(["b", "a"]);
    expect(options[0].snapshot).toEqual({ id: "b", reference: "ghcr.io/org/alpha", digest: DIGEST });
    expect(options[1].snapshot).toBeNull();
    expect(imagePickerOptions([image({ id: "b", reference: "ghcr.io/org/alpha" })], GROUP, "ZETA")).toEqual([]);
    expect(imagePickerOptions([image({ tag_at_add: "V2" })], GROUP, "v2")).toHaveLength(1);
  });
});

describe("ImageStatusBadge", () => {
  it("labels from IMAGE_STATUS_LABEL and adds stale and credential-deleted marks", () => {
    render(<ImageStatusBadge status="scan_failed" stale credentialDeleted />);
    expect(screen.getByText("Scan failed")).toBeInTheDocument();
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("Credential deleted")).toBeInTheDocument();
  });
});

describe("AddImageDialog", () => {
  function open(props: Partial<Parameters<typeof AddImageDialog>[0]> = {}) {
    render(<AddImageDialog open onOpenChange={() => {}} {...props} />);
  }

  it("previews the stored form of what is typed", () => {
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "python:3.12-slim" } });
    expect(screen.getByText("docker.io/library/python:3.12-slim")).toBeInTheDocument();
    expect(screen.getByText(/Allowed registries: docker\.io, ghcr\.io/)).toBeInTheDocument();
  });

  it("explains a reference it cannot store and disables submit", () => {
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), {
      target: { value: "python@sha256:" + "a".repeat(64) },
    });
    expect(screen.getByText(/by tag, not by digest/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add and scan/ })).toBeDisabled();
  });

  it("lists only registry credentials, only the given group's", () => {
    open({ groupId: GROUP });
    const select = screen.getByLabelText("Registry credential") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "None: a public image",
      "ghcr robot (ghcr.io)",
    ]);
  });

  it("submits, then shows the scan waiting when no scanner has picked it up", async () => {
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = {
      scan: scan({ status: "pending", result: null }),
      image: image({ status: "pending", digest: null, verdict: null }),
    };
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "python" } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    await waitFor(() =>
      expect(addMutate).toHaveBeenCalledWith({
        reference: "python",
        tag: undefined,
        registry_connection_id: null,
      }),
    );
    expect(await screen.findByText(/Queued for scanning/)).toBeInTheDocument();
    expect(screen.getByText("Waiting for scan")).toBeInTheDocument();
  });

  it("shows the policy reasons verbatim when the scan rejects the image", async () => {
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = {
      scan: scan(),
      image: image({ status: "rejected", verdict: { pass: false, reasons: ["kev:CVE-2026-2"] } }),
    };
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "ghcr.io/org/img" } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    expect(await screen.findByText("kev:CVE-2026-2")).toBeInTheDocument();
    expect(screen.getByText(/ask an admin for an exception/)).toBeInTheDocument();
  });

  it("offers the approved image back to the caller", async () => {
    const onUse = vi.fn();
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = { scan: scan(), image: image() };
    open({ onUse });
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "ghcr.io/org/img" } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Use this image" }));
    expect(onUse).toHaveBeenCalledWith(expect.objectContaining({ id: "img-1", digest: DIGEST }));
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/images-components.test.tsx`
Expected: FAIL (the component modules do not exist).

- [ ] **Step 3: Write the implementation**

`app/src/components/images/format.ts`:

```ts
/**
 * Presentation helpers for images (C-3, container-images spec §9.2). Pure,
 * so the dashboard, the detail sheet and the deploy form agree.
 */
import { imageScanResultSchema, type ImageScanResult } from "@/lib/images/scan-result";
import type { ImageScan } from "@/lib/images/types";

/** "sha256:" + 12 hex characters: enough to tell digests apart (the gate's
 * own message uses the same cut). */
export function shortDigest(digest: string | null | undefined): string {
  return digest ? digest.slice(0, 19) : "unresolved";
}

/**
 * Spec §3.2: runs on a user image are forced to uid 10001 whatever the
 * image's USER says. The scan records the image config, and the dashboard
 * warns so an author hears it before a run fails on an unreadable file.
 */
export function configWarning(config: Record<string, unknown> | null | undefined): string | null {
  if (!config) return null;
  const user = typeof config.user === "string" ? config.user.trim() : "";
  if (user === "") return "image declares no USER (runs as root by default); runs as 10001";
  const name = user.split(":")[0];
  if (name === "root" || name === "0") return `image declares USER ${user}; runs as 10001`;
  if (name !== "10001") {
    return `image declares USER ${user}; runs as 10001, so the files it needs must be readable by that uid`;
  }
  return null;
}

export type Finding = NonNullable<ImageScanResult["top"]>[number];

/** Spec §9.2: risk-sorted, KEV first. */
export function sortFindings(findings: readonly Finding[]): Finding[] {
  return [...findings].sort(
    (a, b) => Number(b.kev) - Number(a.kev) || b.risk - a.risk || a.id.localeCompare(b.id),
  );
}

/** The newest DONE scan whose `result` reads as the §6.4 document (C-2
 * stores it with `verdict`/`diff` beside it; the lenient reader drops
 * those). `scans` is newest first, as the detail route returns it. */
export function latestScanResult(scans: readonly ImageScan[]): ImageScanResult | null {
  for (const scan of scans) {
    if (scan.status !== "done" || !scan.result) continue;
    const parsed = imageScanResultSchema.safeParse(scan.result);
    if (parsed.success && parsed.data.error === null) return parsed.data;
  }
  return null;
}

/** A failed scan's message (`result.error`, spec §6.3 step 5). */
export function scanError(scan: ImageScan | null | undefined): string | null {
  const error = scan?.result?.error;
  return typeof error === "string" && error.length > 0 ? error : null;
}

/** "DB 9 days old" (spec §6.1): the baked Grype DB's age at the last scan. */
export function dbAgeDays(dbBuiltAt: string | null | undefined, now: Date): number | null {
  if (!dbBuiltAt) return null;
  const built = Date.parse(dbBuiltAt);
  if (!Number.isFinite(built)) return null;
  return Math.max(0, Math.floor((now.getTime() - built) / 86_400_000));
}
```

`app/src/components/images/picker.ts`:

```ts
/**
 * The deploy form's image picker options (C-3, container-images spec §9.3):
 * every non-revoked image, with only the ones this process's GROUP may
 * deploy selectable. The rest are listed disabled with the reason. The
 * rules mirror the deploy gate (`lib/images/gate.ts`), which remains the
 * authority. This only saves a round trip to a 422.
 */
import type { ImageSnapshot } from "@/lib/images/reference";
import { IMAGE_STATUS_LABEL } from "@/lib/images/status";
import type { Image } from "@/lib/images/types";
import { shortDigest } from "./format";

export interface ImageOption {
  id: string;
  label: string;
  /** Present only when the option is selectable. */
  snapshot: ImageSnapshot | null;
  disabledReason: string | null;
}

export function imageUsableReason(image: Image, groupId: string): string | null {
  if (image.status !== "approved") return IMAGE_STATUS_LABEL[image.status];
  if (image.stale === null) return "Image policy unavailable";
  if (image.stale) return "Stale: rescan before deploying";
  if (image.digest === null) return "Digest not resolved";
  const connection = image.registry_connection;
  if (connection) {
    if (connection.deleted) return "Registry credential was deleted";
    if (connection.group_id !== groupId) return "Pulled with another group's registry credential";
  }
  return null;
}

export function imagePickerOptions(
  images: readonly Image[],
  groupId: string,
  search = "",
): ImageOption[] {
  const needle = search.trim().toLowerCase();
  return images
    .filter((image) => image.status !== "revoked")
    .filter(
      (image) => !needle || `${image.reference}:${image.tag_at_add}`.toLowerCase().includes(needle),
    )
    .map((image) => {
      const reason = imageUsableReason(image, groupId);
      return {
        id: image.id,
        label: `${image.reference}:${image.tag_at_add} · ${shortDigest(image.digest)}`,
        snapshot:
          reason === null && image.digest !== null
            ? { id: image.id, reference: image.reference, digest: image.digest }
            : null,
        disabledReason: reason,
      };
    })
    .sort(
      (a, b) =>
        Number(a.disabledReason !== null) - Number(b.disabledReason !== null) ||
        a.label.localeCompare(b.label),
    );
}
```

`app/src/components/images/ImageStatusBadge.tsx`:

```tsx
/**
 * An image's status (C-3). Labels come from `IMAGE_STATUS_LABEL`, which the
 * `image-status.json` fixture test holds to full coverage. The variant map
 * is a `Record<ImageStatus, …>`, so a new status cannot land unstyled
 * either. Stale and a deleted credential are not statuses; they are marks
 * beside one.
 */
import { Badge } from "@stac-higher/shared";
import { IMAGE_STATUS_LABEL, type ImageStatus } from "@/lib/images/status";

const VARIANT: Record<ImageStatus, "default" | "secondary" | "destructive" | "outline"> = {
  pending: "secondary",
  scanning: "secondary",
  approved: "default",
  rejected: "destructive",
  flagged: "outline",
  revoked: "destructive",
  scan_failed: "destructive",
};

export function ImageStatusBadge({
  status,
  stale = false,
  credentialDeleted = false,
}: {
  status: ImageStatus;
  stale?: boolean;
  credentialDeleted?: boolean;
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid="image-status">
      <Badge
        variant={VARIANT[status]}
        className={status === "flagged" ? "border-warning-border text-warning" : undefined}
      >
        {IMAGE_STATUS_LABEL[status]}
      </Badge>
      {stale && (
        <Badge variant="outline" className="border-warning-border text-warning">
          Stale
        </Badge>
      )}
      {credentialDeleted && <Badge variant="destructive">Credential deleted</Badge>}
    </span>
  );
}
```

`app/src/components/images/SeverityStack.tsx`:

```tsx
/**
 * Severity counts as a compact stack with KEV called out (spec §9.2), read
 * from the stored verdict. "—" until a scan has produced one.
 */
import { Badge } from "@stac-higher/shared";
import type { ImageVerdict } from "@/lib/images/verdict";

const SEVERITIES = [
  ["critical", "C"],
  ["high", "H"],
  ["medium", "M"],
  ["low", "L"],
] as const;

export function SeverityStack({ verdict }: { verdict: ImageVerdict | null }) {
  if (!verdict?.counts) return <span className="text-muted-foreground">—</span>;
  const counts = verdict.counts;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-xs tabular-nums">
      {verdict.kev.length > 0 && <Badge variant="destructive">KEV {verdict.kev.length}</Badge>}
      {SEVERITIES.map(([key, short]) => {
        const n = counts[key] ?? 0;
        return (
          <span
            key={key}
            title={key}
            className={n > 0 && key === "critical" ? "font-semibold text-danger" : undefined}
          >
            {short} {n}
          </span>
        );
      })}
    </span>
  );
}
```

`app/src/components/images/AddImageDialog.tsx`:

```tsx
/**
 * "Add image" (C-3, container-images spec §9.2/§9.3): the same dialog on
 * the /images dashboard and in the deploy form's picker.
 *
 * reference (+ optional tag, + optional registry credential) -> 202 ->
 * live status by polling the scan, until `approved` (selectable: "Use this
 * image") or `rejected` (the policy reasons verbatim, and "ask an admin for
 * an exception"). The typed reference is previewed in its stored form using
 * the same normalizer the route runs. Without a scanner (C-2 not deployed)
 * the image stays "Waiting for scan" and the dialog says it can be closed.
 */
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, Input, Label, LoadingState } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useConnections } from "@/lib/connections/queries";
import { normalizeImageInput } from "@/lib/images/normalize";
import { useAddImage, useImagePolicy, useImageScan } from "@/lib/images/queries";
import type { Image } from "@/lib/images/types";
import { readVerdict } from "@/lib/images/verdict";
import { ImageStatusBadge } from "./ImageStatusBadge";
import { scanError, shortDigest } from "./format";

const formSchema = z.object({
  reference: z.string().trim().min(1, "Enter an image reference"),
  tag: z.string(),
  registry_connection_id: z.string(),
});
type FormValues = z.infer<typeof formSchema>;

export function AddImageDialog({
  open,
  onOpenChange,
  groupId,
  onUse,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Limit the credential list to this group's registry connections (the
   * deploy form passes the process's group). */
  groupId?: string;
  /** Offered once the image is approved: the deploy form selects it. */
  onUse?: (image: Image) => void;
}) {
  const { data: connections } = useConnections();
  const { data: policy } = useImagePolicy();
  const addMutation = useAddImage();
  const [pending, setPending] = useState<{ imageId: string; scanId: string } | null>(null);
  const { data: polled } = useImageScan(pending?.imageId ?? null, pending?.scanId ?? null);

  const form = useForm<FormValues>({
    // Zod v4 inference vs zodResolver: the repo's known cast (project-conventions).
    resolver: zodResolver(formSchema) as any,
    defaultValues: { reference: "", tag: "", registry_connection_id: "" },
  });
  const reference = form.watch("reference");
  const tag = form.watch("tag");
  const preview = reference.trim() ? normalizeImageInput(reference, tag || null) : null;
  const registryConnections = (connections ?? []).filter(
    (c) => c.protocol === "registry" && (!groupId || c.group_id === groupId),
  );

  const submit = form.handleSubmit(async (values) => {
    try {
      const added = await addMutation.mutateAsync({
        reference: values.reference,
        tag: values.tag.trim() || undefined,
        registry_connection_id: values.registry_connection_id || null,
      });
      setPending({ imageId: added.image_id, scanId: added.scan_id });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not add the image");
    }
  });

  const image = polled?.image ?? null;
  const scan = polled?.scan ?? null;
  const reasons = readVerdict(image?.verdict)?.reasons ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add image</DialogTitle>
          <DialogDescription>
            The image is scanned (SBOM and vulnerabilities) before anything can
            run it, and only the digest the scan pins ever runs.
          </DialogDescription>
        </DialogHeader>

        {pending === null ? (
          <form onSubmit={submit} className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="image-reference">Image reference</Label>
              <Input
                id="image-reference"
                placeholder="ghcr.io/org/tool:1.2 or python:3.12-slim"
                autoComplete="off"
                {...form.register("reference")}
              />
              {form.formState.errors.reference && (
                <p className="text-xs text-destructive">{form.formState.errors.reference.message}</p>
              )}
              {preview &&
                (preview.ok ? (
                  <p className="text-xs text-muted-foreground">
                    Stored as <code className="tech">{`${preview.reference}:${preview.tag}`}</code>
                  </p>
                ) : (
                  <p className="text-xs text-destructive">{preview.error}</p>
                ))}
              {policy && (
                <p className="text-xs text-muted-foreground">
                  Allowed registries: {policy.allowed_registries.join(", ")}
                </p>
              )}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="image-tag">Tag (optional)</Label>
              <Input id="image-tag" placeholder="latest" autoComplete="off" {...form.register("tag")} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="registry-connection">Registry credential</Label>
              <select
                id="registry-connection"
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                {...form.register("registry_connection_id")}
              >
                <option value="">None: a public image</option>
                {registryConnections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {`${c.name} (${String(c.config.host ?? "")})`}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                An image pulled with a group&apos;s credential can be used only by
                that group&apos;s processes.
              </p>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={addMutation.isPending || (preview !== null && !preview.ok)}
              >
                {addMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Add and scan
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <div className="grid gap-3 text-sm" data-testid="add-image-progress">
            {image ? (
              <div className="flex flex-wrap items-center gap-2">
                <code className="tech">{`${image.reference}:${image.tag_at_add}`}</code>
                <ImageStatusBadge status={image.status} />
              </div>
            ) : (
              <LoadingState message="Waiting for the scan request…" />
            )}
            {(image?.status === "pending" || image?.status === "scanning") && (
              <p className="text-muted-foreground">
                Queued for scanning{scan?.status === "running" ? " (running now)" : ""}. You can
                close this dialog: the image stays on the Images page and its scan continues.
              </p>
            )}
            {image?.status === "approved" && (
              <p>
                Approved: digest <code className="tech">{shortDigest(image.digest)}</code> passed
                this deployment&apos;s policy.
              </p>
            )}
            {image?.status === "rejected" && (
              <div className="grid gap-1.5">
                <p>The scan found issues this deployment&apos;s policy blocks:</p>
                <ul className="list-disc pl-5">
                  {reasons.map((reason) => (
                    <li key={reason}>
                      <code className="tech">{reason}</code>
                    </li>
                  ))}
                </ul>
                <p className="text-muted-foreground">
                  Fix the image and add it again, or ask an admin for an exception.
                </p>
              </div>
            )}
            {image?.status === "scan_failed" && (
              <p className="text-destructive">
                The scan failed: {scanError(scan) ?? "no message recorded"}. Rescan it from the
                Images page.
              </p>
            )}
            <DialogFooter>
              {image?.status === "approved" && onUse && (
                <Button onClick={() => onUse(image)}>Use this image</Button>
              )}
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/images-components.test.tsx`
Expected: PASS. If `getByText("docker.io/library/python:3.12-slim")` fails because the text is split across nodes, check that the `<code>` renders ONE template string (as written), not `{reference}:{tag}` as three text nodes.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green.

```bash
git add app/src/components/images app/src/__tests__/images-components.test.tsx
git commit -m "feat(images): status badge, severity stack, picker options and the Add-image dialog (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: The `/images` dashboard — page, island, detail sheet, nav entry

**Files:**
- Create: `app/src/pages/images.astro`, `app/src/components/images/ImagesPage.tsx`, `app/src/components/images/ImageDetailSheet.tsx`
- Modify: `app/src/components/layout/SidebarNav.tsx` (one `OPERATE` entry and one icon import)
- Test: `app/src/__tests__/images-page.test.tsx`

**Interfaces:**
- Consumes: Task 7 hooks (`useImages`, `useImage`, `useRescanImage`), Task 8 components and helpers, `readVerdict`, `IMAGE_STATUSES`, `IMAGE_STATUS_LABEL`, `useAuthMe`, `timeAgo` (`@/components/monitoring/shared`), app `Table*` and `Sheet*` primitives.
- Produces: `ImagesPage` (the island), `ImageDetailSheet({ imageId: string | null; onClose: () => void; canOperate: boolean })`, the `/images` route and the "Images" nav item.

Spec §9.2 lists these columns: reference + tag, short digest with copy, status badge, severity stack with KEV, last scanned with DB age, in-use count, exception expiry, drift marker. The filters are status, in use and search. The detail sheet shows top findings, the reasons verbatim, scan history, the config warning, and Rescan now for operators. Scan-history DIFFS and the exception FORM are C-4. C-3 lists the history and shows a live exception read-only.

- [ ] **Step 1: Write the failing test** — `app/src/__tests__/images-page.test.tsx`

```tsx
/**
 * /images island (C-3, container-images spec §9.2). Query hooks are mocked;
 * what's under test is the render logic, the operator gating, the filters
 * and the detail sheet's warnings.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { Image, ImageScan } from "@/lib/images/types";

const { useImagesMock, useImageMock, rescanMutate, roles } = vi.hoisted(() => ({
  useImagesMock: vi.fn(),
  useImageMock: vi.fn(),
  rescanMutate: vi.fn(),
  roles: { value: ["operator"] as string[] },
}));

vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => <QueryProvider>{children}</QueryProvider>,
  };
});
vi.mock("@/lib/query/auth", () => ({
  useAuthMe: () => ({
    data: {
      authenticated: true,
      mode: "bypass",
      identity: { sub: "u1", groups: ["earth-observation"], roles: roles.value },
    },
  }),
}));
vi.mock("@/lib/images/queries", () => ({
  useImages: (filters: unknown) => useImagesMock(filters),
  useImage: (id: string | null) => useImageMock(id),
  useRescanImage: () => ({ mutateAsync: rescanMutate, isPending: false }),
  useAddImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useImageScan: () => ({ data: undefined }),
  useImagePolicy: () => ({ data: undefined }),
}));
vi.mock("@/lib/connections/queries", () => ({ useConnections: () => ({ data: [] }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ImagesPage } from "@/components/images/ImagesPage";

const DIGEST = "sha256:" + "a".repeat(64);

function image(overrides: Partial<Image> = {}): Image {
  return {
    id: "img-1",
    reference: "ghcr.io/org/satpy-runtime",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    verdict: { pass: true, reasons: [], counts: { critical: 0, high: 3, medium: 1, low: 0 }, kev: [] },
    size_bytes: 1,
    config: { user: "10001" },
    last_scan_id: "s-1",
    last_scanned_at: new Date().toISOString(),
    stale: false,
    db_built_at: null,
    added_by: "user-1",
    created_at: "2026-09-20T00:00:00.000Z",
    updated_at: "2026-09-20T00:00:00.000Z",
    exception: null,
    tag_current_digest: null,
    tag_checked_at: null,
    drifted: false,
    registry_connection: null,
    in_use_by: 2,
    ...overrides,
  };
}

function scan(overrides: Partial<ImageScan> = {}): ImageScan {
  return {
    id: "s-1",
    image_id: "img-1",
    kind: "admission",
    status: "done",
    requested_by: "user-1",
    requested_at: "2026-09-26T00:00:00.000Z",
    started_at: null,
    finished_at: "2026-09-26T00:05:00.000Z",
    result: null,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

function list(images: Image[], scanWindowDays: number | null = 30) {
  useImagesMock.mockReturnValue({
    data: { images, scan_window_days: scanWindowDays },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
}

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as never);
});

beforeEach(() => {
  useImagesMock.mockReset();
  useImageMock.mockReset();
  useImageMock.mockReturnValue({ data: undefined, isLoading: false, error: null });
  rescanMutate.mockReset();
  roles.value = ["operator"];
});

describe("ImagesPage", () => {
  it("shows the empty state with Add image for an operator", () => {
    list([]);
    render(<ImagesPage />);
    expect(screen.getByRole("heading", { name: "Images", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("No images yet")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add image" }).length).toBeGreaterThan(0);
  });

  it("renders read-only for a member", () => {
    roles.value = ["member"];
    list([]);
    render(<ImagesPage />);
    expect(screen.queryByRole("button", { name: "Add image" })).toBeNull();
  });

  it("renders one row per image with its status, marks, counts and usage", () => {
    list([
      image(),
      image({
        id: "img-2",
        reference: "docker.io/library/python",
        tag_at_add: "3.12-slim",
        status: "flagged",
        stale: true,
        drifted: true,
        in_use_by: 0,
        verdict: { pass: false, reasons: ["kev:CVE-2026-9"], counts: { critical: 1 }, kev: ["CVE-2026-9"] },
        registry_connection: { id: "c-1", name: "hub robot", group_id: "earth-observation", deleted: true },
      }),
    ]);
    render(<ImagesPage />);
    expect(screen.getByText("2 images in the registry")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const flagged = within(rows[1]);
    expect(flagged.getByText("docker.io/library/python:3.12-slim")).toBeInTheDocument();
    expect(flagged.getByText("Flagged")).toBeInTheDocument();
    expect(flagged.getByText("Stale")).toBeInTheDocument();
    expect(flagged.getByText("Credential deleted")).toBeInTheDocument();
    expect(flagged.getByText("KEV 1")).toBeInTheDocument();
    expect(flagged.getByText("tag moved")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Approved")).toBeInTheDocument();
    expect(within(rows[0]).getByText("sha256:aaaaaaaaaaaa")).toBeInTheDocument();
  });

  it("passes the filters to the query", () => {
    list([]);
    render(<ImagesPage />);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "flagged" } });
    fireEvent.click(screen.getByLabelText("In use only"));
    fireEvent.change(screen.getByLabelText("Search images"), { target: { value: "satpy" } });
    expect(useImagesMock).toHaveBeenLastCalledWith({ status: "flagged", q: "satpy", in_use: true });
  });

  it("says why staleness is unknown when the policy cannot be read", () => {
    list([image({ stale: null })], null);
    render(<ImagesPage />);
    expect(screen.getByText(/image policy could not be read/)).toBeInTheDocument();
  });

  it("opens the detail sheet with the credential, config and reason warnings, and Rescan now", async () => {
    const detail = image({
      status: "rejected",
      config: { user: "" },
      verdict: { pass: false, reasons: ["critical_fixed:libxml2"], counts: { critical: 1 }, kev: [] },
      registry_connection: { id: "c-1", name: "ghcr robot", group_id: "earth-observation", deleted: true },
    });
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id
        ? {
            image: detail,
            scans: [scan()],
            in_use_by: [{ process_id: "p-1", name: "geocolor", group_id: "earth-observation" }],
            in_use_elsewhere: 1,
          }
        : undefined,
      isLoading: false,
      error: null,
    }));
    rescanMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-2", kind: "rescan" });
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));

    expect(await screen.findByTestId("credential-deleted")).toHaveTextContent(/ghcr robot/);
    expect(screen.getByTestId("config-warning")).toHaveTextContent(
      "image declares no USER (runs as root by default); runs as 10001",
    );
    expect(screen.getByText("critical_fixed:libxml2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "geocolor" })).toHaveAttribute("href", "/processes/p-1");
    expect(screen.getByText(/1 more in other groups/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Rescan now/ }));
    expect(rescanMutate).toHaveBeenCalledWith("img-1");
  });

  it("hides Rescan now from a member", async () => {
    roles.value = ["member"];
    list([image()]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id ? { image: image(), scans: [], in_use_by: [], in_use_elsewhere: 0 } : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));
    expect(await screen.findByText("Scan history")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Rescan now/ })).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/images-page.test.tsx`
Expected: FAIL (`ImagesPage` does not exist).

- [ ] **Step 3: Write the implementation**

`app/src/pages/images.astro`:

```astro
---
import Layout from "../layouts/Layout.astro";
import { ImagesPage } from "../components/images/ImagesPage";
---

<Layout title="Images">
  <ImagesPage client:only="react" />
</Layout>
```

`app/src/components/images/ImageDetailSheet.tsx`:

```tsx
/**
 * One image, in a sheet over the dashboard (C-3, container-images spec
 * §9.2): what would stop a deploy (credential deleted, stale, the config
 * warning, the policy reasons verbatim), the top findings (KEV first, then
 * risk), the scan history, who uses it, and "Rescan now" for operators.
 * Scan-history diffs and the admin exception form are C-4; a live exception
 * is shown read-only here.
 */
import { Badge, Button, LoadingState } from "@stac-higher/shared";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { timeAgo } from "@/components/monitoring/shared";
import { useImage, useRescanImage } from "@/lib/images/queries";
import { readVerdict } from "@/lib/images/verdict";
import { ImageStatusBadge } from "./ImageStatusBadge";
import { SeverityStack } from "./SeverityStack";
import { configWarning, latestScanResult, scanError, shortDigest, sortFindings } from "./format";

const TOP_FINDINGS = 10;

export function ImageDetailSheet({
  imageId,
  onClose,
  canOperate,
}: {
  imageId: string | null;
  onClose: () => void;
  canOperate: boolean;
}) {
  const { data, isLoading, error } = useImage(imageId);
  const rescan = useRescanImage();
  const image = data?.image ?? null;
  const verdict = readVerdict(image?.verdict);
  const latest = latestScanResult(data?.scans ?? []);
  const findings = sortFindings(latest?.top ?? []).slice(0, TOP_FINDINGS);
  const warning = configWarning(image?.config);

  const requestRescan = async () => {
    if (!image) return;
    try {
      const requested = await rescan.mutateAsync(image.id);
      toast.success(
        requested.kind === "admission" ? "Scan re-requested" : "Rescan requested",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not request a rescan");
    }
  };

  return (
    <Sheet open={imageId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="break-all">
            {image ? `${image.reference}:${image.tag_at_add}` : "Image"}
          </SheetTitle>
          <SheetDescription className="tech break-all">
            {image?.digest ?? "The digest resolves when the scan runs"}
          </SheetDescription>
        </SheetHeader>

        {isLoading && <LoadingState message="Loading image…" />}
        {error && (
          <p className="px-4 text-sm text-destructive">
            {error instanceof Error ? error.message : "Could not load the image"}
          </p>
        )}

        {image && data && (
          <div className="grid gap-5 px-4 pb-6 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <ImageStatusBadge
                status={image.status}
                stale={image.stale === true}
                credentialDeleted={image.registry_connection?.deleted === true}
              />
              <SeverityStack verdict={verdict} />
            </div>

            {image.registry_connection?.deleted && (
              <p className="text-destructive" data-testid="credential-deleted">
                Pulled with the registry credential &quot;{image.registry_connection.name}&quot;,
                which was deleted. No process can deploy this image until it is added again
                with a live credential.
              </p>
            )}
            {image.stale && (
              <p className="text-warning">
                Last scanned {timeAgo(image.last_scanned_at)}, outside the scan window: new
                deploys and launches are refused until a rescan passes.
              </p>
            )}
            {image.drifted && (
              <p>
                Tag <code className="tech">{image.tag_at_add}</code> now points to{" "}
                <code className="tech">{shortDigest(image.tag_current_digest)}</code>. Runs keep
                using the scanned digest; add the reference again to scan the new one.
              </p>
            )}
            {warning && (
              <p className="text-warning" data-testid="config-warning">
                {warning}
              </p>
            )}
            {image.status === "scan_failed" && (
              <p className="text-destructive">
                The last scan failed: {scanError(data.scans[0]) ?? "no message recorded"}.
              </p>
            )}

            {image.exception && (
              <section className="grid gap-1">
                <h3 className="font-semibold">Exception</h3>
                <p>{image.exception.reason}</p>
                <p className="text-muted-foreground">
                  Granted by {image.exception.by}, expires{" "}
                  {new Date(image.exception.expires_at).toLocaleDateString()}
                </p>
              </section>
            )}

            {verdict && verdict.reasons.length > 0 && (
              <section className="grid gap-1">
                <h3 className="font-semibold">Policy reasons</h3>
                <ul className="list-disc pl-5">
                  {verdict.reasons.map((reason) => (
                    <li key={reason}>
                      <code className="tech">{reason}</code>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {findings.length > 0 && (
              <section className="grid gap-1">
                <h3 className="font-semibold">Top findings</h3>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Vulnerability</TableHead>
                      <TableHead>Package</TableHead>
                      <TableHead>Fixed in</TableHead>
                      <TableHead>Risk</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {findings.map((finding) => (
                      <TableRow key={`${finding.id}:${finding.package}`}>
                        <TableCell>
                          <span className="tech">{finding.id}</span>{" "}
                          {finding.kev && <Badge variant="destructive">KEV</Badge>}
                          <span className="block text-xs text-muted-foreground">{finding.severity}</span>
                        </TableCell>
                        <TableCell className="tech">{`${finding.package}@${finding.version}`}</TableCell>
                        <TableCell className="tech">{finding.fixed_in ?? "no fix"}</TableCell>
                        <TableCell className="tabular-nums">{finding.risk.toFixed(2)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>
            )}

            <section className="grid gap-1">
              <h3 className="font-semibold">Scan history</h3>
              {data.scans.length === 0 ? (
                <p className="text-muted-foreground">No scans yet.</p>
              ) : (
                <ul className="grid gap-1">
                  {data.scans.map((s) => (
                    <li key={s.id} className="flex flex-wrap items-center gap-2">
                      <Badge variant={s.status === "failed" ? "destructive" : "secondary"}>
                        {s.status}
                      </Badge>
                      <span>{s.kind}</span>
                      <span className="text-muted-foreground">
                        requested {timeAgo(s.requested_at)}
                        {s.finished_at ? `, finished ${timeAgo(s.finished_at)}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="grid gap-1">
              <h3 className="font-semibold">In use by</h3>
              {data.in_use_by.length === 0 && data.in_use_elsewhere === 0 ? (
                <p className="text-muted-foreground">No process&apos;s current revision uses it.</p>
              ) : (
                <ul className="grid gap-1">
                  {data.in_use_by.map((user) => (
                    <li key={user.process_id}>
                      <a className="text-primary hover:underline" href={`/processes/${user.process_id}`}>
                        {user.name}
                      </a>{" "}
                      <span className="text-muted-foreground">({user.group_id})</span>
                    </li>
                  ))}
                  {data.in_use_elsewhere > 0 && (
                    <li className="text-muted-foreground">
                      {data.in_use_elsewhere} more in other groups
                    </li>
                  )}
                </ul>
              )}
            </section>

            {canOperate && image.status !== "revoked" && (
              <div>
                <Button onClick={requestRescan} disabled={rescan.isPending}>
                  {rescan.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  Rescan now
                </Button>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
```

`app/src/components/images/ImagesPage.tsx`:

```tsx
/**
 * `/images` — the platform-wide image registry (C-3, container-images spec
 * §9.2). Every scanned image is visible to every member (spec decision 7);
 * adding and rescanning are operator verbs, rendered only for them.
 * Metadata only: the platform never stores image bytes.
 */
import { useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  Label,
  LoadingState,
} from "@stac-higher/shared";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AlertTriangle, Boxes, Copy, Plus } from "lucide-react";
import { toast } from "sonner";
import { timeAgo } from "@/components/monitoring/shared";
import { useAuthMe } from "@/lib/query/auth";
import { useImages } from "@/lib/images/queries";
import { IMAGE_STATUSES, IMAGE_STATUS_LABEL, type ImageStatus } from "@/lib/images/status";
import { readVerdict } from "@/lib/images/verdict";
import { AddImageDialog } from "./AddImageDialog";
import { ImageDetailSheet } from "./ImageDetailSheet";
import { ImageStatusBadge } from "./ImageStatusBadge";
import { SeverityStack } from "./SeverityStack";
import { dbAgeDays, shortDigest } from "./format";

function ImagesContent() {
  const { data: me } = useAuthMe();
  const roles = me?.identity?.roles ?? [];
  const canOperate = roles.includes("operator") || roles.includes("admin");

  const [status, setStatus] = useState<ImageStatus | "">("");
  const [inUse, setInUse] = useState(false);
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const filtered = status !== "" || inUse || q.trim() !== "";
  const { data, isLoading, error, refetch } = useImages({
    status: status || undefined,
    q: q.trim() || undefined,
    in_use: inUse ? true : undefined,
  });
  const images = data?.images ?? [];
  const now = new Date();

  const copyDigest = async (digest: string) => {
    try {
      await navigator.clipboard.writeText(digest);
      toast.success("Digest copied");
    } catch {
      toast.error("Could not copy the digest");
    }
  };

  return (
    <div className="container mx-auto space-y-6 px-4 py-8">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Images</h1>
          <p className="text-muted-foreground">
            Container images processes may run on. Every image is scanned before it can be
            deployed, and only the scanned digest ever runs.
          </p>
        </div>
        {canOperate && (
          <Button onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" />
            Add image
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="images-search">Search images</Label>
          <Input
            id="images-search"
            placeholder="reference or tag"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-64"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="images-status">Status</Label>
          <select
            id="images-status"
            className="flex h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
            value={status}
            onChange={(e) => setStatus(e.target.value as ImageStatus | "")}
          >
            <option value="">All statuses</option>
            {IMAGE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {IMAGE_STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            aria-label="In use only"
            checked={inUse}
            onChange={(e) => setInUse(e.target.checked)}
          />
          In use only
        </label>
      </div>

      {data && data.scan_window_days === null && (
        <Card className="border-warning-border bg-warning-subtle">
          <CardContent className="flex items-center gap-3 px-5 py-3 text-sm">
            <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
            The image policy could not be read (PROCESS_IMAGE_POLICY_FILE): staleness is unknown,
            and no image can be added or deployed until it is fixed.
          </CardContent>
        </Card>
      )}

      {isLoading && <LoadingState message="Loading images…" />}
      {error && (
        <ErrorState
          message={error instanceof Error ? error.message : "Failed to load images"}
          onRetry={() => refetch()}
        />
      )}

      {!isLoading && !error && images.length === 0 &&
        (filtered ? (
          <p className="text-sm text-muted-foreground">No image matches these filters.</p>
        ) : (
          <EmptyState
            icon={Boxes}
            title="No images yet"
            description="Add a container image to scan it. Once a scan passes, processes can run on it: as the dependency bundle for your code, or as the process itself."
            action={canOperate ? { label: "Add image", onClick: () => setAdding(true) } : undefined}
          />
        ))}

      {images.length > 0 && (
        <>
          <p className="text-sm text-muted-foreground">
            {`${images.length} ${images.length === 1 ? "image" : "images"} in the registry`}
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Image</TableHead>
                <TableHead>Digest</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Findings</TableHead>
                <TableHead>Last scanned</TableHead>
                <TableHead>In use</TableHead>
                <TableHead>Exception</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {images.map((image) => {
                const dbAge = dbAgeDays(image.db_built_at, now);
                return (
                  <TableRow key={image.id}>
                    <TableCell>
                      <button
                        type="button"
                        className="text-left font-medium hover:underline"
                        onClick={() => setSelected(image.id)}
                      >
                        {`${image.reference}:${image.tag_at_add}`}
                      </button>
                      {image.drifted && (
                        <Badge variant="outline" className="ml-2">
                          tag moved
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1">
                        <code className="tech text-xs">{shortDigest(image.digest)}</code>
                        {image.digest && (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label="Copy digest"
                            onClick={() => copyDigest(image.digest as string)}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </span>
                    </TableCell>
                    <TableCell>
                      <ImageStatusBadge
                        status={image.status}
                        stale={image.stale === true}
                        credentialDeleted={image.registry_connection?.deleted === true}
                      />
                    </TableCell>
                    <TableCell>
                      <SeverityStack verdict={readVerdict(image.verdict)} />
                    </TableCell>
                    <TableCell className="text-sm">
                      {timeAgo(image.last_scanned_at)}
                      {dbAge !== null && (
                        <span className="block text-xs text-muted-foreground">DB {dbAge}d old</span>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">{image.in_use_by}</TableCell>
                    <TableCell className="text-sm">
                      {image.exception
                        ? `until ${new Date(image.exception.expires_at).toLocaleDateString()}`
                        : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </>
      )}

      {adding && <AddImageDialog open onOpenChange={(open) => !open && setAdding(false)} />}
      <ImageDetailSheet imageId={selected} onClose={() => setSelected(null)} canOperate={canOperate} />
    </div>
  );
}

export function ImagesPage() {
  return (
    <AppShell>
      <ImagesContent />
    </AppShell>
  );
}
```

`app/src/components/layout/SidebarNav.tsx`. Add `Boxes` to the lucide import list (alphabetical, before `ChevronDown`):

```ts
import {
  Activity,
  Boxes,
  ChevronDown,
  Cpu,
  Database,
  Layers,
  Map as MapIcon,
  Plug,
  Puzzle,
  Search,
  Share2,
} from "lucide-react";
```

and insert the entry right after Processes in `OPERATE`. Spec §9.2 puts it "in the processes group":

```ts
const OPERATE: NavItem[] = [
  { href: "/", label: "Products", icon: Layers, alsoMatches: ["/collections"] },
  { href: "/map", label: "Map", icon: MapIcon },
  { href: "/processes", label: "Processes", icon: Cpu },
  { href: "/images", label: "Images", icon: Boxes },
  { href: "/connections", label: "Connections", icon: Plug },
  { href: "/graph", label: "Pipeline graph", icon: Share2 },
  { href: "/monitoring", label: "Monitoring", icon: Activity },
];
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/images-page.test.tsx`
Expected: PASS. The "passes the filters" test asserts the LAST call. Each `fireEvent` re-renders, so the final call carries all three filters.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green, and the build emits `/images`.

```bash
git add app/src/pages/images.astro app/src/components/images/ImagesPage.tsx app/src/components/images/ImageDetailSheet.tsx app/src/components/layout/SidebarNav.tsx app/src/__tests__/images-page.test.tsx
git commit -m "feat(images): the /images dashboard, detail sheet and nav entry (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: The deploy form's Runtime chooser, the image picker, and the digest on run rows

**Files:**
- Create: `app/src/lib/processes/command.ts`, `app/src/components/processes/runtime-form.ts`, `app/src/components/images/ImagePicker.tsx`
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` (imports, constants, `CodeCard`, `RunRow`, `RunsCard`, `ProcessDetailContent`)
- Test: `app/src/__tests__/process-runtime-form.test.ts` (new), `app/src/__tests__/process-code-card.test.tsx` (mocks + a new `describe`)

**Interfaces:**
- Consumes: `imageSnapshotSchema`, `ImageSnapshot` (C-1), `PROCESS_RUNTIME_IMAGE_ALIASES`, `MAX_COMMAND_ENTRIES`, `ProcessRuntime`, `ProcessRuntimeKind`, `RuntimeImageAlias`, `ProcessNetwork` (schemas), `imagePickerOptions`, `shortDigest` (Task 8), `AddImageDialog` (Task 8), `useImages` (Task 7).
- Produces:
  - `parseCommand(text: string): { command: string[] | null; error: string | null }`. Empty means `command: null` (the image's own CMD). Whitespace separates entries. `'…'` is literal, `"…"` allows `\"` and `\\`, and a backslash outside quotes escapes the next character.
  - `formatCommand(command: readonly string[]): string` (round-trips through `parseCommand`)
  - `type RuntimeFormState = { kind: ProcessRuntimeKind; runtimeImage: RuntimeImageAlias; image: ImageSnapshot | null; commandText: string }`, `DEFAULT_RUNTIME_FORM`
  - `runtimeFormFromRevision(runtime: Record<string, unknown> | null | undefined): RuntimeFormState`
  - `type RuntimeLimits = { memory_mb: number; timeout_seconds: number; network: ProcessNetwork }`
  - `buildRuntimePayload(form, limits): { ok: true; runtime: ProcessRuntime; carriesCode: boolean } | { ok: false; error: string }`
  - `revisionImageDigest(runtime: Record<string, unknown>): string | null`
  - `<ImagePicker images groupId value onChange onAdd disabled? />`
  - `CodeCard` gains an optional prop `currentRuntime?: Record<string, unknown> | null`.

- [ ] **Step 1: Write the failing tests**

`app/src/__tests__/process-runtime-form.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatCommand, parseCommand } from "@/lib/processes/command";
import {
  DEFAULT_RUNTIME_FORM,
  buildRuntimePayload,
  revisionImageDigest,
  runtimeFormFromRevision,
} from "@/components/processes/runtime-form";
import { processRevisionCreateSchema } from "@/lib/processes/schemas";

const SNAP = {
  id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
  reference: "ghcr.io/org/satpy-runtime",
  digest: "sha256:" + "a".repeat(64),
};
const LIMITS = {
  memory_mb: 512,
  timeout_seconds: 900,
  network: { level: "isolated" as const, hosts: [] },
};

describe("parseCommand / formatCommand (spec §3: command is kind 3's Cmd)", () => {
  it("splits on whitespace and honours quotes and escapes", () => {
    expect(parseCommand("tool --run")).toEqual({ command: ["tool", "--run"], error: null });
    expect(parseCommand(`tool 'a b' "c \\"d\\"" e\\ f`)).toEqual({
      command: ["tool", "a b", 'c "d"', "e f"],
      error: null,
    });
    expect(parseCommand("  ")).toEqual({ command: null, error: null });
  });

  it("refuses what the write gate would refuse", () => {
    expect(parseCommand("tool 'unterminated").error).toMatch(/Unterminated single quote/);
    expect(parseCommand('tool "x').error).toMatch(/Unterminated double quote/);
    expect(parseCommand("tool '' x").error).toMatch(/non-blank/);
    expect(parseCommand(Array.from({ length: 65 }, (_, i) => `a${i}`).join(" ")).error).toMatch(/at most 64/);
  });

  it("round-trips through formatCommand", () => {
    const command = ["tool", "--run", "a b", "it's", "x=1", ""];
    const safe = command.filter((c) => c !== "");
    expect(parseCommand(formatCommand(safe)).command).toEqual(safe);
    expect(formatCommand(["tool", "a b"])).toBe("tool 'a b'");
  });
});

describe("runtimeFormFromRevision (the current revision syncs into the form)", () => {
  it("defaults when nothing is deployed or the runtime is unreadable", () => {
    expect(runtimeFormFromRevision(null)).toEqual(DEFAULT_RUNTIME_FORM);
    expect(runtimeFormFromRevision({ kind: "nonsense" })).toEqual(DEFAULT_RUNTIME_FORM);
  });

  it("keeps a platform alias", () => {
    expect(runtimeFormFromRevision({ kind: "inline_python", runtime_image: "stactools" })).toEqual({
      ...DEFAULT_RUNTIME_FORM,
      runtimeImage: "stactools",
    });
  });

  it("reads a user image snapshot and a container command", () => {
    expect(runtimeFormFromRevision({ kind: "inline_python_on_image", image: SNAP })).toEqual({
      kind: "inline_python_on_image",
      runtimeImage: "default",
      image: SNAP,
      commandText: "",
    });
    expect(
      runtimeFormFromRevision({ kind: "container", image: SNAP, command: ["tool", "a b"] }),
    ).toEqual({ kind: "container", runtimeImage: "default", image: SNAP, commandText: "tool 'a b'" });
  });
});

describe("buildRuntimePayload", () => {
  it("builds each kind in the shape the write gate accepts", () => {
    const inline = buildRuntimePayload({ ...DEFAULT_RUNTIME_FORM, runtimeImage: "stactools" }, LIMITS);
    const onImage = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "inline_python_on_image", image: SNAP },
      LIMITS,
    );
    const container = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "container", image: SNAP, commandText: "tool --run" },
      LIMITS,
    );
    const noCommand = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "container", image: SNAP },
      LIMITS,
    );
    for (const [payload, code] of [
      [inline, "print(1)"],
      [onImage, "print(1)"],
      [container, null],
      [noCommand, null],
    ] as const) {
      if (!payload.ok) throw new Error(payload.error);
      expect(processRevisionCreateSchema.safeParse({ runtime: payload.runtime, code, env: [] }).success).toBe(true);
    }
    expect(inline.ok && inline.runtime).toMatchObject({ kind: "inline_python", image: null, runtime_image: "stactools" });
    expect(onImage.ok && onImage.carriesCode).toBe(true);
    expect(onImage.ok && onImage.runtime).toMatchObject({ kind: "inline_python_on_image", image: SNAP, runtime_image: null });
    expect(container.ok && container.carriesCode).toBe(false);
    expect(container.ok && container.runtime).toMatchObject({ kind: "container", command: ["tool", "--run"] });
    expect(noCommand.ok && noCommand.runtime).toMatchObject({ kind: "container", command: null });
  });

  it("refuses a user-image kind with no image, or a broken command", () => {
    expect(buildRuntimePayload({ ...DEFAULT_RUNTIME_FORM, kind: "container" }, LIMITS)).toEqual({
      ok: false,
      error: "Choose an approved image",
    });
    const broken = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "container", image: SNAP, commandText: "tool 'x" },
      LIMITS,
    );
    expect(broken.ok).toBe(false);
  });
});

describe("revisionImageDigest (run rows)", () => {
  it("reads the pinned digest of a user-image revision only", () => {
    expect(revisionImageDigest({ kind: "container", image: SNAP })).toBe(SNAP.digest);
    expect(revisionImageDigest({ kind: "inline_python", image: null })).toBeNull();
  });
});
```

In `app/src/__tests__/process-code-card.test.tsx`, add a hoisted image list and mock the images queries module. Put this directly after the existing `vi.mock("sonner", …)` line:

```ts
const { imagesState } = vi.hoisted(() => ({ imagesState: { images: [] as unknown[] } }));
vi.mock("@/lib/images/queries", () => ({
  useImages: () => ({ data: { images: imagesState.images, scan_window_days: 30 } }),
  useAddImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useImageScan: () => ({ data: undefined }),
  useImagePolicy: () => ({ data: undefined }),
}));
```

and append at the end of the file:

```tsx
describe("CodeCard runtime chooser (C-3, container-images spec §9.3)", () => {
  const DIGEST = "sha256:" + "a".repeat(64);
  const APPROVED = {
    id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
    reference: "ghcr.io/org/satpy-runtime",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    stale: false,
    registry_connection: null,
  };
  const FLAGGED = { ...APPROVED, id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e71", reference: "ghcr.io/org/old", status: "flagged" };

  beforeEach(() => {
    imagesState.images = [APPROVED, FLAGGED];
  });

  function deployedInput() {
    return mutateAsync.mock.calls[0][0] as unknown as {
      input: { runtime: Record<string, unknown>; code: string | null };
    };
  }

  it("offers the three runtimes with the platform image first and its alias select", () => {
    setup();
    expect(screen.getByRole("group", { name: "Runtime" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Platform image/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Custom image \+ your code/ })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: /Container image/ })).not.toBeChecked();
    const alias = screen.getByLabelText("Image variant") as HTMLSelectElement;
    expect(Array.from(alias.options).map((o) => o.value)).toEqual(["default", "stactools"]);
  });

  it("deploys inline code on the chosen platform alias", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Image variant"), { target: { value: "stactools" } });
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedInput().input.runtime).toMatchObject({
      kind: "inline_python",
      image: null,
      runtime_image: "stactools",
    });
    expect(deployedInput().input.code).toBe("print(1)");
  });

  it("needs an approved image for your code on your image, and lists the flagged one disabled", async () => {
    setup();
    fireEvent.click(screen.getByRole("radio", { name: /Custom image \+ your code/ }));
    expect(screen.getByRole("button", { name: /Deploy revision/ })).toBeDisabled();
    expect(screen.getByText("Choose an approved image", { selector: "span" })).toBeInTheDocument();
    const picker = screen.getByLabelText("Image") as HTMLSelectElement;
    const flagged = Array.from(picker.options).find((o) => o.value === FLAGGED.id);
    expect(flagged?.disabled).toBe(true);
    expect(flagged?.textContent).toMatch(/\(Flagged\)$/);

    fireEvent.change(picker, { target: { value: APPROVED.id } });
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedInput().input.runtime).toMatchObject({
      kind: "inline_python_on_image",
      image: { id: APPROVED.id, reference: APPROVED.reference, digest: DIGEST },
      runtime_image: null,
    });
    expect(deployedInput().input.code).toBe("print(1)");
  });

  it("hides the editor for a container image and deploys its command with no code", async () => {
    setup();
    fireEvent.click(screen.getByRole("radio", { name: /Container image/ }));
    expect(screen.queryByLabelText("Process code")).toBeNull();
    fireEvent.change(screen.getByLabelText("Image"), { target: { value: APPROVED.id } });
    fireEvent.change(screen.getByLabelText("Command (optional)"), {
      target: { value: "tool --run 'a b'" },
    });
    expect(screen.getByTestId("command-preview")).toHaveTextContent('["tool","--run","a b"]');
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedInput().input.runtime).toMatchObject({
      kind: "container",
      command: ["tool", "--run", "a b"],
    });
    expect(deployedInput().input.code).toBeNull();
  });

  it("explains a broken command and will not deploy it", () => {
    setup();
    fireEvent.click(screen.getByRole("radio", { name: /Container image/ }));
    fireEvent.change(screen.getByLabelText("Image"), { target: { value: APPROVED.id } });
    fireEvent.change(screen.getByLabelText("Command (optional)"), { target: { value: "tool 'x" } });
    expect(screen.getByTestId("command-preview")).toHaveTextContent(/Unterminated single quote/);
    expect(screen.getByRole("button", { name: /Deploy revision/ })).toBeDisabled();
  });

  it("starts from the current revision's kind, image and command", () => {
    render(
      <CodeCard
        id={PROCESS_ID}
        groupId="earth-observation"
        currentCode={null}
        currentEnv={[]}
        currentRevision="3a9f1c2e-0000-4000-8000-0000000000b1"
        currentRuntime={{
          kind: "container",
          image: { id: APPROVED.id, reference: APPROVED.reference, digest: DIGEST },
          command: ["tool", "a b"],
        }}
        canMutate
        kind="transform"
      />,
    );
    expect(screen.getByRole("radio", { name: /Container image/ })).toBeChecked();
    expect((screen.getByLabelText("Image") as HTMLSelectElement).value).toBe(APPROVED.id);
    expect(screen.getByLabelText("Command (optional)")).toHaveValue("tool 'a b'");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app && npx vitest run src/__tests__/process-runtime-form.test.ts src/__tests__/process-code-card.test.tsx`
Expected: FAIL. The new modules are missing, and there is no Runtime group.

- [ ] **Step 3: Write the implementation**

`app/src/lib/processes/command.ts`:

```ts
/**
 * `runtime.command` (kind 3, container-images spec §3) as a person types it:
 * shell-like words, "shown as the array it becomes" (spec §9.3). It
 * replaces the image's Cmd, never its Entrypoint or User. The limits match
 * the write gate (`commandSchema` in `schemas.ts`), so the form refuses
 * what the route would.
 */
import { MAX_COMMAND_ENTRIES } from "./schemas";

export interface ParsedCommand {
  /** null = no override: the image's own CMD runs. */
  command: string[] | null;
  error: string | null;
}

export function parseCommand(text: string): ParsedCommand {
  const out: string[] = [];
  let current = "";
  let inToken = false;
  let quote: "'" | '"' | null = null;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote === "'") {
      if (ch === "'") quote = null;
      else current += ch;
      continue;
    }
    if (quote === '"') {
      if (ch === '"') {
        quote = null;
      } else if (ch === "\\" && (text[i + 1] === '"' || text[i + 1] === "\\")) {
        current += text[i + 1];
        i++;
      } else {
        current += ch;
      }
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      inToken = true;
      continue;
    }
    if (ch === "\\" && i + 1 < text.length) {
      current += text[i + 1];
      i++;
      inToken = true;
      continue;
    }
    if (/\s/.test(ch)) {
      if (inToken) {
        out.push(current);
        current = "";
        inToken = false;
      }
      continue;
    }
    current += ch;
    inToken = true;
  }

  if (quote !== null) {
    return {
      command: null,
      error: `Unterminated ${quote === "'" ? "single" : "double"} quote`,
    };
  }
  if (inToken) out.push(current);
  if (out.length === 0) return { command: null, error: null };
  if (out.some((entry) => entry.trim().length === 0)) {
    return { command: null, error: "Command entries must be non-blank" };
  }
  if (out.length > MAX_COMMAND_ENTRIES) {
    return { command: null, error: `A command carries at most ${MAX_COMMAND_ENTRIES} entries` };
  }
  return { command: out, error: null };
}

const SAFE_WORD = /^[A-Za-z0-9_@%+=:,./-]+$/;

/** The inverse, for syncing a deployed revision's command into the form. */
export function formatCommand(command: readonly string[]): string {
  return command
    .map((entry) => (SAFE_WORD.test(entry) ? entry : `'${entry.replace(/'/g, `'"'"'`)}'`))
    .join(" ");
}
```

`app/src/components/processes/runtime-form.ts`:

```ts
/**
 * The deploy form's Runtime chooser (C-3, container-images spec §9.3), as
 * pure state: the three kinds of spec §3, the current revision synced in,
 * and the payload the write gate accepts. The gate (`processRuntimeSchema`
 * + `checkImageGate`) stays the authority. This only keeps the form from
 * sending what it would refuse on shape.
 */
import { imageSnapshotSchema, type ImageSnapshot } from "@/lib/images/reference";
import { formatCommand, parseCommand } from "@/lib/processes/command";
import {
  PROCESS_RUNTIME_IMAGE_ALIASES,
  type ProcessNetwork,
  type ProcessRuntime,
  type ProcessRuntimeKind,
  type RuntimeImageAlias,
} from "@/lib/processes/schemas";

export interface RuntimeFormState {
  kind: ProcessRuntimeKind;
  /** Kind 1 only: the platform image alias. */
  runtimeImage: RuntimeImageAlias;
  /** Kinds 2–3: the snapshot taken from the picked registry row. */
  image: ImageSnapshot | null;
  /** Kind 3 only: the Cmd override as typed. */
  commandText: string;
}

export const DEFAULT_RUNTIME_FORM: RuntimeFormState = {
  kind: "inline_python",
  runtimeImage: "default",
  image: null,
  commandText: "",
};

export function runtimeFormFromRevision(
  runtime: Record<string, unknown> | null | undefined,
): RuntimeFormState {
  if (!runtime) return DEFAULT_RUNTIME_FORM;
  const kind = runtime.kind;
  if (kind === "inline_python_on_image" || kind === "container") {
    const snapshot = imageSnapshotSchema.safeParse(runtime.image);
    const command =
      kind === "container" &&
      Array.isArray(runtime.command) &&
      runtime.command.every((entry) => typeof entry === "string")
        ? formatCommand(runtime.command as string[])
        : "";
    return {
      kind,
      runtimeImage: "default",
      image: snapshot.success ? snapshot.data : null,
      commandText: command,
    };
  }
  if (kind !== "inline_python") return DEFAULT_RUNTIME_FORM;
  const alias = (PROCESS_RUNTIME_IMAGE_ALIASES as readonly string[]).includes(
    String(runtime.runtime_image),
  )
    ? (runtime.runtime_image as RuntimeImageAlias)
    : "default";
  return { ...DEFAULT_RUNTIME_FORM, runtimeImage: alias };
}

export interface RuntimeLimits {
  memory_mb: number;
  timeout_seconds: number;
  network: ProcessNetwork;
}

export type RuntimePayload =
  | { ok: true; runtime: ProcessRuntime; carriesCode: boolean }
  | { ok: false; error: string };

export function buildRuntimePayload(form: RuntimeFormState, limits: RuntimeLimits): RuntimePayload {
  const common = {
    memory_mb: limits.memory_mb,
    timeout_seconds: limits.timeout_seconds,
    retry: { max_attempts: 3, backoff: "exponential" as const },
    network: limits.network,
    // K-1: no hardware picker in this form yet (K-2 adds it) — the schema's
    // own default profile at its default cpu, no GPU.
    hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
  };
  if (form.kind === "inline_python") {
    return {
      ok: true,
      carriesCode: true,
      runtime: { kind: "inline_python", image: null, runtime_image: form.runtimeImage, ...common },
    };
  }
  if (!form.image) return { ok: false, error: "Choose an approved image" };
  if (form.kind === "inline_python_on_image") {
    return {
      ok: true,
      carriesCode: true,
      runtime: { kind: "inline_python_on_image", image: form.image, runtime_image: null, ...common },
    };
  }
  const parsed = parseCommand(form.commandText);
  if (parsed.error) return { ok: false, error: parsed.error };
  return {
    ok: true,
    carriesCode: false,
    runtime: {
      kind: "container",
      image: form.image,
      runtime_image: null,
      command: parsed.command,
      ...common,
    },
  };
}

/** The digest a revision pins, for the run rows (kinds 2–3 only). */
export function revisionImageDigest(runtime: Record<string, unknown>): string | null {
  const snapshot = imageSnapshotSchema.safeParse(runtime.image);
  return snapshot.success ? snapshot.data.digest : null;
}
```

`app/src/components/images/ImagePicker.tsx`:

```tsx
/**
 * The deploy form's image picker (C-3, container-images spec §9.3):
 * approved, fresh images this process's group may use are selectable.
 * Flagged, stale, pending ones and those pulled with another group's (or a
 * deleted) credential are listed disabled with the reason. "Add image…"
 * opens the same dialog as the dashboard. A current revision's image that
 * is no longer listed stays visible, disabled, so the form never silently
 * shows a different image than the one deployed.
 */
import { useId, useState } from "react";
import { Button, Input, Label } from "@stac-higher/shared";
import { Plus } from "lucide-react";
import type { ImageSnapshot } from "@/lib/images/reference";
import type { Image } from "@/lib/images/types";
import { shortDigest } from "./format";
import { imagePickerOptions } from "./picker";

export function ImagePicker({
  images,
  groupId,
  value,
  onChange,
  onAdd,
  disabled = false,
}: {
  images: readonly Image[];
  groupId: string;
  value: ImageSnapshot | null;
  onChange: (image: ImageSnapshot | null) => void;
  onAdd: () => void;
  disabled?: boolean;
}) {
  const uid = useId();
  const [search, setSearch] = useState("");
  const options = imagePickerOptions(images, groupId, search);
  const missing = value !== null && !options.some((option) => option.id === value.id);
  const anyListed = images.some((image) => image.status !== "revoked");

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid min-w-0 flex-1 gap-1.5">
          <Label htmlFor={`${uid}-image`}>Image</Label>
          <select
            id={`${uid}-image`}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
            value={value?.id ?? ""}
            disabled={disabled}
            onChange={(e) =>
              onChange(options.find((option) => option.id === e.target.value)?.snapshot ?? null)
            }
          >
            <option value="">Choose an approved image</option>
            {missing && value && (
              <option value={value.id} disabled>
                {`${value.reference} · ${shortDigest(value.digest)} (deployed now; not selectable)`}
              </option>
            )}
            {options.map((option) => (
              <option key={option.id} value={option.id} disabled={option.disabledReason !== null}>
                {option.disabledReason ? `${option.label} (${option.disabledReason})` : option.label}
              </option>
            ))}
          </select>
        </div>
        <Button type="button" variant="outline" onClick={onAdd} disabled={disabled}>
          <Plus className="h-4 w-4" />
          Add image…
        </Button>
      </div>
      <Input
        aria-label="Find an image"
        placeholder="Filter images by reference or tag"
        value={search}
        disabled={disabled}
        onChange={(e) => setSearch(e.target.value)}
      />
      {!anyListed && (
        <p className="text-xs text-muted-foreground">
          No images yet. Add one: it is scanned before it can be chosen.
        </p>
      )}
    </div>
  );
}
```

`app/src/components/processes/ProcessDetailPage.tsx`. Make these edits.

(a) Imports. Change the first line to `import { useEffect, useId, useState } from "react";`. Add after the `CodeEditor` import:

```ts
import { AddImageDialog } from "@/components/images/AddImageDialog";
import { ImagePicker } from "@/components/images/ImagePicker";
import { shortDigest } from "@/components/images/format";
import {
  buildRuntimePayload,
  revisionImageDigest,
  runtimeFormFromRevision,
  type RuntimeFormState,
} from "@/components/processes/runtime-form";
import { useImages } from "@/lib/images/queries";
import { parseCommand } from "@/lib/processes/command";
```

Extend the existing `@/lib/processes/schemas` import to:

```ts
import {
  PROCESS_NETWORK_LEVELS,
  PROCESS_RUNTIME_IMAGE_ALIASES,
  type NetworkLevel,
  type ProcessEnv,
  type ProcessKind,
  type ProcessRuntimeKind,
  type RuntimeImageAlias,
} from "@/lib/processes/schemas";
```

and the existing `@/lib/processes/types` import to include `ProcessRevision`:

```ts
import type {
  Process,
  ProcessCheck,
  ProcessRevision,
  ProcessRun,
  ProcessSource,
} from "@/lib/processes/types";
```

(b) Constants. Directly above `export function CodeCard(`, add:

```tsx
/** The Lambda-style choice (container-images spec §3, §9.3). */
const RUNTIME_KIND_OPTIONS: { kind: ProcessRuntimeKind; label: string; hint: string }[] = [
  {
    kind: "inline_python",
    label: "Platform image",
    hint: "Your code on a platform-built image.",
  },
  {
    kind: "inline_python_on_image",
    label: "Custom image + your code",
    hint: "Your image carries the libraries; the platform injects its runner and your code. The image needs python3 (3.10+) on PATH.",
  },
  {
    kind: "container",
    label: "Container image",
    hint: "The image is the process: its own entrypoint speaks the run contract. Runs as uid 10001 whatever its USER says.",
  },
];

const RUNTIME_IMAGE_LABELS: Record<RuntimeImageAlias, string> = {
  default: "default (the platform runtime)",
  stactools: "stactools (with the built-in extractor library)",
};
```

(c) Replace the whole `CodeCard` function (from `export function CodeCard({` down to its closing `}` just before the `// sources / outputs` banner) with:

```tsx
export function CodeCard({
  id,
  groupId,
  currentCode,
  currentEnv,
  currentRevision,
  currentRuntime = null,
  canMutate,
  kind,
}: {
  id: string;
  groupId: string;
  currentCode: string | null;
  currentEnv: ProcessEnv;
  currentRevision: string | null;
  /** The current revision's stored runtime: the chooser starts from it (C-3). */
  currentRuntime?: Record<string, unknown> | null;
  canMutate: boolean;
  kind: ProcessKind;
}) {
  const uid = useId();
  const [code, setCode] = useState(currentCode ?? STARTER_CODE);
  const [env, setEnv] = useState<ProcessEnv>(currentEnv);
  const [runtimeForm, setRuntimeForm] = useState<RuntimeFormState>(() =>
    runtimeFormFromRevision(currentRuntime),
  );
  const [addingImage, setAddingImage] = useState(false);
  const [memoryMb, setMemoryMb] = useState(512);
  // GOES spec §6.5: an extractor runs against one ingested file rather than a
  // batch, so it defaults to a much shorter timeout than a transform.
  const [timeoutSeconds, setTimeoutSeconds] = useState(
    kind === "extractor" ? 120 : 900,
  );
  const [networkLevel, setNetworkLevel] = useState<NetworkLevel>("isolated");
  const networkMax = readUiNetworkMax();
  const deployMutation = useDeployRevision();
  // Every connection the caller can see; EnvEditor narrows to the PROCESS's
  // group, which is the scope a secret_ref may name.
  const { data: connections } = useConnections();
  // The platform-wide registry; the picker narrows to what this group may use.
  const { data: imageList } = useImages();

  useEffect(() => {
    if (currentCode !== null) setCode(currentCode);
  }, [currentCode]);

  // A deploy starts from what is deployed: re-sync when the current revision
  // moves, so the form is an edit of the live env rather than a blank slate
  // that would silently drop it.
  useEffect(() => {
    setEnv(currentEnv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRevision]);

  // C-3: the same for the runtime (kind, image, command). Also re-synced when
  // the revision's runtime first arrives, because the revisions query can
  // land after the process query.
  const runtimeLoaded = currentRuntime !== null;
  useEffect(() => {
    setRuntimeForm(runtimeFormFromRevision(currentRuntime));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRevision, runtimeLoaded]);

  const payload = buildRuntimePayload(runtimeForm, {
    memory_mb: memoryMb,
    timeout_seconds: timeoutSeconds,
    // Slice 1: `hosts` is only meaningful at the `hosts` level, which the
    // write gate does not accept yet.
    network: { level: networkLevel, hosts: [] },
  });
  const command =
    runtimeForm.kind === "container" ? parseCommand(runtimeForm.commandText) : null;

  const deploy = async () => {
    if (!payload.ok) {
      toast.error(payload.error);
      return;
    }
    try {
      await deployMutation.mutateAsync({
        id,
        input: {
          runtime: payload.runtime,
          // Kind 3 carries no code: the image is the process (spec §3).
          code: payload.carriesCode ? code : null,
          env,
        },
      });
      toast.success("Deployed a new revision");
    } catch (err) {
      // A user-image deploy the gate refuses answers 422 with the reason
      // (image_not_approved / image_stale / image_group_mismatch /
      // image_digest_mismatch); its message is the toast.
      toast.error(err instanceof Error ? err.message : "Deploy failed");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Code</CardTitle>
        <CardDescription>
          Choose what runs, then deploy. Deploying creates an immutable revision
          and makes it current; every run pins the revision (and image digest)
          that executed it.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <fieldset
          className="grid gap-3 rounded-md border border-border p-3"
          disabled={!canMutate}
        >
          <legend className="px-1 text-sm font-medium">Runtime</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {RUNTIME_KIND_OPTIONS.map((option) => (
              <label
                key={option.kind}
                className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-2 text-sm has-[:checked]:border-primary"
              >
                <input
                  type="radio"
                  className="mt-1"
                  name={`${uid}-runtime-kind`}
                  value={option.kind}
                  checked={runtimeForm.kind === option.kind}
                  onChange={() => setRuntimeForm((form) => ({ ...form, kind: option.kind }))}
                />
                <span>
                  <span className="font-medium">{option.label}</span>
                  <span className="block text-xs text-muted-foreground">{option.hint}</span>
                </span>
              </label>
            ))}
          </div>

          {runtimeForm.kind === "inline_python" ? (
            <div className="grid gap-2">
              <Label htmlFor={`${uid}-runtime-image`}>Image variant</Label>
              <select
                id={`${uid}-runtime-image`}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50 sm:max-w-sm"
                value={runtimeForm.runtimeImage}
                onChange={(e) =>
                  setRuntimeForm((form) => ({
                    ...form,
                    runtimeImage: e.target.value as RuntimeImageAlias,
                  }))
                }
              >
                {PROCESS_RUNTIME_IMAGE_ALIASES.map((alias) => (
                  <option key={alias} value={alias}>
                    {RUNTIME_IMAGE_LABELS[alias]}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <ImagePicker
              images={imageList?.images ?? []}
              groupId={groupId}
              value={runtimeForm.image}
              onChange={(image) => setRuntimeForm((form) => ({ ...form, image }))}
              onAdd={() => setAddingImage(true)}
              disabled={!canMutate}
            />
          )}

          {runtimeForm.kind === "container" && (
            <div className="grid gap-2">
              <Label htmlFor={`${uid}-command`}>Command (optional)</Label>
              <Input
                id={`${uid}-command`}
                placeholder="tool --run"
                value={runtimeForm.commandText}
                onChange={(e) =>
                  setRuntimeForm((form) => ({ ...form, commandText: e.target.value }))
                }
              />
              <p className="text-xs text-muted-foreground" data-testid="command-preview">
                {command?.error ? (
                  <span className="text-destructive">{command.error}</span>
                ) : command?.command ? (
                  <>
                    Runs as <code className="tech">{JSON.stringify(command.command)}</code>.
                    Replaces the image&apos;s CMD, never its ENTRYPOINT or USER.
                  </>
                ) : (
                  "Empty: the image's own CMD runs."
                )}
              </p>
            </div>
          )}
        </fieldset>

        {runtimeForm.kind !== "container" && (
          <CodeEditor
            ariaLabel="Process code"
            value={code}
            onChange={setCode}
            language="python"
            disabled={!canMutate}
            minHeight="26rem"
          />
        )}
        <EnvEditor
          value={env}
          onChange={setEnv}
          connections={connections ?? []}
          groupId={groupId}
          disabled={!canMutate}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label htmlFor="memory">Memory (MB)</Label>
            <Input
              id="memory"
              type="number"
              min={128}
              value={memoryMb}
              disabled={!canMutate}
              onChange={(e) => setMemoryMb(Number(e.target.value))}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="timeout">Timeout (seconds)</Label>
            <Input
              id="timeout"
              type="number"
              min={1}
              max={86400}
              value={timeoutSeconds}
              disabled={!canMutate}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
            />
          </div>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="network-level">Network access</Label>
          {/* Plain <select>, like the env and trigger-kind pickers on this
              page: a small native control with disabled options. */}
          <select
            id="network-level"
            aria-label="Network access"
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50 sm:max-w-sm"
            value={networkLevel}
            disabled={!canMutate}
            onChange={(e) => setNetworkLevel(e.target.value as NetworkLevel)}
          >
            {PROCESS_NETWORK_LEVELS.map((level) => (
              <option
                key={level}
                value={level}
                disabled={
                  !NETWORK_LEVELS_AVAILABLE.includes(level) ||
                  !networkLevelWithinCap(level, networkMax)
                }
              >
                {NETWORK_LEVEL_LABELS[level]}
              </option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">
            Slice 1 runs every process isolated; inputs are staged into the run.
            Higher levels arrive with the egress proxy and are enabled per
            deployment (PROCESS_NETWORK_MAX).
          </p>
        </div>
        {canMutate && (
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={deploy} disabled={deployMutation.isPending || !payload.ok}>
              {deployMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Rocket className="h-4 w-4" />
              )}
              Deploy revision
            </Button>
            {!payload.ok && (
              <span className="text-sm text-muted-foreground">{payload.error}</span>
            )}
            {currentRevision && (
              <span className="text-sm text-muted-foreground">
                Current: {currentRevision.slice(0, 8)}
              </span>
            )}
          </div>
        )}
      </CardContent>
      {addingImage && (
        <AddImageDialog
          open
          onOpenChange={(open) => !open && setAddingImage(false)}
          groupId={groupId}
          onUse={(image) => {
            if (image.digest) {
              setRuntimeForm((form) => ({
                ...form,
                image: { id: image.id, reference: image.reference, digest: image.digest as string },
              }));
            }
            setAddingImage(false);
          }}
        />
      )}
    </Card>
  );
}
```

(d) `RunRow`. Add `imageDigest` to its props:

```tsx
function RunRow({
  run,
  processId,
  canMutate,
  isExtractor,
  imageDigest,
}: {
  run: ProcessRun;
  processId: string;
  canMutate: boolean;
  isExtractor: boolean;
  /** The user-image digest the run's revision pins (C-3), or null. */
  imageDigest: string | null;
}) {
```

and directly after `{run.is_test && <Badge variant="outline">test</Badge>}` add:

```tsx
          {imageDigest && (
            <Badge variant="outline" className="tech" title={imageDigest}>
              {shortDigest(imageDigest)}
            </Badge>
          )}
```

(e) `RunsCard`. Add a `revisions` prop, build the lookup, and pass it down:

```tsx
function RunsCard({
  id,
  canMutate,
  isExtractor,
  revisions,
}: {
  id: string;
  canMutate: boolean;
  isExtractor: boolean;
  /** The process's revisions: each run pins one, and its image digest shows on the row. */
  revisions: ProcessRevision[] | undefined;
}) {
  const { data: runs, isLoading } = useRuns(id);
  const digestByRevision = new Map(
    (revisions ?? []).map((revision) => [revision.id, revisionImageDigest(revision.runtime)]),
  );
```

and in its `runs?.map`, pass `imageDigest={digestByRevision.get(run.revision_id) ?? null}` to `RunRow`.

(f) `ProcessDetailContent`. Pass `currentRuntime={current?.runtime ?? null}` to `CodeCard`, and `revisions={revisions}` to `RunsCard`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/process-runtime-form.test.ts src/__tests__/process-code-card.test.tsx`
Expected: PASS, including the three pre-existing CodeCard tests (network access, explicit isolated block, timeout defaults). The "timeout default" test renders two cards at once. `useId` keeps their radio groups separate.

If `getByText("Choose an approved image", { selector: "span" })` finds nothing, the payload error span renders the text. Check that the `!payload.ok` span is inside `canMutate &&`, where `setup()` passes `canMutate`. The `<option>` with the same text is excluded by the selector on purpose.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green.

```bash
git add app/src/lib/processes/command.ts app/src/components/processes/runtime-form.ts app/src/components/images/ImagePicker.tsx app/src/components/processes/ProcessDetailPage.tsx app/src/__tests__/process-runtime-form.test.ts app/src/__tests__/process-code-card.test.tsx
git commit -m "feat(processes): Lambda-style runtime chooser with an image picker; digest on run rows (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: The overview derivation — flagged or stale images in use

**Files:**
- Modify: `app/src/components/layout/overview.ts` (one exported function)
- Modify: `app/src/components/layout/DashboardPage.tsx` (one query and the Processes tile's `sub`)
- Test: `app/src/__tests__/overview.test.ts` (one added `describe`)

**Interfaces:**
- Consumes: `useImages` (Task 7), type `Image`.
- Produces: `countImagesAtRisk(images: readonly Image[] | undefined): number`. It counts images that some live process's current revision uses (`in_use_by > 0`) and that are `flagged`, `revoked` or stale.

Spec §9.2: "the count of `flagged` + stale images in use, surfaced with the process-health verdicts". C-3 surfaces the count on the home overview's Processes tile, beside "not deployed". The per-process health verdict comes from the `process_image_flagged` alert, which C-4 writes; `health.ts` already defers to open alerts. `revoked` is counted too: a revoked image in use is at least as urgent as a flagged one, and its runs die at launch (C-2).

- [ ] **Step 1: Write the failing test.** Append to `app/src/__tests__/overview.test.ts`, and add `countImagesAtRisk` to the existing import from `@/components/layout/overview` plus `import type { Image } from "@/lib/images/types";`:

```ts
describe("countImagesAtRisk (C-3, container-images spec §9.2)", () => {
  const row = (over: Partial<Image>) =>
    ({ status: "approved", stale: false, in_use_by: 1, ...over }) as Image;

  it("counts in-use images that are flagged, revoked or stale", () => {
    expect(
      countImagesAtRisk([
        row({ status: "flagged" }),
        row({ status: "revoked" }),
        row({ stale: true }),
        row({}),
      ]),
    ).toBe(3);
  });

  it("ignores images nobody uses and unknown staleness", () => {
    expect(countImagesAtRisk([row({ status: "flagged", in_use_by: 0 }), row({ stale: null })])).toBe(0);
    expect(countImagesAtRisk(undefined)).toBe(0);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/overview.test.ts`
Expected: FAIL (`countImagesAtRisk` is not exported).

- [ ] **Step 3: Write the implementation.** In `app/src/components/layout/overview.ts`, add `import type { Image } from "@/lib/images/types";` beside the other type imports, and append:

```ts
/**
 * C-3 (container-images spec §9.2): images a live process's CURRENT
 * revision uses that need a human: flagged (new deploys refused), revoked
 * (runs die at launch) or stale (deploys AND launches refused). Unknown
 * staleness (policy unreadable) is not counted: no evidence is not a
 * finding, the same rule as the health verdicts above.
 */
export function countImagesAtRisk(images: readonly Image[] | undefined): number {
  return (images ?? []).filter(
    (image) =>
      image.in_use_by > 0 &&
      (image.status === "flagged" || image.status === "revoked" || image.stale === true),
  ).length;
}
```

In `app/src/components/layout/DashboardPage.tsx`, add the import `import { useImages } from "@/lib/images/queries";`, and add `countImagesAtRisk` to the existing `@/components/layout/overview` import. In `DashboardContent`, next to the other hooks and BEFORE the `if (catalogs.length === 0)` early return, add:

```ts
  // C-3: in-use images a human must look at. A member without a session
  // gets a 401 here; the tile then simply shows nothing extra.
  const { data: inUseImages } = useImages({ in_use: true });
```

After `const stats = buildStats(graph, flows, chips);`, add:

```ts
  const imagesAtRisk = countImagesAtRisk(inUseImages?.images);
  const processSub =
    [
      stats.processesUndeployed > 0 ? `${stats.processesUndeployed} not deployed` : null,
      imagesAtRisk > 0
        ? `${imagesAtRisk} on a flagged or stale image${imagesAtRisk === 1 ? "" : "s"}`
        : null,
    ]
      .filter(Boolean)
      .join(" · ") || undefined;
```

and replace the Processes `StatTile`'s `sub={…}` expression with `sub={processSub}`. Leave everything else on the tile unchanged.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/overview.test.ts`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green.

```bash
git add app/src/components/layout/overview.ts app/src/components/layout/DashboardPage.tsx app/src/__tests__/overview.test.ts
git commit -m "feat(overview): count flagged or stale images in use on the Processes tile (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: e2e selectors for the changed flows, and the docs

**Files:**
- Modify: `app/e2e/processes.spec.ts` (the teammate edits it; only the lead runs it)
- Modify: `docs/backend.md` (route table rows, one env row), `docs/processes.md` ("Bring your own image"), `docs/FEATURES.md` (the C-3 row)

The `processes` e2e spec today covers: the sidebar link "Processes", the `/processes` list or empty state, the "New process" dialog, the graph, and the lineage panel. C-3 changes that surface in three ways. The sidebar gains an "Images" link. Playwright's `name` is a substring match, but "Images" does not contain "Processes", so the existing `getByRole("link", { name: "Processes" })` stays unique and needs no edit. There is a new `/images` page. And the deploy card on `/processes/[id]` gains the Runtime fieldset. No existing selector breaks. The edits ADD coverage for the new flows, read-only, in the suite's style: nothing is submitted, and a bare database is a legal state.

- [ ] **Step 1: Add the e2e tests.** In `app/e2e/processes.spec.ts`, insert this block directly after the `test.describe("Processes", …)` block:

```ts
test.describe("Images (C-3)", () => {
  test("is reachable from the sidebar nav", async ({ page }) => {
    await page.goto("/catalogs");
    await page.getByRole("link", { name: "Images", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Images", level: 1 })).toBeVisible();
  });

  test("shows either images or the empty state, never an error", async ({ page }) => {
    await page.goto("/images");
    await expect(
      page.getByText(/No images yet|\d+ images? in the registry/).first(),
    ).toBeVisible();
    await expect(page.getByText(/Failed to load/)).toHaveCount(0);
  });

  test("previews the stored reference in Add image without submitting", async ({ page }) => {
    // Dev-bypass identity is an operator. The dialog is opened but NOT
    // submitted: adding writes a durable row and queues a scan.
    await page.goto("/images");
    await page.getByRole("button", { name: "Add image" }).first().click();
    await expect(page.getByRole("heading", { name: "Add image" })).toBeVisible();
    await page.getByLabel("Image reference").fill("python:3.12-slim");
    await expect(page.getByText("docker.io/library/python:3.12-slim")).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("heading", { name: "Add image" })).toHaveCount(0);
  });
});

test.describe("Deploy form runtime chooser (C-3)", () => {
  test("offers the three runtimes and hides the editor for a container image", async ({ page }) => {
    await page.goto("/processes");
    await expect(
      page.getByText(/No processes yet|Ceiling \d+ runs\/hour/).first(),
    ).toBeVisible();
    const first = page.locator('a[href^="/processes/"]').first();
    // A bare database has no process to open: a legal state for this suite.
    if ((await first.count()) === 0) return;
    await first.click();
    const runtime = page.getByRole("group", { name: "Runtime" });
    // A built-in process shows the read-only built-in card, not the code form.
    if ((await runtime.count()) === 0) return;
    await expect(runtime.getByRole("radio", { name: /Platform image/ })).toBeVisible();
    await expect(runtime.getByRole("radio", { name: /Custom image \+ your code/ })).toBeVisible();
    const container = runtime.getByRole("radio", { name: /Container image/ });
    await expect(container).toBeVisible();
    if (await container.isDisabled()) return; // a member sees the form read-only
    await container.check();
    await expect(page.getByLabel("Command (optional)")).toBeVisible();
    await expect(page.getByLabel("Process code")).toHaveCount(0);
    // Deliberately NOT deployed: nothing is approved without a scanner.
  });
});
```

- [ ] **Step 2: Docs — `docs/backend.md`.** In the "Astro server routes" table, insert these rows directly after the `/api/processes/hardware-profiles` row:

```markdown
| `/api/processes/image-policy` | GET | The deployment's image policy minus `scan_limits` (member+; 503 `image_policy_unavailable` when unreadable) — C-3 |
| `/api/images` | GET, POST | The platform-wide image registry (C-3, ADR 0021). GET (member+): `?status`, `?q`, `?in_use`; rows carry computed `stale` and `in_use_by`. POST (operator+, audited `create` on `container_image`): `{reference, tag?, registry_connection_id?}` → the typed reference is normalized, its host checked against `allowed_registries` (422 `registry_not_allowed`), a credential must be a `registry` connection of the caller's group for that host; INSERTs a `pending` row + an `admission` scan and answers **202** `{id, image_id, scan_id, deduplicated}` (an open admission of the same reference/tag/credential is reused). The app never touches a registry |
| `/api/images/[id]` | GET | One image, its last ten scans, and the processes in the caller's groups whose current revision uses it (`in_use_elsewhere` counts the rest) — member+ |
| `/api/images/[id]/scans/[scanId]` | GET | Poll a scan by the 202's ids; returns `{scan, image}` with the image the scan belongs to now (the drain may de-duplicate by digest) — member+ |
| `/api/images/[id]/scans/[scanId]/findings` | GET | Authorize → 302 to a short-lived presigned URL for the full Grype JSON (`findings_ref`) — member+ |
| `/api/images/[id]/rescan` | POST | Request a scan (operator+, audited `rescan`): a `rescan` row, or an `admission` retry for an image that never scanned successfully (`scan_failed` → `pending`); 409 `scan_pending` / `image_revoked` |
| `/api/images/[id]/exception` | POST | **Admin** (checked in-route; audited `exception` with the reason): `{reason, expires_at}`, expiry ≤ `exception_max_days`; `rejected`/`flagged` → `approved`; 409 `image_not_exceptionable` otherwise |
| `/api/images/[id]/revoke` | POST | **Admin** (checked in-route; audited `revoke`): any status → `revoked` (terminal), ending a live exception |
| `/api/internal/images/approved` | GET | `?digest=sha256:…` → `{approved}`: could a run on that digest LAUNCH (approved/flagged, scanned inside the window). No session; network-gated (never route `/api/internal/*` publicly), plus `X-Internal-Token` when `INTERNAL_API_TOKEN` is set. For the K-6 admission policy (spec §12) |
```

In the environment table, directly after the `PROCESS_IMAGE_POLICY_FILE` row, insert:

```markdown
| `INTERNAL_API_TOKEN` | Optional shared secret for `/api/internal/*` (C-3). When set, a caller must send it as `X-Internal-Token`; unset, those routes rely on network gating alone (a deployment never routes `/api/internal/*` from its public ingress). |
```

- [ ] **Step 3: Docs — `docs/processes.md`.** In "Bring your own image", replace the sentences

```markdown
The scanner, the
image registry page and rescans are being built now. Until the scanner
exists nothing is approved, so every kind 2/3 deploy is refused with
`image_not_approved`, and a kind 2/3 run that reached the pipeline by any
other route dies naming ADR 0021.
```

with

```markdown
Images live on the **Images** page (`/images`): add one by reference
(`python:3.12-slim` is stored as `docker.io/library/python`, tag
`3.12-slim`; adding by digest is not supported, because the scan resolves
the tag and pins the digest it scanned), watch its scan, see its findings,
policy reasons, who uses it, and request a rescan. On a process, the deploy
form's **Runtime** choice picks the kind: *Platform image* (kind 1, with the
`default`/`stactools` variant), *Custom image + your code* (kind 2) or
*Container image* (kind 3, code editor hidden, optional command shown as the
array it becomes). The picker offers only approved, fresh images your
process's group may use; others are listed disabled with the reason. Until
the scanner is deployed (C-2) every added image stays "Waiting for scan".
```

- [ ] **Step 4: Docs — `docs/FEATURES.md`.** Replace the C-3 row of the C-queue table:

```markdown
| C-3 · API, `/images` dashboard, deploy-form chooser | ⬜ | #52 |
```

with

```markdown
| C-3 · API, `/images` dashboard, deploy-form chooser | ✅ | `/api/images` (list with `status`/`q`/`in_use`, 202 add with typed-reference normalization + `allowed_registries` + credential group/host checks + open-admission reuse, detail with group-scoped `in_use_by`, scan poll that survives the drain's dedup, findings 302), `rescan` (operator; admission retry when no SBOM), `exception` and `revoke` (admin, in-route; the exception reason reaches the audit row via `locals.auditDetail`), `GET /api/processes/image-policy`, `GET /api/internal/images/approved?digest=` (K-6 seam). Audit actions `create`/`rescan`/`exception`/`revoke` on `container_image`. `/images` page + detail sheet (`components/images/`), "Images" in the sidebar, the Add-image dialog (RHF + Zod, live scan status). The deploy card's Runtime chooser (platform image / custom image + your code / container image) with an image picker and a command field; the current revision's kind, image and command sync in; run rows show the pinned digest. Connection delete impact counts images. Overview: flagged/stale images in use on the Processes tile. Exception form, history diffs → C-4 |
```

- [ ] **Step 5: Gate and commit**

Run: `npm run verify`. Expected: green. `verify` does not execute Playwright specs, and the teammate must NOT run them. The lead runs `processes.spec.ts` in Task 13. Re-read the inserted block once for balanced braces and the exact selector strings above.

```bash
git add app/e2e/processes.spec.ts docs/backend.md docs/processes.md docs/FEATURES.md
git commit -m "docs+e2e: image routes, INTERNAL_API_TOKEN, the Images page and Runtime chooser; e2e coverage for both (C-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Rebase, full gates, e2e, PR (LEAD ONLY)

- [ ] `git fetch origin main && git rebase origin/main`. C-2 (#51) is the likely earlier merge. Expected conflicts and how to resolve them:
  - `docs/backend.md`, `docs/processes.md`, `docs/FEATURES.md`: keep both sides' additions.
  - `app/src/lib/images/storage.ts`: C-3 only appended, so keep both.
  - Anything under `app/src/lib/images/` that C-2 changed in a contract (for example `scan-result.ts`): re-run `images-components.test.tsx`. Its `SCAN_DOC` must still parse, so update the fixture-shaped literal if C-2 moved a key.
  - `package-lock.json`: `git checkout --theirs package-lock.json && npm install && git add package-lock.json`.
- [ ] **Cross-slice contract check with C-2.** Before merging, confirm C-2 stores `image_scans.result` as the spec §6.4 document with `verdict` and `diff` as TOP-LEVEL siblings. `latestScanResult` and the dashboard's DB age (`result->'scanner'->>'db_built_at'`) read that shape. If C-2 nested the summary, fix the two readers (`components/images/format.ts`, the `IMAGE_ROWS_SQL` join in `storage.ts`) in this PR. Also confirm C-2's drain handles an `admission` row for an EXISTING image with `status = 'pending'` (C-3's retry path after `scan_failed`).
- [ ] Gates: `npm run verify`. The pipeline is not touched, so no pytest is needed unless the rebase brought pipeline changes that conflict. Resolving those is C-2's.
- [ ] e2e (Docker stack up, `astro dev stop` first, `CREDENTIALS_MASTER_KEY` sourced by absolute path from the main checkout's `.env`): `cd app && npm run test:e2e:ci -- processes`, then `npm run test:e2e:ci -- connections` (the delete dialog gained a line). The processes spec now has `Images (C-3)` and `Deploy form runtime chooser (C-3)` blocks. Both are read-only and pass on a bare database.
- [ ] Optional live look (Docker policy permitting): open `/images`, add `python:3.12-slim`, and see it land as "Waiting for scan" (or scanned, if C-2 is deployed). Open a process and see the three runtimes. Nothing is deployed.
- [ ] `git push -u origin feat/c3-images-dashboard`, then `gh pr create --base main --title "C-3: images API, /images dashboard and the runtime chooser"`. The body starts `Closes #52` and lists the gates run and the e2e run. It lists the e2e additions (no existing selector changed: the "Images" nav label does not collide with `getByRole("link", { name: "Processes" })`). It copies "Decisions made in this plan" below as deviations and choices, flags the C-2 contract check above, and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] After CI is green, squash-merge, then `git worktree remove .claude/worktrees/c3-images-dashboard`.
- [ ] Follow-ups to record on C-4 #53: the exception form and a revoke button on the detail sheet (routes exist), scan-history diffs, and the `process_image_flagged` health verdict. On K-6: the probe exists at `/api/internal/images/approved` with optional `INTERNAL_API_TOKEN`.

---

## Decisions made in this plan

Each resolves a C-3 question the spec leaves open, choosing the option most consistent with spec §14 and the C-1 decisions. They go into the PR body verbatim.

1. **No DDL.** Migration 030 has every column C-3 reads or writes. Nothing here needs a migration number.
2. **Audit actions are `(action, resource_type)` pairs.** The spec's `image.add`, `image.rescan`, `image.exception` and `image.revoke` are recorded as `create`, `rescan`, `exception` and `revoke` on `container_image`. `add` is a `create`, so the guard's created-id extraction records the new image id (the 202 body carries `id`). `GatedAction` gains the three verbs.
3. **Routes may add audit detail.** `locals.auditDetail` is merged by the guard into the ALLOWED row, beneath `outcome`/`status`, and redacted by `writeAudit`. Spec §4.4 keeps exceptions as columns "with an audit trail", so the reason must reach the audit row. Add, rescan and exception set it. This is a small cross-cutting change to `guard.ts` and `env.d.ts`.
4. **Admin checks are in-route.** The guard gates operator+, and `exception`/`revoke` re-check `isAdmin` and answer 403 `forbidden`. The guard still writes the one audit row (outcome allowed, status 403) — the same pattern as group-ownership refusals.
5. **Typed-reference normalization** follows Docker's rules. A tag is only after the last `/`, so `localhost:5000/x` has a port. A host needs a `.`, a `:` or `localhost`. Hub aliases fold to `docker.io`, and single-component Hub names gain `library/`. The repository is lowercased and the tag keeps its case. **Adding by digest is refused (400 `invalid_reference`)**: spec §6.3's scanner resolves a TAG, and `tag_at_add` is NOT NULL. A typed tag that conflicts with the tag field is a 400.
6. **A registry credential on add** must be visible to the caller (a missing one and another group's are the same 404), must be `protocol = registry` (400), and its `config.host`, canonicalized, must equal the reference's host (422 `registry_connection_host_mismatch`). An admin may use any group's credential; the gate then binds the image to that group.
7. **An open admission is reused.** The same reference, tag and credential with an admission still `pending`/`running` answers 202 with those ids and `deduplicated: true`. There is no unique constraint, so a true race can still create two rows, and the drain's digest dedup merges them.
8. **The scan poll survives the drain's dedup.** `getImageScan` matches the scan by id when its `image_id` is the path id OR the path's image no longer exists. The response carries the image the scan belongs to now.
9. **Rescan of a never-scanned image is an admission retry.** A row with no `sbom_ref` gets `kind = admission`, and `scan_failed` goes back to `pending`. Spec §4.3 says "retry by re-requesting", and a rescan needs a stored SBOM (§6.3). Otherwise the verb gives 409 `scan_pending` (naming the open scan) or 409 `image_revoked`. **C-2 must accept an admission row for an existing image.**
10. **In use means the CURRENT revision of a live process** (the §10 alert's definition). The detail names only processes in the caller's groups (admin: all) plus `in_use_elsewhere`. This deviates from §9.1's `[{process_id, name, group}]` so that a process outside your groups stays invisible, as everywhere else. The list's `in_use_by` is a platform-wide count.
11. **Staleness is computed server-side per row** with the gate's boundary, and only for `approved`/`flagged` images. When the policy is unreadable the list still works, with `stale: null` and `scan_window_days: null` and a banner. Writes stay fail-closed (503).
12. **Findings are a 302 sub-route** (`…/scans/[scanId]/findings`, the run-log precedent), not a URL embedded in the poll JSON, which is fetched every 2 s.
13. **The internal probe answers "could this digest LAUNCH"** (approved or flagged, fresh), because an admission policy guards runs and flagged blocks deploys, not runs. There is no session, per the spec. As defence in depth, an optional `INTERNAL_API_TOKEN` is checked against `X-Internal-Token` with a constant-time compare. Unset means network gating only. A policy failure is 503, never "approved".
14. **Cross-slice contract with C-2** (flagged for the lead): `image_scans.result` is the §6.4 document with `verdict` and `diff` as top-level siblings. The app never sets `container_images.last_scan_id`; the drain does, and the list's DB age joins through it.
15. **The exception FORM, a revoke button, and history diffs are C-4** (spec §15). C-3 ships all §9.1 routes, shows a live exception read-only, and lists scan history without diffs.
16. **The deploy card keeps its `useState` pattern** (the existing card, with 7+ interdependent controls and a sync effect). The Add-image dialog is RHF + Zod. Hardware stays the schema default (K-2 owns the picker).
17. **The picker lists every non-revoked image,** with only approved, fresh, group-usable ones selectable, mirroring the gate. The gate stays the authority. A deployed image that is no longer listable shows as a disabled "(deployed now; not selectable)" entry. A flagged current image stays selected, and a redeploy gets the gate's 422 as a toast (flagged blocks deploys).
18. **The run-row digest is derived client-side** from the revisions the page already fetches (`revision_id` → `runtime.image.digest`). There is no storage or route change.
19. **Overview:** the count covers in-use images that are flagged, **revoked** or stale, on the Processes tile. Revoked is added because its runs die at launch. The per-process verdict is C-4's alert.
20. **C-1 modules are touched additively only.** `storage.ts` gains appended functions and two imports. `status.ts`, `reference.ts`, `policy.ts`, `scan-result.ts` and `gate.ts` are unchanged. New logic lives in new files (`normalize`, `stale`, `verdict`, `schemas`, `access`, `api`, `queries`, `types`).

## Self-review

- **Spec coverage.**
  - §9.1 routes, every row: Tasks 4, 5 and 6. Permissions and audit: Task 3. Credentials never leave the envelope (the image row carries `{id, name, group_id, deleted}` only): Task 2.
  - §9.2 dashboard: page, island, table columns, filters, detail sheet, Add dialog polling, `SidebarNav` (Tasks 8, 9). History diffs and the exception form are C-4 per §15 (Decision 15).
  - §9.2 overview derivation: Task 11.
  - §9.3 chooser: three kinds, alias select, picker with disabled reasons, Add dialog in the picker, command shown as its array, current-revision sync, run-row digest (Task 10). `docs/processes.md` UI text: Task 12.
  - §7.2 app enforcement: `allowed_registries` on add (Task 4), `exception_max_days` (Task 5), the UI reads the policy (Tasks 6, 8).
  - The carried C-1 items: normalization + `registryAllowed`/`registryHost` (Tasks 1, 4), impact counts images (Task 6), the soft-deleted credential surfaced (Tasks 2, 8, 9), badges from `IMAGE_STATUS_LABEL` (Task 8), pending without C-2 (Tasks 8, 9 tests).
- **Placeholders.** None. Every file's code is complete, and every edit names its anchor text.
- **Type consistency.**
  - `ImageView` (Task 2) is used by `currentImageView` (Task 4) and every route.
  - `ApiImage`/`ApiImageScan`/`ApiImageUser` are re-exported as `Image`/`ImageScan`/`ImageUser` (Task 7) and used in Tasks 8–11.
  - `AddedImage` exists as both a storage type and a client type; they have different shapes, because the client one adds `id`/`reference`/`tag`. They live in different modules and are never imported together.
  - `ImageListQuery` (client, `in_use`) differs from `ImageListFilters` (storage, `inUse`). The route maps between them.
  - `RuntimeFormState`, `buildRuntimePayload` and `runtimeFormFromRevision` are defined in Task 10 and used only there. `shortDigest` is defined in Task 8 and used in Tasks 8–10.
  - The `ScanRequestOutcome`, `ExceptionOutcome` and `RevokeOutcome` literals match the Task 5 route switches.
- **Review Focus.** Each of the five lines has its test in the owning task: Task 1 (typed references), Tasks 2/8/9 (deleted credential), Tasks 4/9 (policy unreadable), Task 4 (open admission reuse), Tasks 2/4 (dedup-surviving poll).
