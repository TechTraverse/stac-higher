"""Grype's JSON -> the result's summary (container-images spec §6.4).

- A finding is one (vulnerability, package, version) match; duplicates count
  once. Severity is Grype's, lower-cased, ``unknown`` when unrecognised.
- ``fixed_in`` is set only when Grype says ``fixed`` and names a version.
- ``kev`` is the COMPLETE list of KEV ids (C-1 contract), from Grype's
  ``knownExploited`` records.
- Numbers are finite, always: a NaN or infinite EPSS becomes null (clamped
  into [0, 1] otherwise) and a NaN or infinite risk becomes 0.0 (C-1's
  parser rejects non-finite numbers; the scanner never emits them).
- ``top`` (<= 25) is CHOSEN by what the policy blocks on -- KEV, then fixed
  CRITICAL, unfixed CRITICAL, fixed HIGH by EPSS, unfixed HIGH, then the
  rest by risk -- and EMITTED by risk, descending. C-1's ``evaluate()`` can
  only name a finding that is in ``top`` (C-1 plan decision 11).
- ``published_at`` is not in Grype's JSON; it comes from the Grype DB's
  ``vulnerability_handles.published_date`` (Decision 3), and is null when
  the lookup fails -- which ``evaluate()`` treats as an unknown age, i.e.
  fail closed.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "negligible", "unknown")
MAX_TOP = 25


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
    published_at: str | None = None


def _finite(value: Any, *, lo: float = 0.0, hi: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    number = max(lo, number)
    return min(hi, number) if hi is not None else number


def _kev_ids(vuln: dict[str, Any]) -> list[str]:
    records = vuln.get("knownExploited")
    if not isinstance(records, list) or not records:
        return []
    ids = [r.get("cve") for r in records if isinstance(r, dict) and isinstance(r.get("cve"), str)]
    ids = [i.strip() for i in ids if i and i.strip()]
    return ids or [str(vuln.get("id") or "").strip()]


def findings_from_grype(doc: dict[str, Any]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    findings: list[Finding] = []
    for m in doc.get("matches") or []:
        if not isinstance(m, dict):
            continue
        vuln = m.get("vulnerability") if isinstance(m.get("vulnerability"), dict) else {}
        artifact = m.get("artifact") if isinstance(m.get("artifact"), dict) else {}
        vid = str(vuln.get("id") or "").strip()
        package = str(artifact.get("name") or "").strip()
        if not vid or not package:
            continue
        version = str(artifact.get("version") or "")
        key = (vid, package, version)
        if key in seen:
            continue
        seen.add(key)
        severity = str(vuln.get("severity") or "unknown").strip().lower()
        if severity not in SEVERITIES:
            severity = "unknown"
        fix = vuln.get("fix") if isinstance(vuln.get("fix"), dict) else {}
        versions = [v for v in fix.get("versions") or [] if isinstance(v, str) and v.strip()]
        fixed_in = versions[0] if fix.get("state") == "fixed" and versions else None
        epss_values = [
            _finite(e.get("epss"), hi=1.0) for e in vuln.get("epss") or [] if isinstance(e, dict)
        ]
        epss_known = [e for e in epss_values if e is not None]
        findings.append(
            Finding(
                id=vid,
                severity=severity,
                package=package,
                version=version,
                fixed_in=fixed_in,
                kev=bool(_kev_ids(vuln)),
                epss=max(epss_known) if epss_known else None,
                risk=_finite(vuln.get("risk")) or 0.0,
            )
        )
    return findings


def _tier(f: Finding) -> int:
    if f.kev:
        return 0
    if f.severity == "critical":
        return 1 if f.fixed_in else 2
    if f.severity == "high":
        return 3 if f.fixed_in else 4
    return 5


def select_top(findings: Iterable[Finding], limit: int = MAX_TOP) -> list[Finding]:
    ranked = sorted(
        findings,
        key=lambda f: (
            _tier(f),
            -(f.epss or 0.0) if _tier(f) == 3 else 0.0,
            -f.risk,
            f.id,
            f.package,
        ),
    )
    return sorted(ranked[:limit], key=lambda f: (-f.risk, f.id, f.package))


def published_dates(db_file: Path, ids: Iterable[str]) -> dict[str, str]:
    """``{lower-cased id: YYYY-MM-DD}`` from the Grype DB (schema v6), the
    earliest published date per name. Any failure is an empty answer."""
    wanted = sorted({i for i in ids if i})
    if not wanted or not Path(db_file).is_file():
        return {}
    placeholders = ",".join("?" for _ in wanted)
    query = (
        "SELECT lower(name), MIN(published_date) FROM vulnerability_handles"
        f" WHERE name COLLATE NOCASE IN ({placeholders}) AND published_date IS NOT NULL"
        " GROUP BY lower(name)"
    )
    try:
        with sqlite3.connect(f"file:{db_file}?mode=ro", uri=True) as conn:
            rows = conn.execute(query, wanted).fetchall()
    except sqlite3.Error:
        return {}
    return {name: str(date)[:10] for name, date in rows if name and date}


def build_summary(
    doc: dict[str, Any],
    *,
    published: Callable[[list[str]], dict[str, str]] | None = None,
) -> dict[str, Any]:
    findings = findings_from_grype(doc)
    counts = dict.fromkeys(SEVERITIES, 0)
    fixed_counts = dict.fromkeys(SEVERITIES, 0)
    kev: set[str] = set()
    for f in findings:
        counts[f.severity] += 1
        if f.fixed_in:
            fixed_counts[f.severity] += 1
    for m in doc.get("matches") or []:
        vuln = m.get("vulnerability") if isinstance(m, dict) else None
        if isinstance(vuln, dict):
            kev.update(i for i in _kev_ids(vuln) if i)
    top = select_top(findings)
    dates = published([f.id for f in top]) if published is not None else {}
    top = [replace(f, published_at=dates.get(f.id.lower())) for f in top]
    return {
        "counts": counts,
        "fixed_counts": fixed_counts,
        "kev": sorted(kev),
        "max_risk": max((f.risk for f in findings), default=0.0),
        "top": [asdict(f) for f in top],
    }
