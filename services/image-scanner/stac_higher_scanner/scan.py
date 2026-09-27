"""The scanner's entrypoint (container-images spec §6.3).

Admission: refuse a registry outside the policy, resolve tag -> digest ->
platform manifest (refusing an oversized image before any layer is read),
``syft`` the platform digest into syft-json + CycloneDX, ``grype`` the SBOM,
write the objects, then ``result.json``. Rescan: ``grype`` the stored SBOM
only (nothing is pulled). Drift (spec §8.2) is C-4's.

Exit codes: 0 a result was written; 1 an error result was written; 2 no
result could be written (the drain records that as a failed scan itself).
The registry credential is read once and removed from the environment the
binaries inherit; no message names it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stac_higher_scanner.registry import (
    DOCKER_HUB_HOSTS,
    Credentials,
    RegistryClient,
    RegistryError,
    registry_allowed,
    split_reference,
)
from stac_higher_scanner.store import ObjectStore
from stac_higher_scanner.summary import build_summary, published_dates

RESULT_VERSION = 1
EXIT_OK = 0
EXIT_SCAN_FAILED = 1
EXIT_PLATFORM_ERROR = 2
ERROR_MAX_CHARS = 1000
#: go-containerregistry names Docker Hub `index.docker.io`; Syft matches its
#: auth entries against that name (Decision 24: C-5 confirms it live).
SYFT_AUTHORITY_DOCKER_HUB = "index.docker.io"
_TOOL_ENV_KEEP = ("PATH", "HOME", "TMPDIR", "GRYPE_DB_CACHE_DIR", "GRYPE_DB_UPDATE_URL")


class ScanRefused(Exception):
    """The scan cannot proceed; the message is the result's ``error``."""


@dataclass(frozen=True)
class ScanConfig:
    scan_id: str
    kind: str
    reference: str
    tag: str
    platform: str
    max_image_bytes: int
    allowed_registries: tuple[str, ...]
    bucket: str
    prefix: str
    sbom_key: str | None = None
    identity: dict[str, Any] | None = None
    db_update: bool = False
    credentials: Credentials | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> ScanConfig:
        def need(name: str) -> str:
            value = (env.get(name) or "").strip()
            if not value:
                raise ScanRefused(f"{name} is not set")
            return value

        kind = need("STAC_HIGHER_SCAN_KIND")
        if kind not in ("admission", "rescan"):
            raise ScanRefused(f"STAC_HIGHER_SCAN_KIND must be admission or rescan, got {kind!r}")
        prefix = need("STAC_HIGHER_OUTPUT_PREFIX")
        if not prefix.endswith("/"):
            raise ScanRefused("STAC_HIGHER_OUTPUT_PREFIX must end with /")
        sbom_key = identity = None
        if kind == "rescan":
            sbom_key = need("STAC_HIGHER_SBOM_KEY")
            identity = json.loads(need("STAC_HIGHER_IMAGE_IDENTITY"))
            if not isinstance(identity, dict):
                raise ScanRefused("STAC_HIGHER_IMAGE_IDENTITY must be a JSON object")
        username = env.get("REGISTRY_USERNAME") or ""
        password = env.get("REGISTRY_PASSWORD") or ""
        return cls(
            scan_id=need("STAC_HIGHER_SCAN_ID"),
            kind=kind,
            reference=need("STAC_HIGHER_IMAGE_REF"),
            tag=need("STAC_HIGHER_IMAGE_TAG"),
            platform=need("STAC_HIGHER_PLATFORM"),
            max_image_bytes=int(need("STAC_HIGHER_MAX_IMAGE_BYTES")),
            allowed_registries=tuple(
                p.strip() for p in need("STAC_HIGHER_ALLOWED_REGISTRIES").split(",") if p.strip()
            ),
            bucket=need("STAC_HIGHER_OUTPUT_BUCKET"),
            prefix=prefix,
            sbom_key=sbom_key,
            identity=identity,
            db_update=env.get("STAC_HIGHER_DB_UPDATE") == "1",
            credentials=Credentials(username, password) if username and password else None,
        )


