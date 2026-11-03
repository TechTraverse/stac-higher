# R-3 Release Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the derived `auth-proxy` image (now carrying the configurable audience claim), attach provenance + SBOM attestations to release builds, and attach a `release-manifest.json` of image digests to every `v*` GitHub release.

**Architecture:** Upstream stac-auth-proxy cannot match any claim but `aud` (Task 1), so the derived image grows an app factory that swaps upstream's `EnforceAuthMiddleware` for a subclass honouring `AUTH_BEARER_AUDIENCE_CLAIM`. `containers.yml` gains the `auth-proxy` matrix leg, release-only attestations, a per-leg digest artifact and a `release-manifest` job. That job runs a dependency-free Node generator (`.github/scripts/release-manifest.mjs`, unit-tested by vitest so `npm run verify` covers it). It produces a dry-run artifact on every run and attaches the file to the release on `v*` tags.

**Tech Stack:** GitHub Actions (docker/build-push-action v7, docker/bake-action v6, actions/upload-artifact / download-artifact v7), Node 22 stdlib (`node:util` `parseArgs`, `node:fs`), vitest, Python 3.12 + stac-auth-proxy 1.2.0 + PyJWT, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §5, §6.2, §8 (audience-claim bullet), §11. Issue #139, epic #136. ADR 0024, ADR 0015.

## Global Constraints

