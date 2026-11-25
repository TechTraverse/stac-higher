# R-9 Chart Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every `v*` tag, package the `stac-higher` chart with `version` and `appVersion` stamped from the tag and push it to `oci://ghcr.io/<owner>/charts/stac-higher`, the ref the R-3 release manifest already names. Every pull request that touches the chart packages it without pushing. Document how a GitLab or GitHub deployment pipeline consumes a release, and the lead's one-time steps.

**Architecture:** One script, `scripts/package-chart.sh <tag> <out-dir>`, holds every packaging command: tag validation (the manifest's semver rule), the subchart build from `Chart.lock`, `helm package` with the stamped versions, then a render of the archive and a check of its metadata. `release.yml` grows two jobs. `chart` runs the script on every run: a `v*` tag stamps the tag, while a pull request or a manual run stamps `0.0.0-dryrun.<run number>`. It uploads the archive as a workflow artifact. `chart-push` runs on `v*` tags only. It downloads that archive and is the only job holding `packages: write`, the same split as `release-manifest`/`release-attach`. The workflow-level token drops to `contents: read`, and the existing `release` job takes `contents: write` at job level and runs on tag pushes only.

**Tech Stack:** GitHub Actions (`azure/setup-helm@v5.0.1` at Helm v4.3.1, as `helm.yml` pins; `actions/upload-artifact` / `download-artifact` v7), Helm OCI (`helm package`, `helm dependency build`, `helm registry login`, `helm push`), bash.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §6 (the chart, its OCI ref), §11 (releases). Issue #145, epic #136. ADR 0024. Contract: `docs/releases.md` (R-3).

## Global Constraints

- **Worktree:** `.claude/worktrees/r9-chart-release`, branch `feat/r9-chart-release`. Teammate gates only: `helm` locally, writing to a temp dir outside the repo, plus `actionlint`. Run `npm run verify` only if `app/` or `packages/` changes (this plan changes neither). No e2e, dev server, Docker or push. **No tag, no `helm push`, no `gh release`, no GHCR visibility change.** Those are the lead's.
- **Chart path:** `infra/helm/stac-higher/` (R-6 ruling). The issue's `charts/stac-higher` is stale. The OCI ref keeps `charts/` (`oci://ghcr.io/<owner-lc>/charts/stac-higher`) because R-3's manifest generator pins it (`chart.ref`).
- **Versions:** `version` = `appVersion` = the tag without its leading `v` (`v0.1.0` → `0.1.0`). This is the manifest's `chart.version` and the image tag `containers.yml` publishes, so a chart installed without `global.imageDigests` pulls the same release's images (`global.imageTag: ""` → `.Chart.AppVersion`). The spec's "appVersion = the tag" is read as this, because a `v`-prefixed appVersion would name image tags that do not exist.
- **Tag rule:** the release manifest's `VERSION_RE` (`vX.Y.Z` or `vX.Y.Z-pre`, no `+build`), duplicated in bash. A non-semver `v*` tag fails the `chart` job, naming the tag, before anything is pushed.
- **Dry run:** every non-tag run (pull requests touching `infra/helm/**`, `scripts/package-chart.sh` or `release.yml`, and `workflow_dispatch`) packages `0.0.0-dryrun.<run number>`, the manifest's dry-run convention without the `v`, and uploads the `chart` artifact. It never logs in or pushes.
- **Subcharts (R-7 lands an eoapi-k8s dependency concurrently):** the script always runs `helm dependency build`. That is a no-op with no dependencies; with dependencies it fetches exactly what `Chart.lock` pins and fails if the lock is out of sync with `Chart.yaml`. If `Chart.yaml` declares `dependencies:` and `Chart.lock` is missing, the script fails rather than let `dependency build` fall back to `update` and re-resolve ranges at release time (verified: Helm 4.1 does exactly that and writes a fresh lock).
- **Owner/repo** come from `github.repository_owner` / `$GITHUB_REPOSITORY_OWNER`, lowercased. Never hard-code the org in the workflow.
- **Least privilege:** workflow-level `contents: read`; `release` gets `contents: write`; `chart` gets `contents: read`; `chart-push` gets `packages: write` only (no checkout, so no `contents`).
- **Attestations:** none for the chart. Images get buildx provenance + SBOM. The chart has no buildx path, cosign is deferred (spec §12), and R-3 ruled out GitHub attestations. The push digest goes to the job summary; recording it in the manifest is a follow-up (`docs/ISSUES.md`).
- **Release manifest:** unchanged. R-3 already emits `chart.ref` and `chart.version` from the same owner and tag. This plan makes the push target match them byte for byte.
- **Never commit** account ids, ARNs, hostnames or secrets (ADR 0024). Docs use `<owner>`, `<repo>`, placeholders.
- **Docs carry no status.** `docs/releases.md` is the contract doc; `docs/FEATURES.md` gets an R-9 row; `docs/ISSUES.md` gets the next free number (I-164) under the R section.
- **Commits:** `R-9: <what>`, ending with the session's attribution trailer.

## Review Focus

1. **A `v*` tag that is not semver** (`v1`, `v0.1`, `v1.2.3+build`) must fail before any push. Pinned in Task 1 Step 3.
2. **Chart dependencies resolved at release time.** A chart with `dependencies:` and no `Chart.lock` must fail packaging, not silently re-resolve. A stale lock must fail too (`helm dependency build`'s own check). Pinned in Task 1 Step 4.
3. **The pushed ref must equal the manifest's `chart.ref`/`chart.version`.** Both lowercase the owner and strip the `v`. Pinned by reading `release-manifest.mjs` against the push step in Task 2.
4. **A pull request must never hold `packages: write` or reach `helm push`.** `chart-push` is gated on `push` of `refs/tags/v*`; the `release` job is gated on `push`.
5. **The archive must stand alone.** `files/` must be inside it (not `.helmignore`d), and subcharts with it, so it renders without the checkout. Pinned by the script's render of the `.tgz`.

---

### Task 1: `scripts/package-chart.sh`

**Files:**
- Create: `scripts/package-chart.sh` (executable)
- Modify: `infra/helm/stac-higher/Chart.yaml` (comment only: who stamps the versions)

**Interface:** `scripts/package-chart.sh <tag> <out-dir>`, run from the repo root. It writes `<out-dir>/stac-higher-<version>.tgz` and prints that path as the last stdout line. Helm's own output goes to stderr. Exit 1 with a `package-chart:` message on a bad tag, a missing lock, a failed build, package or render, or stamped metadata that does not match.

- [ ] **Step 1: Write the script**

1. Usage check; `tag` and `out` required.
2. Validate `tag` against `^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$`; `version=${tag#v}`.
3. If `Chart.yaml` has a top-level `dependencies:` key and no `Chart.lock`, fail with a message telling the author to run `helm dependency update` and commit `Chart.lock`.
4. `helm dependency build infra/helm/stac-higher`.
5. `helm package … --version "$version" --app-version "$version" --destination "$out"`.
6. `helm template stac-higher <archive> --namespace stac-higher --kube-version 1.34.0 -f infra/helm/stac-higher/tests/values/base.yaml > /dev/null`. `tests/` is `.helmignore`d, so the values come from the checkout.
7. `helm show chart <archive>` must contain the lines `version: $version` and `appVersion: $version`.
8. Print the archive path.

- [ ] **Step 2: Package the real chart locally**

Run: `scripts/package-chart.sh v0.0.0-dryrun.1 "$TMPDIR/r9-out"` and `scripts/package-chart.sh v0.1.0 "$TMPDIR/r9-out"`.
Expected: two archives; `helm show chart` reports the stamped versions; `tar -tzf` lists `stac-higher/files/…` and no `stac-higher/tests/`. `git status` stays clean.

- [ ] **Step 3: Bad tags**

Run the script with `v1`, `v0.1`, `v1.2.3+build`, `1.2.3`, `v01.2.3`.
Expected: each exits 1 with `package-chart: tag must look like …`; no archive written.

- [ ] **Step 4: Dependencies (on a copy of the chart outside the repo)**

Point the script at a copy via `CHART_DIR` (an override for this test only; default `infra/helm/stac-higher`). Use throwaway `HELM_CACHE_HOME`/`HELM_CONFIG_HOME`/`HELM_DATA_HOME`. Add an `eoapi` dependency from `https://devseed.com/eoapi-k8s/`.
- No `Chart.lock` → exits 1 with the missing-lock message.
- After `helm dependency update` writes the lock → packages, and the archive holds `stac-higher/charts/eoapi-*.tgz`.
- Lock edited out of sync (dependency version range changed in `Chart.yaml`) → exits 1 from `helm dependency build`.

- [ ] **Step 5: Commit** · `R-9: scripts/package-chart.sh — stamp, build subcharts from Chart.lock, package, render the archive`

---

### Task 2: `release.yml` — `chart` and `chart-push` jobs, dry run on PRs

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Triggers and permissions**

Add `pull_request` with `paths: [infra/helm/**, scripts/package-chart.sh, .github/workflows/release.yml]` and `workflow_dispatch`. Workflow-level `permissions: contents: read`. The `release` job gets `if: github.event_name == 'push'` and job-level `permissions: contents: write`; its steps are unchanged.

- [ ] **Step 2: `chart` job**

`contents: read`; checkout; `azure/setup-helm@v5.0.1` with `v4.3.1`. Run `scripts/package-chart.sh "$TAG" "$RUNNER_TEMP/chart"` with
`TAG: ${{ github.event_name == 'push' && github.ref_name || format('v0.0.0-dryrun.{0}', github.run_number) }}`.
Upload `${{ runner.temp }}/chart/*.tgz` as artifact `chart` (`if-no-files-found: error`, 7-day retention).

- [ ] **Step 3: `chart-push` job**

`needs: [chart]`, `if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')`, `permissions: packages: write`. Steps: setup-helm (same pin); download artifact `chart`; then:
- `version=${GITHUB_REF_NAME#v}`, `archive=chart/stac-higher-$version.tgz`, `registry=oci://ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/charts`;
- `helm registry login ghcr.io --username "$GITHUB_ACTOR" --password-stdin` from `secrets.GITHUB_TOKEN`;
- `helm push "$archive" "$registry"`, its output teed to a log;
- `helm show chart "$registry/stac-higher" --version "$version"` (proves the push resolves);
- the ref and the `Digest:` line from the push log to `$GITHUB_STEP_SUMMARY`.

- [ ] **Step 4: Lint**

Run `actionlint` (built from source at CI's pinned v1.7.12 into a temp dir with `go install`; never Docker). Expected: no findings. shellcheck is not installed locally, so CI's actionlint image covers the `run:` scripts.

- [ ] **Step 5: Commit** · `R-9: release.yml — package the chart on every run, push to GHCR OCI on v* tags`

---

### Task 3: Docs

**Files:**
- Modify: `docs/releases.md`, `docs/FEATURES.md`, `docs/ISSUES.md`, `infra/helm/stac-higher/README.md` (one pointer line in "Install paths")

- [ ] **Step 1: `docs/releases.md`**

- "What a `v*` tag publishes", chart bullet: `release.yml` `chart` + `chart-push`; `version` = `appVersion` = tag without `v`; subcharts from `Chart.lock`.
- New "The chart package" section: the script, the dry run (artifact `chart`, `0.0.0-dryrun.<run>`), the push, no signature or attestation (I-164), and pulling before the package is public (`helm registry login` with a `read:packages` token).
- New "Consuming a release" section: one pin (the tag) drives everything. Fetch `release-manifest.json` from the GitHub release. Chart ref and version from `chart.*`, digests into `digests.values.yaml`, Terraform `source` = `terraformModules.ref`, which must be a literal, so a CI guard greps for it. Worked GitHub Actions and GitLab CI jobs with placeholders.
- New "Cutting a release (lead)" section: preconditions, tag, what to watch, the first-release visibility change, the anonymous pull check (registry token probe with `curl`, and `helm pull` from a credential-free container), and confirming the manifest's `chart.ref` resolves.

- [ ] **Step 2: FEATURES, ISSUES, chart README**

- `docs/FEATURES.md`: an R-9 row in "Deployment artifacts (R queue)".
- `docs/ISSUES.md`: I-164 "The chart is not signed or attested, and its digest is not in the release manifest" 🟡. The version tag is re-pushable (a re-run overwrites it), so pin by digest from the job summary until the manifest carries it.
- Chart README "Install paths": point at `docs/releases.md` "Consuming a release".

- [ ] **Step 3: Gates** · `helm lint --strict` via `scripts/check-helm.sh "$TMPDIR/r9-rendered"` (Chart.yaml comment changed), the Task 1 runs again, actionlint. `npm run verify` is not needed: `app/` and `packages/` are untouched. `readme.test.ts` checks values keys only, and no values change.

- [ ] **Step 4: Commit** · `R-9: docs — chart package, consuming a release from GitHub/GitLab, lead release steps`

### Task 4: Review fixes (2026-11-25) — supersede Task 2's placement

Review found that a chart in `release.yml` could publish without its images or manifest, and the other way round. It also found tags (`v1.2.3-rc..1`, `v1.2.3-01`) that passed both regexes but that Helm rejects. Lead ruling: the preferred fix.

- **Placement:** `chart` and `chart-push` move to `containers.yml`, ordered images → chart → manifest (spec §11):
  - a new `version` job checks the tag (`scripts/package-chart.sh --check-tag`) before any image builds, and outputs the version;
  - `build` and `runtime` need `version`;
  - `chart` (contents: read) runs on every event and is the dry run;
  - `chart-push` (`packages: write`) runs only on `v*` tag pushes and needs `build`, `runtime` and `chart`; it outputs the digest Helm reports (`Digest:`, Helm 4.3.1 `pkg/registry/client.go`);
  - `release-manifest` needs `chart-push`. On a tag it requires `chart-push` success; on other runs it accepts `skipped`, and in both cases it requires `build`, `runtime` and `chart` success.
- **`release.yml`** goes back to tag pushes only and keeps the GitHub release job: workflow `contents: read`, job `contents: write`.
- **Tag rule:** `VERSION_RE` (now exported) and the script's bash copy become SemVer 2.0 pre-release exact, minus `+build`. Every accepted version is one Helm accepts. Checked locally against `helm package` for 12 versions. A vitest case runs the script's `--check-tag` over the accepted and rejected sets and asserts it agrees with `VERSION_RE`.
- **`chart.digest`:** an optional manifest key, set only when `--chart-digest` is passed (always on a release, never on a dry run). It is additive, so `schemaVersion` stays 1 and R-3's shape test is unchanged.
- **Script:**
  - dependencies and repository URLs come from `helm show chart`, which is normalised for block or flow style and omits `[]`, instead of the truncated `helm dependency list`;
  - `infra/helm/stac-higher/charts/*.tgz` is gitignored.
- **Concurrency on `containers.yml`:** the group is the ref for pull requests (cancel in progress) and the run id otherwise, so `main` and tag runs are never cancelled or queued.
- **Docs:**
  - the GitLab example's script items become block scalars, and both examples parse with `yaml`;
  - "When a release half-fails";
  - a chart digest check for consumers;
  - I-164 narrowed to signing and re-pushable tags.

## Lead-only (after merge)

1. Cut the first tag from a green `main`: `git tag -a v0.1.0 -m "v0.1.0" && git push origin v0.1.0`.
2. Watch Containers (images, manifest attach) and Release (GitHub release, `chart`, `chart-push`).
3. First release only: make the `charts/stac-higher` package public in the org's package settings, to match the images.
4. Anonymous check: `docs/releases.md` "Cutting a release", step 5.
