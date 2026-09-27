"""The scanner's ``result.json`` (C-1, container-images spec section 6.4).

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

import math
from dataclasses import dataclass, field
from typing import Any

from pipeline.images.reference import is_image_digest, is_image_reference
from pipeline.images.status import SCAN_KINDS

SEVERITIES = ("critical", "high", "medium", "low", "negligible", "unknown")
MAX_TOP_FINDINGS = 25
SCAN_RESULT_VERSION = 1


class ScanResultError(ValueError):
    """The scanner's result is not a usable section 6.4 document."""


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
    if not math.isfinite(raw):
        raise ScanResultError(f"{what} must be a finite number")
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