- **Worktree:** `.claude/worktrees/r3-release-artifacts`, branch `feat/r3-release-artifacts`. Teammates run `npm run verify` (repo root) and the proxy-policy `uv run pytest` + `uv run ruff check .` **only**. No e2e, no dev server, no Docker (no local image builds, no `docker run actionlint`), no push.
- **Files in scope** (issue list, extended where the work demands it): `.github/workflows/{containers,release,ci}.yml`, `.github/scripts/`, `infra/proxy-policy/`, `services/proxy-policy/` (the derived image's code), `app/src/__tests__/release-manifest.test.ts`, `docs/`.
- **Image naming is what `containers.yml` already does:** `ghcr.io/${{ github.repository }}-<name>`, lowercased by `docker/metadata-action` → `ghcr.io/techtraverse/stac-higher-<name>`. A `v0.1.0` tag publishes the image tag `0.1.0` (`type=semver,pattern={{version}}` strips the `v`). Do not introduce a `ghcr.io/<owner>/<repo>/<name>` form.
- **Owner/repo come from `github.repository` / `$GITHUB_REPOSITORY`.** Never hard-code `TechTraverse` or `stac-higher` in a workflow or script. Docs may show `<owner>/<repo>`.
- **Publish rules unchanged:** PR and non-main branch builds verify (linux/amd64, `load`); `main` and `v*` tags push multi-arch (titiler stays linux/amd64).
- **Attestations:** on `v*` builds only, `provenance: mode=max` and `sbom: true` on both build-push-action (matrix) and bake-action (runtime). Everywhere else pass `''`, which keeps the action default (the `mode=min` provenance that `main` images already carry: the published `process-runtime:latest` index has two `attestation-manifest` entries today). No cosign (spec §12). No `actions/attest-build-provenance`: no workflow uses GitHub attestations today.
- **Release manifest image keys, exactly these eight, in this order, never renamed:** `app`, `pipeline`, `titiler`, `cube-server`, `image-scanner`, `process-runtime`, `process-runtime-stactools`, `auth-proxy`. `titiler` is included because `infra/titiler/Dockerfile` is a derived image this repo builds.
- **Manifest refs:** image `ghcr.io/<owner-lc>/<repo-lc>-<key>:<semver>`; chart `oci://ghcr.io/<owner-lc>/charts/stac-higher` (spec §11) with `version` `<semver>`; modules `git::https://github.com/<owner>/<repo>.git//infra/terraform/modules/stac-higher?ref=<tag>` (spec §7's consumption form, pointing at the composition module). `<semver>` is the tag without its leading `v`.
- **`AUTH_BEARER_AUDIENCE_CLAIM` at the proxy:** unset or blank → `aud`; exactly `aud` or `client_id` after trimming (case-sensitive, because JWT claim names are); anything else stops the proxy at startup with an error naming the setting. In `client_id` mode the allowed values are still `ALLOWED_JWT_AUDIENCES` (the app client ids), and the token's `client_id` must be a string equal to one of them. In `aud` mode behaviour is byte-for-byte upstream's.
- **Upstream pin lockstep:** `infra/proxy-policy/Dockerfile` `FROM …stac-auth-proxy:v1.2.0@sha256:3f09…`, `docker-compose.yml`'s `auth-proxy` image, and the new `stac-auth-proxy==1.2.0` dev pin in `services/proxy-policy/pyproject.toml` move together.
- **Dependencies:** no new npm packages. One new Python **dev-only** dependency, `stac-auth-proxy==1.2.0`: the tests must subclass and exercise the exact upstream middleware the image runs. `uv.lock` is regenerated with `uv lock`, never hand-edited.
- **Never commit** account ids, ARNs, hostnames, CIDRs or secrets (ADR 0024). Test fixtures use `*.invalid` hosts and generated keys.
- **Workflow validation:** `actionlint` is not installed locally. Run it if `command -v actionlint` finds it; otherwise CI's `actionlint` job validates. Never run it through Docker.
- **Commits:** `R-3: <what>` subject, ending with the session's attribution trailer.

## Review Focus

1. **A forged or mis-signed token carrying the right `client_id`** must get 401, because the claim check runs only on a verified payload. Pinned in Task 2 (`test_bad_signature_is_rejected_before_the_claim_check`).
2. **A typo in `AUTH_BEARER_AUDIENCE_CLAIM`** (`client-id`, `CLIENT_ID`, `sub`) must stop the proxy, not silently fall back to `aud` (a Cognito deployment would then 401 every write with no clue why). Pinned in Task 2 (`test_resolve_rejects_unknown_claims`) and Task 3 (`test_create_app_fails_fast_on_a_bad_claim`).
3. **An upstream bump that renames or reorders `EnforceAuthMiddleware`** (a Dependabot PR on `infra/proxy-policy`) must fail the proxy-policy tests or the proxy's startup. It must never ship an image that silently runs upstream's `aud`-only check. Pinned in Task 3 (`test_dev_pin_matches_the_dockerfile_base`, `test_create_app_raises_when_upstream_middleware_is_missing`).
4. **A `v*` tag that is not semver** (`v1`, `v0.1`, `v1.2.3+build`) must fail the manifest job with a message naming the tag, not attach a manifest whose refs point at tags metadata-action never pushed. Pinned in Task 4 (`rejects non-semver versions`).
5. **A missing, extra or malformed digest** (a leg that pushed nothing, a stale artifact, an image ID where a digest belongs on a release) must fail the job. A half-filled manifest must never reach the release. Pinned in Task 4 (`rejects a missing image`, `rejects an unexpected image`, `rejects a malformed digest`) and in Task 5's record step (a pushed build with an empty digest exits 1).

---

### Task 1: Confirm the upstream audience finding (investigation, settled while planning)

The planner read upstream `v1.2.0` (the pinned base) and `v1.3.1` (latest, 2026-10-25). In both, `EnforceAuthMiddleware.validate_token` calls `jwt.decode(..., audience=self.allowed_jwt_audiences)`, and `Settings` has only `allowed_jwt_audiences`. PyJWT matches `audience` against `aud` only, and when an audience is configured it raises `MissingRequiredClaimError("aud")` for a token without `aud`, which is every Cognito access token. **Upstream cannot match a configurable claim, so the check lands in our derived image (Tasks 2–3).** This task re-confirms that in five minutes and stops the slice if it has become false.

**Files:** none.

- [ ] **Step 1: Re-read upstream at the pinned tag and at the latest release**

Run:
```bash
for ref in v1.2.0 $(gh release view -R developmentseed/stac-auth-proxy --json tagName --jq .tagName); do
  echo "== $ref"
  gh api "repos/developmentseed/stac-auth-proxy/contents/src/stac_auth_proxy/middleware/EnforceAuthMiddleware.py?ref=$ref" --jq .content | base64 -d | grep -n -E 'audience|client_id|claim'
  gh api "repos/developmentseed/stac-auth-proxy/contents/src/stac_auth_proxy/config.py?ref=$ref" --jq .content | base64 -d | grep -n -i -E 'aud|claim'
done
```
Expected: only `allowed_jwt_audiences` / `audience=self.allowed_jwt_audiences` lines; no `client_id` and no claim-name setting.

- [ ] **Step 2: Decide**

If the output matches, continue with Task 2. Put one line in the PR body: "upstream v1.2.0–<latest> matches `aud` only; the check lives in the derived image." If a release now offers a claim setting, **stop and report to the lead**: the remedy is an upstream bump (ADR 0015's deliberate-bump procedure), not Tasks 2–3.

---

### Task 2: Claim-aware auth middleware (proxy-policy)

**Files:**
- Create: `services/proxy-policy/src/stac_higher_proxy_policy/audience.py`
- Modify: `services/proxy-policy/pyproject.toml` (dev extra), `services/proxy-policy/uv.lock` (via `uv lock`)
- Test: `services/proxy-policy/tests/test_audience.py`

**Interfaces:**
- Produces (in `stac_higher_proxy_policy.audience`):
  - `AUDIENCE_CLAIM_ENV = "AUTH_BEARER_AUDIENCE_CLAIM"`
  - `AudienceClaim = Literal["aud", "client_id"]`
  - `resolve_audience_claim(environ: Mapping[str, str]) -> AudienceClaim`: raises `ValueError` whose message contains `AUTH_BEARER_AUDIENCE_CLAIM`, `'aud'`, `'client_id'` and the offending value.
  - `@dataclass class ClaimAwareEnforceAuthMiddleware(stac_auth_proxy.middleware.EnforceAuthMiddleware)` with a new field `audience_claim: str = "aud"`, a field `allowed_client_ids: Optional[Sequence[str]] = field(default=None, init=False)`, and overridden `__post_init__` and `validate_token`.

- [ ] **Step 1: Add the dev dependency and lock**

In `pyproject.toml`'s `dev` extra add `"stac-auth-proxy==1.2.0"`, with a comment: test-only, the image gets it from its `FROM`; keep it equal to the Dockerfile `FROM` tag and the `docker-compose.yml` pin (ADR 0015 lockstep). Run `cd services/proxy-policy && uv lock && uv sync --extra dev`.
Expected: `uv.lock` gains `stac-auth-proxy 1.2.0`; `uv run python -c "import stac_auth_proxy"` exits 0.

- [ ] **Step 2: Write the failing tests**

`tests/test_audience.py`. Fixtures: an RSA key pair from `cryptography` (`rsa.generate_private_key(public_exponent=65537, key_size=2048)`), and `sign(claims, key=PRIVATE) -> str` via `jwt.encode(claims, key, algorithm="RS256")`, with `exp` one hour ahead. A fake OIDC config whose `jwks_client.get_signing_key_from_jwt(token)` returns an object whose `.key` is the public key, and whose `metadata` is `{}`. `make_mw(claim, audiences)` builds `ClaimAwareEnforceAuthMiddleware(app=<noop ASGI>, private_endpoints={}, public_endpoints={}, default_public=True, oidc_discovery_url="http://idp.invalid/.well-known/openid-configuration", allowed_jwt_audiences=audiences, audience_claim=claim)` and sets `mw._oidc_config` to the fake. "Rejected" means `pytest.raises(HTTPException)` with `status_code == 401`.

```python
@pytest.mark.parametrize("raw", [None, "", "   ", "aud", " aud "])
def test_resolve_defaults_to_aud(raw): ...           # == "aud" (None = key absent)
def test_resolve_accepts_client_id(): ...            # {"AUTH_BEARER_AUDIENCE_CLAIM": " client_id "} -> "client_id"
@pytest.mark.parametrize("raw", ["client-id", "CLIENT_ID", "sub", "azp"])
def test_resolve_rejects_unknown_claims(raw): ...    # ValueError; str(err) contains AUDIENCE_CLAIM_ENV and repr(raw)

# client_id mode, audiences ["app-client"]
def test_cognito_access_token_is_accepted(): ...     # claims {sub, client_id: "app-client", token_use: "access", scope: "openid"} -> payload["client_id"] == "app-client"
def test_mismatched_client_id_is_rejected(): ...     # client_id "other-client" -> 401, detail == "Invalid token audience"
def test_missing_client_id_is_rejected(): ...        # no client_id -> 401, detail == "Invalid token audience"
def test_non_string_client_id_is_rejected(): ...     # client_id ["app-client"] -> 401
def test_token_carrying_aud_is_rejected_in_client_id_mode(): ...  # aud "app-client" + client_id "app-client" -> 401 (PyJWT: no audience configured, aud present)
def test_bad_signature_is_rejected_before_the_claim_check(): ...  # signed by a second key, client_id "app-client" -> 401, detail == "Invalid or expired token"
def test_client_id_mode_without_audiences_skips_the_check(): ...  # audiences None, client_id "anything" -> payload returned
def test_client_id_mode_moves_audiences_off_pyjwt(): ...          # mw.allowed_jwt_audiences is None and mw.allowed_client_ids == ["app-client"]

# aud mode, audiences ["stac-higher"]: upstream behaviour unchanged
def test_aud_mode_accepts_matching_aud(): ...        # aud "stac-higher" -> payload
def test_aud_mode_rejects_wrong_aud(): ...           # aud "other" -> 401, detail == "Invalid token audience"
def test_aud_mode_rejects_cognito_access_token(): ...# client_id only, no aud -> 401 (why the setting exists)

def test_asgi_rejection_has_the_upstream_shape(): ...
# Wrap a Starlette app (POST /collections -> 200 JSON request.state.payload["client_id"]) in
# ClaimAwareEnforceAuthMiddleware with private_endpoints={r"^/collections$": ["POST"]},
# default_public=False, client_id mode. Use starlette.testclient.TestClient.
# Mismatched token -> 401 and json() == {"detail": "Invalid token audience"};
# matching token -> 200 and body == "app-client".
```

- [ ] **Step 3: Run to verify failure**

Run: `cd services/proxy-policy && uv run pytest tests/test_audience.py -q`
Expected: collection error, `ModuleNotFoundError: stac_higher_proxy_policy.audience`.

- [ ] **Step 4: Implement `audience.py`**

`resolve_audience_claim`: `.get(AUDIENCE_CLAIM_ENV, "")`, strip, `""` → `"aud"`. `__post_init__`: run `resolve_audience_claim({AUDIENCE_CLAIM_ENV: self.audience_claim})` to validate the field. In `client_id` mode, move `allowed_jwt_audiences` to `allowed_client_ids` and set `allowed_jwt_audiences = None`, so PyJWT does not demand `aud`. `validate_token(self, auth_header, auto_error=True, required_scopes=None)`: `payload = super().validate_token(...)`. In `aud` mode, or when the payload is `None`, or when `allowed_client_ids` is empty, return the payload. Otherwise, a payload whose `client_id` is not a `str` in `allowed_client_ids` raises `HTTPException(401, detail="Invalid token audience", headers={"WWW-Authenticate": "Bearer"})`, the exact upstream shape. Log one `logger.warning("Token client_id not in ALLOWED_JWT_AUDIENCES")` with no token or claim values. Scopes are checked inside `super()` before the claim check; that order is fine because both failures reject the request.

- [ ] **Step 5: Run to verify pass, then lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all tests pass (the existing `test_factory.py` included); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/proxy-policy/pyproject.toml services/proxy-policy/uv.lock services/proxy-policy/src/stac_higher_proxy_policy/audience.py services/proxy-policy/tests/test_audience.py
git commit -m "R-3: proxy-policy — claim-aware auth middleware for AUTH_BEARER_AUDIENCE_CLAIM"
```

---

### Task 3: Proxy entry point, image wiring, CI job, docs

**Files:**
- Create: `services/proxy-policy/src/stac_higher_proxy_policy/app.py`, `services/proxy-policy/src/stac_higher_proxy_policy/__main__.py`
- Modify: `infra/proxy-policy/Dockerfile` (CMD + header comment), `services/proxy-policy/README.md`, `.github/workflows/ci.yml`, `docs/auth.md`, `docs/ISSUES.md`
- Test: `services/proxy-policy/tests/test_app.py`

**Interfaces:**
- Consumes: Task 2's `resolve_audience_claim`, `ClaimAwareEnforceAuthMiddleware`, `AUDIENCE_CLAIM_ENV`.
- Produces: `stac_higher_proxy_policy.app.create_app(settings: stac_auth_proxy.Settings | None = None) -> fastapi.FastAPI`. `app.py` imports `EnforceAuthMiddleware` at module level (`from stac_auth_proxy.middleware import EnforceAuthMiddleware`) and matches entries with `is` against that name, so the missing-middleware test can monkeypatch it. The image's process becomes `python -m stac_higher_proxy_policy`.

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py`. Settings fixture: `Settings(upstream_url="http://upstream.invalid", oidc_discovery_url="http://idp.invalid/.well-known/openid-configuration", allowed_jwt_audiences="app-client")`. Pass a string: upstream's validator parses comma lists. Clear `AUTH_BEARER_AUDIENCE_CLAIM` with `monkeypatch` by default.

```python
def test_create_app_swaps_in_the_claim_aware_middleware(monkeypatch): ...
# claim env "client_id" -> exactly one app.user_middleware entry with .cls is ClaimAwareEnforceAuthMiddleware,
# none with .cls is EnforceAuthMiddleware; that entry's kwargs["audience_claim"] == "client_id" and
# kwargs["allowed_jwt_audiences"] == ["app-client"] (the rest of upstream's kwargs carried over unchanged)
def test_create_app_defaults_to_aud(monkeypatch): ...           # env unset -> kwargs["audience_claim"] == "aud"
def test_create_app_fails_fast_on_a_bad_claim(monkeypatch): ... # env "client-id" -> pytest.raises(ValueError, match="AUTH_BEARER_AUDIENCE_CLAIM")
def test_create_app_raises_when_upstream_middleware_is_missing(monkeypatch): ...
# monkeypatch stac_higher_proxy_policy.app's reference to the upstream class with a dummy class
# -> pytest.raises(RuntimeError, match="EnforceAuthMiddleware")
def test_dev_pin_matches_the_dockerfile_base(): ...
# version parsed by r"FROM ghcr\.io/developmentseed/stac-auth-proxy:v([^@\s]+)@" from
# Path(__file__).resolve().parents[3] / "infra/proxy-policy/Dockerfile"
# == importlib.metadata.version("stac-auth-proxy")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app.py -q`
Expected: `ModuleNotFoundError: stac_higher_proxy_policy.app` (the pin test passes already).

- [ ] **Step 3: Implement `app.py` and `__main__.py`**

`create_app`: call `resolve_audience_claim(os.environ)` first (fail fast). Then `app = stac_auth_proxy.create_app(settings)` and find the entries of `app.user_middleware` whose `.cls is EnforceAuthMiddleware`. Anything other than exactly one raises `RuntimeError("upstream stac-auth-proxy no longer registers exactly one EnforceAuthMiddleware; re-check the derived image against the new version (ADR 0015)")`. Replace that entry in place with `starlette.middleware.Middleware(ClaimAwareEnforceAuthMiddleware, *entry.args, **entry.kwargs, audience_claim=claim)`. The swap happens before Starlette builds its middleware stack (that is lazy, on the first request), and it applies in `aud` mode too, so one code path ships. `__main__.py` mirrors upstream `stac_auth_proxy/__main__.py` (v1.2.0): `uvicorn.run("stac_higher_proxy_policy.app:create_app", factory=True, host="0.0.0.0", port=int(os.getenv("PORT", 8000)), log_config=…)`, with the same `LOGGING_CONFIG` extension for the `stac_auth_proxy` logger plus `stac_higher_proxy_policy` at `INFO`. Drop the `RELOAD` switch.

- [ ] **Step 4: Run to verify pass, then lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass, ruff clean.

- [ ] **Step 5: Wire the image**

`infra/proxy-policy/Dockerfile`: append `CMD ["python", "-m", "stac_higher_proxy_policy"]` after `USER user`. Extend the header comment: the image also carries the `AUTH_BEARER_AUDIENCE_CLAIM` check (spec §8, R-3); the base tag moves in lockstep with `docker-compose.yml` **and** the `stac-auth-proxy` dev pin in `services/proxy-policy/pyproject.toml`; `test_dev_pin_matches_the_dockerfile_base` enforces the second pairing. `.dockerignore` needs no change: `services/proxy-policy` is re-included and `src/` comes with it.

- [ ] **Step 6: Add the CI job**

The proxy-policy suite runs in no workflow today. Add this to `ci.yml` after `cube-server`:
```yaml
  proxy-policy:
    name: Proxy policy · ruff, pytest
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults:
      run:
        working-directory: services/proxy-policy
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.0.1
        with:
          enable-cache: true
          cache-dependency-glob: services/proxy-policy/uv.lock
      - run: uv sync --frozen --extra dev
      - name: Lint (ruff check)
        run: uv run ruff check .
      - name: Tests
        run: uv run pytest -q
```

- [ ] **Step 7: Docs**

- `services/proxy-policy/README.md`: in "Wiring", add the entry point (`python -m stac_higher_proxy_policy` → `app.create_app`, upstream's app with the auth middleware swapped). Add an env-table row for `AUTH_BEARER_AUDIENCE_CLAIM` (no, default `aud`; `aud` | `client_id`; in `client_id` mode `ALLOWED_JWT_AUDIENCES` lists app client ids; tokens carrying `aud` are rejected in that mode). Add `uv run pytest` to CI in "Development".
- `docs/auth.md`: after "Bearer tokens on `/api/*`", add a short `### Audience claim at the proxy` subsection covering what the setting does at the proxy, why (Cognito access tokens carry `client_id`, not `aud`; upstream matches `aud` only), the startup failure on a bad value, and the `aud`-bearing-token rejection in `client_id` mode. Note that the app half is R-1's `AUTH_BEARER_AUDIENCE_CLAIM`, with the same name and values. Leave the env table to R-1, so the two slices do not conflict.
- `docs/ISSUES.md`: new entry under "Known limitations / residual risk". Take the next number above the file's actual maximum (`grep -n '^### I-' docs/ISSUES.md | sort -t- -k2 -n | tail -1`; `I-148` if nothing landed meanwhile). Title: `· The proxy's client_id audience mode rides on upstream internals 🟡`. Body: upstream ≤ v1.3.1 matches `aud` only. The derived image swaps `EnforceAuthMiddleware` in `app.user_middleware`, guarded by the startup `RuntimeError` and the lockstep pin test. The swap goes away if upstream gains a claim setting (worth proposing upstream). Tokens with `aud` are refused in `client_id` mode. Tracked in: `services/proxy-policy/src/stac_higher_proxy_policy/app.py`; ADR 0015 bump procedure.

- [ ] **Step 8: Validate and commit**

Run: `uv run pytest -q && uv run ruff check .` (in `services/proxy-policy`), and `command -v actionlint && actionlint .github/workflows/ci.yml` (if missing, CI validates).
```bash
git add services/proxy-policy infra/proxy-policy/Dockerfile .github/workflows/ci.yml docs/auth.md docs/ISSUES.md
git commit -m "R-3: derived proxy entry point applies AUTH_BEARER_AUDIENCE_CLAIM; CI runs the proxy-policy suite"
```

---

### Task 4: Release manifest generator

**Files:**
- Create: `.github/scripts/release-manifest.mjs`
- Test: `app/src/__tests__/release-manifest.test.ts`

**Interfaces:**
- Produces (named ESM exports, Node ≥ 22 stdlib only, JSDoc-typed):
  - `IMAGE_KEYS`: frozen array, the eight keys in Global Constraints order.
  - `buildReleaseManifest({ version, gitSha, repository, digests }) -> ReleaseManifest`. Throws one `Error` whose message lists **every** problem, one per line.
  - `readDigestsDir(dir: string) -> Record<string, string>`: one file per image, named by its key, content is the digest; trims whitespace; ignores nothing.
  - `main(argv: string[]) -> number`: flags `--version`, `--git-sha`, `--repository`, `--digests-dir`, `--out` (all required, `node:util` `parseArgs`, `strict: true`). Writes `JSON.stringify(manifest, null, 2) + "\n"` to `--out` and returns 0; on any error prints `release-manifest: <message>` to stderr and returns 1.
  - CLI guard: `if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = main(process.argv.slice(2));`
- Validation rules: `version` `^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$` (no `+build`: Docker tags cannot carry `+`); `gitSha` `^[0-9a-f]{40}$`; `repository` `^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$`; each digest `^sha256:[0-9a-f]{64}$`; `digests` keys exactly `IMAGE_KEYS`.

- [ ] **Step 1: Write the failing tests**

`app/src/__tests__/release-manifest.test.ts`, first line `// @vitest-environment node`. Import from `../../../.github/scripts/release-manifest.mjs`. `D(c)` = `"sha256:" + c.repeat(64)`, and `digests` = each key mapped to `D(hex digit i)`. `SHA` = `"0123456789abcdef0123456789abcdef01234567"`.

```ts
it("builds the contract shape for a release tag", () => {
  const m = buildReleaseManifest({ version: "v0.1.0", gitSha: SHA, repository: "TechTraverse/stac-higher", digests });
  expect(m).toEqual({
    schemaVersion: 1, version: "v0.1.0", gitSha: SHA,
    images: { app: { ref: "ghcr.io/techtraverse/stac-higher-app:0.1.0", digest: digests.app }, /* …all eight… */ },
    chart: { ref: "oci://ghcr.io/techtraverse/charts/stac-higher", version: "0.1.0" },
    terraformModules: { ref: "git::https://github.com/TechTraverse/stac-higher.git//infra/terraform/modules/stac-higher?ref=v0.1.0" },
  });
  expect(Object.keys(m.images)).toEqual([...IMAGE_KEYS]);   // stable order
});
it("keeps a prerelease suffix in tags and chart version", ...)   // v0.2.0-rc.1 -> ":0.2.0-rc.1", chart.version "0.2.0-rc.1", ?ref=v0.2.0-rc.1
it("rejects non-semver versions", ...)                           // each of "v1", "v0.1", "0.1.0", "v1.2.3+build", "v01.2.3" throws /version/ and the message contains the value
it("rejects a missing image", ...)                               // delete digests["auth-proxy"] -> throws /auth-proxy/
it("rejects an unexpected image", ...)                           // digests["titiler-old"] = D("a") -> throws /titiler-old/
it("rejects a malformed digest", ...)                            // "sha256:ABC…" (uppercase), "abc", "" -> throws naming the key
it("reports every problem at once", ...)                         // bad sha + missing app -> message matches both /gitSha/ and /app/
it("rejects a malformed repository or sha", ...)                 // "stac-higher" (no owner), "a/b/c"; sha of 39 chars
it("reads one digest file per image and trims whitespace", ...)  // mkdtemp, write "<key>" files with "sha256:…\n" -> equals digests
it("CLI writes the manifest and exits 0", ...)                   // execFileSync(process.execPath, [script, …flags]) -> JSON.parse(file).images.app.digest === digests.app; file ends with "\n"
it("CLI exits 1 with a message when a digest file is missing", ...) // spawnSync -> status 1, stderr matches /^release-manifest: .*auth-proxy/m
```

- [ ] **Step 2: Run to verify failure**

Run: `cd app && npx vitest run src/__tests__/release-manifest.test.ts`
Expected: FAIL, cannot resolve `../../../.github/scripts/release-manifest.mjs`.

- [ ] **Step 3: Implement `.github/scripts/release-manifest.mjs`**

The header comment says the output is the contract `docs/releases.md` documents and the chart's `global.imageDigests` reads (spec §6.2, §11). Adding an image means adding it to `IMAGE_KEYS`, the workflow and the docs table in the same PR. Lowercase owner and repo for the `ghcr.io` refs only.

- [ ] **Step 4: Run to verify pass, then the full gate**

Run: `cd app && npx vitest run src/__tests__/release-manifest.test.ts`, then `npm run verify` from the repo root.
Expected: PASS; verify green (astro check accepts the `.mjs` import through `allowJs`; nothing is type-checked inside it).

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release-manifest.mjs app/src/__tests__/release-manifest.test.ts
git commit -m "R-3: release-manifest generator + unit tests"
```

---

### Task 5: Containers workflow — auth-proxy leg, release attestations, digest export

**Files:**
- Modify: `.github/workflows/containers.yml`

**Interfaces:**
- Produces: one workflow artifact per build job named `digest-<name>` (matrix) or `digest-process-runtime` (bake), holding files `<key>` whose content is one `sha256:…` line. Task 6's job downloads them with `pattern: digest-*`, `merge-multiple: true`. File names are exactly the manifest keys.

- [ ] **Step 1: Add the matrix leg**

After `image-scanner` in `build.strategy.matrix.image`:
```yaml
          - name: auth-proxy
            # The derived stac-auth-proxy (ADR 0015 write policy + the
            # AUTH_BEARER_AUDIENCE_CLAIM check, spec §8). Repo-root context:
            # .dockerignore re-includes services/proxy-policy. The upstream
            # base is multi-arch, so the default platforms apply.
            dockerfile: infra/proxy-policy/Dockerfile
            context: .
```

- [ ] **Step 2: Release-only attestations**

Add `RELEASE: ${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}` beside `PUSH` in **both** jobs' `env`. Give the build step `id: build` and add:
```yaml
          provenance: ${{ env.RELEASE == 'true' && 'mode=max' || '' }}
          sbom: ${{ env.RELEASE == 'true' && 'true' || '' }}
```
Give the bake step `id: bake` and the same two inputs (bake-action applies them to every target). An empty string keeps each action's default, so PR and `main` builds behave as today.

- [ ] **Step 3: Record and upload each digest**

Matrix job, after the build step, before Trivy:
```yaml
      - name: Record digest
        env:
          NAME: ${{ matrix.image.name }}
          DIGEST: ${{ steps.build.outputs.digest }}
          IMAGEID: ${{ steps.build.outputs.imageid }}
        run: |
          set -euo pipefail
          if [ -z "$DIGEST" ]; then
            if [ "$PUSH" = "true" ]; then echo "::error::pushed $NAME but build-push-action returned no digest"; exit 1; fi
            DIGEST="$IMAGEID"   # unpushed build: the dry-run manifest gets the local image id
          fi
          mkdir -p digests && printf '%s\n' "$DIGEST" > "digests/$NAME"
      - uses: actions/upload-artifact@v7
        with:
          name: digest-${{ matrix.image.name }}
          path: digests/${{ matrix.image.name }}
          if-no-files-found: error
          retention-days: 1
```
Runtime job, after bake: the same step reading `METADATA: ${{ steps.bake.outputs.metadata }}` with `jq -r --arg t "$target" '.[$t]["containerimage.digest"] // empty'` for `runtime` → file `process-runtime` and `stactools` → file `process-runtime-stactools`. It uses the same empty-digest rule, falling back to `["containerimage.config.digest"]` only when `PUSH != true`. Upload `digests/` as `digest-process-runtime`.

- [ ] **Step 4: Validate**

Run: `command -v actionlint && actionlint .github/workflows/containers.yml` (if missing, CI's actionlint job validates).
Expected: no findings. The PR's Containers run shows `Image · auth-proxy` green and eight `digest-*` files across seven artifacts (acceptance: "the containers workflow builds `auth-proxy` on a PR").

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/containers.yml
git commit -m "R-3: containers — publish auth-proxy, provenance + SBOM on v* builds, export digests"
```

---

### Task 6: Manifest job, release attachment, release docs

**Files:**
- Modify: `.github/workflows/containers.yml` (new job), `.github/workflows/release.yml`
- Create: `docs/releases.md`
- Modify: `docs/README.md`, `docs/FEATURES.md`, `docs/backend.md`

**Interfaces:**
- Consumes: Task 4's CLI flags; Task 5's `digest-*` artifacts.
- Produces: workflow artifact `release-manifest` on every Containers run; release asset `release-manifest.json` on `v*`.

- [ ] **Step 1: Add the `release-manifest` job to `containers.yml`**

```yaml
  release-manifest:
    # Collects every image digest into release-manifest.json (docs/releases.md).
    # Every run produces a dry-run artifact (version v0.0.0-dryrun.<run>), so
    # the wiring is proven on each PR. On a v* tag the file is attached to the
    # GitHub release.
    name: Release manifest
    needs: [build, runtime]
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    env:
      RELEASE: ${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with:
          node-version: 22
      - uses: actions/download-artifact@v7
        with:
          pattern: digest-*
          merge-multiple: true
          path: digests
      - name: Generate
        env:
          VERSION: ${{ env.RELEASE == 'true' && github.ref_name || format('v0.0.0-dryrun.{0}', github.run_number) }}
        run: >
          node .github/scripts/release-manifest.mjs --version "$VERSION" --git-sha "$GITHUB_SHA"
          --repository "$GITHUB_REPOSITORY" --digests-dir digests --out release-manifest.json
      - uses: actions/upload-artifact@v7
        with:
          name: release-manifest
          path: release-manifest.json
          retention-days: 7
      - name: Attach to the GitHub release
        if: env.RELEASE == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ github.ref_name }}
        # release.yml creates the release and usually finishes first; either
        # side may create it, and the loser uploads to it.
        run: |
          set -euo pipefail
          if gh release view "$TAG" >/dev/null 2>&1; then
            gh release upload "$TAG" release-manifest.json --clobber
          else
            gh release create "$TAG" --verify-tag --generate-notes release-manifest.json \
              || gh release upload "$TAG" release-manifest.json --clobber
          fi
```

- [ ] **Step 2: Make `release.yml` tolerate an existing release**

Change its create step to:
```bash
assets=("stac-higher-app-${GITHUB_REF_NAME}.tar.gz" services/pipeline/dist/*)
gh release create "$GITHUB_REF_NAME" --verify-tag --generate-notes "${assets[@]}" \
  || gh release upload "$GITHUB_REF_NAME" --clobber "${assets[@]}"
```
Add a one-line comment pointing at `containers.yml`'s `release-manifest` job. This keeps both workflows independent: a lost race costs the notes nothing, because the creator generated them.

- [ ] **Step 3: Dry-run the generator locally (PR sample)**

```bash
SCRATCH=$(mktemp -d); mkdir "$SCRATCH/digests"
for k in app pipeline titiler cube-server image-scanner process-runtime process-runtime-stactools auth-proxy; do
  printf 'sha256:%s\n' "$(printf '%s' "$k" | shasum -a 256 | cut -c1-64)" > "$SCRATCH/digests/$k"; done
node .github/scripts/release-manifest.mjs --version v0.0.0-dryrun.1 --git-sha "$(git rev-parse HEAD)" \
  --repository "$(gh repo view --json nameWithOwner --jq .nameWithOwner)" --digests-dir "$SCRATCH/digests" --out "$SCRATCH/release-manifest.json"
cat "$SCRATCH/release-manifest.json"
```
Expected: exit 0, eight images, `ghcr.io/techtraverse/stac-higher-*:0.0.0-dryrun.1` refs. Tell the lead the path. The lead attaches the PR's CI `release-manifest` artifact (real digests) to the PR (acceptance criterion).

- [ ] **Step 4: Write `docs/releases.md`**

This is a new reference doc rather than a `backend.md` section: `backend.md` is the local stack, app env and routes, while this file's reader is a deployment repo and the chart. The header names spec §5/§11 and ADR 0024. Sections:
1. **What a `v*` tag publishes**: the images (tags `X.Y.Z`, `X.Y`, `sha-<long>`), the GitHub release (app dist tarball, pipeline wheel + sdist, `release-manifest.json`), the chart (R-9, not yet), and the modules (the tag).
2. **Published images**: table of key → GHCR repository (`ghcr.io/<owner>/<repo>-<key>`) → built from (Dockerfile/bake target) → platforms. The eight rows; titiler linux/amd64 only.
3. **Attestations**: `v*` images carry SLSA provenance (`mode=max`) and an SPDX SBOM as in-index attestation manifests. Inspect with `docker buildx imagetools inspect <ref> --format '{{json .Provenance}}'` and `'{{json .SBOM}}'`. `main` images carry only the default minimal provenance. Not signed (cosign deferred, spec §12).
4. **Release manifest contract**: the JSON example (from Task 4's first test). Field rules: `digest` is the multi-arch index digest; pull by `<ref without tag>@<digest>`; the tag in `ref` is informational and mutable. `chart.version` is the tag without `v`; `terraformModules.ref` is a ready `source`. Stability: keys are never renamed or removed; adding an image adds a key and keeps `schemaVersion: 1`; changing a field's meaning bumps `schemaVersion`. Dry runs: every Containers run uploads a `release-manifest` artifact versioned `v0.0.0-dryrun.<run>`; on unpushed builds the digests are local image ids and not pullable.
5. **Feeding the chart**: `jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml` (JSON is valid YAML). Keys are the image keys verbatim (`index .Values.global.imageDigests "cube-server"` in templates, R-6/R-9).

- [ ] **Step 5: Index and cross-links**

- `docs/README.md` reference list: `- [releases.md](releases.md) — what a v* tag publishes, the image list, attestations, and the release-manifest contract the chart reads (Phase 8).`
- `docs/backend.md` "Auth-enforced overlay": one sentence saying the derived proxy image is also published as `stac-higher-auth-proxy` (see `releases.md`) and honours `AUTH_BEARER_AUDIENCE_CLAIM` (`auth.md`).
- `docs/FEATURES.md`: update the CI/CD row "Container images + releases". It now reads: all eight GHCR images incl. the derived auth-proxy, provenance + SBOM on `v*`, `release-manifest.json` on each release, entry points `containers.yml`, `release.yml`, `.github/scripts/release-manifest.mjs`, `docs/releases.md`. Add `proxy-policy` to the CI gate row's job list. Under "Phase 8", add an R-3 line (or a row, if another R slice has already added a Deployment-artifacts table; rebase first).

- [ ] **Step 6: Validate and commit**

Run: `command -v actionlint && actionlint` (if missing, CI validates), then `npm run verify` from the repo root.
Expected: green.
```bash
git add .github/workflows/containers.yml .github/workflows/release.yml docs/releases.md docs/README.md docs/FEATURES.md docs/backend.md
git commit -m "R-3: release-manifest job attaches image digests to v* releases; docs/releases.md"
```

---

## Lead-only steps (PR body)

- Attach the PR run's `release-manifest` artifact (dry run, real digests) to the PR.
- Auth-enforced live confirm (ADR 0015 / P7-Z), because the derived image's entry point changed: `docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --build --wait`, then `node --test tests/integration/proxy-policy.test.mjs tests/integration/proxy-enforcement.test.mjs` (default `aud` mode must be unchanged).
- First `v0.x` tag, with R-9: check the release carries `release-manifest.json` and that `docker buildx imagetools inspect ghcr.io/<owner>/<repo>-auth-proxy:<X.Y.Z> --format '{{json .SBOM}}'` is non-empty.
- Hand R-1's planner the proxy's `AUTH_BEARER_AUDIENCE_CLAIM` semantics (Global Constraints) so the app mirrors them, and hand R-9 the chart OCI path `oci://ghcr.io/<owner>/charts/stac-higher` the manifest already names.
