# ADR 0021 — User-supplied process images: scan, approve by policy, run by digest

- **Status:** proposed (2026-09-13 — awaiting the lead's approval of the
  design spec `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md`;
  implemented by the C queue in `TODO.md` once approved)
- **Amends:** ADR 0013 "Slice-1 scope" (the `container` runtime kind was
  deferred, not refused forever) and ADR 0019 §Consequences ("user-supplied
  images remain refused"). Every isolation invariant in ADR 0013 and every
  hardware/executor invariant in ADR 0019 stands unchanged.
- **Related:** ADR 0004 (the request-row bridge — `image_scans` is one),
  ADR 0014 (scan artifacts use the run-scoped credential path), ADR 0018
  (network profiles apply to runs on user images unchanged), K8s spec §12
  (which explicitly did not lift the refusal), ISSUES I-122…I-125.

## Context

Processes run inline Python on a platform-built image. ADR 0013 kept
"user-image supply-chain review, registry policy, and image-scanning
machinery out of the first accreditation surface" on purpose, and carried
`runtime.kind = "container"` in the contract so nothing was foreclosed.
The demand is now concrete: Satpy for GOES, PyTorch (I-95), and tools that
already ship as containers. The Lambda console is the reference UX — code
in the console *or* a container image, tag resolved to a digest at deploy.

What the source material requires of the gate: NIST SP 800-190 §4.1.1
(policy-driven quality gates on image vulnerabilities, visibility at the
registry), §4.1.5 (a central inventory of trusted images, cryptographic
identity per image, hosts run only approved images, ongoing monitoring as
vulnerabilities change), §4.2.2 (immutable names, not `latest`); the
FedRAMP *Vulnerability Scanning Requirements for Containers* ("only
containers from images that have been scanned within a 30-day
vulnerability scanning window can be actively deployed", a unique
identifier per image class, a mechanism that restricts non-conforming
containers from deploying); CISA BOD 26-04 and the FedRAMP Vulnerability
Detection and Response standard, which make **known-exploited** (KEV) the
first cut, ahead of raw severity.

## Options

### A. Scan as a platform run through the `Executor` seam — **chosen**

A platform-built scanner image (Syft + Grype) is launched by the pipeline
exactly like a process — own container, memory/timeout limits, an egress
network, run-scoped credentials to a `scans/` prefix, the registry
credential resolved at launch into its env only. The pipeline evaluates
policy on the summary it writes.

- **For:** reuses the isolation boundary and its limits, log capture and
  credential minting; the scanner (which parses hostile image content)
  gets the untrusted-code posture; on Kubernetes it is a Job with no
  redesign; one drain and one ledger for admission and rescans.
- **Against:** a third platform image to build and refresh; a second
  Docker network with egress; a blocking `wait` until K-4's async
  executor (a scan holds a worker slot for minutes — I-124).

### B. Syft/Grype as a subprocess in the pipeline worker

- **For:** least to build.
- **Against:** a 4 GB, 15-minute scan inside the worker process and
  slot — the shape ADR 0019 is moving away from; registry egress from the
  worker outside every existing adapter; scanner tooling in the pipeline
  pod on Kubernetes.

### C. Delegate to a scanning registry (Harbor locally; ECR pull-through cache + Inspector in GovCloud)

- **For:** scanning, UI and a "prevent vulnerable images from running"
  policy come with the product.
- **Against:** another stateful service to accredit; severity-only policy
  (no KEV/EPSS); Harbor's proxy cache serves the *first* pull before the
  scan completes; the FedRAMP per-image 30-day inventory would still have
  to be ours. Kept as the cloud deployment shape for the bytes (ECR pull-
  through + Inspector as a second opinion), never as the mechanism.

### Scanner: Syft + Grype over Trivy

Trivy already scans the platform's own images in CI and stays there. For
user images Grype's DB (v6, since 2026-03) carries KEV and EPSS natively
and matches a stored SBOM without re-pulling, which is what a daily rescan
of every in-use image needs. Trivy has no KEV/EPSS fields and warns that
foreign SBOMs match inaccurately. Both are Apache-2.0 and daemon-less.
Secret/misconfig scanning (Trivy's strength) needs the bytes and is a
documented extension, not v1.

## Decision

1. **Three runtime kinds.** `inline_python` (unchanged), `inline_python_on_image`
   (a user image as the dependency bundle; the platform's runner and the
   revision's code are injected through the environment, no mount, no
   rebuild — the image needs only `python3` on `PATH`), `container` (the
   image's own entrypoint is the process; optional `command` overrides
   `Cmd`, never `Entrypoint` or `User`).
2. **Scan-then-approve, by policy, with audited admin exceptions.** An
   image is added by reference, resolved to a digest, SBOM'd and scanned
   in a platform run, and evaluated against a per-deployment policy
   document (`PROCESS_IMAGE_POLICY_FILE`, golden fixture): allowed
   registries, platform, size cap, and block rules that put KEV first
   (any KEV; fixed CRITICAL; unfixed CRITICAL older than N days; fixed
   HIGH with EPSS ≥ threshold). Pass ⇒ `approved`. Fail ⇒ `rejected`
   until an admin grants an expiring, audited exception.
3. **Run only by digest.** A revision snapshots `{id, reference, digest}`
   immutably; the executor pulls and runs `reference@digest`. Tag drift
   is detected and shown, never followed.
4. **Rescan daily from the stored SBOM; act on the diff.** A newly
   failing rescan makes an in-use image `flagged`: new deploys are
   refused, triggered runs continue, an alert fires on every process
   using it. An image not scanned within the policy window (30 days) is
   **stale** and blocks launches too. Notifications key off the diff (new
   CRITICAL/HIGH, new KEV, verdict flip), not the raw counts.
5. **The registry is platform-wide metadata; pull credentials are
   group-owned.** `container_images` holds references, digests, verdicts,
   SBOM pointers and exceptions — never bytes. A new `registry`
   connection protocol carries private-registry credentials in the
   existing envelope; an image pulled with a group's credential is
   usable only by that group's processes.
6. **User images get the platform's hardening, not their own.** CapDrop
   ALL, no-new-privileges, the revision's limits and network profile, and
   a forced non-root uid (10001), regardless of the image's `USER`.

## Invariants (new; ADR 0013's and 0019's all carried)

- **No user image runs that was not scanned, and nothing runs but the
  digest that was scanned.** The app's write gate and the pipeline's
  launch path each check the row, the digest and the status
  independently; the executor never launches a user image by tag.
- **An image that has not been scanned within the policy window does not
  launch** (FedRAMP 30-day rule). Exceptions cover findings, never
  staleness.
- **A failing rescan never stops a running pipeline by itself; it blocks
  new deploys and raises an alert.** Halting is a human decision
  (revoke, or disable the process).
- **Registry credentials and the scanner's own findings never enter the
  pipeline worker's process as trusted content.** Credentials resolve at
  launch into the scanner's / daemon's auth only; the summary is parsed
  as untrusted data and the full findings are stored as objects.
- **The scanner is untrusted-adjacent and runs with the process
  posture** — its own container, limits, no platform credentials, a
  run-scoped storage credential for its scan prefix.
- **Policy is a deployment document read by both runtimes and enforced
  in both** (the hardware-profiles pattern); a missing or invalid policy
  fails closed.

## Consequences

- The socket proxy gains `IMAGES=1`: the pipeline can pull (and delete)
  images on the daemon. Mitigated by the proxy being reachable only from
  the pipeline and every pull being a digest with an approved row;
  recorded in `docs/backend.md`. On Kubernetes the kubelet pulls with a
  per-run `imagePullSecret` and a Kyverno policy can call the internal
  "is this digest approved" endpoint at admission (K-5/K-6 addendum).
- A second egress-capable Docker network (`scanner-egress`) exists; it
  cannot filter by host, so `allowed_registries` is enforced in software
  twice and the scanner is the only thing on it (I-123).
- Until K-4, a scan holds a worker slot for up to the policy timeout
  (I-124); M3-D's concurrency makes that a slot, not a stall.
- The Docker Hub anonymous budget (100 pulls / 6 h / IP) is shared by
  scans and launches; a deployment credential is optional and the
  exposure is logged (I-125).
- A third platform image (the scanner, with a baked vulnerability DB)
  enters the supply-chain surface; its DB age is visible on the dashboard
  and refreshed at scan start where egress allows, or by rebuilding the
  image where it does not.
- `container_images` becomes the CM-8 component inventory for user code
  and RA-5's "scan at defined frequency and when new vulnerabilities are
  identified" evidence; both belong in the FISMA control-mapping
  inventory beside the executor seam.

## Revisit

- When a platform-controlled registry exists (ECR pull-through / Harbor
  in cloud): add `cosign verify` and provenance as metadata, then as
  policy; produce scan attestations.
- When Trivy's SBOM interop or a KEV/EPSS enrichment matures: a second
  engine at admission for secrets/misconfig.
- When K-4 lands: the drain stops blocking; when K-6 lands: admission
  enforcement moves into the cluster as defence in depth.
- If a partner site needs an air-gapped DB: the mirror URL is already a
  setting; a scheduled scanner-image rebuild is the documented path.
