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

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.images.reference import registry_host
from pipeline.images.scan_result import ScanResult, ScanResultError

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


# ---------------------------------------------------------------------------
# evaluate (spec section 7.3) - pure; reasons are stable strings the dashboard
# shows verbatim. It works over `kev` (complete) and `top` (<= 25 findings by
# RISK, not severity, so a page of high-risk HIGHs can push a lower-risk
# CRITICAL out of `top` entirely). `counts`/`fixed_counts` are the fallback
# for what fell out of `top`: a fixed CRITICAL, an unfixed CRITICAL (whose age
# is then unknowable), or an unfixed HIGH (when the policy blocks on it) that
# `top` does not carry is still counted and blocks, unnamed (`critical_fixed:
# *`, `critical_unfixed_age:*:unknown`, `high_unfixed:*`). A fixed HIGH
# outside `top` does NOT block: its EPSS is unknowable, the same as a finding
# with a null EPSS.
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
        """The spec section 4.1 ``verdict`` jsonb (``container_images.verdict``
        and ``image_scans.result.verdict``)."""
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
        unfixed_critical_in_top = 0
        for f in result.top:
            if f.severity == "critical" and not f.fixed_in:
                unfixed_critical_in_top += 1
                age = _age_days(f.published_at, now)
                if age is None:
                    add(f"critical_unfixed_age:{f.id}:unknown")
                elif age > block.critical_unfixed_older_than_days:
                    add(f"critical_unfixed_age:{f.id}:{age}d")
        unfixed_critical_total = result.counts.get("critical", 0) - result.fixed_counts.get(
            "critical", 0
        )
        if unfixed_critical_total > unfixed_critical_in_top:
            add("critical_unfixed_age:*:unknown")
    if block.high_fixed_epss_at_least is not None:
        # A fixed HIGH outside `top` has no EPSS to check, so it never blocks
        # here, the same as a finding whose EPSS is explicitly null.
        for f in result.top:
            if (
                f.severity == "high"
                and f.fixed_in
                and f.epss is not None
                and f.epss >= block.high_fixed_epss_at_least
            ):
                add(f"high_fixed_epss:{f.id}:{format(f.epss, 'g')}")
    if block.high_unfixed:
        unfixed_high_in_top = 0
        for f in result.top:
            if f.severity == "high" and not f.fixed_in:
                unfixed_high_in_top += 1
                add(f"high_unfixed:{f.id}")
        unfixed_high_total = result.counts.get("high", 0) - result.fixed_counts.get("high", 0)
        if unfixed_high_total > unfixed_high_in_top:
            add("high_unfixed:*")
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
