"""Hardware profiles (K-1, process-compute spec §3) — the Python half.

A deployment describes the hardware a run may ask for as named PROFILES —
a CPU/memory range, an optional accelerator with a GPU-count range, a
queue-wait promise and a base image — in one JSON document both runtimes
read. The app validates it strictly at its write gate and serves it to the
UI; this reader is LENIENT in the established direction (an image built
before a key was added must not brick), keeps ``backend`` opaque until the
executors consume it (K-3 Docker, K-5 Kubernetes), and re-checks a run's
``hardware`` block against the bounds at launch, independently of the app.

**Packaging:** the deployment set reaches the image as ``COPY --from=hardware``
out of a named build context pointing at ``infra/hardware-profiles``
(compose ``additional_contexts``, CI ``build-contexts``) and the image
publishes the copy's path in ``PROCESS_HARDWARE_PROFILES_FILE``; unset (dev,
pytest) means the repo checkout's ``local.json``. One file, no vendored copy.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIERS = ("cpu", "cpu-large", "gpu")
DEFAULT_HARDWARE_PROFILE = "standard"
#: What an absent `hardware` block means on BOTH sides — must equal the
#: shipped sets' `standard.cpu.default` (tests pin it).
DEFAULT_HARDWARE_CPU = 1.0
DEFAULT_HARDWARE_GPU_COUNT = 0
PROFILES_ENV_VAR = "PROCESS_HARDWARE_PROFILES_FILE"
_CHECKOUT_PROFILES = (
    Path(__file__).resolve().parents[5] / "infra" / "hardware-profiles" / "local.json"
)
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class HardwareProfileError(ValueError):
    """A profile document the reader cannot use, or a run outside its profile's bounds."""


@dataclass(frozen=True)
class Bounds:
    min: float
    max: float
    default: float


@dataclass(frozen=True)
class HardwareProfile:
    id: str
    label: str
    description: str
    tier: str
    accelerator: dict[str, Any] | None
    cpu: Bounds
    memory_mb: Bounds
    gpu_count: Bounds | None
    max_queue_wait_seconds: int
    image: str | None
    #: Pipeline-only, opaque here: K-3 reads `backend["docker"]`, K-5 `backend["kubernetes"]`.
    backend: dict[str, Any]


@dataclass(frozen=True)
class HardwareProfileSet:
    version: int
    profiles: tuple[HardwareProfile, ...]

    def get(self, profile_id: str) -> HardwareProfile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    @property
    def standard(self) -> HardwareProfile:
        profile = self.get(DEFAULT_HARDWARE_PROFILE)
        assert profile is not None  # parse_hardware_profiles guarantees it
        return profile