def tool_env(env: dict[str, str]) -> dict[str, str]:
    """The binaries' environment, built from scratch: no AWS keys, no
    registry secret (Syft gets the latter per call, Grype never does)."""
    out = {k: env[k] for k in _TOOL_ENV_KEEP if env.get(k)}
    out.update(
        {
            "GRYPE_DB_AUTO_UPDATE": "false",
            # The baked DB is days old by design; its age is reported, not
            # refused (Decision 4).
            "GRYPE_DB_VALIDATE_AGE": "false",
            "GRYPE_CHECK_FOR_APP_UPDATE": "false",
            "SYFT_CHECK_FOR_APP_UPDATE": "false",
        }
    )
    return out


def syft_auth_env(cfg: ScanConfig) -> dict[str, str]:
    if cfg.credentials is None:
        return {}
    host, _ = split_reference(cfg.reference)
    authority = SYFT_AUTHORITY_DOCKER_HUB if host in DOCKER_HUB_HOSTS else host
    return {
        "SYFT_REGISTRY_AUTH_AUTHORITY": authority,
        "SYFT_REGISTRY_AUTH_USERNAME": cfg.credentials.username,
        "SYFT_REGISTRY_AUTH_PASSWORD": cfg.credentials.password,
    }


@dataclass
class Tools:
    """The two binaries, behind a seam the tests replace."""

    env: dict[str, str]

    def _run(self, args: list[str], *, extra: dict[str, str] | None = None, stdout=None) -> None:
        done = subprocess.run(
            args,
            env={**self.env, **(extra or {})},
            stdout=stdout if stdout is not None else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if done.returncode != 0:
            tail = done.stderr.decode("utf-8", "replace")[-2000:]
            name = " ".join(args[:2])
            print(f"[scanner] {name} exited {done.returncode}\n{tail}", file=sys.stderr)
            raise ScanRefused(f"{' '.join(args[:2])} exited {done.returncode}")

    def syft(self, source: str, syft_json: Path, cyclonedx: Path, auth_env: dict[str, str]) -> None:
        self._run(
            [
                "syft",
                "scan",
                source,
                "-o",
                f"syft-json={syft_json}",
                "-o",
                f"cyclonedx-json={cyclonedx}",
                "-q",
            ],
            extra=auth_env,
        )

    def grype(self, sbom: Path, out: Path) -> None:
        with out.open("wb") as fh:
            self._run(["grype", f"sbom:{sbom}", "-o", "json", "-q"], stdout=fh)

    def grype_db_update(self) -> bool:
        try:
            self._run(["grype", "db", "update"])
        except ScanRefused:
            return False
        return True

    def grype_db_status(self) -> dict[str, Any]:
        done = subprocess.run(
            ["grype", "db", "status", "-o", "json"], env=self.env, capture_output=True, check=False
        )
        try:
            doc = json.loads(done.stdout or b"{}")
        except ValueError:
            return {}
        return doc if isinstance(doc, dict) else {}


def db_file(status: dict[str, Any], env: dict[str, str]) -> Path:
    path = str(status.get("path") or "")
    if not path:
        return Path(env.get("GRYPE_DB_CACHE_DIR", "/opt/grype-db")) / "6" / "vulnerability.db"
    p = Path(path)
    return p if p.suffix == ".db" else p / "vulnerability.db"


def scanner_info(status: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    return {
        "syft": env.get("SYFT_VERSION") or "unknown",
        "grype": env.get("GRYPE_VERSION") or "unknown",
        "db_built_at": str(status.get("built") or "unknown"),
    }


def build_result(
    cfg: ScanConfig,
    identity: dict[str, Any],
    *,
    sbom_ref: str,
    findings_ref: str,
    scanner: dict[str, str],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """The spec §6.4 document, key for key."""
    return {
        "version": RESULT_VERSION,
        "kind": cfg.kind,
        "reference": cfg.reference,
        "tag": cfg.tag,
        "digest": identity["digest"],
        "platform_digest": identity["platform_digest"],
        "platform": identity["platform"],
        "size_bytes": identity["size_bytes"],
        "config": identity["config"],
        "scanner": scanner,
        "sbom_ref": sbom_ref,
        "findings_ref": findings_ref,
        "counts": summary["counts"],
        "fixed_counts": summary["fixed_counts"],
        "kev": summary["kev"],
        "max_risk": summary["max_risk"],
        "top": summary["top"],
        "tag_drift": None,
        "error": None,
    }


def failure_result(kind: str, reference: str, tag: str, error: str) -> dict[str, Any]:
    """The C-1 failure shape: identity plus ``error``."""
    message = (error or "").strip() or "scan failed"
    return {
        "version": RESULT_VERSION,
        "kind": kind,
        "reference": reference,
        "tag": tag,
        "error": message[:ERROR_MAX_CHARS],
    }


def run_scan(
    cfg: ScanConfig,
    store: ObjectStore,
    tools: Tools,
    workdir: Path,
    *,
    client_factory: Callable[..., RegistryClient] = RegistryClient,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    sbom = workdir / "sbom.syft.json"
    findings = workdir / "findings.grype.json"
    if cfg.kind == "admission":
        host, _ = split_reference(cfg.reference)
        if not registry_allowed(host, cfg.allowed_registries):
            raise ScanRefused(
                f"registry_not_allowed: {host} is outside the policy's allowed_registries"
            )
        resolved = client_factory(cfg.reference, cfg.credentials).resolve(
            cfg.tag, cfg.platform, cfg.max_image_bytes
        )
        cyclonedx = workdir / "sbom.cdx.json"
        tools.syft(
            f"registry:{cfg.reference}@{resolved.platform_digest}",
            sbom,
            cyclonedx,
            syft_auth_env(cfg),
        )
        sbom_ref = f"{cfg.prefix}sbom.syft.json"
        store.put_file(sbom_ref, sbom)
        store.put_file(f"{cfg.prefix}sbom.cdx.json", cyclonedx)
        identity = {
            "digest": resolved.digest,
            "platform_digest": resolved.platform_digest,
            "platform": resolved.platform,
            "size_bytes": resolved.size_bytes,
            "config": resolved.config,
        }
    else:
        assert cfg.sbom_key is not None and cfg.identity is not None
        store.get_file(cfg.sbom_key, sbom)
        sbom_ref = cfg.sbom_key
        identity = cfg.identity
    if cfg.db_update and not tools.grype_db_update():
        print("[scanner] grype db update failed; scanning with the baked database", file=sys.stderr)
    status = tools.grype_db_status()
    tools.grype(sbom, findings)
    findings_ref = f"{cfg.prefix}findings.grype.json"
    store.put_file(findings_ref, findings)
    grype_doc = json.loads(findings.read_text())
    if not isinstance(grype_doc, dict):
        raise ScanRefused("grype wrote something that is not a JSON object")
    database = db_file(status, env)
    summary = build_summary(grype_doc, published=lambda ids: published_dates(database, ids))
    return build_result(
        cfg,
        identity,
        sbom_ref=sbom_ref,
        findings_ref=findings_ref,
        scanner=scanner_info(status, env),
        summary=summary,
    )


def describe(exc: BaseException) -> str:
    if isinstance(exc, (ScanRefused, RegistryError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    try:
        cfg = ScanConfig.from_env(env)
        store = ObjectStore.from_env(env)
    except Exception as exc:  # no job, no store: nothing can be recorded
        print(f"[scanner] cannot start: {describe(exc)}", file=sys.stderr)
        return EXIT_PLATFORM_ERROR
    # The credential now lives only in cfg; the binaries never inherit it.
    for name in ("REGISTRY_USERNAME", "REGISTRY_PASSWORD"):
        os.environ.pop(name, None)
        env.pop(name, None)
    tools = Tools(env=tool_env(env))
    code = EXIT_OK
    with tempfile.TemporaryDirectory(prefix="scan-") as tmp:
        try:
            doc = run_scan(cfg, store, tools, Path(tmp), env=env)
        except Exception as exc:  # every failure becomes the result's error
            traceback.print_exc()
            doc = failure_result(cfg.kind, cfg.reference, cfg.tag, describe(exc))
            code = EXIT_SCAN_FAILED
    try:
        store.put_json(f"{cfg.prefix}result.json", doc)
    except Exception as exc:
        print(f"[scanner] result.json could not be written: {describe(exc)}", file=sys.stderr)
        return EXIT_PLATFORM_ERROR
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
