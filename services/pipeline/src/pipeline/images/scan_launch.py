"""One image scan as a platform run (C-2, container-images spec §6.2).

The scanner parses hostile image content, so it gets exactly the process
posture (ADR 0021): its own container through the same Executor a process
uses, the policy's ``scan_limits``, the scanner network, an STS credential
bounded to ``scans/{image_id}/{scan_id}/`` (plus read on the stored SBOM's
prefix for a rescan), and nothing of the platform's -- no DB URL, no master
key, no platform keys. A registry credential reaches only an ADMISSION
scan's environment; a rescan pulls nothing.

This module is synchronous (the Engine API client is): the job runs it in
``asyncio.to_thread`` so a 15-minute scan never blocks the event loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pipeline.config import Settings
from pipeline.images.policy import ImagePolicy
from pipeline.images.repo import ImageRow
from pipeline.images.scan_result import ScanResultError
from pipeline.process.credentials import RunCredentials, mint_prefix_credentials
from pipeline.process.executor import Executor, ExitStatus, RegistryAuth, RunSpec
from pipeline.process.logs import store_log
from pipeline.storage.keys import SCANS_PREFIX, image_scan_log_key, image_scan_prefix
from pipeline.storage.platform import get_object_range, head_object

SCANNER_RUN_KIND = "image_scan"
#: result.json is a summary (top <= 25); anything this large is not one.
RESULT_MAX_BYTES = 1024 * 1024

#: A scan id shaped path segment (the drain's own scan ids are uuid4s, and a
#: legitimate `sbom_ref` always names one -- ruling: `sbom_read_prefix` must
#: refuse anything else, including a flat `scans/{image_id}/sbom.json`).
_SCAN_ID_SEGMENT_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


@dataclass(frozen=True)
class ScanRun:
    status: ExitStatus
    handle_id: str
    #: None when the log could not be stored (the verdict never depends on it).
    log_ref: str | None


def _identity(image: ImageRow) -> dict[str, Any]:
    return {
        "digest": image.digest,
        "platform_digest": image.platform_digest,
        "platform": image.platform,
        "size_bytes": image.size_bytes,
        "config": image.config,
    }


def scan_env(
    settings: Settings,
    policy: ImagePolicy,
    *,
    scan_id: str,
    image: ImageRow,
    kind: str,
    registry_auth: RegistryAuth | None,
) -> dict[str, str]:
    """The scanner's job (spec §6.2 plus Decision 19's four variables)."""
    env = {
        "STAC_HIGHER_SCAN_ID": scan_id,
        "STAC_HIGHER_SCAN_KIND": kind,
        "STAC_HIGHER_IMAGE_REF": image.reference,
        "STAC_HIGHER_IMAGE_TAG": image.tag_at_add,
        "STAC_HIGHER_PLATFORM": policy.platform,
        "STAC_HIGHER_MAX_IMAGE_BYTES": str(policy.max_image_bytes),
        "STAC_HIGHER_ALLOWED_REGISTRIES": ",".join(policy.allowed_registries),
        "STAC_HIGHER_DB_UPDATE": "1" if settings.image_scanner_db_update else "0",
    }
    if settings.grype_db_update_url:
        env["GRYPE_DB_UPDATE_URL"] = settings.grype_db_update_url
    if kind == "rescan":
        env["STAC_HIGHER_SBOM_KEY"] = image.sbom_ref or ""
        env["STAC_HIGHER_IMAGE_IDENTITY"] = json.dumps(_identity(image))
    elif registry_auth is not None:
        env["REGISTRY_USERNAME"] = registry_auth.username
        env["REGISTRY_PASSWORD"] = registry_auth.password
    return env


def sbom_read_prefix(image: ImageRow) -> str:
    """The stored SBOM's scan prefix, which a rescan may read.

    Must be shaped exactly ``scans/{image_id}/{scan_id}/...`` -- a flat key
    directly under the image's own ``scans/{image_id}/`` (no scan-id
    segment) is refused, since granting read there would grant the image's
    WHOLE scan history rather than the one scan the SBOM actually came from
    (ruling)."""
    ref = image.sbom_ref or ""
    parts = ref.split("/")
    valid = (
        len(parts) >= 4
        and parts[0] == SCANS_PREFIX
        and parts[1] == image.id
        and _SCAN_ID_SEGMENT_RE.fullmatch(parts[2]) is not None
        and all(parts)
        and ".." not in ref
    )
    if not valid:
        raise ValueError(f"the stored SBOM {ref!r} is outside the image's scan prefix")
    return "/".join(parts[:3]) + "/"


def build_scan_spec(
    settings: Settings,
    policy: ImagePolicy,
    *,
    scan_id: str,
    image: ImageRow,
    kind: str,
    credentials: RunCredentials,
    registry_auth: RegistryAuth | None,
) -> RunSpec:
    env = scan_env(
        settings, policy, scan_id=scan_id, image=image, kind=kind, registry_auth=registry_auth
    )
    env.update(credentials.as_env())  # platform-controlled, applied last
    return RunSpec(
        run_id=scan_id,
        process_id=image.id,
        image=settings.image_scanner_image,
        env=env,
        memory_mb=policy.scan_memory_mb,
        timeout_seconds=policy.scan_timeout_seconds,
        network=settings.process_scanner_network,
        kind=SCANNER_RUN_KIND,
    )


def execute_scan(
    executor: Executor,
    settings: Settings,
    policy: ImagePolicy,
    storage_client,
    *,
    scan_id: str,
    image: ImageRow,
    kind: str,
    registry_auth: RegistryAuth | None,
    sts_client=None,
) -> ScanRun:
    """Mint, launch, wait (the policy timeout), capture the log, ALWAYS reap."""
    read_prefixes = (sbom_read_prefix(image),) if kind == "rescan" else ()
    credentials = mint_prefix_credentials(
        settings,
        session_name=f"stac-scan-{scan_id}",
        prefix=image_scan_prefix(image.id, scan_id),
        timeout_seconds=policy.scan_timeout_seconds,
        sts_client=sts_client,
        read_prefixes=read_prefixes,
    )
    spec = build_scan_spec(
        settings,
        policy,
        scan_id=scan_id,
        image=image,
        kind=kind,
        credentials=credentials,
        registry_auth=registry_auth,
    )
    handle = executor.launch(spec)
    try:
        status = executor.wait(handle, policy.scan_timeout_seconds)
        payload = executor.logs(handle, settings.process_log_max_bytes)
    finally:
        executor.reap(handle)
    log_ref = store_log(
        storage_client,
        settings.staging_bucket,
        image_scan_log_key(image.id, scan_id),
        payload,
        settings.process_log_max_bytes,
        context={"scan_id": scan_id, "image_id": image.id},
    )
    return ScanRun(status=status, handle_id=handle.id, log_ref=log_ref)


def read_scan_result(storage_client, bucket: str, key: str) -> dict[str, Any] | None:
    """The scanner's result.json, or None when it wrote none. UNTRUSTED: the
    caller parses it with ``parse_scan_result``.

    M1: the HEAD below is only a fast pre-check for the common "no result"
    case -- a compromised scanner could swap the object for an oversized one
    in the window between a HEAD and a separate full GET, so the cap is
    enforced on the GET's own returned length. The GET itself is RANGED
    (``bytes=0-RESULT_MAX_BYTES``), so an oversized object is never read in
    full: the drain asks for one byte more than the cap, and if that many
    come back, the object exceeds it.
    """
    try:
        head_object(storage_client, bucket, key)
    except Exception:  # absent (or unreadable): the scanner left no result
        return None
    body = get_object_range(storage_client, bucket, key, f"bytes=0-{RESULT_MAX_BYTES}")
    if len(body) > RESULT_MAX_BYTES:
        raise ScanResultError(f"result.json exceeds the cap of {RESULT_MAX_BYTES} bytes")
    try:
        doc = json.loads(body)
    except ValueError as err:
        raise ScanResultError("result.json is not JSON") from err
    return doc if isinstance(doc, dict) else {"version": None}