def _number(raw: Any, what: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise HardwareProfileError(f"{what} must be a number, got {raw!r}")
    return float(raw)


def _bounds(raw: Any, what: str) -> Bounds:
    if not isinstance(raw, dict):
        raise HardwareProfileError(f"{what} must be an object with min/max/default, got {raw!r}")
    b = Bounds(_number(raw.get("min"), f"{what}.min"), _number(raw.get("max"), f"{what}.max"),
               _number(raw.get("default"), f"{what}.default"))
    if not (b.min <= b.default <= b.max):
        raise HardwareProfileError(f"{what} must satisfy min <= default <= max, got {raw!r}")
    return b


def _profile(raw: Any) -> HardwareProfile:
    if not isinstance(raw, dict):
        raise HardwareProfileError(f"profile must be an object, got {raw!r}")
    profile_id = raw.get("id")
    if not isinstance(profile_id, str) or not _ID_RE.match(profile_id):
        raise HardwareProfileError(f"profile id must match {_ID_RE.pattern}, got {profile_id!r}")
    what = f"profile {profile_id!r}"
    tier = raw.get("tier")
    if tier not in TIERS:
        raise HardwareProfileError(f"{what}: tier must be one of {TIERS}, got {tier!r}")
    accelerator = raw.get("accelerator")
    if accelerator is not None and not isinstance(accelerator, dict):
        raise HardwareProfileError(f"{what}: accelerator must be null or an object")
    gpu_raw = raw.get("gpu_count")
    gpu_count = None if gpu_raw is None else _bounds(gpu_raw, f"{what}.gpu_count")
    if (accelerator is None) != (gpu_count is None):
        raise HardwareProfileError(
            f"{what}: gpu_count is required with an accelerator and forbidden without one"
        )
    wait = raw.get("max_queue_wait_seconds")
    if isinstance(wait, bool) or not isinstance(wait, int) or wait < 0:
        raise HardwareProfileError(f"{what}: max_queue_wait_seconds must be a non-negative integer")
    image = raw.get("image")
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise HardwareProfileError(f"{what}: image must be null or a non-empty string")
    backend = raw.get("backend") or {}
    if not isinstance(backend, dict):
        raise HardwareProfileError(f"{what}: backend must be an object")
    return HardwareProfile(
        id=profile_id,
        label=str(raw.get("label") or profile_id),
        description=str(raw.get("description") or ""),
        tier=tier,
        accelerator=dict(accelerator) if accelerator is not None else None,
        cpu=_bounds(raw.get("cpu"), f"{what}.cpu"),
        memory_mb=_bounds(raw.get("memory_mb"), f"{what}.memory_mb"),
        gpu_count=gpu_count,
        max_queue_wait_seconds=wait,
        image=image,
        backend=dict(backend),
    )


def parse_hardware_profiles(document: Any) -> HardwareProfileSet:
    """Read a profile document. Lenient: unknown keys are ignored; anything
    present but unusable, a missing `standard`, or a duplicate id raises."""
    if not isinstance(document, dict) or document.get("version") != 1:
        raise HardwareProfileError("hardware profiles: version must be 1")
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise HardwareProfileError("hardware profiles: profiles must be a non-empty list")
    profiles = tuple(_profile(p) for p in raw_profiles)
    ids = [p.id for p in profiles]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise HardwareProfileError(f"hardware profiles: duplicate ids {dupes}")
    if DEFAULT_HARDWARE_PROFILE not in ids:
        raise HardwareProfileError(
            f"hardware profiles: exactly one profile must be {DEFAULT_HARDWARE_PROFILE!r}"
        )
    return HardwareProfileSet(version=1, profiles=profiles)


def hardware_profiles_path(env: dict[str, str] | None = None) -> Path:
    """`PROCESS_HARDWARE_PROFILES_FILE` when set (the image), else the checkout's local set."""
    override = (os.environ if env is None else env).get(PROFILES_ENV_VAR)
    if override:
        return Path(override)
    return _CHECKOUT_PROFILES


def load_hardware_profiles(path: Path | None = None) -> HardwareProfileSet:
    where = path or hardware_profiles_path()
    try:
        document = json.loads(where.read_text())
    except (OSError, ValueError) as exc:
        raise HardwareProfileError(
            f"could not read the hardware profiles at {where}: {exc}"
        ) from exc
    return parse_hardware_profiles(document)


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def check_hardware_bounds(
    *, profiles: HardwareProfileSet, profile_id: str, cpu: float, gpu_count: int, memory_mb: int
) -> HardwareProfile:
    """The launch-time half of the dual enforcement (spec §4). Returns the
    resolved profile; raises with the SAME message text the app's write gate
    produces (the fixture's `bounds_cases[].reason` pins both)."""
    profile = profiles.get(profile_id)
    if profile is None:
        raise HardwareProfileError(
            f"hardware.profile {profile_id!r} is not a hardware profile of this deployment"
        )
    if not (profile.cpu.min <= cpu <= profile.cpu.max):
        raise HardwareProfileError(
            f"hardware.cpu {_fmt(cpu)} is outside profile {profile_id!r} bounds "
            f"{_fmt(profile.cpu.min)}–{_fmt(profile.cpu.max)}"  # noqa: RUF001 -- en dash, pinned by the fixture
        )
    if profile.gpu_count is None:
        if gpu_count != 0:
            raise HardwareProfileError(
                f"hardware.gpu_count must be 0: profile {profile_id!r} has no accelerator"
            )
    elif not (profile.gpu_count.min <= gpu_count <= profile.gpu_count.max):
        raise HardwareProfileError(
            f"hardware.gpu_count {gpu_count} is outside profile {profile_id!r} bounds "
            f"{_fmt(profile.gpu_count.min)}–{_fmt(profile.gpu_count.max)}"  # noqa: RUF001
        )
    if not (profile.memory_mb.min <= memory_mb <= profile.memory_mb.max):
        raise HardwareProfileError(
            f"memory_mb {memory_mb} is outside profile {profile_id!r} bounds "
            f"{_fmt(profile.memory_mb.min)}–{_fmt(profile.memory_mb.max)}"  # noqa: RUF001
        )
    return profile
