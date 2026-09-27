"""The rescan diff (C-4, container-images spec §8.2).

When the drain records a rescan, it compares the result with the image's
PREVIOUS scan and stores the difference beside the verdict in
``image_scans.result.diff``. The alert message and the dashboard's scan
history read it, and the alert-fatigue rule keys off it (new findings, a new
KEV, a verdict flip), never off raw counts.

The comparison is over the two stored SUMMARIES (spec §6.4): ``kev`` (the
complete KEV list) and ``top`` (<= 25 findings, chosen policy-first). A
finding that falls below the top-25 cut therefore reads as ``resolved``
although the scanner may still see it; ``counts_delta``, taken from the
complete counts, is the authoritative "how many" (ISSUES I-140). Diffing the
two full Grype JSON files would be exact, but they are large, untrusted and
object-stored, while the summaries are already parsed and validated.

Pinned by ``tests/contract-fixtures/image-scan-diff.json`` against the app's
reader ``app/src/lib/images/scan-diff.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.images.scan_result import (
    SEVERITIES,
    ScanResult,
    ScanResultError,
    parse_scan_result,
)


class ScanDiffError(ValueError):
    """A stored diff is not the §8.2 shape."""


@dataclass(frozen=True)
class ScanDiff:
    previous_scan_id: str | None
    new: tuple[str, ...]
    resolved: tuple[str, ...]
    newly_fixed: tuple[str, ...]
    new_kev: tuple[str, ...]
    verdict_changed: bool
    counts_delta: dict[str, int]

    def as_json(self) -> dict[str, Any]:
        return {
            "previous_scan_id": self.previous_scan_id,
            "new": list(self.new),
            "resolved": list(self.resolved),
            "newly_fixed": list(self.newly_fixed),
            "new_kev": list(self.new_kev),
            "verdict_changed": self.verdict_changed,
            "counts_delta": dict(self.counts_delta),
        }


def _ids(result: ScanResult) -> set[str]:
    return {f.id for f in result.top} | set(result.kev)


def _fixed(result: ScanResult) -> dict[str, bool]:
    """Per vulnerability id in ``top``: is a fix available for any package."""
    state: dict[str, bool] = {}
    for f in result.top:
        state[f.id] = state.get(f.id, False) or f.fixed_in is not None
    return state


def _stored_pass(doc: dict[str, Any]) -> bool | None:
    verdict = doc.get("verdict")
    if isinstance(verdict, dict) and isinstance(verdict.get("pass"), bool):
        return verdict["pass"]
    return None


def scan_diff(
    previous_doc: Any,
    current: ScanResult,
    *,
    current_pass: bool,
    previous_scan_id: str | None,
) -> ScanDiff | None:
    """``current`` against the previous scan's stored ``image_scans.result``,
    or None when there is no successful previous scan to compare with."""
    if not isinstance(previous_doc, dict) or current.error is not None:
        return None
    try:
        previous = parse_scan_result(previous_doc)
    except ScanResultError:
        return None
    if previous.error is not None:
        return None
    before, after = _ids(previous), _ids(current)
    fixed_before, fixed_after = _fixed(previous), _fixed(current)
    newly_fixed = sorted(
        cve for cve, fixed in fixed_after.items() if fixed and fixed_before.get(cve) is False
    )
    previous_pass = _stored_pass(previous_doc)
    return ScanDiff(
        previous_scan_id=previous_scan_id,
        new=tuple(sorted(after - before)),
        resolved=tuple(sorted(before - after)),
        newly_fixed=tuple(newly_fixed),
        new_kev=tuple(sorted(set(current.kev) - set(previous.kev))),
        verdict_changed=previous_pass is not None and previous_pass != current_pass,
        counts_delta={
            sev: current.counts.get(sev, 0) - previous.counts.get(sev, 0) for sev in SEVERITIES
        },
    )


def _id_list(raw: Any, what: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(s, str) and s.strip() for s in raw):
        raise ScanDiffError(f"{what} must be a list of vulnerability ids")
    return tuple(raw)


def parse_scan_diff(raw: Any) -> ScanDiff:
    """A stored diff read back (the alert message, the fixture). Unknown keys
    are ignored; everything named is type-checked."""
    if not isinstance(raw, dict):
        raise ScanDiffError("the diff must be an object")
    previous = raw.get("previous_scan_id")
    if previous is not None and (not isinstance(previous, str) or not previous.strip()):
        raise ScanDiffError("previous_scan_id must be a scan id or null")
    changed = raw.get("verdict_changed")
    if not isinstance(changed, bool):
        raise ScanDiffError("verdict_changed must be true or false")
    delta_raw = raw.get("counts_delta")
    if not isinstance(delta_raw, dict):
        raise ScanDiffError("counts_delta must be an object")
    delta: dict[str, int] = {}
    for sev in SEVERITIES:
        value = delta_raw.get(sev)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ScanDiffError(f"counts_delta.{sev} must be an integer")
        delta[sev] = value
    return ScanDiff(
        previous_scan_id=previous,
        new=_id_list(raw.get("new"), "new"),
        resolved=_id_list(raw.get("resolved"), "resolved"),
        newly_fixed=_id_list(raw.get("newly_fixed"), "newly_fixed"),
        new_kev=_id_list(raw.get("new_kev"), "new_kev"),
        verdict_changed=changed,
        counts_delta=delta,
    )
