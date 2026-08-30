"""The `staged_uploads` status/result writer contract (Phase 7 spec §10, §11).

The pipeline is the STRICT side of the push-upload-status contract: it writes
only the statuses and rejection reasons pinned here, and only the ``result``
keys below — the app's poll-route reader is deliberately lenient (unknown
keys stripped, unknown reason strings tolerated) so it survives a newer
writer, but this writer never exercises that leniency. Golden fixture:
``tests/contract-fixtures/push-upload-status.json`` (status-contract style);
the recorder self-checks every document it emits through
:func:`validate_status_doc`, so a drifting writer fails at write time, not at
fixture-review time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

STATUS_PENDING = "pending"
STATUS_FINALIZING = "finalizing"
STATUS_FINALIZED = "finalized"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

STATUSES = frozenset(
    {STATUS_PENDING, STATUS_FINALIZING, STATUS_FINALIZED, STATUS_REJECTED, STATUS_EXPIRED}
)
TERMINAL_STATUSES = frozenset({STATUS_FINALIZED, STATUS_REJECTED, STATUS_EXPIRED})

#: the closed rejection-reason set (mirrors the app's PUSH_REJECTION_REASONS).
REASON_MULTI_SESSION = "multi_session"
REASON_SESSION_TERMINAL = "session_terminal"
REASON_UNKNOWN_SESSION = "unknown_session"
REASON_WRONG_COLLECTION = "wrong_collection"
REASON_BOUND_TO_OTHER_ITEM = "bound_to_other_item"
REASON_GC_PENDING = "gc_pending"
REASON_COLLECTION_ARCHIVED = "collection_archived"
REASON_MISSING_BYTES = "missing_bytes"
REASON_CHECKSUM_MISMATCH = "checksum_mismatch"
REASON_INVALID_ITEM = "invalid_item"

REJECTION_REASONS = frozenset(
    {
        REASON_MULTI_SESSION,
        REASON_SESSION_TERMINAL,
        REASON_UNKNOWN_SESSION,
        REASON_WRONG_COLLECTION,
        REASON_BOUND_TO_OTHER_ITEM,
        REASON_GC_PENDING,
        REASON_COLLECTION_ARCHIVED,
        REASON_MISSING_BYTES,
        REASON_CHECKSUM_MISMATCH,
        REASON_INVALID_ITEM,
    }
)

_RESULT_KEYS = frozenset({"upserted", "rejected", "restored", "checksums"})


class StatusContractError(ValueError):
    """A status document this writer must never produce."""


def finalized_result(
    upserted: Sequence[Mapping[str, str]], checksums: Mapping[str, str]
) -> dict[str, Any]:
    """The ``result`` jsonb for a finalized session."""
    return {
        "upserted": [
            {"collection_id": u["collection_id"], "item_id": u["item_id"]} for u in upserted
        ],
        "checksums": dict(checksums),
    }


def rejected_result(reasons: Sequence[str], *, restored: bool = False) -> dict[str, Any]:
    """The ``result`` jsonb for a rejected session. ``restored`` records the
    §6.3 brokered-update snapshot restore."""
    doc: dict[str, Any] = {"rejected": [{"reason": r} for r in reasons]}
    if restored:
        doc["restored"] = True
    return doc


def validate_status_doc(doc: Mapping[str, Any]) -> None:
    """The strict writer gate: raise :class:`StatusContractError` for any
    ``{status, result, error}`` document outside what this pipeline writes."""
    status = doc.get("status")
    if status not in STATUSES:
        raise StatusContractError(f"unknown status: {status!r}")
    error = doc.get("error")
    if error is not None and not isinstance(error, str):
        raise StatusContractError(f"error must be a string or null: {error!r}")
    result = doc.get("result")
    if result is None:
        return
    if not isinstance(result, Mapping):
        raise StatusContractError(f"result must be an object or null: {result!r}")
    unknown = set(result) - _RESULT_KEYS
    if unknown:
        raise StatusContractError(f"unknown result keys: {sorted(unknown)}")
    upserted = result.get("upserted")
    if upserted is not None:
        if not isinstance(upserted, Sequence) or isinstance(upserted, str | bytes):
            raise StatusContractError("result.upserted must be a list")
        for entry in upserted:
            if (
                not isinstance(entry, Mapping)
                or set(entry) != {"collection_id", "item_id"}
                or not all(isinstance(v, str) for v in entry.values())
            ):
                raise StatusContractError(f"bad upserted entry: {entry!r}")
    rejected = result.get("rejected")
    if rejected is not None:
        if not isinstance(rejected, Sequence) or isinstance(rejected, str | bytes):
            raise StatusContractError("result.rejected must be a list")
        for entry in rejected:
            if (
                not isinstance(entry, Mapping)
                or set(entry) != {"reason"}
                or entry["reason"] not in REJECTION_REASONS
            ):
                raise StatusContractError(f"bad rejected entry: {entry!r}")
    restored = result.get("restored")
    if restored is not None and not isinstance(restored, bool):
        raise StatusContractError(f"result.restored must be a bool: {restored!r}")
    checksums = result.get("checksums")
    if checksums is not None and (
        not isinstance(checksums, Mapping)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in checksums.items())
    ):
        raise StatusContractError(f"result.checksums must map filename to digest: {checksums!r}")
