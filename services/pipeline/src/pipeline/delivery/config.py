"""Typed view over a delivery association's ``config`` jsonb (ROADMAP §5.1).

Python side of the cross-runtime contract: the app writes the config through
``app/src/lib/associations/schemas.ts`` (Zod, which applies every default) and
the pipeline reads the same JSON back out of ``collection_connections.config``.
Field names and default values MUST NOT drift from ``deliveryConfigSchema``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

ON_UPDATE = ("redeliver", "ignore")
OVERWRITE = ("never", "always", "if_newer")
CHECKSUMS = ("md5", "sha256")
BACKOFF = ("exponential", "fixed")

DEFAULT_MAX_CONCURRENT_TRANSFERS = 4
DEFAULT_MAX_ATTEMPTS = 5


class DeliveryConfigError(ValueError):
    """The stored ``config`` jsonb is not a usable delivery config."""


def _default_payload() -> dict[str, Any]:
    return {"item_json": False, "checksums": None, "completion_marker": False}


@dataclass(frozen=True)
class DeliveryConfig:
    path_template: str
    item_filter: str | None = None
    asset_keys: tuple[str, ...] | None = None
    payload: dict[str, Any] = field(default_factory=_default_payload)
    on_update: str = "redeliver"
    overwrite: str = "if_newer"
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff: str = "exponential"
    max_concurrent_transfers: int = DEFAULT_MAX_CONCURRENT_TRANSFERS


def _enum(raw: Any, allowed: Sequence[str], default: str, field_name: str) -> str:
    if raw is None:
        return default
    value = str(raw)
    if value not in allowed:
        raise DeliveryConfigError(f"{field_name} must be one of {allowed}, got {value!r}")
    return value


def _enum_or_none(raw: Any, allowed: Sequence[str], field_name: str) -> str | None:
    if raw is None:
        return None
    value = str(raw)
    if value not in allowed:
        raise DeliveryConfigError(f"{field_name} must be one of {allowed} or null, got {value!r}")
    return value


def _opt_str_list(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise DeliveryConfigError("asset_keys must be an array of strings or null")
    return tuple(str(item) for item in raw)


def parse_delivery_config(raw: dict[str, Any]) -> DeliveryConfig:
    """Parse a ``collection_connections.config`` dict into a :class:`DeliveryConfig`.

    Raises :class:`DeliveryConfigError` when a required field is missing/invalid.
    Optional fields fall back to the §5.1 defaults.
    """
    path_template = raw.get("path_template")
    if not isinstance(path_template, str) or not path_template.strip():
        raise DeliveryConfigError("path_template is required")

    payload_raw = raw.get("payload") or {}
    payload = {
        "item_json": bool(payload_raw.get("item_json", False)),
        "checksums": _enum_or_none(payload_raw.get("checksums"), CHECKSUMS, "payload.checksums"),
        "completion_marker": bool(payload_raw.get("completion_marker", False)),
    }

    retry_raw = raw.get("retry") or {}
    item_filter = raw.get("item_filter")
    return DeliveryConfig(
        path_template=path_template,
        item_filter=str(item_filter) if item_filter else None,
        asset_keys=_opt_str_list(raw.get("asset_keys")),
        payload=payload,
        on_update=_enum(raw.get("on_update"), ON_UPDATE, "redeliver", "on_update"),
        overwrite=_enum(raw.get("overwrite"), OVERWRITE, "if_newer", "overwrite"),
        max_attempts=int(retry_raw.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
        backoff=_enum(retry_raw.get("backoff"), BACKOFF, "exponential", "retry.backoff"),
        max_concurrent_transfers=int(
            raw.get("max_concurrent_transfers", DEFAULT_MAX_CONCURRENT_TRANSFERS)
        ),
    )
