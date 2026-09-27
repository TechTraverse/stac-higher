# C-4 · Rescans, Drift, Flagged/Stale Alerts, Exceptions and Retention — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep approvals honest over time. The pipeline requests a rescan of every approved or flagged image each `rescan_interval_hours`. The drain stores each rescan's diff against the previous scan and the tag's current digest. An in-use image that goes flagged, revoked or stale raises one `process_image_flagged` alert per process, routed like `process_failed` and read as *degraded* by process health. Admin exceptions expire on their own, audited. Old scan objects and rows age out. On `/images`, the detail sheet gains scan-history diffs, the exception form (grant or replace) and a revoke button, and the "expired" wording stops depending on the verdict.

**Architecture:** Most of the work is in the pipeline. Three new modules sit beside C-2's drain: `images/lifecycle.py` (the hourly tick: exception expiry and rescan requests), `images/alerts.py` (the single writer of `process_image_flagged`, a state-observed condition reconciled each minute through the flow monitor's `sync_alerts`) and `images/retention.py` (a keep-set sweep of `scans/` objects, then rows, run from `history_retention`). Two helpers feed the drain: `images/diff.py` computes the summary diff, and `images/drift.py` does the registry HEAD, through `resolve_pinned`, with the image's pull credential. The drain gets two small additions: the diff and the drift. The notify fan-out learns the process anchor. On the app side there is a storage and route change for re-granting, two client verbs, the health treatment, and the detail-sheet UI. The app and pipeline share two contracts: `alert-kinds.json` (the kind leaves `declared_kinds` for a new writer list) and a new `image-scan-diff.json`. **No DDL, no migration 032**: migration 030 already has every column, index and table this slice touches.

**Tech Stack:** Python 3.12 + psycopg + pytest + ruff (pipeline), stdlib `http.client` for the HEAD. Astro 7 API routes, Zod v4, TanStack Query v5, React 19, React Hook Form, shadcn primitives (shared `Button`/`Input`/`Label`/`Textarea`, app `Dialog`/`Sheet`), lucide-react, vitest + Testing Library. Playwright is lead-only.

**Spec:** `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` §4.3, §4.4, §8.2, §8.3, §10 and the C-4 text in §15. §2 and §14 are settled, so do not reopen them. ADR `docs/decisions/0021-user-images-scan-then-approve.md`. Epic #56, issue #53 (read its three comments: they are folded in below). Merged predecessors are `docs/superpowers/plans/2026-09-27-c1-image-contracts.md`, `-c2-scanner-drain.md` and `-c3-images-dashboard.md`; their "Decisions made in this plan" bind (C-2's 6, 8, 21 and 25 and C-3's 9, 14 and 15 especially).

## Global Constraints

- **Worktree:** `.claude/worktrees/c4-rescans-drift`, branch `feat/c4-rescans-drift` (GitHub issue #53, epic #56), off `main` at `e951386` (C-1 #58, C-3 #59 and C-2 #62 merged). `npm install` is done. Never work in the main checkout or in another worktree.
- **Gates:** every task ends with `npm run verify` from the worktree root. A task that touches `services/` also runs `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`. Teammates never run e2e, the dev server or Docker, and never `git push`.
- **Python text must avoid en/em dashes and other ambiguous unicode** in strings, comments and docstrings (ruff RUF001-RUF003). Use `-`, `->`, `<=`, `>=`. The `§` sign is fine. Lines are at most 100 characters: if a pasted line trips E501, wrap it without changing its content.
- **No DDL.** Migration 029 is reserved for K-3 (#11), 030 is C-1's, and 031 is reserved for K-4 (#12). This plan needs no schema change. If a step seems to need one, STOP and report: the lead reserves **032** on the issue.
- **No new npm or Python dependency.** Never hand-edit `components/ui/` in the app or in `packages/shared` (a hook blocks it).
- **Contract fixtures change in the SAME commit as both runtimes' consumers** (backend-invariants): Task 1 (`image-scan-diff.json`) and Task 4 (`alert-kinds.json`).
- **Do not regress C-2:** image writes stay compare-and-set on `(status, exception_expires_at)`. `revoked` is terminal. `flagged` still launches. `inline_python` is untouched. The rescan scanner still receives no registry credential (C-2 Decision 25).
- **Structured logging:** messages are constant, and data goes in `extra={...}`. Never log a registry password, token or `Authorization` header, or the scanner's own text.
- **Spec vocabulary, verbatim:** image statuses `pending | scanning | approved | rejected | flagged | revoked | scan_failed`; scan kinds `admission | rescan`; scan statuses `pending | running | done | failed`; alert kind `process_image_flagged`.
- **RBAC:** the exception and revoke verbs are ADMIN (checked in-route since C-3). The UI renders them only when `useAuthMe().identity.roles` includes `admin`.
- **Commit trailer.** Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## Review Focus

These are the inputs and failure modes a person will meet that the spec implies but no happy-path test exercises, most likely first. Each line names the owning task, and that task carries the test.

1. **An admin re-grants or revokes while the expiry tick is between its read and its write.** The admin must win: no status change, no cleared columns, and no audit row for a write that did not happen (Task 6 test "an admin acting between the tick's read and write wins").
2. **An exception that expired while the latest verdict passes.** The dashboard must say "expired <date>" (not "until <past date>") and must NOT warn that deploys are refused. Within the hour the tick clears the columns and the image stays `approved` (Task 10 test "keys 'expired' on the date alone"; Task 6 test "a passing re-evaluation stays approved and clears the exception").
3. **The retention sweep must never delete what is still needed:** the image's current SBOM (every rescan reads it), anything under a scan still pending or running, and any object younger than the 24-hour grace. It MUST delete the orphaned `scans/{provisional_id}/…` objects a digest dedup leaves (Task 7 tests).
4. **A registry that is down, slow or hostile during the drift HEAD.** The rescan still completes, `tag_drift` reads `current_digest: null`, a non-https token realm is refused, and no log line carries the credential (Task 2 tests).
5. **A process whose image is fine but stale, or whose process is disabled or deleted.** Stale (last scanned exactly `scan_window_days` ago is still fresh, the gate's boundary) raises the alert. A disabled or deleted process raises none. A process that deploys a revision off the image resolves its alert on the next minute (Task 4 tests).

## File map

| File | Task | Responsibility |
|---|---|---|
| `services/pipeline/src/pipeline/images/diff.py` (new) | 1 | `scan_diff`, `parse_scan_diff`, `ScanDiff` |
| `tests/contract-fixtures/image-scan-diff.json` (new), `tests/contract-fixtures/README.md` | 1 | the stored diff contract |
| `app/src/lib/images/scan-diff.ts` (new) | 1 | lenient app reader `readScanDiff` |
| `services/pipeline/src/pipeline/images/drift.py` (new) | 2 | `head_tag_digest`, `tag_drift` |
| `services/pipeline/src/pipeline/images/repo.py`, `images/drain.py`, `jobs/image_scans.py`, `tests/_images_fake.py`, `services/image-scanner/stac_higher_scanner/scan.py` (docstring) | 3 | the drain stores diff + drift |
| `services/pipeline/src/pipeline/images/alerts.py` (new), `tests/contract-fixtures/alert-kinds.json`, `jobs/image_scans.py`, `docs/monitoring.md` | 4 | the `process_image_flagged` writer |
| `services/pipeline/src/pipeline/notify/repo.py`, `notify/fanout.py` | 5 | process-anchored alerts route to the process's group |
| `services/pipeline/src/pipeline/images/lifecycle.py` (new), `jobs/image_scans.py` | 6 | `pipeline.image_rescan_tick`: exception expiry + rescan requests |
| `services/pipeline/src/pipeline/storage/platform.py`, `images/retention.py` (new), `jobs/history.py` | 7 | scan object + row retention |
| `app/src/lib/images/storage.ts`, `pages/api/images/[id]/exception.ts`, `lib/images/api.ts`, `lib/images/queries.ts` | 8 | re-grant over a live exception; client verbs |
| `app/src/components/monitoring/shared.ts`, `components/processes/health.ts`, `components/layout/overview.ts` | 9 | `process_image_flagged` reads as degraded |
| `app/src/components/images/format.ts`, `ImagesPage.tsx`, `ImageDetailSheet.tsx`, `lib/images/verdict.ts` (comment) | 10 | history diffs, drift per scan, the expired-word fix |
| `app/src/components/images/ImageAdminActions.tsx` (new), `ImageDetailSheet.tsx`, `ImagesPage.tsx` | 11 | exception form (grant/replace) and revoke |
| `app/e2e/processes.spec.ts`, `docs/FEATURES.md`, `docs/ISSUES.md`, `docs/processes.md`, `docs/backend.md`, `services/pipeline/README.md` | 12 | e2e coverage + docs |

---

### Task 0: Precondition (read-only)

- [ ] From the worktree root, run each command and check its result. If any check fails, STOP and report.
  - `git branch --show-current` prints `feat/c4-rescans-drift`.
  - `git merge-base --is-ancestor e951386 HEAD && echo ok` prints `ok`.
  - `grep -c '"03[12]_' app/src/lib/db/migrate.ts` prints `0` (no migration after 030 on this branch).
  - `grep -n '"declared_kinds": \["process_image_flagged"\]' tests/contract-fixtures/alert-kinds.json` shows one line.
  - `grep -n '"diff": None' services/pipeline/src/pipeline/images/drain.py` shows one line.
  - `ls services/pipeline/src/pipeline/images/alerts.py 2>/dev/null` prints nothing.

---

### Task 1: The scan diff and its contract

**Files:**
- Create: `services/pipeline/src/pipeline/images/diff.py`
- Create: `tests/contract-fixtures/image-scan-diff.json`
- Create: `app/src/lib/images/scan-diff.ts`
- Modify: `tests/contract-fixtures/README.md` (the C-queue fixture list)
- Test: `services/pipeline/tests/test_contract_fixtures.py`, `services/pipeline/tests/test_image_diff.py` (new), `app/src/__tests__/contract-fixtures.test.ts`

**Interfaces:**
- Consumes: `pipeline.images.scan_result.{SEVERITIES, ScanResult, ScanResultError, parse_scan_result}`.
- Produces (Python): `class ScanDiffError(ValueError)`; `@dataclass(frozen=True) ScanDiff(previous_scan_id: str | None, new: tuple[str, ...], resolved: tuple[str, ...], newly_fixed: tuple[str, ...], new_kev: tuple[str, ...], verdict_changed: bool, counts_delta: dict[str, int])` with `.as_json() -> dict`; `scan_diff(previous_doc: Any, current: ScanResult, *, current_pass: bool, previous_scan_id: str | None) -> ScanDiff | None`; `parse_scan_diff(raw: Any) -> ScanDiff`.
- Produces (TS, `@/lib/images/scan-diff`): `imageScanDiffSchema`, `type ImageScanDiff`, `readScanDiff(result: unknown): ImageScanDiff | null`, which reads `result.diff`.

The stored shape (`image_scans.result.diff`) is spec §8.2's four keys plus three that the history view and the alert need: `new_kev`, `counts_delta` and `previous_scan_id`. It is `null` for an admission, and for a rescan without a readable previous scan.

- [ ] **Step 1: Write the fixture.** Create `tests/contract-fixtures/image-scan-diff.json`:

```json
{
  "style": "producer-golden",
  "$comment": "C-4 (container-images spec §8.2): the diff the scan drain stores beside the verdict in image_scans.result.diff when it records a rescan. The pipeline is its only writer (pipeline/images/diff.py scan_diff): pytest asserts that scan_diff(given) equals `document`. The app reads it for the dashboard's scan history (app/src/lib/images/scan-diff.ts; lenient, unknown keys stripped). `given.previous_patch` and `given.current_patch` are patches over image-scan-result.json's `document`; the previous one also carries the verdict the drain stored beside it. The comparison is over the two SUMMARIES (`top` plus the complete `kev`), so a finding that drops below the top-25 cut reads as resolved; `counts_delta` (current minus previous, per severity) is the authoritative count change. An admission, or a rescan with no readable previous scan, stores null.",
  "given": {
    "previous_scan_id": "11111111-2222-4333-8444-555555555555",
    "current_pass": false,
    "previous_patch": {
      "kind": "rescan",
      "counts": { "critical": 1, "high": 3, "medium": 12, "low": 40, "negligible": 5, "unknown": 1 },
      "fixed_counts": { "critical": 0, "high": 1, "medium": 4, "low": 2, "negligible": 0, "unknown": 0 },
      "kev": [],
      "top": [
        { "id": "CVE-2026-0002", "severity": "critical", "package": "openssl", "version": "3.0.1",
          "fixed_in": null, "kev": false, "epss": 0.05, "risk": 0.5, "published_at": "2026-09-01" },
        { "id": "CVE-2026-1234", "severity": "high", "package": "libxml2", "version": "2.12.7",
          "fixed_in": "2.12.9", "kev": false, "epss": 0.31, "risk": 0.42, "published_at": "2026-07-01" },
        { "id": "CVE-2026-0009", "severity": "medium", "package": "zlib", "version": "1.3",
          "fixed_in": null, "kev": false, "epss": 0.01, "risk": 0.1, "published_at": "2026-06-01" }
      ],
      "verdict": { "pass": true, "reasons": [] }
    },
    "current_patch": {
      "kind": "rescan",
      "counts": { "critical": 1, "high": 4, "medium": 11, "low": 40, "negligible": 5, "unknown": 1 },
      "fixed_counts": { "critical": 1, "high": 2, "medium": 4, "low": 2, "negligible": 0, "unknown": 0 },
      "kev": ["CVE-2026-0003"],
      "top": [
        { "id": "CVE-2026-0003", "severity": "high", "package": "curl", "version": "8.1",
          "fixed_in": "8.2", "kev": true, "epss": 0.9, "risk": 0.95, "published_at": "2026-09-20" },
        { "id": "CVE-2026-0002", "severity": "critical", "package": "openssl", "version": "3.0.1",
          "fixed_in": "3.0.9", "kev": false, "epss": 0.05, "risk": 0.5, "published_at": "2026-09-01" },
        { "id": "CVE-2026-1234", "severity": "high", "package": "libxml2", "version": "2.12.7",
          "fixed_in": "2.12.9", "kev": false, "epss": 0.31, "risk": 0.42, "published_at": "2026-07-01" }
      ]
    }
  },
  "document": {
    "previous_scan_id": "11111111-2222-4333-8444-555555555555",
    "new": ["CVE-2026-0003"],
    "resolved": ["CVE-2026-0009"],
    "newly_fixed": ["CVE-2026-0002"],
    "new_kev": ["CVE-2026-0003"],
    "verdict_changed": true,
    "counts_delta": { "critical": 0, "high": 1, "medium": -1, "low": 0, "negligible": 0, "unknown": 0 }
  },
  "cases": [
    { "name": "the golden document", "app": "accept", "pipeline": "accept" },
    { "name": "no previous scan id (the previous row was pruned)", "patch": { "previous_scan_id": null },
      "app": "accept", "pipeline": "accept" },
    { "name": "an unknown key (both sides ignore it)", "patch": { "note": "later writer" },
      "app": "accept", "pipeline": "accept" },
    { "name": "new as a string", "patch": { "new": "CVE-2026-0003" }, "app": "reject", "pipeline": "reject" },
    { "name": "verdict_changed missing", "remove": ["verdict_changed"], "app": "reject", "pipeline": "reject" },
    { "name": "a count delta that is not an integer",
      "patch": { "counts_delta": { "critical": 0, "high": "1", "medium": -1, "low": 0, "negligible": 0, "unknown": 0 } },
      "app": "reject", "pipeline": "reject" },
    { "name": "counts_delta missing a severity",
      "patch": { "counts_delta": { "critical": 0, "high": 1, "medium": -1, "low": 0, "negligible": 0 } },
      "app": "reject", "pipeline": "reject" }
  ]
}
```

- [ ] **Step 2: Write the failing pytest consumers.** In `services/pipeline/tests/test_contract_fixtures.py`, add `IMAGE_SCAN_DIFF = _load("image-scan-diff.json")` directly under the `IMAGE_SCAN_RESULT = …` line. Then append at the end of the file:

```python
def test_image_scan_diff_producer_matches_golden():
    """The drain's diff writer reproduces the golden document (C-4, spec §8.2)."""
    from pipeline.images.diff import scan_diff
    from pipeline.images.scan_result import parse_scan_result

    given = IMAGE_SCAN_DIFF["given"]
    base = IMAGE_SCAN_RESULT["document"]
    previous = {**base, **given["previous_patch"]}
    current = parse_scan_result({**base, **given["current_patch"]})
    diff = scan_diff(
        previous,
        current,
        current_pass=given["current_pass"],
        previous_scan_id=given["previous_scan_id"],
    )
    assert diff is not None
    assert diff.as_json() == IMAGE_SCAN_DIFF["document"]


def _diff_doc(case: dict[str, Any]) -> dict[str, Any]:
    doc = {**IMAGE_SCAN_DIFF["document"], **case.get("patch", {})}
    for key in case.get("remove", []):
        doc.pop(key, None)
    return doc


@pytest.mark.parametrize("case", IMAGE_SCAN_DIFF["cases"], ids=lambda c: c["name"])
def test_image_scan_diff_cases(case):
    from pipeline.images.diff import ScanDiffError, parse_scan_diff

    if case["pipeline"] == "accept":
        parse_scan_diff(_diff_doc(case))
    else:
        with pytest.raises(ScanDiffError):
            parse_scan_diff(_diff_doc(case))
```

Create `services/pipeline/tests/test_image_diff.py`:

```python
"""The rescan diff (container-images spec §8.2): what changed since the
image's previous scan, over the stored summaries."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.images.diff import scan_diff
from pipeline.images.scan_result import parse_scan_result

BASE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
    .read_text()
)["document"]
PREV = "11111111-2222-4333-8444-555555555555"


def _doc(**patch):
    return {**BASE, "kind": "rescan", **patch}


def test_no_previous_document_means_no_diff():
    current = parse_scan_result(_doc())
    assert scan_diff(None, current, current_pass=True, previous_scan_id=PREV) is None


def test_a_failed_previous_scan_means_no_diff():
    failed = {"version": 1, "kind": "rescan", "reference": BASE["reference"],
              "tag": BASE["tag"], "error": "boom"}
    current = parse_scan_result(_doc())
    assert scan_diff(failed, current, current_pass=True, previous_scan_id=PREV) is None


def test_an_unreadable_previous_document_means_no_diff():
    current = parse_scan_result(_doc())
    assert scan_diff({"nonsense": 1}, current, current_pass=True, previous_scan_id=PREV) is None


def test_an_unchanged_scan_diffs_to_nothing():
    current = parse_scan_result(_doc())
    diff = scan_diff(
        {**_doc(), "verdict": {"pass": True}}, current, current_pass=True, previous_scan_id=PREV
    )
    assert diff is not None
    assert (diff.new, diff.resolved, diff.newly_fixed, diff.new_kev) == ((), (), (), ())
    assert diff.verdict_changed is False
    assert set(diff.counts_delta.values()) == {0}


def test_a_previous_verdict_that_is_missing_never_reads_as_changed():
    current = parse_scan_result(_doc())
    diff = scan_diff(_doc(), current, current_pass=False, previous_scan_id=PREV)
    assert diff is not None and diff.verdict_changed is False
```

Run (from `services/pipeline/`): `uv run pytest tests/test_contract_fixtures.py -q -k diff tests/test_image_diff.py`. Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.images.diff'`.

- [ ] **Step 3: Implement `pipeline/images/diff.py`**

```python
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
```

Run the Step 2 command. Expected: PASS.

- [ ] **Step 4: Write the failing vitest consumer.** In `app/src/__tests__/contract-fixtures.test.ts`, add below the line `import { imageScanResultSchema } from "@/lib/images/scan-result";`:

```ts
import { imageScanDiffSchema, readScanDiff } from "@/lib/images/scan-diff";
```

Append at the end of the file:

```ts
describe("image scan diff contract (tests/contract-fixtures/image-scan-diff.json)", () => {
  const fixture = loadFixture("image-scan-diff.json") as unknown as {
    document: Record<string, unknown>;
    cases: {
      name: string;
      patch?: Record<string, unknown>;
      remove?: string[];
      app: "accept" | "reject";
    }[];
  };

  function diffDoc(c: (typeof fixture.cases)[number]): unknown {
    const doc: Record<string, unknown> = { ...fixture.document, ...(c.patch ?? {}) };
    for (const key of c.remove ?? []) delete doc[key];
    return doc;
  }

  it.each(fixture.cases)("$app: $name", (c) => {
    expect(imageScanDiffSchema.safeParse(diffDoc(c)).success).toBe(c.app === "accept");
  });

  it("readScanDiff reads the diff stored beside the §6.4 document", () => {
    expect(readScanDiff({ verdict: { pass: false }, diff: fixture.document })?.new).toEqual([
      "CVE-2026-0003",
    ]);
    expect(readScanDiff({ diff: null })).toBeNull();
    expect(readScanDiff(null)).toBeNull();
    expect(readScanDiff({ diff: { new: "x" } })).toBeNull();
  });
});
```

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts -t "diff"`. Expected: FAIL (module `@/lib/images/scan-diff` not found).

- [ ] **Step 5: Implement `app/src/lib/images/scan-diff.ts`**

```ts
/**
 * `image_scans.result.diff` as the UI reads it (C-4, container-images spec
 * §8.2): what a rescan changed since the image's previous scan. The pipeline
 * writes it (`pipeline/images/diff.py`); this reader is lenient like the
 * other image readers, so unknown keys are stripped. Null for an admission
 * or a first rescan. Client-safe (zod only). Pinned by
 * `tests/contract-fixtures/image-scan-diff.json`.
 */
import { z } from "zod";

const ids = z.array(z.string().min(1));
const delta = z.number().int();

export const imageScanDiffSchema = z.object({
  previous_scan_id: z.string().min(1).nullable(),
  new: ids,
  resolved: ids,
  newly_fixed: ids,
  new_kev: ids,
  verdict_changed: z.boolean(),
  counts_delta: z.object({
    critical: delta,
    high: delta,
    medium: delta,
    low: delta,
    negligible: delta,
    unknown: delta,
  }),
});

export type ImageScanDiff = z.infer<typeof imageScanDiffSchema>;

/** The diff stored beside a scan's result, or null when there is none or it
 * does not read as the §8.2 shape. */
export function readScanDiff(result: unknown): ImageScanDiff | null {
  if (result === null || typeof result !== "object") return null;
  const parsed = imageScanDiffSchema.safeParse((result as Record<string, unknown>).diff);
  return parsed.success ? parsed.data : null;
}
```

Run the Step 4 command. Expected: PASS.

- [ ] **Step 6: README.** In `tests/contract-fixtures/README.md`, directly after the bullet that starts `` - `image-scan-result.json` is style `document`. ``, add:

```markdown
- `image-scan-diff.json` is style `producer-golden` (C-4). It is the diff the scan drain stores beside the verdict in `image_scans.result.diff` for a rescan (spec §8.2): `{previous_scan_id, new, resolved, newly_fixed, new_kev, verdict_changed, counts_delta}`. `given` holds patches over `image-scan-result.json`'s `document` for the previous and current scans, and pytest asserts `scan_diff(given)` equals `document`. `cases[]` (`patch`/`remove` over `document`) run through `parse_scan_diff` and the app's `imageScanDiffSchema`. Both ignore unknown keys. The comparison is over the summaries (`top` plus the complete `kev`), which is why `counts_delta` exists.
```

- [ ] **Step 7: Gates and commit.** Run `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`. All must be green.

```bash
git add tests/contract-fixtures/image-scan-diff.json tests/contract-fixtures/README.md \
  services/pipeline/src/pipeline/images/diff.py services/pipeline/tests/test_image_diff.py \
  services/pipeline/tests/test_contract_fixtures.py app/src/lib/images/scan-diff.ts \
  app/src/__tests__/contract-fixtures.test.ts
git commit -m "feat(images): the rescan diff and its cross-runtime contract (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: The tag-drift HEAD

**Files:**
- Create: `services/pipeline/src/pipeline/images/drift.py`
- Test: `services/pipeline/tests/test_image_drift.py` (new)

**Interfaces:**
- Consumes: `pipeline.connections.egress.{EgressBlocked, resolve_pinned}`, `pipeline.connections.registry.{HttpResponse, parse_bearer_challenge, registry_api_host}`, `pipeline.images.reference.registry_host`, `pipeline.images.repo.ImageRow`, `pipeline.process.executor.RegistryAuth`.
- Produces: `class DriftCheckFailed(Exception)`; `Request = Callable[[str, str, Mapping[str, str]], HttpResponse]` (method, url, headers); `head_tag_digest(reference: str, tag: str, auth: RegistryAuth | None, allow_hosts: Iterable[str], *, request: Request = _https_request) -> str`; `tag_drift(image: ImageRow, auth: RegistryAuth | None, allow_hosts: Iterable[str], *, request: Request = _https_request) -> dict[str, Any]`. The last one never raises and returns `{"current_digest": str | None, "drifted": bool}`, the §6.4 `tag_drift` shape.

Spec §5 puts the drift HEAD in the pipeline ("goes through `resolve_pinned` when the pipeline touches it (drift HEADs, §8.2)"). Doing it here, not in the rescan scanner, keeps C-2 Decision 25: a rescan scanner never receives a registry credential. The HEAD is synchronous; Task 3 runs it in `asyncio.to_thread`.

- [ ] **Step 1: Write the failing tests.** Create `services/pipeline/tests/test_image_drift.py`:

```python
"""Tag drift (container-images spec §8.2, §5): one HEAD on the tag the image
was added with, through the egress policy, never fatal, never leaking the
credential."""

from __future__ import annotations

import logging

import pytest

from pipeline.connections.egress import EgressBlocked
from pipeline.connections.registry import HttpResponse
from pipeline.images import drift as drift_mod
from pipeline.images.drift import DriftCheckFailed, head_tag_digest, tag_drift
from pipeline.images.repo import ImageRow
from pipeline.process.executor import RegistryAuth

SCANNED = "sha256:" + "a" * 64
MOVED = "sha256:" + "b" * 64
HUB_CHALLENGE = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
IMAGE = ImageRow(
    id="7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
    reference="docker.io/library/python",
    tag_at_add="3.12-slim",
    status="approved",
    digest=SCANNED,
)


@pytest.fixture
def pinned(monkeypatch):
    """No DNS in unit tests: record which hosts would have been resolved."""
    hosts: list[str] = []

    def fake_resolve(host, allow_hosts=()):
        hosts.append(host)
        return []

    monkeypatch.setattr(drift_mod, "resolve_pinned", fake_resolve)
    return hosts


class Registry:
    """A scripted registry: answers in order, records every request."""

    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers):
        self.calls.append((method, url, dict(headers)))
        return self.responses.pop(0)


def ok(digest: str = MOVED) -> HttpResponse:
    return HttpResponse(200, {"docker-content-digest": digest}, b"")


def challenge(header: str = HUB_CHALLENGE) -> HttpResponse:
    return HttpResponse(401, {"www-authenticate": header}, b"")


def token() -> HttpResponse:
    return HttpResponse(200, {}, b'{"token": "t0k3n"}')


def test_anonymous_docker_hub_head_follows_the_bearer_challenge(pinned):
    registry = Registry(challenge(), token(), ok())
    digest = head_tag_digest(
        IMAGE.reference, IMAGE.tag_at_add, None, frozenset(), request=registry
    )
    assert digest == MOVED
    (m1, u1, h1), (m2, u2, h2), (m3, _u3, h3) = registry.calls
    assert (m1, u1) == (
        "HEAD", "https://registry-1.docker.io/v2/library/python/manifests/3.12-slim"
    )
    assert "Authorization" not in h1
    assert m2 == "GET" and u2.startswith("https://auth.docker.io/token?")
    assert "scope=repository%3Alibrary%2Fpython%3Apull" in u2
    assert "service=registry.docker.io" in u2
    assert "Authorization" not in h2  # anonymous: no Basic header to the realm
    assert (m3, h3["Authorization"]) == ("HEAD", "Bearer t0k3n")
    assert "application/vnd.oci.image.index.v1+json" in h3["Accept"]
    assert pinned == ["registry-1.docker.io", "auth.docker.io"]


def test_a_credential_goes_only_to_the_token_realm_as_basic(pinned):
    registry = Registry(challenge(), token(), ok())
    head_tag_digest(
        IMAGE.reference, IMAGE.tag_at_add, RegistryAuth("robot", "pat"), frozenset(),
        request=registry,
    )
    assert registry.calls[1][2]["Authorization"].startswith("Basic ")
    assert registry.calls[2][2]["Authorization"] == "Bearer t0k3n"


def test_a_basic_challenge_uses_the_credential(pinned):
    registry = Registry(challenge('Basic realm="r"'), ok())
    head_tag_digest(
        "ghcr.io/org/tool", "1.0", RegistryAuth("robot", "pat"), frozenset(), request=registry
    )
    assert registry.calls[1][2]["Authorization"].startswith("Basic ")


def test_a_basic_challenge_without_a_credential_fails(pinned):
    with pytest.raises(DriftCheckFailed):
        head_tag_digest(
            "ghcr.io/org/tool", "1.0", None, frozenset(),
            request=Registry(challenge('Basic realm="r"')),
        )


def test_a_token_realm_that_is_not_https_is_refused(pinned):
    registry = Registry(challenge('Bearer realm="http://evil.example/token"'))
    with pytest.raises(DriftCheckFailed, match="not https"):
        head_tag_digest(IMAGE.reference, IMAGE.tag_at_add, None, frozenset(), request=registry)
    assert len(registry.calls) == 1


def test_a_missing_or_malformed_digest_header_fails(pinned):
    with pytest.raises(DriftCheckFailed):
        head_tag_digest(
            "ghcr.io/org/tool", "1.0", None, frozenset(),
            request=Registry(HttpResponse(200, {"docker-content-digest": "md5:x"}, b"")),
        )


def test_tag_drift_says_whether_the_tag_moved(pinned):
    moved = tag_drift(IMAGE, None, frozenset(), request=Registry(ok(MOVED)))
    assert moved == {"current_digest": MOVED, "drifted": True}
    same = tag_drift(IMAGE, None, frozenset(), request=Registry(ok(SCANNED)))
    assert same == {"current_digest": SCANNED, "drifted": False}


@pytest.mark.parametrize(
    "failure",
    [
        HttpResponse(404, {}, b""),
        HttpResponse(500, {}, b""),
    ],
)
def test_a_registry_failure_is_recorded_not_raised(pinned, failure):
    assert tag_drift(IMAGE, None, frozenset(), request=Registry(failure)) == {
        "current_digest": None,
        "drifted": False,
    }


def test_an_unreachable_registry_is_recorded_not_raised(pinned):
    def boom(method, url, headers):
        raise OSError("connection refused")

    assert tag_drift(IMAGE, None, frozenset(), request=boom)["current_digest"] is None


def test_an_egress_refusal_is_recorded_not_raised(monkeypatch):
    def refuse(host, allow_hosts=()):
        raise EgressBlocked(f"{host} resolves to a private address")

    monkeypatch.setattr(drift_mod, "resolve_pinned", refuse)
    assert tag_drift(IMAGE, None, frozenset(), request=Registry())["current_digest"] is None


def test_no_log_line_carries_the_credential(pinned, caplog):
    caplog.set_level(logging.DEBUG)
    registry = Registry(challenge(), HttpResponse(401, {}, b""))
    tag_drift(IMAGE, RegistryAuth("robot", "s3cr3t-pat"), frozenset(), request=registry)
    assert "s3cr3t-pat" not in caplog.text
    assert "t0k3n" not in caplog.text
```

Run (from `services/pipeline/`): `uv run pytest tests/test_image_drift.py -q`. Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.images.drift'`.

- [ ] **Step 2: Implement `pipeline/images/drift.py`**

```python
"""Tag drift (C-4, container-images spec §8.2, §5).

On a rescan the pipeline asks the registry, with one ``HEAD`` on the tag the
image was added with (``tag_at_add``), which manifest digest the tag names
NOW. A tag that moved is informational (spec §8.2): runs keep the scanned
digest, and re-adding the reference makes a new image row. A HEAD is free on
Docker Hub (ISSUES I-125).

This is the pipeline touching the registry (spec §5: drift HEADs go through
``resolve_pinned``), which keeps the rescan scanner free of any registry
credential (C-2 Decision 25). Every host dialled, the registry and a Bearer
token realm, is resolved through ``resolve_pinned`` first. Only HTTPS is
spoken, redirects are not followed, and no message or log line names a
credential or a token. A failure is recorded, never fatal: ``tag_drift`` then
says ``current_digest: null``.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import re
import ssl
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from pipeline.connections.egress import EgressBlocked, resolve_pinned
from pipeline.connections.registry import (
    HttpResponse,
    parse_bearer_challenge,
    registry_api_host,
)
from pipeline.images.reference import registry_host
from pipeline.images.repo import ImageRow
from pipeline.process.executor import RegistryAuth

logger = logging.getLogger(__name__)

ACCEPT = ", ".join(
    sorted(
        {
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        }
    )
)
TIMEOUT_SECONDS = 15.0
_MAX_BODY = 64 * 1024
_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")

Request = Callable[[str, str, Mapping[str, str]], HttpResponse]


class DriftCheckFailed(Exception):
    """The registry could not say where the tag points now."""


def _https_request(
    method: str, url: str, headers: Mapping[str, str]
) -> HttpResponse:  # pragma: no cover - network
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(
        parts.hostname or "",
        parts.port or 443,
        timeout=TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        conn.request(method, path, headers={"User-Agent": "stac-higher-pipeline", **headers})
        resp = conn.getresponse()
        body = b"" if method == "HEAD" else resp.read(_MAX_BODY)
        return HttpResponse(resp.status, {k.lower(): v for k, v in resp.getheaders()}, body)
    finally:
        conn.close()


def _hostname(host: str) -> str:
    return host.rsplit(":", 1)[0] if ":" in host else host


def _basic(auth: RegistryAuth | None) -> str | None:
    if auth is None:
        return None
    raw = f"{auth.username}:{auth.password}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _bearer(
    challenge: dict[str, str],
    repository: str,
    auth: RegistryAuth | None,
    allow_hosts: Iterable[str],
    request: Request,
) -> str:
    realm_url = challenge.get("realm", "")
    realm = urlsplit(realm_url)
    if realm.scheme != "https" or not realm.hostname:
        raise DriftCheckFailed("the registry names a token endpoint that is not https")
    resolve_pinned(realm.hostname, allow_hosts)
    query = {"scope": f"repository:{repository}:pull"}
    if challenge.get("service"):
        query["service"] = challenge["service"]
    url = f"{realm_url}{'&' if realm.query else '?'}{urlencode(query)}"
    basic = _basic(auth)
    resp = request("GET", url, {"Authorization": basic} if basic else {})
    if resp.status != 200:
        raise DriftCheckFailed(f"the token endpoint returned {resp.status}")
    try:
        doc = json.loads(resp.body)
    except ValueError as err:
        raise DriftCheckFailed("the token endpoint sent malformed JSON") from err
    value = (doc.get("token") or doc.get("access_token")) if isinstance(doc, dict) else None
    if not isinstance(value, str) or not value:
        raise DriftCheckFailed("the token endpoint returned no token")
    return f"Bearer {value}"


def head_tag_digest(
    reference: str,
    tag: str,
    auth: RegistryAuth | None,
    allow_hosts: Iterable[str],
    *,
    request: Request = _https_request,
) -> str:
    """The digest ``reference:tag`` names now. Raises on anything else."""
    host = registry_host(reference)
    repository = reference.split("/", 1)[1]
    api = registry_api_host(host)
    url = f"https://{api}/v2/{repository}/manifests/{quote(tag, safe='')}"
    resolve_pinned(_hostname(api), allow_hosts)
    headers = {"Accept": ACCEPT}
    resp = request("HEAD", url, headers)
    if resp.status == 401:
        header = resp.headers.get("www-authenticate", "")
        challenge = parse_bearer_challenge(header)
        if challenge is not None:
            authorization = _bearer(challenge, repository, auth, allow_hosts, request)
        elif header.strip().lower().startswith("basic") and auth is not None:
            authorization = _basic(auth) or ""
        else:
            raise DriftCheckFailed(f"{host} requires credentials the pipeline does not have")
        resp = request("HEAD", url, {**headers, "Authorization": authorization})
    if resp.status != 200:
        raise DriftCheckFailed(f"HEAD manifests/{tag} on {host} returned {resp.status}")
    digest = resp.headers.get("docker-content-digest", "")
    if not _DIGEST_RE.fullmatch(digest):
        raise DriftCheckFailed(f"{host} reported no sha256 digest for tag {tag!r}")
    return digest


def tag_drift(
    image: ImageRow,
    auth: RegistryAuth | None,
    allow_hosts: Iterable[str],
    *,
    request: Request = _https_request,
) -> dict[str, Any]:
    """The §6.4 ``tag_drift`` record for one rescan. Never raises."""
    try:
        current = head_tag_digest(
            image.reference, image.tag_at_add, auth, allow_hosts, request=request
        )
    except (DriftCheckFailed, EgressBlocked, OSError, http.client.HTTPException, ValueError) as err:
        logger.warning(
            "tag drift check failed",
            extra={"image_id": image.id, "error_type": type(err).__name__},
        )
        return {"current_digest": None, "drifted": False}
    return {"current_digest": current, "drifted": current != image.digest}
```

Run the Step 1 command. Expected: PASS.

- [ ] **Step 3: Gates and commit.** `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`.

```bash
git add services/pipeline/src/pipeline/images/drift.py services/pipeline/tests/test_image_drift.py
git commit -m "feat(images): the pipeline's tag-drift HEAD through the egress policy (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The drain records the diff and the drift

**Files:**
- Modify: `services/pipeline/src/pipeline/images/repo.py` (`ImageRow`, `_IMAGE_COLUMNS`, `_to_image`, `ImagesRepo.get_scan_result`, `record_rescan`)
- Modify: `services/pipeline/src/pipeline/images/drain.py`
- Modify: `services/pipeline/src/pipeline/jobs/image_scans.py`
- Modify: `services/pipeline/tests/_images_fake.py`
- Modify: `services/image-scanner/stac_higher_scanner/scan.py` (module docstring only)
- Test: `services/pipeline/tests/test_image_scan_drain.py` (append)

**Interfaces:**
- Consumes: Task 1's `scan_diff`, Task 2's `tag_drift`.
- Produces: `ImageRow.last_scan_id: str | None = None` (last field); `ImagesRepo.get_scan_result(scan_id: str) -> dict[str, Any] | None`; `ImagesRepo.record_rescan(..., tag_current_digest: str | None = None)`, which writes `tag_current_digest` and `tag_checked_at = at` only when it is not None; `drain_one(..., check_drift: CheckDrift | None = None)` with `CheckDrift = Callable[[ImageRow], Awaitable[dict[str, Any] | None]]`; `FakeImagesRepo.tag_digests: dict[str, str]`; `FakeImagesRepo.add_scan(..., result=None)`.

A rescan's stored `image_scans.result` now carries `diff` (Task 1's shape, or null) and `tag_drift` (the pipeline's HEAD, or null when no check ran). The scanner's own `tag_drift` is always null and is overwritten. The previous scan is the image's `last_scan_id` as read at claim time. Admissions, including a digest fold, store `diff: null` and never HEAD.

- [ ] **Step 1: Write the failing tests.** Append to `services/pipeline/tests/test_image_scan_drain.py`:

```python
# ---------------------------------------------------------------------------
# C-4: the rescan's diff (spec §8.2) and the pipeline's drift HEAD.
# ---------------------------------------------------------------------------

MOVED = "sha256:" + "b" * 64


def rescan_repo(**image) -> FakeImagesRepo:
    repo = repo_with(
        status="approved",
        scan_kind="rescan",
        digest=DIGEST,
        sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
        last_scan_id=OLD,
        **image,
    )
    repo.add_scan(
        OLD,
        IMG,
        kind="admission",
        status="done",
        requested_at=NOW - dt.timedelta(days=1),
        result={**result_doc(), "verdict": {"pass": True, "reasons": []}, "diff": None},
    )
    return repo


class Drift:
    def __init__(self, answer):
        self.answer = answer
        self.images: list[str] = []

    async def __call__(self, image):
        self.images.append(image.id)
        return self.answer


async def drain_with(repo, scanner, check_drift, policy=POLICY):
    return await drain_one(
        repo,
        policy=policy,
        max_running=1,
        run_scan=scanner.run_scan,
        read_result=scanner.read_result,
        clock=lambda: NOW,
        check_drift=check_drift,
    )


def rescan_doc(**overrides):
    return result_doc(kind="rescan", sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json", **overrides)


async def test_a_rescan_stores_its_diff_against_the_previous_scan():
    repo = rescan_repo()
    outcome = await drain_with(repo, Scanner(rescan_doc(kev=["CVE-2026-0001"])), None)
    assert outcome.image_status == "flagged"
    diff = repo.scans[SCAN]["result"]["diff"]
    assert diff["previous_scan_id"] == OLD
    assert diff["new_kev"] == ["CVE-2026-0001"]
    assert diff["new"] == ["CVE-2026-0001"]
    assert diff["verdict_changed"] is True


async def test_a_rescan_without_a_readable_previous_scan_stores_a_null_diff():
    repo = repo_with(
        status="approved",
        scan_kind="rescan",
        digest=DIGEST,
        sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
    )
    await drain_with(repo, Scanner(rescan_doc()), None)
    assert repo.scans[SCAN]["result"]["diff"] is None


async def test_an_admission_stores_a_null_diff_and_never_heads_the_tag():
    repo = repo_with()
    drift = Drift({"current_digest": MOVED, "drifted": True})
    await drain_with(repo, Scanner(result_doc()), drift)
    assert drift.images == []
    stored = repo.scans[SCAN]["result"]
    assert stored["diff"] is None and stored["tag_drift"] is None
    assert IMG not in repo.tag_digests


async def test_a_rescan_records_where_the_tag_points_now():
    repo = rescan_repo()
    drift = Drift({"current_digest": MOVED, "drifted": True})
    await drain_with(repo, Scanner(rescan_doc()), drift)
    assert drift.images == [IMG]
    assert repo.scans[SCAN]["result"]["tag_drift"] == {"current_digest": MOVED, "drifted": True}
    assert repo.tag_digests[IMG] == MOVED


async def test_a_failed_head_is_recorded_but_leaves_the_last_known_digest():
    repo = rescan_repo()
    drift = Drift({"current_digest": None, "drifted": False})
    outcome = await drain_with(repo, Scanner(rescan_doc()), drift)
    assert outcome.scan_status == "done"
    assert repo.scans[SCAN]["result"]["tag_drift"] == {"current_digest": None, "drifted": False}
    assert IMG not in repo.tag_digests


async def test_no_head_against_a_registry_the_policy_no_longer_allows():
    repo = rescan_repo()
    drift = Drift({"current_digest": MOVED, "drifted": True})
    policy = replace(POLICY, allowed_registries=("ghcr.io",))
    await drain_with(repo, Scanner(rescan_doc()), drift, policy=policy)
    assert drift.images == []


async def test_the_diff_does_not_disturb_the_compare_and_set():
    """An exception granted while the rescan ran still wins (C-2 ruling B1)."""
    repo = rescan_repo()
    granted = NOW + dt.timedelta(days=7)

    class Granting(Scanner):
        async def run_scan(self, scan, kind):
            repo._set(IMG, exception_expires_at=granted)
            return await super().run_scan(scan, kind)

    outcome = await drain_with(repo, Granting(rescan_doc(kev=["CVE-2026-0001"])), None)
    assert outcome.image_status == "approved"
    assert repo.images[IMG].exception_expires_at == granted
    assert repo.scans[SCAN]["result"]["diff"]["new_kev"] == ["CVE-2026-0001"]
```

Run (from `services/pipeline/`): `uv run pytest tests/test_image_scan_drain.py -q`. Expected: FAIL (`ImageRow` has no `last_scan_id`, and `drain_one` has no `check_drift`).

- [ ] **Step 2: The repository seam.** In `services/pipeline/src/pipeline/images/repo.py`:

(a) In `class ImageRow`, add as the LAST field (after `exception_expires_at`):

```python
    #: The latest scan (C-4: the rescan diff's "previous scan"). No FK.
    last_scan_id: str | None = None
```

(b) Replace `_IMAGE_COLUMNS` and `_to_image` with:

```python
_IMAGE_COLUMNS = (
    "id::text, reference, tag_at_add, status, digest, platform_digest, platform,"
    " size_bytes, config, sbom_ref, last_scanned_at, registry_connection_id::text,"
    " exception_expires_at, last_scan_id::text"
)


def _to_image(row: Sequence[Any]) -> ImageRow:
    return ImageRow(
        id=row[0],
        reference=row[1],
        tag_at_add=row[2],
        status=row[3],
        digest=row[4],
        platform_digest=row[5],
        platform=row[6],
        size_bytes=int(row[7]) if row[7] is not None else None,
        config=row[8],
        sbom_ref=row[9],
        last_scanned_at=row[10],
        registry_connection_id=row[11],
        exception_expires_at=row[12],
        last_scan_id=row[13],
    )
```

(c) In `class ImagesRepo`, add after `get_image`:

```python
    @abc.abstractmethod
    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:
        """The stored ``image_scans.result`` of one scan, or None (C-4: the
        previous scan a rescan's diff is computed against)."""
```

(d) Change the abstract `record_rescan` signature and docstring to:

```python
    @abc.abstractmethod
    async def record_rescan(
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
        tag_current_digest: str | None = None,
    ) -> bool:
        """Spec §8.1: only verdict, status, last_scan_id, last_scanned_at --
        plus, when the pipeline's drift HEAD answered (C-4, spec §8.2),
        ``tag_current_digest`` and ``tag_checked_at = at``. The same
        compare-and-set as :meth:`record_admission`."""
```

(e) In `PgImagesRepo`, add after `get_image`:

```python
    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT result FROM stac_higher.image_scans WHERE id = %s::uuid", (scan_id,)
            )
            row = await cur.fetchone()
        return row[0] if row and isinstance(row[0], dict) else None
```

and replace `PgImagesRepo.record_rescan` with:

```python
    async def record_rescan(  # pragma: no cover
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
        tag_current_digest: str | None = None,
    ) -> bool:
        from psycopg.types.json import Json

        checked_at = at if tag_current_digest is not None else None
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.container_images SET verdict = %s, status = %s,"
                " last_scan_id = %s::uuid, last_scanned_at = %s,"
                " tag_current_digest = COALESCE(%s, tag_current_digest),"
                " tag_checked_at = COALESCE(%s, tag_checked_at), updated_at = now()"
                " WHERE id = %s::uuid AND status = %s AND status <> 'revoked'"
                " AND exception_expires_at IS NOT DISTINCT FROM %s RETURNING id",
                (
                    Json(verdict),
                    status,
                    scan_id,
                    at,
                    tag_current_digest,
                    checked_at,
                    image_id,
                    expected_status,
                    expected_exception_expires_at,
                ),
            )
            changed = await cur.fetchone() is not None
            await conn.commit()
        return changed
```

- [ ] **Step 3: The fake.** In `services/pipeline/tests/_images_fake.py`:

(a) Add the field `tag_digests: dict[str, str] = field(default_factory=dict)` directly under `last_scan_ids`.

(b) Replace `add_scan` with:

```python
    def add_scan(
        self,
        scan_id: str,
        image_id: str,
        kind: str = "admission",
        *,
        requested_at: dt.datetime | None = None,
        status: str = "pending",
        started_at: dt.datetime | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.scans[scan_id] = {
            "id": scan_id,
            "image_id": image_id,
            "kind": kind,
            "status": status,
            "requested_by": "user-1",
            "requested_at": requested_at or self.clock,
            "started_at": started_at,
            "result": result,
            "findings_ref": None,
            "log_ref": None,
            "executor_handle": None,
        }
```

(c) Add after `get_image`:

```python
    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:
        scan = self.scans.get(scan_id)
        return scan["result"] if scan else None
```

(d) In `record_admission` and `merge_admission`, the `self._set(...)` calls also set `last_scan_id=scan_id`. For `record_admission`, add `last_scan_id=scan_id,` after `last_scanned_at=at,`. For `merge_admission`, the call becomes `self._set(existing_id, status=status, last_scanned_at=at, last_scan_id=scan_id)`.

(e) Replace `record_rescan` with:

```python
    async def record_rescan(
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
        tag_current_digest: str | None = None,
    ) -> bool:
        if not self._cas(image_id, expected_status, expected_exception_expires_at):
            return False
        self._set(image_id, status=status, last_scanned_at=at, last_scan_id=scan_id)
        self.verdicts[image_id] = verdict
        self.last_scan_ids[image_id] = scan_id
        if tag_current_digest is not None:
            self.tag_digests[image_id] = tag_current_digest
        return True
```

- [ ] **Step 4: The drain.** In `services/pipeline/src/pipeline/images/drain.py`:

(a) Replace the docstring sentence `The rescan TICK, the diff, drift, alerts and exception expiry are C-4's; the drain records ``diff: null``.` with:

```
A rescan's stored result carries the diff against the image's previous scan
(C-4, ``pipeline/images/diff.py``) and the pipeline's own tag-drift HEAD
(``pipeline/images/drift.py``, injected as ``check_drift``); the tag columns
follow a HEAD that answered. An admission stores ``diff: null`` and never
HEADs. The rescan tick, alerts and exception expiry live beside the drain
(``lifecycle.py``, ``alerts.py``).
```

(b) Add `from pipeline.images.diff import scan_diff` directly above `from pipeline.images.policy import ImagePolicy, evaluate, registry_allowed` (isort order).

(c) Below `ReadResult = …` add:

```python
#: The pipeline's drift HEAD for a rescan (C-4, spec §8.2): the §6.4
#: ``tag_drift`` record, or None when no check ran.
CheckDrift = Callable[[ImageRow], Awaitable[dict[str, Any] | None]]
```

(d) Change the `drain_one` signature to add `check_drift: CheckDrift | None = None,` after `clock: Callable[[], dt.datetime],`.

(e) Replace the block from `now = clock()` through `verdict_json = verdict.as_json()` with:

```python
    drift: dict[str, Any] | None = None
    if (
        kind == "rescan"
        and check_drift is not None
        and registry_allowed(registry_host(image.reference), policy.allowed_registries)
    ):
        drift = await check_drift(image)

    now = clock()
    verdict = evaluate(result, policy, now=now)
    verdict_json = verdict.as_json()

    diff: dict[str, Any] | None = None
    if kind == "rescan" and image.last_scan_id and image.last_scan_id != scan.id:
        found = scan_diff(
            await repo.get_scan_result(image.last_scan_id),
            result,
            current_pass=verdict.passed,
            previous_scan_id=image.last_scan_id,
        )
        diff = found.as_json() if found is not None else None
```

(f) In the rescan branch's inner `write(...)`, pass the drift digest to `record_rescan` by adding this keyword after `at=now,`:

```python
                tag_current_digest=(drift or {}).get("current_digest"),
```

(g) Replace the final `finish_scan` call's `result=…` argument. The line `result={**scan_result_to_json(result), "verdict": verdict_json, "diff": None},` becomes `result=stored,`, and directly above `finished = await repo.finish_scan(` (the success tail, not `_fail`) insert:

```python
    stored = scan_result_to_json(result)
    if kind == "rescan":
        # The scanner's own tag_drift is always null (C-2); the pipeline's
        # HEAD is the record (spec §8.2, §5).
        stored["tag_drift"] = drift
    stored = {**stored, "verdict": verdict_json, "diff": diff}
```

- [ ] **Step 5: The job's HEAD.** In `services/pipeline/src/pipeline/jobs/image_scans.py`:

(a) Change the imports to:

```python
import asyncio
import datetime as dt
import logging
from typing import Any

from pipeline.config import Settings
from pipeline.images.drain import STALL_GRACE_SECONDS, drain_one
from pipeline.images.drift import tag_drift
from pipeline.images.policy import ImagePolicyError, load_image_policy
from pipeline.images.registry_auth import (
    RegistryAuthUnavailable,
    RegistryCredentialGone,
    resolve_registry_auth,
)
from pipeline.images.repo import ClaimedScan, ImageRow, PgImagesRepo
```

The remaining imports are unchanged.

(b) Directly after the `read_result` closure, add:

```python
        async def check_drift(image: ImageRow) -> dict[str, Any]:
            # The pull credential the daemon would use, so a private tag can
            # be HEADed. If it cannot be resolved, the HEAD goes anonymous and
            # a private tag reads as unchecked (current_digest null), never a
            # failed scan.
            key = (
                load_key_or_skip(settings, JOB_NAME) if image.registry_connection_id else None
            )
            try:
                auth = await resolve_registry_auth(
                    image, repo=repo, settings=settings, master_key=key
                )
            except (RegistryCredentialGone, RegistryAuthUnavailable):
                auth = None
            return await asyncio.to_thread(tag_drift, image, auth, settings.egress_allow_hosts)
```

(c) Pass it to the drain: add `check_drift=check_drift,` to the `drain_one(...)` call after `clock=…`.

- [ ] **Step 6: The scanner's docstring.** In `services/image-scanner/stac_higher_scanner/scan.py`, replace `Drift (spec §8.2) is C-4's.` with `Drift (spec §8.2) is the pipeline's own HEAD (C-4), so a rescan needs no registry credential and ``tag_drift`` stays null here.`

- [ ] **Step 7: Run the tests.** `uv run pytest tests/test_image_scan_drain.py tests/test_image_repo_fake.py tests/test_image_registry_auth.py -q`. Expected: PASS. The C-2 drain tests pass unchanged, since `check_drift` defaults to None.

- [ ] **Step 8: Gates and commit.** `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`.

```bash
git add services/pipeline/src/pipeline/images/repo.py services/pipeline/src/pipeline/images/drain.py \
  services/pipeline/src/pipeline/jobs/image_scans.py services/pipeline/tests/_images_fake.py \
  services/pipeline/tests/test_image_scan_drain.py services/image-scanner/stac_higher_scanner/scan.py
git commit -m "feat(images): a rescan stores its diff and the tag's current digest (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: The `process_image_flagged` writer, and the kind leaves `declared_kinds`

**Files:**
- Create: `services/pipeline/src/pipeline/images/alerts.py`
- Modify: `tests/contract-fixtures/alert-kinds.json`, `tests/contract-fixtures/README.md` (the `pinned-enum` paragraph)
- Modify: `services/pipeline/src/pipeline/jobs/image_scans.py`
- Modify: `docs/monitoring.md`
- Test: `services/pipeline/tests/test_image_alerts.py` (new), `services/pipeline/tests/test_contract_fixtures.py`, `app/src/__tests__/contract-fixtures.test.ts`

**Interfaces:**
- Consumes: `pipeline.flow.repo.{AlertCondition, PgFlowMonitorRepo}` (`sync_alerts(conditions, owned_kinds) -> (raised, resolved)`).
- Produces: `IMAGE_FLAGGED_KIND = "process_image_flagged"`; `IMAGE_ALERT_KINDS = ("process_image_flagged",)`; `ALERT_SOURCE = "health"`; `@dataclass(frozen=True) ImageInUse(process_id, process_name, image_id, snapshot_reference, snapshot_digest, status: str | None, last_scanned_at: dt.datetime | None, verdict: dict | None, diff: dict | None)`; `ImageAlertsRepo.list_images_in_use() -> list[ImageInUse]`; `PgImageAlertsRepo(database_url)`; `image_alert_conditions(rows, *, scan_window_days: int, now: dt.datetime) -> list[AlertCondition]`; `sync_image_alerts(repo, sync_alerts, *, scan_window_days, now) -> tuple[int, int]`. Fixture: `alert-kinds.json` gains `"image_kinds": ["process_image_flagged"]`, and `declared_kinds` becomes `[]`.

Spec §10: there is one open alert per process whose CURRENT revision references an image that is `flagged`, `revoked` or stale. It is evaluated as a condition from state, like M5-E's process kinds, so auto-resolve falls out of its absence. The writer is its own module with its own writer list, so the flow monitor never gains auto-resolve authority over it. It runs every minute from the image-drain job, BEFORE the drain: a 15-minute scan must not delay it.

- [ ] **Step 1: Write the failing tests.** Create `services/pipeline/tests/test_image_alerts.py`:

```python
"""The process_image_flagged alert (container-images spec §10): one per
process whose current revision snapshots an image that is flagged, revoked,
gone or stale; resolved by absence."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from pipeline.images.alerts import (
    ALERT_SOURCE,
    IMAGE_ALERT_KINDS,
    IMAGE_FLAGGED_KIND,
    ImageAlertsRepo,
    ImageInUse,
    image_alert_conditions,
    sync_image_alerts,
)

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
DIGEST = "sha256:" + "a" * 64
ROW = ImageInUse(
    process_id="p-1",
    process_name="geocolor",
    image_id="img-1",
    snapshot_reference="ghcr.io/org/satpy-runtime",
    snapshot_digest=DIGEST,
    status="approved",
    last_scanned_at=NOW - dt.timedelta(days=1),
    verdict={"pass": True, "reasons": []},
    diff=None,
)


def conditions(*rows, window=30):
    return image_alert_conditions(list(rows), scan_window_days=window, now=NOW)


def test_an_approved_fresh_image_raises_nothing():
    assert conditions(ROW) == []


def test_a_flagged_image_in_use_raises_one_process_anchored_alert():
    row = replace(
        ROW,
        status="flagged",
        verdict={"pass": False, "reasons": ["kev:CVE-2026-0001"]},
        diff={"new": ["CVE-2026-0001"], "new_kev": ["CVE-2026-0001"], "verdict_changed": True},
    )
    (c,) = conditions(row)
    assert (c.source, c.kind, c.process_id) == (ALERT_SOURCE, IMAGE_FLAGGED_KIND, "p-1")
    assert c.connection_id is None and c.source_id is None
    assert "ghcr.io/org/satpy-runtime@sha256:aaaaaaaaaaaa" in c.message
    assert "kev:CVE-2026-0001" in c.message
    assert "1 new KEV" in c.message and "verdict changed" in c.message
    assert "runs continue" in c.message


def test_many_reasons_are_named_up_to_five():
    reasons = [f"kev:CVE-2026-000{i}" for i in range(7)]
    (c,) = conditions(replace(ROW, status="flagged", verdict={"pass": False, "reasons": reasons}))
    assert "kev:CVE-2026-0004" in c.message
    assert "kev:CVE-2026-0005" not in c.message
    assert "and 2 more" in c.message


def test_a_stale_image_raises_even_when_approved():
    stale = replace(ROW, last_scanned_at=NOW - dt.timedelta(days=31))
    (c,) = conditions(stale)
    assert "stale" in c.message and "30-day scan window" in c.message


def test_the_stale_boundary_is_the_gates_exactly_the_window_ago_is_fresh():
    assert conditions(replace(ROW, last_scanned_at=NOW - dt.timedelta(days=30))) == []


def test_never_scanned_counts_as_stale():
    (c,) = conditions(replace(ROW, last_scanned_at=None))
    assert "never" in c.message


def test_revoked_and_gone_images_say_runs_die_at_launch():
    (revoked,) = conditions(replace(ROW, status="revoked"))
    assert "revoked" in revoked.message and "die at launch" in revoked.message
    (gone,) = conditions(replace(ROW, status=None, last_scanned_at=None, verdict=None))
    assert "no longer in the image registry" in gone.message


def test_only_live_enabled_processes_on_their_current_revision_are_considered():
    from pipeline.images.alerts import IMAGES_IN_USE_SQL

    assert "p.deleted_at IS NULL AND p.enabled" in IMAGES_IN_USE_SQL
    assert "r.id = p.current_revision" in IMAGES_IN_USE_SQL


def test_one_condition_per_process():
    a = replace(ROW, status="flagged")
    assert len(conditions(a, replace(a, image_id="img-2"))) == 1


class Repo(ImageAlertsRepo):
    def __init__(self, rows):
        self.rows = rows

    async def list_images_in_use(self):
        return list(self.rows)


class Sink:
    def __init__(self):
        self.calls = []

    async def __call__(self, found, owned):
        self.calls.append((found, owned))
        return (len(found), 0)


async def test_sync_is_scoped_to_the_image_kind_and_always_runs():
    """An empty condition list must still reach sync_alerts: that is what
    auto-resolves the alert once the image is approved again or the process
    moved off it."""
    sink = Sink()
    assert await sync_image_alerts(Repo([ROW]), sink, scan_window_days=30, now=NOW) == (0, 0)
    assert sink.calls == [([], IMAGE_ALERT_KINDS)]

    flagged = replace(ROW, status="flagged")
    raised, _ = await sync_image_alerts(Repo([flagged]), sink, scan_window_days=30, now=NOW)
    assert raised == 1 and sink.calls[1][1] == ("process_image_flagged",)
```

In `services/pipeline/tests/test_contract_fixtures.py`, replace `test_alert_kinds_match_golden` and `test_process_image_flagged_is_declared_not_written` with:

```python
def test_alert_kinds_match_golden():
    """The pinned-enum fixture (P7-H): each writer-side constant equals its
    fixture list VERBATIM (order included - the fixture is canonical), and
    the writer lists partition the full enum exactly."""
    from pipeline.flow.monitor import MONITOR_KINDS
    from pipeline.images.alerts import IMAGE_ALERT_KINDS
    from pipeline.notify.repo import WEBHOOK_FAILED_KIND

    assert ALERT_KINDS["monitor_kinds"] == list(MONITOR_KINDS)
    assert ALERT_KINDS["image_kinds"] == list(IMAGE_ALERT_KINDS)
    assert ALERT_KINDS["notify_kinds"] == [WEBHOOK_FAILED_KIND]
    assert ALERT_KINDS["kinds"] == (
        ALERT_KINDS["monitor_kinds"]
        + ALERT_KINDS["image_kinds"]
        + ALERT_KINDS["declared_kinds"]
        + ALERT_KINDS["notify_kinds"]
    )
    assert len(set(ALERT_KINDS["kinds"])) == len(ALERT_KINDS["kinds"])


def test_process_image_flagged_is_written_by_the_image_alerts_module():
    """C-4 moved the kind out of declared_kinds (container-images spec §10):
    pipeline/images/alerts.py is its single writer, and the flow monitor must
    never own it (monitor ownership would let the monitor auto-resolve it)."""
    from pipeline.flow.monitor import MONITOR_KINDS

    assert ALERT_KINDS["image_kinds"] == ["process_image_flagged"]
    assert ALERT_KINDS["declared_kinds"] == []
    assert "process_image_flagged" not in MONITOR_KINDS
```

In `app/src/__tests__/contract-fixtures.test.ts`, inside `describe("alert kind enum …")`: add `image_kinds: string[];` to the fixture type (after `monitor_kinds: string[];`). Replace the partition test's array with `[...fixture.monitor_kinds, ...fixture.image_kinds, ...fixture.declared_kinds, ...fixture.notify_kinds]`. Replace the test `it("process_image_flagged waits in declared_kinds until C-4 names its writer", …)` with:

```ts
  it("process_image_flagged is written by the image alerts module (C-4)", () => {
    expect(fixture.image_kinds).toEqual(["process_image_flagged"]);
    expect(fixture.declared_kinds).toEqual([]);
    expect(fixture.monitor_kinds).not.toContain("process_image_flagged");
  });
```

Run: `uv run pytest tests/test_image_alerts.py tests/test_contract_fixtures.py -q -k "alert or image_flagged"` (from `services/pipeline/`) and `npx vitest run src/__tests__/contract-fixtures.test.ts -t "alert"` (from `app/`). Expected: FAIL (no module `pipeline.images.alerts`; the fixture has no `image_kinds`).

- [ ] **Step 2: Implement `pipeline/images/alerts.py`**

```python
"""The ``process_image_flagged`` alert (C-4, container-images spec §10).

One open alert per LIVE, ENABLED process whose CURRENT revision snapshots a
user image that can no longer be trusted as deployed: ``flagged`` (a rescan
newly failed it), ``revoked``, gone from the registry, any other
non-approved status, or stale (not scanned inside the policy's
``scan_window_days``; spec §4.3 -- the same boundary as the deploy gate, so
exactly the window ago is still fresh). Anchored on ``process_id``, source
``health``. A disabled process raises nothing; it cannot run.

It is a CONDITION observed from state every minute (the image drain job runs
it before the drain), so auto-resolve falls out of its absence: the image is
approved and fresh again, or the process deployed a revision that no longer
uses it. An image nobody uses shows on the dashboard only.

This module is the kind's single writer (``alert-kinds.json``
``image_kinds``). It reconciles through the flow monitor's ``sync_alerts``,
scoped to :data:`IMAGE_ALERT_KINDS`, so neither writer ever resolves the
other's rows. Only a NEW alert row notifies (ADR 0010): a later rescan that
adds findings to an image that is already flagged updates the open row's
message, and does not re-notify (ISSUES I-141).
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pipeline.flow.repo import AlertCondition

IMAGE_FLAGGED_KIND = "process_image_flagged"
#: The kinds this module owns (raise AND auto-resolve).
IMAGE_ALERT_KINDS = (IMAGE_FLAGGED_KIND,)
ALERT_SOURCE = "health"
#: Policy reasons named in a message; the rest are counted.
MAX_REASONS = 5

SyncAlerts = Callable[[list[AlertCondition], tuple[str, ...]], Awaitable[tuple[int, int]]]


@dataclass(frozen=True)
class ImageInUse:
    process_id: str
    process_name: str
    image_id: str
    #: The revision's snapshot: what the process runs.
    snapshot_reference: str
    snapshot_digest: str
    #: None when the registry row no longer exists.
    status: str | None
    last_scanned_at: dt.datetime | None
    verdict: dict[str, Any] | None
    #: The latest scan's stored diff (C-4), or None.
    diff: dict[str, Any] | None


class ImageAlertsRepo(abc.ABC):
    @abc.abstractmethod
    async def list_images_in_use(self) -> list[ImageInUse]:
        """One row per live, enabled process whose current revision snapshots
        a user image (kinds 2 and 3), joined to the registry row (if any) and
        its latest scan's stored diff."""


IMAGES_IN_USE_SQL = (
    "SELECT p.id::text, p.name, r.runtime->'image'->>'id',"
    " r.runtime->'image'->>'reference', r.runtime->'image'->>'digest',"
    " i.status, i.last_scanned_at, i.verdict, s.result->'diff'"
    " FROM stac_higher.processes p"
    " JOIN stac_higher.process_revisions r ON r.id = p.current_revision"
    " LEFT JOIN stac_higher.container_images i"
    "   ON i.id::text = r.runtime->'image'->>'id'"
    " LEFT JOIN stac_higher.image_scans s ON s.id = i.last_scan_id"
    " WHERE p.deleted_at IS NULL AND p.enabled"
    " AND r.runtime->'image'->>'id' IS NOT NULL"
)


@dataclass
class PgImageAlertsRepo(ImageAlertsRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def list_images_in_use(self) -> list[ImageInUse]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(IMAGES_IN_USE_SQL)
            rows = await cur.fetchall()
        return [
            ImageInUse(
                process_id=r[0],
                process_name=r[1],
                image_id=r[2],
                snapshot_reference=r[3] or "",
                snapshot_digest=r[4] or "",
                status=r[5],
                last_scanned_at=r[6],
                verdict=r[7] if isinstance(r[7], dict) else None,
                diff=r[8] if isinstance(r[8], dict) else None,
            )
            for r in rows
        ]


def _is_stale(row: ImageInUse, scan_window_days: int, now: dt.datetime) -> bool:
    if row.status not in ("approved", "flagged"):
        return False
    if row.last_scanned_at is None:
        return True
    return row.last_scanned_at < now - dt.timedelta(days=scan_window_days)


def _reasons(verdict: dict[str, Any] | None) -> str:
    raw = verdict.get("reasons") if isinstance(verdict, dict) else None
    named = [r for r in raw if isinstance(r, str)] if isinstance(raw, list) else []
    if not named:
        return ""
    shown = ", ".join(named[:MAX_REASONS])
    more = len(named) - MAX_REASONS
    return f" ({shown}{f' and {more} more' if more > 0 else ''})"


def _diff(diff: dict[str, Any] | None) -> str:
    if not isinstance(diff, dict):
        return ""
    parts: list[str] = []
    new = diff.get("new")
    new_kev = diff.get("new_kev")
    if isinstance(new, list) and new:
        parts.append(f"{len(new)} new")
    if isinstance(new_kev, list) and new_kev:
        parts.append(f"{len(new_kev)} new KEV")
    if diff.get("verdict_changed") is True:
        parts.append("verdict changed")
    return f", since the previous scan: {', '.join(parts)}" if parts else ""


def image_alert_conditions(
    rows: list[ImageInUse], *, scan_window_days: int, now: dt.datetime
) -> list[AlertCondition]:
    """Pure: the currently-true conditions, one per process."""
    found: dict[str, AlertCondition] = {}
    for row in rows:
        if row.process_id in found:
            continue
        stale = _is_stale(row, scan_window_days, now)
        problems: list[str] = []
        if row.status is None:
            problems.append("no longer in the image registry, so its runs die at launch")
        elif row.status == "flagged":
            problems.append(
                f"flagged{_reasons(row.verdict)}{_diff(row.diff)}; new deploys are refused,"
                " runs continue"
            )
        elif row.status == "revoked":
            problems.append("revoked, so its runs die at launch")
        elif row.status != "approved":
            problems.append(f"{row.status}, so its runs die at launch")
        if stale:
            when = row.last_scanned_at.date().isoformat() if row.last_scanned_at else "never"
            problems.append(
                f"stale (last scanned {when}, outside the {scan_window_days}-day scan window),"
                " so its runs are refused until a rescan passes"
            )
        if not problems:
            continue
        named = f"image {row.snapshot_reference}@{row.snapshot_digest[:19]}"
        found[row.process_id] = AlertCondition(
            source=ALERT_SOURCE,
            kind=IMAGE_FLAGGED_KIND,
            process_id=row.process_id,
            message=f"{named}: " + "; ".join(problems),
        )
    return list(found.values())


async def sync_image_alerts(
    repo: ImageAlertsRepo,
    sync_alerts: SyncAlerts,
    *,
    scan_window_days: int,
    now: dt.datetime,
) -> tuple[int, int]:
    """Reconcile the kind's alerts with the current state. Always calls
    ``sync_alerts``, even with no conditions: that is the auto-resolve."""
    rows = await repo.list_images_in_use()
    found = image_alert_conditions(rows, scan_window_days=scan_window_days, now=now)
    return await sync_alerts(found, IMAGE_ALERT_KINDS)
```

- [ ] **Step 3: The fixture.** In `tests/contract-fixtures/alert-kinds.json`:
  - Insert `"image_kinds": ["process_image_flagged"],` directly after the `monitor_kinds` array.
  - Set `"declared_kinds": []`.
  - In `description`, replace the final sentence (from ` C-1 (container-images spec §10) declared` to the end) with: ` C-1 (container-images spec §10) declared \`process_image_flagged\` (one open alert per process whose CURRENT revision references an image that is flagged, revoked or stale); C-4 moved it to its own writer list, \`image_kinds\` (pipeline/images/alerts.py IMAGE_ALERT_KINDS), which reconciles through sync_alerts scoped to that list. \`declared_kinds\` is empty and stays as the waiting room. The partition order is monitor, image, declared, notify.`
  - `kinds` does not change (`process_image_flagged` already sits between the monitor kinds and `webhook_failed`).

In `tests/contract-fixtures/README.md`, in the `pinned-enum` paragraph, replace `one list per **writer** (\`monitor_kinds\`, \`notify_kinds\`) plus` with `one list per **writer** (\`monitor_kinds\`, \`image_kinds\` since C-4, \`notify_kinds\`) plus`, and `(\`MONITOR_KINDS\`, \`WEBHOOK_FAILED_KIND\`)` with `(\`MONITOR_KINDS\`, \`IMAGE_ALERT_KINDS\`, \`WEBHOOK_FAILED_KIND\`)`.

- [ ] **Step 4: Wire it into the image drain job.** In `services/pipeline/src/pipeline/jobs/image_scans.py`:

(a) Add these imports in isort order: `from pipeline import metrics` (first in the `pipeline` block), `from pipeline.flow.repo import PgFlowMonitorRepo`, and `from pipeline.images.alerts import PgImageAlertsRepo, sync_image_alerts` (before `pipeline.images.drain`).

(b) In `image_scan_drain`, directly after the `try/except ImagePolicyError … return` block (the policy is loaded) and BEFORE `repo = PgImagesRepo(...)`, insert:

```python
        # C-4 (spec §10): reconcile process_image_flagged BEFORE the drain,
        # so a 15-minute scan never delays it. A failure here must not stop
        # the drain.
        try:
            raised, resolved = await sync_image_alerts(
                PgImageAlertsRepo(settings.database_url),
                PgFlowMonitorRepo(settings.database_url).sync_alerts,
                scan_window_days=policy.scan_window_days,
                now=dt.datetime.now(dt.UTC),
            )
        except Exception:
            logger.exception("image alert sync failed", extra={"job": JOB_NAME})
        else:
            if raised:
                metrics.ALERTS.labels(event="raised").inc(raised)
            if resolved:
                metrics.ALERTS.labels(event="auto_resolved").inc(resolved)
            if raised or resolved:
                logger.info(
                    "image alerts reconciled",
                    extra={"raised": raised, "auto_resolved": resolved},
                )
```

(c) Extend the module docstring with this paragraph:

```
Before the drain, the same tick reconciles the ``process_image_flagged``
alerts (C-4, ``pipeline/images/alerts.py``): one per process whose current
revision uses a flagged, revoked, gone or stale image.
```

- [ ] **Step 5: Docs.** In `docs/monitoring.md`, add this row to the Source/Raised-when/Kinds table, directly before the `(notify)` row:

```markdown
| `health` (C-4) | A live, enabled process's CURRENT revision snapshots a user image that is `flagged`, `revoked`, gone or stale (not scanned inside the image policy's `scan_window_days`) — process-anchored; written by `pipeline/images/alerts.py` every minute from `pipeline.image_scan_drain` (before the drain), outside `MONITOR_KINDS` (`alert-kinds.json` `image_kinds`) | `process_image_flagged` (auto-resolved when the image is approved and fresh again or the process deploys off it) |
```

Replace the paragraph that starts `Declared, no writer yet (\`alert-kinds.json\` \`declared_kinds\`): \`process_image_flagged\`` with:

```markdown
`process_image_flagged` (C-4, container-images spec §10) routes like `process_failed`: to the process's group channels (the notify fan-out derives a process-anchored alert's group from `processes.group_id`). The process health verdict reads it as **degraded, not failing**, because a flagged image blocks new deploys while its runs continue. Only a new alert row notifies: a later rescan that adds findings to an image that is already flagged updates the open alert's message and does not re-notify (ISSUES I-141). `declared_kinds` is empty again.
```

- [ ] **Step 6: Run the tests.** Rerun the Step 1 commands. Expected: PASS.

- [ ] **Step 7: Gates and commit.** `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`.

```bash
git add services/pipeline/src/pipeline/images/alerts.py services/pipeline/tests/test_image_alerts.py \
  services/pipeline/src/pipeline/jobs/image_scans.py services/pipeline/tests/test_contract_fixtures.py \
  tests/contract-fixtures/alert-kinds.json tests/contract-fixtures/README.md \
  app/src/__tests__/contract-fixtures.test.ts docs/monitoring.md
git commit -m "feat(alerts): process_image_flagged gets its writer (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Process-anchored alerts route to the process's group

**Files:**
- Modify: `services/pipeline/src/pipeline/notify/repo.py` (`NotifiableAlert`, `PgNotifyRepo._ALERT_JOIN`, `_ALERT_COLUMNS`, `_alert_from_row`)
- Modify: `services/pipeline/src/pipeline/notify/fanout.py` (`build_payload`)
- Modify: `docs/monitoring.md` (the group-scoping sentence)
- Test: `services/pipeline/tests/test_notify_fanout.py` (append)

**Interfaces:**
- Produces: `NotifiableAlert.process_id: str | None = None` (last field). The webhook payload gains `alert.process_id` (additive; ADR 0010's shape only grows).

Today the fan-out derives an alert's group from connection, channel or collection only. So `process_failed`, `process_stalled` and `process_rate_limited` have a NULL group and reach no webhook channel: they are marked notified and dropped. "Routing treats it like `process_failed`" (spec §10) is therefore only meaningful once process anchors route. This task routes all four process kinds, and the lead should see it in the PR body (Decision 7).

- [ ] **Step 1: Write the failing tests.** Append to `services/pipeline/tests/test_notify_fanout.py`:

```python
def test_the_payload_names_the_process_of_a_process_anchored_alert():
    body = build_payload(
        alert(kind="process_image_flagged", connection_id=None, process_id="p-1")
    )
    payload = json.loads(body)
    assert payload["alert"]["process_id"] == "p-1"
    assert json.loads(build_payload(alert()))["alert"]["process_id"] is None


def test_process_anchored_alerts_derive_their_group_from_the_process():
    """C-4: process_id (or a source's parent process) is the fourth group
    leg, after connection, channel and collection; a deleted process routes
    nowhere."""
    from pipeline.notify.repo import PgNotifyRepo

    join = PgNotifyRepo._ALERT_JOIN
    assert "LEFT JOIN stac_higher.process_sources ps ON ps.id = a.source_id" in join
    assert "pr.id = COALESCE(a.process_id, ps.process_id) AND pr.deleted_at IS NULL" in join
    assert "COALESCE(c.group_id, nch.group_id, cs.group_id, pr.group_id)" in (
        PgNotifyRepo._ALERT_COLUMNS
    )


async def test_a_process_alert_with_a_group_fans_out_to_that_groups_webhooks():
    repo = FakeNotifyRepo(
        alerts=[alert(kind="process_image_flagged", connection_id=None, process_id="p-1")],
        channels=[channel("ch1")],
    )
    result = await tick(repo)
    assert result.deliveries_created == 1
```

Run: `uv run pytest tests/test_notify_fanout.py -q`. Expected: FAIL (`NotifiableAlert` has no `process_id`).

- [ ] **Step 2: Implement.** In `services/pipeline/src/pipeline/notify/repo.py`:

(a) In `NotifiableAlert`, add as the LAST field:

```python
    #: The effective process of a process-anchored alert (its own
    #: ``process_id``, else the parent of its ``source_id``); None otherwise.
    process_id: str | None = None
```

Extend the `group_id` field's comment by one sentence: `C-4: a process-anchored alert takes its process's group.`

(b) Replace `_ALERT_JOIN` and `_ALERT_COLUMNS` with:

```python
    _ALERT_JOIN = (
        " FROM stac_higher.alerts a"
        " LEFT JOIN stac_higher.collection_connections cc ON cc.id = a.association_id"
        " LEFT JOIN stac_higher.connections c"
        "   ON c.id = COALESCE(a.connection_id, cc.connection_id)"
        " LEFT JOIN stac_higher.notification_channels nch ON nch.id = a.channel_id"
        " LEFT JOIN stac_higher.collection_settings cs"
        "   ON cs.collection_id = a.collection_id"
        # C-4: process-anchored alerts (process_failed, process_rate_limited,
        # process_image_flagged; process_stalled through its source) route
        # to the process's group. A deleted process routes nowhere.
        " LEFT JOIN stac_higher.process_sources ps ON ps.id = a.source_id"
        " LEFT JOIN stac_higher.processes pr"
        "   ON pr.id = COALESCE(a.process_id, ps.process_id) AND pr.deleted_at IS NULL"
    )
    _ALERT_COLUMNS = (
        "SELECT a.id, a.source, a.kind, a.message,"
        " COALESCE(c.group_id, nch.group_id, cs.group_id, pr.group_id),"
        " a.connection_id, a.association_id, a.channel_id,"
        " c.name, COALESCE(a.collection_id, cc.collection_id),"
        " a.first_seen, a.last_seen, pr.id"
    )
```

(c) In `_alert_from_row`, add `process_id=str(r[12]) if r[12] else None,` after `last_seen=r[11],`.

In `services/pipeline/src/pipeline/notify/fanout.py` `build_payload`, add `"process_id": alert.process_id,` after `"collection_id": alert.collection_id,`.

In `docs/monitoring.md`, replace `Group scoping is derived (alert → connection | channel | collection → group, coalesced in that order since Phase 7), never stored.` with `Group scoping is derived (alert → connection | channel | collection | process → group, coalesced in that order; the process leg since C-4), never stored. Webhook payloads carry \`process_id\` for process-anchored alerts.`

- [ ] **Step 3: Run the tests.** `uv run pytest tests/test_notify_fanout.py tests/test_notify_webhook.py -q`. Expected: PASS.

- [ ] **Step 4: Gates and commit.** `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`.

```bash
git add services/pipeline/src/pipeline/notify/repo.py services/pipeline/src/pipeline/notify/fanout.py \
  services/pipeline/tests/test_notify_fanout.py docs/monitoring.md
git commit -m "fix(notify): process-anchored alerts route to the process's group (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: The rescan tick: exception expiry and rescan requests

**Files:**
- Create: `services/pipeline/src/pipeline/images/lifecycle.py`
- Modify: `services/pipeline/src/pipeline/jobs/image_scans.py` (a second periodic job)
- Test: `services/pipeline/tests/test_image_lifecycle.py` (new), `services/pipeline/tests/test_main_jobs.py`

**Interfaces:**
- Consumes: `pipeline.images.policy.{ImagePolicy, evaluate}`, `pipeline.images.scan_result.{ScanResultError, parse_scan_result}`.
- Produces: `PIPELINE_ACTOR = "pipeline"`, `EXPIRY_AUDIT_ACTION = "exception_expired"`, `AUDIT_RESOURCE_TYPE = "container_image"`; `@dataclass(frozen=True) ExpiredException(image_id, status, exception_expires_at, exception_by, last_scan_id)`; `ExpiryDecision(status, verdict: dict | None, detail: dict)`; `LifecycleResult(exceptions_expired, flagged_on_expiry, rescans_requested)`; `ImageLifecycleRepo` with `list_expired_exceptions(*, now)`, `get_scan_result(scan_id)`, `expire_exception(image_id, *, expected_status, expected_expires_at, status, verdict, audit_detail) -> bool`, and `request_due_rescans(*, scanned_before) -> int`; `PgImageLifecycleRepo(database_url)`; `decide_expiry(row, result_doc, policy, now) -> ExpiryDecision`; `lifecycle_tick(repo, *, policy, now) -> LifecycleResult`; module constants `EXPIRED_EXCEPTIONS_SQL`, `EXPIRE_EXCEPTION_SQL`, `DUE_RESCANS_SQL`. Job: `RESCAN_JOB_NAME = "pipeline.image_rescan_tick"`, `RESCAN_CRON = "15 * * * *"` in `jobs/image_scans.py`.

What the tick does:
- **Expiry first.** For every row with `exception_expires_at <= now` (the gate's boundary), clear the four `exception_*` columns (migration 030's all-or-nothing CHECK). An `approved` row is re-evaluated: the latest stored scan result, parsed, against the CURRENT policy. A pass stays `approved` with the fresh verdict. A fail goes `flagged` (never `rejected`: it may be in use, spec §4.3), and so does a result that cannot be read (fail closed). A row the drain already moved (flagged) just loses the columns. The UPDATE and its `audit_log` row are ONE statement, a compare-and-set on `(status, exception_expires_at)`. After this, C-3's `exceptionLapsed` gate rule is defence in depth.
- **Then rescans.** Every `approved`/`flagged` row with a stored SBOM and digest, scanned at least `rescan_interval_hours` ago and with no pending or running scan, gets one `rescan` row with `requested_by = 'pipeline'`. The rows are locked `FOR UPDATE SKIP LOCKED`, which serializes with the app's `requestImageScan` (it locks the same image row) and so never makes two open scans.

- [ ] **Step 1: Write the failing tests.** Create `services/pipeline/tests/test_image_lifecycle.py`:

```python
"""The hourly image tick (container-images spec §4.3, §4.4, §8.2): exception
expiry re-evaluates and is audited; rescans are requested as rows."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pipeline.images import lifecycle
from pipeline.images.lifecycle import (
    DUE_RESCANS_SQL,
    EXPIRE_EXCEPTION_SQL,
    EXPIRED_EXCEPTIONS_SQL,
    ExpiredException,
    ImageLifecycleRepo,
    decide_expiry,
    lifecycle_tick,
)
from pipeline.images.policy import load_image_policy

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
POLICY = load_image_policy()
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
    .read_text()
)["document"]
ZERO = dict.fromkeys(("critical", "high", "medium", "low", "negligible", "unknown"), 0)
PASSING = {**FIXTURE, "top": [], "kev": [], "fixed_counts": ZERO, "counts": ZERO}
FAILING = {**PASSING, "kev": ["CVE-2026-0001"]}


@dataclass
class Img:
    status: str
    exception_expires_at: dt.datetime | None = None
    exception_by: str | None = None
    last_scan_id: str | None = None
    verdict: dict[str, Any] | None = None
    sbom_ref: str | None = "scans/i/s/sbom.syft.json"
    digest: str | None = "sha256:" + "a" * 64
    last_scanned_at: dt.datetime | None = None
    open_scan: bool = False


@dataclass
class FakeLifecycleRepo(ImageLifecycleRepo):
    """The behavioural contract the three SQL constants must match."""

    images: dict[str, Img] = field(default_factory=dict)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    audit: list[dict[str, Any]] = field(default_factory=list)
    requested: list[str] = field(default_factory=list)
    #: Simulates an admin acting between the tick's read and its write.
    before_write: Any = None

    async def list_expired_exceptions(self, *, now):
        return [
            ExpiredException(
                image_id=i,
                status=img.status,
                exception_expires_at=img.exception_expires_at,
                exception_by=img.exception_by,
                last_scan_id=img.last_scan_id,
            )
            for i, img in self.images.items()
            if img.exception_expires_at is not None
            and img.exception_expires_at <= now
            and img.status != "revoked"
        ]

    async def get_scan_result(self, scan_id):
        return self.results.get(scan_id)

    async def expire_exception(
        self, image_id, *, expected_status, expected_expires_at, status, verdict, audit_detail
    ):
        if self.before_write is not None:
            self.before_write(self.images[image_id])
        img = self.images[image_id]
        if (
            img.status != expected_status
            or img.status == "revoked"
            or img.exception_expires_at != expected_expires_at
        ):
            return False
        img.status = status
        if verdict is not None:
            img.verdict = verdict
        img.exception_expires_at = None
        img.exception_by = None
        self.audit.append(
            {
                "actor": lifecycle.PIPELINE_ACTOR,
                "action": lifecycle.EXPIRY_AUDIT_ACTION,
                "resource_type": lifecycle.AUDIT_RESOURCE_TYPE,
                "resource_id": image_id,
                "detail": audit_detail,
            }
        )
        return True

    async def request_due_rescans(self, *, scanned_before):
        count = 0
        for i, img in self.images.items():
            if (
                img.status in ("approved", "flagged")
                and img.sbom_ref
                and img.digest
                and (img.last_scanned_at is None or img.last_scanned_at <= scanned_before)
                and not img.open_scan
            ):
                img.open_scan = True
                self.requested.append(i)
                count += 1
        return count


def expired(status="approved", **kw) -> Img:
    return Img(
        status=status,
        exception_expires_at=NOW - dt.timedelta(minutes=5),
        exception_by="admin-1",
        last_scan_id="scan-1",
        last_scanned_at=NOW - dt.timedelta(hours=1),
        **kw,
    )


async def tick(repo, policy=POLICY):
    return await lifecycle_tick(repo, policy=policy, now=NOW)


async def test_a_passing_re_evaluation_stays_approved_and_clears_the_exception():
    repo = FakeLifecycleRepo(images={"i1": expired()}, results={"scan-1": PASSING})
    result = await tick(repo)
    img = repo.images["i1"]
    assert (img.status, img.exception_expires_at, img.exception_by) == ("approved", None, None)
    assert img.verdict["pass"] is True
    assert result.exceptions_expired == 1 and result.flagged_on_expiry == 0
    (row,) = repo.audit
    assert (row["actor"], row["action"], row["resource_type"]) == (
        "pipeline", "exception_expired", "container_image"
    )
    assert row["detail"]["status"] == "approved" and row["detail"]["verdict_pass"] is True
    assert row["detail"]["previous_status"] == "approved"


async def test_a_failing_re_evaluation_flags_never_rejects():
    repo = FakeLifecycleRepo(images={"i1": expired()}, results={"scan-1": FAILING})
    result = await tick(repo)
    assert repo.images["i1"].status == "flagged"
    assert result.flagged_on_expiry == 1
    assert repo.audit[0]["detail"]["reasons"] == ["kev:CVE-2026-0001"]


async def test_the_current_policy_decides_not_the_stored_verdict():
    """A result that passed when scanned fails a tightened policy on expiry."""
    img = expired(verdict={"pass": True, "reasons": []})
    stricter = replace(POLICY, allowed_registries=("docker.io",))
    repo = FakeLifecycleRepo(images={"i1": img}, results={"scan-1": PASSING})
    await tick(repo, policy=stricter)
    assert repo.images["i1"].status == "flagged"
    assert repo.images["i1"].verdict["reasons"] == ["registry_not_allowed"]


async def test_an_unreadable_latest_result_fails_closed():
    for results in ({}, {"scan-1": {"nonsense": True}}, {"scan-1": {**FAILING, "error": "x"}}):
        repo = FakeLifecycleRepo(images={"i1": expired()}, results=results)
        await tick(repo)
        assert repo.images["i1"].status == "flagged"
        assert repo.audit[0]["detail"]["verdict_pass"] is None


async def test_a_flagged_row_only_loses_its_expired_columns():
    kept = {"pass": False, "reasons": ["kev:CVE-2026-0009"]}
    repo = FakeLifecycleRepo(
        images={"i1": expired(status="flagged", verdict=kept)}, results={"scan-1": PASSING}
    )
    await tick(repo)
    img = repo.images["i1"]
    assert (img.status, img.verdict, img.exception_expires_at) == ("flagged", kept, None)
    assert repo.audit[0]["detail"]["status"] == "flagged"


async def test_an_admin_acting_between_the_ticks_read_and_write_wins():
    regranted = NOW + dt.timedelta(days=30)

    def regrant(img):
        img.exception_expires_at = regranted

    repo = FakeLifecycleRepo(
        images={"i1": expired()}, results={"scan-1": FAILING}, before_write=regrant
    )
    result = await tick(repo)
    assert repo.images["i1"].status == "approved"
    assert repo.images["i1"].exception_expires_at == regranted
    assert repo.audit == [] and result.exceptions_expired == 0


async def test_a_live_exception_is_left_alone():
    img = expired()
    img.exception_expires_at = NOW + dt.timedelta(seconds=1)
    repo = FakeLifecycleRepo(images={"i1": img}, results={"scan-1": FAILING})
    await tick(repo)
    assert repo.images["i1"].status == "approved" and repo.audit == []


async def test_rescans_are_requested_once_for_due_approved_and_flagged_images():
    old = NOW - dt.timedelta(hours=POLICY.rescan_interval_hours)
    repo = FakeLifecycleRepo(
        images={
            "due-approved": Img("approved", last_scanned_at=old),
            "due-flagged": Img("flagged", last_scanned_at=old - dt.timedelta(days=3)),
            "fresh": Img("approved", last_scanned_at=old + dt.timedelta(minutes=1)),
            "open": Img("approved", last_scanned_at=old, open_scan=True),
            "rejected": Img("rejected", last_scanned_at=old),
            "no-sbom": Img("approved", last_scanned_at=old, sbom_ref=None),
        }
    )
    result = await tick(repo)
    assert sorted(repo.requested) == ["due-approved", "due-flagged"]
    assert result.rescans_requested == 2
    assert (await tick(repo)).rescans_requested == 0  # one open scan per image


async def test_expiry_runs_before_the_rescan_requests():
    """An image flagged by its expiry is still rescanned in the same tick."""
    img = expired()
    img.last_scanned_at = NOW - dt.timedelta(days=2)
    repo = FakeLifecycleRepo(images={"i1": img}, results={"scan-1": FAILING})
    await tick(repo)
    assert repo.requested == ["i1"] and repo.images["i1"].status == "flagged"


def test_decide_expiry_is_pure_and_names_the_expiry():
    row = ExpiredException("i1", "approved", NOW, "admin-1", "scan-1")
    decision = decide_expiry(row, PASSING, POLICY, NOW)
    assert decision.detail["expired_at"] == NOW.isoformat()
    assert decision.detail["exception_by"] == "admin-1"


def test_the_sql_keeps_the_boundaries_and_the_guards():
    """The Pg statements match the fake's contract (DB-gated SQL is not run
    in unit tests, so its load-bearing fragments are pinned here)."""
    assert "exception_expires_at <= %s" in EXPIRED_EXCEPTIONS_SQL
    assert "status <> 'revoked'" in EXPIRED_EXCEPTIONS_SQL
    for fragment in (
        "AND status = %s AND status <> 'revoked'",
        "AND exception_expires_at = %s",
        "exception_reason = NULL, exception_by = NULL",
        "exception_at = NULL, exception_expires_at = NULL",
        "INSERT INTO stac_higher.audit_log",
        "FROM changed",
    ):
        assert fragment in EXPIRE_EXCEPTION_SQL, fragment
    for fragment in (
        "i.status IN ('approved', 'flagged')",
        "i.sbom_ref IS NOT NULL AND i.digest IS NOT NULL",
        "i.last_scanned_at <= %s",
        "s.status IN ('pending', 'running')",
        "FOR UPDATE OF i SKIP LOCKED",
    ):
        assert fragment in DUE_RESCANS_SQL, fragment
```

In `services/pipeline/tests/test_main_jobs.py`, change the import to `from pipeline.jobs.image_scans import JOB_NAME as IMAGE_SCAN_JOB, RESCAN_JOB_NAME as IMAGE_RESCAN_JOB` (split it across lines in isort style if ruff asks), and add below the C-2 assertion:

```python
    # C-4: the hourly rescan tick (exception expiry + rescan requests).
    assert IMAGE_RESCAN_JOB in registered
```

Run: `uv run pytest tests/test_image_lifecycle.py tests/test_main_jobs.py -q`. Expected: FAIL (no module `pipeline.images.lifecycle`).

- [ ] **Step 2: Implement `pipeline/images/lifecycle.py`**

```python
"""The rescan tick and exception expiry (C-4, container-images spec §8.2, §4.3, §4.4).

``pipeline.image_rescan_tick`` runs HOURLY (``15 * * * *``) and does two
things, in this order:

1. **Exception expiry.** Every image whose ``exception_expires_at`` has
   passed (the deploy gate's boundary: an expiry exactly now counts as
   expired) loses the four ``exception_*`` columns, which migration 030 ties
   together. An ``approved`` image is RE-EVALUATED: its latest stored scan
   result against the CURRENT policy. A pass keeps it ``approved`` with the
   fresh verdict. A fail makes it ``flagged``, never ``rejected``, because it
   may be in use (spec §4.3). A result that cannot be read fails closed
   (``flagged``). A row that is no longer approved (the drain flagged it
   after the exception lapsed) only has the columns cleared. The update and
   its ``audit_log`` row (actor ``pipeline``, action ``exception_expired`` on
   ``container_image``) are ONE statement, a compare-and-set on
   ``(status, exception_expires_at)``: an admin who re-grants or revokes in
   between wins, and nothing is audited for a write that did not happen.
   After this, the deploy gate's ``exceptionLapsed`` rule (C-3) is defence
   in depth.
2. **Rescan requests.** Every ``approved`` or ``flagged`` image with a stored
   SBOM and digest, last scanned at least ``rescan_interval_hours`` ago and
   with no scan pending or running, gets ONE ``rescan`` row
   (``requested_by = 'pipeline'``). The drain does the work (spec §4.2: a
   row, never a call). The image rows are locked ``FOR UPDATE SKIP LOCKED``,
   which serializes with the app's "Rescan now" (it locks the same row), so
   there is never a second open scan.

Hourly with an interval check, rather than spec §8.2's daily ``15 3 * * *``:
a daily tick with a 24-hour interval skips every image whose previous rescan
finished after 03:15, and an expiring exception is enforced within the hour
instead of within the day.
"""

from __future__ import annotations

import abc
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from pipeline.images.policy import ImagePolicy, evaluate
from pipeline.images.scan_result import ScanResultError, parse_scan_result

logger = logging.getLogger(__name__)

PIPELINE_ACTOR = "pipeline"
EXPIRY_AUDIT_ACTION = "exception_expired"
AUDIT_RESOURCE_TYPE = "container_image"
#: One tick's bound; the rest wait an hour.
EXPIRY_BATCH = 500


@dataclass(frozen=True)
class ExpiredException:
    image_id: str
    status: str
    exception_expires_at: dt.datetime
    exception_by: str | None
    last_scan_id: str | None


@dataclass(frozen=True)
class ExpiryDecision:
    status: str
    #: The fresh verdict to store, or None to keep the stored one.
    verdict: dict[str, Any] | None
    #: The audit row's ``detail``.
    detail: dict[str, Any]


@dataclass(frozen=True)
class LifecycleResult:
    exceptions_expired: int
    flagged_on_expiry: int
    rescans_requested: int


class ImageLifecycleRepo(abc.ABC):
    @abc.abstractmethod
    async def list_expired_exceptions(self, *, now: dt.datetime) -> list[ExpiredException]:
        """Rows whose ``exception_expires_at <= now``, never ``revoked``."""

    @abc.abstractmethod
    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:
        """The stored ``image_scans.result`` of one scan, or None."""

    @abc.abstractmethod
    async def expire_exception(
        self,
        image_id: str,
        *,
        expected_status: str,
        expected_expires_at: dt.datetime,
        status: str,
        verdict: dict[str, Any] | None,
        audit_detail: dict[str, Any],
    ) -> bool:
        """Compare-and-set on ``(status, exception_expires_at)``, never onto a
        revoked row: write ``status`` (and ``verdict`` when given), clear the
        four ``exception_*`` columns, and append the audit row in the same
        statement. Returns whether the row changed."""

    @abc.abstractmethod
    async def request_due_rescans(self, *, scanned_before: dt.datetime) -> int:
        """Insert one pipeline ``rescan`` row per due image; return how many."""


EXPIRED_EXCEPTIONS_SQL = (
    "SELECT id::text, status, exception_expires_at, exception_by, last_scan_id::text"
    " FROM stac_higher.container_images"
    " WHERE exception_expires_at IS NOT NULL AND exception_expires_at <= %s"
    " AND status <> 'revoked'"
    " ORDER BY exception_expires_at LIMIT %s"
)

EXPIRE_EXCEPTION_SQL = (
    "WITH changed AS ("
    "  UPDATE stac_higher.container_images"
    "     SET status = %s, verdict = COALESCE(%s::jsonb, verdict),"
    "         exception_reason = NULL, exception_by = NULL,"
    "         exception_at = NULL, exception_expires_at = NULL, updated_at = now()"
    "   WHERE id = %s::uuid AND status = %s AND status <> 'revoked'"
    "     AND exception_expires_at = %s"
    "  RETURNING id"
    ")"
    " INSERT INTO stac_higher.audit_log (actor, action, resource_type, resource_id, detail)"
    " SELECT %s, %s, %s, id::text, %s FROM changed"
    " RETURNING 1"
)

DUE_RESCANS_SQL = (
    "WITH due AS ("
    "  SELECT i.id FROM stac_higher.container_images i"
    "   WHERE i.status IN ('approved', 'flagged')"
    "     AND i.sbom_ref IS NOT NULL AND i.digest IS NOT NULL"
    "     AND (i.last_scanned_at IS NULL OR i.last_scanned_at <= %s)"
    "     AND NOT EXISTS ("
    "       SELECT 1 FROM stac_higher.image_scans s"
    "        WHERE s.image_id = i.id AND s.status IN ('pending', 'running'))"
    "   FOR UPDATE OF i SKIP LOCKED"
    ")"
    " INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)"
    " SELECT id, 'rescan', %s FROM due"
    " RETURNING id"
)


@dataclass
class PgImageLifecycleRepo(ImageLifecycleRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def list_expired_exceptions(  # pragma: no cover
        self, *, now: dt.datetime
    ) -> list[ExpiredException]:
        async with await self._connect() as conn:
            cur = await conn.execute(EXPIRED_EXCEPTIONS_SQL, (now, EXPIRY_BATCH))
            rows = await cur.fetchall()
        return [ExpiredException(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT result FROM stac_higher.image_scans WHERE id = %s::uuid", (scan_id,)
            )
            row = await cur.fetchone()
        return row[0] if row and isinstance(row[0], dict) else None

    async def expire_exception(  # pragma: no cover
        self,
        image_id: str,
        *,
        expected_status: str,
        expected_expires_at: dt.datetime,
        status: str,
        verdict: dict[str, Any] | None,
        audit_detail: dict[str, Any],
    ) -> bool:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            cur = await conn.execute(
                EXPIRE_EXCEPTION_SQL,
                (
                    status,
                    Json(verdict) if verdict is not None else None,
                    image_id,
                    expected_status,
                    expected_expires_at,
                    PIPELINE_ACTOR,
                    EXPIRY_AUDIT_ACTION,
                    AUDIT_RESOURCE_TYPE,
                    Json(audit_detail),
                ),
            )
            changed = await cur.fetchone() is not None
            await conn.commit()
        return changed

    async def request_due_rescans(  # pragma: no cover
        self, *, scanned_before: dt.datetime
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(DUE_RESCANS_SQL, (scanned_before, PIPELINE_ACTOR))
            rows = await cur.fetchall()
            await conn.commit()
        return len(rows)


def decide_expiry(
    row: ExpiredException, result_doc: Any, policy: ImagePolicy, now: dt.datetime
) -> ExpiryDecision:
    """Pure: what an expired exception leaves behind (spec §4.3)."""
    detail: dict[str, Any] = {
        "previous_status": row.status,
        "expired_at": row.exception_expires_at.isoformat(),
        "exception_by": row.exception_by,
    }
    if row.status != "approved":
        return ExpiryDecision(row.status, None, {**detail, "status": row.status})
    try:
        if not isinstance(result_doc, dict):
            raise ScanResultError("no stored scan result")
        verdict = evaluate(parse_scan_result(result_doc), policy, now=now)
    except ScanResultError:
        return ExpiryDecision(
            "flagged",
            None,
            {**detail, "status": "flagged", "verdict_pass": None,
             "note": "the latest scan result could not be read; fails closed"},
        )
    status = "approved" if verdict.passed else "flagged"
    return ExpiryDecision(
        status,
        verdict.as_json(),
        {**detail, "status": status, "verdict_pass": verdict.passed,
         "reasons": list(verdict.reasons)},
    )


async def lifecycle_tick(
    repo: ImageLifecycleRepo, *, policy: ImagePolicy, now: dt.datetime
) -> LifecycleResult:
    expired = flagged = 0
    for row in await repo.list_expired_exceptions(now=now):
        doc = await repo.get_scan_result(row.last_scan_id) if row.last_scan_id else None
        decision = decide_expiry(row, doc, policy, now)
        changed = await repo.expire_exception(
            row.image_id,
            expected_status=row.status,
            expected_expires_at=row.exception_expires_at,
            status=decision.status,
            verdict=decision.verdict,
            audit_detail=decision.detail,
        )
        if not changed:
            logger.info(
                "exception expiry skipped: the image changed meanwhile",
                extra={"image_id": row.image_id},
            )
            continue
        expired += 1
        if row.status == "approved" and decision.status == "flagged":
            flagged += 1
    requested = await repo.request_due_rescans(
        scanned_before=now - dt.timedelta(hours=policy.rescan_interval_hours)
    )
    return LifecycleResult(expired, flagged, requested)
```

- [ ] **Step 3: Register the tick.** In `services/pipeline/src/pipeline/jobs/image_scans.py`:

(a) Add `from pipeline.images.lifecycle import PgImageLifecycleRepo, lifecycle_tick` (isort: after `pipeline.images.drift`).

(b) Below `CRON = "* * * * *"` add:

```python
#: C-4 (spec §8.2): hourly, each image rescanned once its interval is up.
RESCAN_JOB_NAME = "pipeline.image_rescan_tick"
RESCAN_CRON = "15 * * * *"
```

(c) At the end of `register(...)` (after `queue.register_periodic(image_scan_drain, …)`), add:

```python
    async def image_rescan_tick(timestamp: int) -> None:  # pragma: no cover - needs a DB
        try:
            policy = load_image_policy()
        except ImagePolicyError as err:
            logger.error(
                "image rescan tick skipped: the image policy is unavailable",
                extra={"job": RESCAN_JOB_NAME, "error": str(err)},
            )
            return
        result = await lifecycle_tick(
            PgImageLifecycleRepo(settings.database_url),
            policy=policy,
            now=dt.datetime.now(dt.UTC),
        )
        if result.exceptions_expired or result.rescans_requested:
            logger.info(
                "image rescan tick",
                extra={
                    "exceptions_expired": result.exceptions_expired,
                    "flagged_on_expiry": result.flagged_on_expiry,
                    "rescans_requested": result.rescans_requested,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(image_rescan_tick, name=RESCAN_JOB_NAME, cron=RESCAN_CRON)
```

(d) Extend the module docstring with this paragraph:

```
``pipeline.image_rescan_tick`` (``15 * * * *``, C-4) expires admin
exceptions (re-evaluating the latest scan, audited) and inserts one
``rescan`` row per image due for its periodic rescan; this drain runs them.
```

- [ ] **Step 4: Run the tests.** `uv run pytest tests/test_image_lifecycle.py tests/test_main_jobs.py -q`. Expected: PASS.

- [ ] **Step 5: Gates and commit.** `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`.

```bash
git add services/pipeline/src/pipeline/images/lifecycle.py services/pipeline/tests/test_image_lifecycle.py \
  services/pipeline/src/pipeline/jobs/image_scans.py services/pipeline/tests/test_main_jobs.py
git commit -m "feat(images): hourly rescan tick with audited exception expiry (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: Retention of scan objects and rows

**Files:**
- Modify: `services/pipeline/src/pipeline/storage/platform.py` (two primitives)
- Create: `services/pipeline/src/pipeline/images/retention.py`
- Modify: `services/pipeline/src/pipeline/jobs/history.py`
- Test: `services/pipeline/tests/test_image_scan_retention.py` (new)

**Interfaces:**
- Consumes: `pipeline.storage.keys.{SCANS_PREFIX, image_scan_prefix}`, `pipeline.storage.platform.build_platform_client`.
- Produces (platform): `list_objects(client, bucket, prefix) -> list[tuple[str, dt.datetime]]`, `delete_keys(client, bucket, keys: Sequence[str]) -> int` (batched by 1000).
- Produces (retention): `KEEP_SCANS_PER_IMAGE = 10`, `OBJECT_GRACE = dt.timedelta(hours=24)`; `@dataclass(frozen=True) KeptScan(scan_id, image_id, status, findings_ref, log_ref)`; `RetentionResult(objects_deleted, rows_deleted)`; `ScanRetentionRepo` with `list_kept_scans(*, keep)`, `list_sbom_refs()`, `detach_pruned_refs(*, keep) -> int` and `prune_scan_rows(*, older_than, keep) -> int`; `PgScanRetentionRepo(database_url)`; `scan_prefix(scan) -> str`; `keep_set(scans, sbom_refs) -> tuple[frozenset[str], frozenset[str]]`; `keys_to_delete(objects, *, keep_keys, keep_prefixes, now, grace=OBJECT_GRACE) -> list[str]`; `scan_retention_tick(repo, *, list_objects, delete_keys, history_days, now) -> RetentionResult`; SQL constants `KEPT_SCANS_SQL`, `DETACH_PRUNED_REFS_SQL` and `PRUNE_SCAN_ROWS_SQL`.

Spec §8.3: keep the latest SBOM pair and the last ten findings files and logs, and prune objects BEFORE rows (the run-log precedent, I-62). A row outside the ten keeps its history, but its `findings_ref`/`log_ref` are nulled once their objects are gone. The row window is `HISTORY_RETENTION_DAYS` (365). Spec §8.3 names `PROCESS_RUN_RETENTION_DAYS`, which does not exist (Decision 11). Objects are pruned by keep-set, not per row: listing `scans/` also finds objects no row names, which covers the dedup's orphaned `scans/{provisional_id}/…`, crashed scans, and the objects of rows already pruned. `asset_gc` is never involved (these are platform bytes).

- [ ] **Step 1: Write the failing tests.** Create `services/pipeline/tests/test_image_scan_retention.py`:

```python
"""Scan retention (container-images spec §8.3): the latest SBOM pair and each
image's ten newest scans stay; everything else under scans/ ages out, objects
before rows."""

from __future__ import annotations

import datetime as dt

import pytest

from pipeline.images.retention import (
    DETACH_PRUNED_REFS_SQL,
    KEEP_SCANS_PER_IMAGE,
    KEPT_SCANS_SQL,
    PRUNE_SCAN_ROWS_SQL,
    KeptScan,
    ScanRetentionRepo,
    keep_set,
    keys_to_delete,
    scan_prefix,
    scan_retention_tick,
)
from pipeline.storage.platform import delete_keys, list_objects

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
OLD = NOW - dt.timedelta(days=3)
IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
PROV = "99999999-8888-4777-8666-555555555555"
S_ADM = "11111111-2222-4333-8444-555555555555"
S_NEW = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"
S_FOLD = "22222222-3333-4444-8555-666666666666"
S_RUN = "33333333-4444-4555-8666-777777777777"


def keys(prefix: str, *names: str) -> list[str]:
    return [f"{prefix}{n}" for n in names]


def test_a_folded_scan_keeps_the_prefix_its_refs_name():
    folded = KeptScan(S_FOLD, IMG, "done", f"scans/{PROV}/{S_FOLD}/findings.grype.json", None)
    assert scan_prefix(folded) == f"scans/{PROV}/{S_FOLD}/"
    bare = KeptScan(S_NEW, IMG, "failed", None, None)
    assert scan_prefix(bare) == f"scans/{IMG}/{S_NEW}/"
    foreign = KeptScan(S_NEW, IMG, "done", f"scans/{IMG}/{S_ADM}/findings.grype.json", None)
    assert scan_prefix(foreign) == f"scans/{IMG}/{S_NEW}/"  # a ref naming another scan


def test_the_keep_set_and_what_it_deletes():
    kept_scans = [
        KeptScan(S_NEW, IMG, "done", f"scans/{IMG}/{S_NEW}/findings.grype.json",
                 f"scans/{IMG}/{S_NEW}/log"),
        KeptScan(S_RUN, IMG, "running", None, None),
    ]
    sboms = [f"scans/{IMG}/{S_ADM}/sbom.syft.json"]
    keep, whole = keep_set(kept_scans, sboms)
    objects = [
        # the newest scan: findings/result/log kept, a stray SBOM there is not current
        *[(k, OLD) for k in keys(f"scans/{IMG}/{S_NEW}/", "findings.grype.json",
                                 "result.json", "log", "sbom.syft.json")],
        # the admission scan fell out of the ten: only its SBOM pair stays
        *[(k, OLD) for k in keys(f"scans/{IMG}/{S_ADM}/", "findings.grype.json",
                                 "result.json", "log", "sbom.syft.json", "sbom.cdx.json")],
        # a dedup's orphan under a deleted provisional image
        *[(k, OLD) for k in keys(f"scans/{PROV}/{S_FOLD}/", "sbom.syft.json", "result.json")],
        # a scan in flight: everything under it stays
        (f"scans/{IMG}/{S_RUN}/partial.tmp", OLD),
        # written in the last hour: the grace keeps it whatever it is
        (f"scans/{PROV}/{S_FOLD}/log", NOW - dt.timedelta(hours=1)),
    ]
    doomed = keys_to_delete(objects, keep_keys=keep, keep_prefixes=whole, now=NOW)
    assert sorted(doomed) == sorted(
        [
            f"scans/{IMG}/{S_NEW}/sbom.syft.json",
            f"scans/{IMG}/{S_ADM}/findings.grype.json",
            f"scans/{IMG}/{S_ADM}/result.json",
            f"scans/{IMG}/{S_ADM}/log",
            f"scans/{PROV}/{S_FOLD}/sbom.syft.json",
            f"scans/{PROV}/{S_FOLD}/result.json",
        ]
    )
    # never the current SBOM pair
    assert f"scans/{IMG}/{S_ADM}/sbom.syft.json" not in doomed
    assert f"scans/{IMG}/{S_ADM}/sbom.cdx.json" not in doomed


class Repo(ScanRetentionRepo):
    def __init__(self, order):
        self.order = order
        self.prune_args = None

    async def list_kept_scans(self, *, keep):
        self.order.append(("kept", keep))
        return []

    async def list_sbom_refs(self):
        return [f"scans/{IMG}/{S_ADM}/sbom.syft.json"]

    async def detach_pruned_refs(self, *, keep):
        self.order.append(("detach", keep))
        return 2

    async def prune_scan_rows(self, *, older_than, keep):
        self.order.append("prune")
        self.prune_args = (older_than, keep)
        return 4


async def test_objects_go_before_rows_and_rows_use_the_history_window():
    order: list = []

    async def listing():
        order.append("list")
        return [(f"scans/{PROV}/{S_FOLD}/result.json", OLD)]

    async def delete(found):
        order.append(("delete", tuple(found)))
        return len(found)

    repo = Repo(order)
    result = await scan_retention_tick(
        repo, list_objects=listing, delete_keys=delete, history_days=365, now=NOW
    )
    assert order == [
        ("kept", KEEP_SCANS_PER_IMAGE),
        "list",
        ("delete", (f"scans/{PROV}/{S_FOLD}/result.json",)),
        ("detach", KEEP_SCANS_PER_IMAGE),
        "prune",
    ]
    assert repo.prune_args == (NOW - dt.timedelta(days=365), KEEP_SCANS_PER_IMAGE)
    assert (result.objects_deleted, result.rows_deleted) == (1, 4)


async def test_a_failed_object_leg_prunes_no_rows():
    order: list = []

    async def listing():
        raise OSError("storage down")

    async def delete(found):  # pragma: no cover - never reached
        return 0

    repo = Repo(order)
    with pytest.raises(OSError):
        await scan_retention_tick(
            repo, list_objects=listing, delete_keys=delete, history_days=365, now=NOW
        )
    assert "prune" not in order
    assert not any(isinstance(step, tuple) and step[0] == "detach" for step in order)


def test_the_sql_keeps_the_newest_ten_the_last_scan_and_scans_in_flight():
    window = "row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC)"
    assert window in KEPT_SCANS_SQL
    assert "s.rn <= %s OR s.status IN ('pending', 'running') OR s.id = i.last_scan_id" in (
        KEPT_SCANS_SQL
    )
    assert "SET findings_ref = NULL, log_ref = NULL" in DETACH_PRUNED_REFS_SQL
    assert "s.id IS DISTINCT FROM i.last_scan_id" in DETACH_PRUNED_REFS_SQL
    for fragment in (
        "r.rn > %s",
        "s.requested_at < %s",
        "s.status IN ('done', 'failed')",
        "s.id IS DISTINCT FROM i.last_scan_id",
    ):
        assert fragment in PRUNE_SCAN_ROWS_SQL, fragment


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.delete_calls: list[list[str]] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        found = [{"Key": k, "LastModified": m} for k, m in self.objects if k.startswith(Prefix)]
        yield {"Contents": found[:1]}
        yield {"Contents": found[1:]}
        yield {}

    def delete_objects(self, Bucket, Delete):
        self.delete_calls.append([o["Key"] for o in Delete["Objects"]])


def test_list_objects_pages_and_scopes_to_the_prefix():
    s3 = FakeS3([("scans/a", OLD), ("scans/b", NOW), ("logs/c", OLD)])
    assert list_objects(s3, "bucket", "scans/") == [("scans/a", OLD), ("scans/b", NOW)]


def test_delete_keys_batches_by_a_thousand():
    s3 = FakeS3([])
    assert delete_keys(s3, "bucket", [f"scans/{i}" for i in range(2500)]) == 2500
    assert [len(c) for c in s3.delete_calls] == [1000, 1000, 500]
    assert delete_keys(s3, "bucket", []) == 0
```

Run: `uv run pytest tests/test_image_scan_retention.py -q`. Expected: FAIL (`ImportError` for `list_objects` and for `pipeline.images.retention`).

- [ ] **Step 2: The storage primitives.** In `services/pipeline/src/pipeline/storage/platform.py`, change `from typing import Any, BinaryIO, Protocol` to be preceded by `from collections.abc import Sequence` (isort: the `collections.abc` import goes after `import datetime as dt` and before `from dataclasses import …`), and add after `list_keys`:

```python
def list_objects(client: S3Like, bucket: str, prefix: str) -> list[tuple[str, dt.datetime]]:
    """Every ``(key, LastModified)`` under ``prefix``, paginated (C-4: the
    scan retention leg judges objects by key AND age). Pure over an injected
    client; synchronous boto3, so wrap it in ``asyncio.to_thread``."""
    found: list[tuple[str, dt.datetime]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        found.extend((obj["Key"], obj["LastModified"]) for obj in page.get("Contents", []))
    return found


def delete_keys(client: S3Like, bucket: str, keys: Sequence[str]) -> int:
    """Delete exactly these keys, 1000 per ``DeleteObjects`` call (its cap).
    Deleting a key that is already gone is not an error. Returns the number
    of keys sent."""
    pending = list(keys)
    for start in range(0, len(pending), 1000):
        batch = [{"Key": key} for key in pending[start : start + 1000]]
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
    return len(pending)
```

- [ ] **Step 3: Implement `pipeline/images/retention.py`**

```python
"""Retention of scan objects and rows (C-4, container-images spec §8.3).

Scan objects are PLATFORM bytes under ``scans/`` (like run logs under
``logs/``), never catalog assets, so ``asset_gc`` is not involved (ADR 0011).
The hourly ``history_retention`` sweep runs this leg after its table legs:

1. **Objects.** Keep, per image, the objects of its ten newest scans
   (``findings.grype.json``, ``result.json``, ``log``) and its CURRENT SBOM
   pair (``sbom_ref`` and the ``sbom.cdx.json`` beside it; every rescan reads
   it). Keep everything under a scan that is still pending or running.
   Delete everything else under ``scans/`` that is older than a 24-hour
   grace: older findings and logs, superseded SBOMs, the orphaned
   ``scans/{provisional_id}/...`` objects a digest dedup leaves (spec §9.1),
   and the objects of rows that no longer exist. The grace covers a scan
   that starts between this sweep's database read and its listing.
2. **Refs.** A terminal row outside its image's ten newest (and not its
   ``last_scan_id``) has its ``findings_ref``/``log_ref`` set to NULL: step 1
   removed those objects, and no row may keep naming them (the findings
   route then answers 404 instead of redirecting to a missing object).
3. **Rows**, AFTER the objects (spec §8.3, the run-log precedent I-62):
   ``image_scans`` rows older than ``HISTORY_RETENTION_DAYS``, outside their
   image's ten newest, terminal, and not the image's ``last_scan_id``. If
   step 1 raises, steps 2 and 3 do not run.

A scan's objects live under the prefix its refs name, because a folded
admission keeps the provisional image id in its keys; otherwise they live
under ``scans/{image_id}/{scan_id}/``.
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from pipeline.storage.keys import SCANS_PREFIX, image_scan_prefix

KEEP_SCANS_PER_IMAGE = 10
OBJECT_GRACE = dt.timedelta(hours=24)
SCAN_OBJECT_NAMES = ("findings.grype.json", "result.json", "log")
SBOM_NAMES = ("sbom.syft.json", "sbom.cdx.json")


@dataclass(frozen=True)
class KeptScan:
    scan_id: str
    image_id: str
    status: str
    findings_ref: str | None
    log_ref: str | None


@dataclass(frozen=True)
class RetentionResult:
    objects_deleted: int
    rows_deleted: int


class ScanRetentionRepo(abc.ABC):
    @abc.abstractmethod
    async def list_kept_scans(self, *, keep: int) -> list[KeptScan]:
        """Each image's ``keep`` newest scans (by ``requested_at``), every
        pending or running scan, and every image's ``last_scan_id``."""

    @abc.abstractmethod
    async def list_sbom_refs(self) -> list[str]:
        """Every image's current ``sbom_ref``."""

    @abc.abstractmethod
    async def detach_pruned_refs(self, *, keep: int) -> int:
        """NULL ``findings_ref``/``log_ref`` on terminal rows outside their
        image's ``keep`` newest that are not its ``last_scan_id``."""

    @abc.abstractmethod
    async def prune_scan_rows(self, *, older_than: dt.datetime, keep: int) -> int:
        """Delete terminal rows requested before ``older_than`` that are
        outside their image's ``keep`` newest and are not its
        ``last_scan_id``. Returns how many."""


KEPT_SCANS_SQL = (
    "SELECT s.id::text, s.image_id::text, s.status, s.findings_ref, s.log_ref"
    " FROM (SELECT id, image_id, status, findings_ref, log_ref,"
    "        row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC) AS rn"
    "       FROM stac_higher.image_scans) s"
    " JOIN stac_higher.container_images i ON i.id = s.image_id"
    " WHERE s.rn <= %s OR s.status IN ('pending', 'running') OR s.id = i.last_scan_id"
)

DETACH_PRUNED_REFS_SQL = (
    "UPDATE stac_higher.image_scans s SET findings_ref = NULL, log_ref = NULL"
    " FROM (SELECT id,"
    "        row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC) AS rn"
    "       FROM stac_higher.image_scans) r,"
    "      stac_higher.container_images i"
    " WHERE r.id = s.id AND i.id = s.image_id AND r.rn > %s"
    " AND s.status IN ('done', 'failed')"
    " AND s.id IS DISTINCT FROM i.last_scan_id"
    " AND (s.findings_ref IS NOT NULL OR s.log_ref IS NOT NULL)"
)

PRUNE_SCAN_ROWS_SQL = (
    "DELETE FROM stac_higher.image_scans s"
    " USING (SELECT id,"
    "          row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC) AS rn"
    "         FROM stac_higher.image_scans) r,"
    "       stac_higher.container_images i"
    " WHERE r.id = s.id AND i.id = s.image_id"
    " AND r.rn > %s AND s.requested_at < %s"
    " AND s.status IN ('done', 'failed')"
    " AND s.id IS DISTINCT FROM i.last_scan_id"
)


@dataclass
class PgScanRetentionRepo(ScanRetentionRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def list_kept_scans(self, *, keep: int) -> list[KeptScan]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(KEPT_SCANS_SQL, (keep,))
            rows = await cur.fetchall()
        return [KeptScan(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    async def list_sbom_refs(self) -> list[str]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT sbom_ref FROM stac_higher.container_images WHERE sbom_ref IS NOT NULL"
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def detach_pruned_refs(self, *, keep: int) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(DETACH_PRUNED_REFS_SQL, (keep,))
            count = cur.rowcount or 0
            await conn.commit()
        return count

    async def prune_scan_rows(  # pragma: no cover
        self, *, older_than: dt.datetime, keep: int
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(PRUNE_SCAN_ROWS_SQL, (keep, older_than))
            count = cur.rowcount or 0
            await conn.commit()
        return count


def scan_prefix(scan: KeptScan) -> str:
    """Where one scan's objects live: the prefix its refs name when they name
    THIS scan (a folded admission keeps its provisional image id), else
    ``scans/{image_id}/{scan_id}/``."""
    for ref in (scan.findings_ref, scan.log_ref):
        parts = (ref or "").split("/")
        if len(parts) >= 4 and parts[0] == SCANS_PREFIX and parts[1] and parts[2] == scan.scan_id:
            return "/".join(parts[:3]) + "/"
    return image_scan_prefix(scan.image_id, scan.scan_id)


def _sbom_pair(sbom_ref: str) -> tuple[str, ...]:
    head, _, _ = sbom_ref.rpartition("/")
    return tuple(f"{head}/{name}" for name in SBOM_NAMES) if head else ()


def keep_set(
    scans: Iterable[KeptScan], sbom_refs: Iterable[str]
) -> tuple[frozenset[str], frozenset[str]]:
    """``(keys to keep, prefixes to keep whole)``."""
    keep: set[str] = set()
    whole: set[str] = set()
    for scan in scans:
        prefix = scan_prefix(scan)
        if scan.status in ("pending", "running"):
            whole.add(prefix)
        else:
            keep.update(prefix + name for name in SCAN_OBJECT_NAMES)
    for ref in sbom_refs:
        keep.update(_sbom_pair(ref))
    return frozenset(keep), frozenset(whole)


def keys_to_delete(
    objects: Iterable[tuple[str, dt.datetime]],
    *,
    keep_keys: frozenset[str],
    keep_prefixes: frozenset[str],
    now: dt.datetime,
    grace: dt.timedelta = OBJECT_GRACE,
) -> list[str]:
    cutoff = now - grace
    return [
        key
        for key, modified in objects
        if key not in keep_keys
        and not any(key.startswith(p) for p in keep_prefixes)
        and modified < cutoff
    ]


async def scan_retention_tick(
    repo: ScanRetentionRepo,
    *,
    list_objects: Callable[[], Awaitable[list[tuple[str, dt.datetime]]]],
    delete_keys: Callable[[list[str]], Awaitable[int]],
    history_days: int,
    now: dt.datetime,
) -> RetentionResult:
    scans = await repo.list_kept_scans(keep=KEEP_SCANS_PER_IMAGE)
    sboms = await repo.list_sbom_refs()
    keep, whole = keep_set(scans, sboms)
    doomed = keys_to_delete(await list_objects(), keep_keys=keep, keep_prefixes=whole, now=now)
    deleted = await delete_keys(doomed) if doomed else 0
    await repo.detach_pruned_refs(keep=KEEP_SCANS_PER_IMAGE)
    rows = await repo.prune_scan_rows(
        older_than=now - dt.timedelta(days=history_days), keep=KEEP_SCANS_PER_IMAGE
    )
    return RetentionResult(objects_deleted=deleted, rows_deleted=rows)
```

- [ ] **Step 4: Wire it into `history_retention`.** In `services/pipeline/src/pipeline/jobs/history.py`:

(a) The imports become:

```python
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from botocore.exceptions import BotoCoreError, ClientError

from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked
from pipeline.history.sweep import PgHistoryRepo, history_tick
from pipeline.images.retention import PgScanRetentionRepo, scan_retention_tick
from pipeline.queue.interface import QueueBackend
from pipeline.storage.keys import SCANS_PREFIX
from pipeline.storage.platform import build_platform_client, delete_keys, list_objects
```

(b) Inside `history_retention`, after the existing `if (...): logger.info("history retention sweep", …)` block, append:

```python
        # C-4 (spec §8.3): scan objects, then scan rows. Storage faults skip
        # this leg (next hour retries); the table legs above already ran.
        try:
            client = build_platform_client(settings)

            async def list_scan_objects():
                return await asyncio.to_thread(
                    list_objects, client, settings.staging_bucket, f"{SCANS_PREFIX}/"
                )

            async def delete_scan_keys(found: list[str]) -> int:
                return await asyncio.to_thread(
                    delete_keys, client, settings.staging_bucket, found
                )

            scans = await scan_retention_tick(
                PgScanRetentionRepo(settings.database_url),
                list_objects=list_scan_objects,
                delete_keys=delete_scan_keys,
                history_days=settings.history_retention_days,
                now=dt.datetime.fromtimestamp(timestamp, dt.UTC),
            )
        except (EgressBlocked, ClientError, BotoCoreError, OSError) as exc:
            logger.error(
                "scan retention skipped",
                extra={"job": JOB_NAME, "error_type": type(exc).__name__},
            )
        else:
            if scans.objects_deleted or scans.rows_deleted:
                logger.info(
                    "scan retention sweep",
                    extra={
                        "objects_deleted": scans.objects_deleted,
                        "rows_deleted": scans.rows_deleted,
                        "scheduled_timestamp": timestamp,
                    },
                )
```

(c) Extend the module docstring's first sentence with `; C-4 adds the scan-object and image_scans leg (pipeline/images/retention.py)`.

- [ ] **Step 5: Run the tests.** `uv run pytest tests/test_image_scan_retention.py tests/test_history_sweep.py tests/test_staging_cleanup.py tests/test_main_jobs.py -q`. Expected: PASS.

- [ ] **Step 6: Gates and commit.** `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`.

```bash
git add services/pipeline/src/pipeline/storage/platform.py services/pipeline/src/pipeline/images/retention.py \
  services/pipeline/src/pipeline/jobs/history.py services/pipeline/tests/test_image_scan_retention.py
git commit -m "feat(images): scan object and row retention in history_retention (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Re-grant over a live exception, and the client verbs

**Files:**
- Modify: `app/src/lib/images/storage.ts` (`grantImageException`)
- Modify: `app/src/pages/api/images/[id]/exception.ts` (409 message, docstring)
- Modify: `app/src/lib/images/api.ts`, `app/src/lib/images/queries.ts`
- Test: `app/src/__tests__/images-registry-storage.test.ts`, `app/src/__tests__/api-images-verbs.test.ts`, `app/src/__tests__/images-client.test.ts`

**Interfaces:**
- Consumes: `ImageException` (`{reason, expires_at}`) from `@/lib/images/schemas`.
- Produces: `grantImageException(id: string, body: ImageException): Promise<{ image: Image }>` and `revokeImage(id: string): Promise<{ image: Image }>` in `@/lib/images/api`; `useGrantImageException()` (mutate arg `{ id: string; body: ImageException }`) and `useRevokeImage()` (mutate arg `id: string`) in `@/lib/images/queries`, both invalidating `imageKeys.all()`.

Spec §4.4: "Revoking an exception early = `image.revoke` or a new grant." A grant therefore REPLACES the exception on an `approved` image that carries one, live or expired. An `approved` image with no exception passed on its own and takes none (409). Every grant is its own audit row (C-3 Decision 3), so the history of replaced grants is kept.

- [ ] **Step 1: Write the failing tests.**

In `app/src/__tests__/images-registry-storage.test.ts`, replace the test `it("grants an exception only from rejected or flagged, or an approved image whose OWN exception already expired (Fix A)", …)` with:

```ts
  it("grants from rejected or flagged, and REPLACES the exception of an approved image that carries one, live or expired (C-4, spec §4.4)", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ id: IMG }] } as never);
    expect(
      await grantImageException({ imageId: IMG, reason: "vendor fix pending", by: "admin-1", expiresAt: EXPIRES }),
    ).toEqual({ outcome: "granted" });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status = 'approved'");
    expect(sql).toContain("status IN ('rejected','flagged')");
    expect(sql).toContain("(status = 'approved' AND exception_expires_at IS NOT NULL))");
    // C-3 allowed only an EXPIRED exception to be re-granted; C-4 drops that.
    expect(sql).not.toContain("exception_expires_at <= now()");
    expect(params).toEqual([IMG, "vendor fix pending", "admin-1", EXPIRES.toISOString()]);
  });
```

In `app/src/__tests__/api-images-verbs.test.ts`, in the test `says why the image cannot take an exception, including the re-grant remedy (Fix A)`, replace `expect(body.error).toMatch(/approved image whose exception has expired/);` with `expect(body.error).toMatch(/replaces the exception on an approved image that carries one/);`.

In `app/src/__tests__/images-client.test.ts`, add `grantImageException` and `revokeImage` to the `@/lib/images/api` import list, and append inside `describe("images client", …)`:

```ts
  it("POSTs the admin verbs and surfaces a refusal's code", async () => {
    // A Response body reads once: a fresh reply per call.
    fetchMock.mockImplementation(async () => reply(200, { image: { id: "img-1" } }));
    const body = { reason: "vendor fix lands next sprint", expires_at: "2026-10-27T12:00:00.000Z" };
    await grantImageException("img-1", body);
    let [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/images/img-1/exception");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(body);

    await revokeImage("img-1");
    [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/images/img-1/revoke");
    expect(init.method).toBe("POST");

    fetchMock.mockImplementation(async () =>
      reply(409, { error: "This image is already revoked", code: "image_already_revoked" }),
    );
    await expect(revokeImage("img-1")).rejects.toMatchObject({
      status: 409,
      code: "image_already_revoked",
    });
  });
```

Run (from `app/`): `npx vitest run src/__tests__/images-registry-storage.test.ts src/__tests__/api-images-verbs.test.ts src/__tests__/images-client.test.ts`. Expected: FAIL.

- [ ] **Step 2: Storage.** In `app/src/lib/images/storage.ts`, replace the doc comment above `grantImageException` with:

```ts
/** `rejected`/`flagged` -> `approved` with the one live exception (spec §4.4).
 * On an `approved` image that already carries an exception, live or
 * expired, the grant REPLACES it (C-4; spec §4.4: "revoking an exception
 * early = image.revoke or a new grant"). The new reason and expiry
 * overwrite the old; each grant stays in the audit log. An approved image
 * with no exception passed on its own and takes none. Conditional UPDATE,
 * so a concurrent drain transition cannot be overwritten by a stale read. */
```

and in its SQL replace

```sql
        AND (status IN ('rejected','flagged')
             OR (status = 'approved' AND exception_expires_at IS NOT NULL AND exception_expires_at <= now()))
```

with

```sql
        AND (status IN ('rejected','flagged')
             OR (status = 'approved' AND exception_expires_at IS NOT NULL))
```

- [ ] **Step 3: Route.** In `app/src/pages/api/images/[id]/exception.ts`, replace the docstring sentence `Only a \`rejected\` or \`flagged\` image takes an exception (-> \`approved\`).` with `A \`rejected\` or \`flagged\` image takes an exception (-> \`approved\`); on an \`approved\` image that carries one, the grant replaces it (C-4, spec §4.4).`. Replace the 409 body's `error` with:

```ts
        error: `An exception applies to a rejected or flagged image, or replaces the exception on an approved image that carries one; this one is ${outcome.status}`,
```

- [ ] **Step 4: Client verbs.** In `app/src/lib/images/api.ts`, change `import type { ImageAdd } from "./schemas";` to `import type { ImageAdd, ImageException } from "./schemas";` and append:

```ts
/** Admin (spec §4.4): grant an expiring exception, or replace the one an
 * approved image carries. The route re-checks the role and the policy's
 * `exception_max_days`. */
export async function grantImageException(
  id: string,
  body: ImageException,
): Promise<{ image: Image }> {
  return apiFetch<{ image: Image }>(`/api/images/${enc(id)}/exception`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Admin (spec §4.3): any status -> `revoked`, terminal. */
export async function revokeImage(id: string): Promise<{ image: Image }> {
  return apiFetch<{ image: Image }>(`/api/images/${enc(id)}/revoke`, { method: "POST" });
}
```

In `app/src/lib/images/queries.ts`, add `grantImageException` and `revokeImage` to the `./api` import list, change `import type { ImageAdd } from "./schemas";` to `import type { ImageAdd, ImageException } from "./schemas";`, and append:

```ts
export function useGrantImageException() {
  return useImageMutation(({ id, body }: { id: string; body: ImageException }) =>
    grantImageException(id, body),
  );
}

export function useRevokeImage() {
  return useImageMutation((id: string) => revokeImage(id));
}
```

- [ ] **Step 5: Run the tests.** Rerun the Step 1 command. Expected: PASS.

- [ ] **Step 6: Gates and commit.** `npm run verify`.

```bash
git add app/src/lib/images/storage.ts "app/src/pages/api/images/[id]/exception.ts" \
  app/src/lib/images/api.ts app/src/lib/images/queries.ts \
  app/src/__tests__/images-registry-storage.test.ts app/src/__tests__/api-images-verbs.test.ts \
  app/src/__tests__/images-client.test.ts
git commit -m "feat(images): a new grant replaces a live exception; client exception/revoke verbs (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `process_image_flagged` reads as degraded, not failing

**Files:**
- Modify: `app/src/components/monitoring/shared.ts`
- Modify: `app/src/components/processes/health.ts`
- Modify: `app/src/components/layout/overview.ts`
- Test: `app/src/__tests__/processes-health.test.ts`, `app/src/__tests__/overview.test.ts`

**Interfaces:**
- Produces (`@/components/monitoring/shared`): `DEGRADED_ALERT_KINDS: ReadonlySet<string>` (`{"process_image_flagged"}`); `openAlertHealth(alert: { state: string; kind: string }): "error" | "warn"`.

Spec §10: "health.ts's process verdict treats an open `process_image_flagged` as degraded (not failing)". The overview's process nodes and product rows read the same helper, so `/processes` and the home page agree (the I-117 lesson). The A-1 completeness gate (`alertsAreComplete`) is unchanged.

- [ ] **Step 1: Write the failing tests.** In `app/src/__tests__/processes-health.test.ts`, change `import { alertKindLabel } from "@/components/monitoring/shared";` to `import { alertKindLabel, openAlertHealth } from "@/components/monitoring/shared";` and append:

```ts
describe("process_image_flagged is degraded, not failing (C-4, spec §10)", () => {
  it("a firing image alert is a warning labelled by its kind", () => {
    const v = processVerdict(process, [run()], 1, [alert({ kind: "process_image_flagged" })]);
    expect(v).toEqual({
      health: "warn",
      label: "Degraded",
      reason: alertKindLabel("process_image_flagged"),
    });
  });

  it("a failing alert on the same process still wins", () => {
    const v = processVerdict(process, [run()], 1, [
      alert({ id: "img", kind: "process_image_flagged" }),
      alert({ id: "dead", kind: "process_failed" }),
    ]);
    expect(v.health).toBe("error");
    expect(v.reason).toBe(alertKindLabel("process_failed"));
  });

  it("openAlertHealth: firing is an error unless the kind only degrades; acknowledged is a warning", () => {
    expect(openAlertHealth({ state: "firing", kind: "process_failed" })).toBe("error");
    expect(openAlertHealth({ state: "firing", kind: "process_image_flagged" })).toBe("warn");
    expect(openAlertHealth({ state: "acknowledged", kind: "process_failed" })).toBe("warn");
  });
});
```

In `app/src/__tests__/overview.test.ts`, inside the `describe` that contains `it("claims a process alert through a wired process (I-84)", …)`, add after that test:

```ts
  it("a flagged image degrades its process and its product, never fails them (C-4)", () => {
    const { rows } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      alertsAreComplete: true,
      openAlerts: [alert({ id: "img", kind: "process_image_flagged", process_id: "p1" })],
    });
    expect(rows[0].health).toBe("warn");
    expect(rows[0].reason).toBe(alertKindLabel("process_image_flagged"));
    const processes = rows[0].lineage.find((g) => g.kind === "process")!;
    expect(processes.nodes.every((n) => n.health === "warn")).toBe(true);
  });
```

Run (from `app/`): `npx vitest run src/__tests__/processes-health.test.ts src/__tests__/overview.test.ts`. Expected: FAIL (`openAlertHealth` is not exported; the verdicts are `error`).

- [ ] **Step 2: The shared helper.** In `app/src/components/monitoring/shared.ts`, directly after `alertKindLabel`, add:

```ts
/** Alert kinds whose FIRING state reads as degraded, not failing (C-4,
 * container-images spec §10): a flagged image blocks new deploys, but the
 * process keeps running on it. */
export const DEGRADED_ALERT_KINDS: ReadonlySet<string> = new Set(["process_image_flagged"]);

/** The health an OPEN alert implies: firing is an error unless its kind only
 * degrades; acknowledged is a warning we still show. */
export function openAlertHealth(alert: { state: string; kind: string }): "error" | "warn" {
  return alert.state === "firing" && !DEGRADED_ALERT_KINDS.has(alert.kind) ? "error" : "warn";
}
```

- [ ] **Step 3: The process verdict.** In `app/src/components/processes/health.ts`:
  - Change the import to `import { DEGRADED_ALERT_KINDS, alertKindLabel, openAlertHealth } from "@/components/monitoring/shared";`.
  - Append to the module docstring: `C-4: a firing \`process_image_flagged\` is degraded (warn), not failing: the image blocks new deploys while the process keeps running (container-images spec §10).`
  - Replace

```ts
  const firingAlert = own.find((a) => a.state === "firing");
  const acknowledgedAlert = own.find((a) => a.state === "acknowledged");
  if (firingAlert) {
    return { health: "error", label: "Failing", reason: alertKindLabel(firingAlert.kind) };
  }
```

with

```ts
  const firingAlert = own.find((a) => openAlertHealth(a) === "error");
  const degradedAlert = own.find(
    (a) => a.state === "firing" && DEGRADED_ALERT_KINDS.has(a.kind),
  );
  const acknowledgedAlert = own.find((a) => a.state === "acknowledged");
  if (firingAlert) {
    return { health: "error", label: "Failing", reason: alertKindLabel(firingAlert.kind) };
  }
  if (degradedAlert) {
    return { health: "warn", label: "Degraded", reason: alertKindLabel(degradedAlert.kind) };
  }
```

- [ ] **Step 4: The overview.** In `app/src/components/layout/overview.ts`:
  - Change the import to `import { DEGRADED_ALERT_KINDS, alertKindLabel, isLate, openAlertHealth, readFlowStats } from "@/components/monitoring/shared";`.
  - Replace the body of `function alertHealth(alert: Alert): LineageHealth` with `return openAlertHealth(alert);` and its comment with `/** An open alert's health (C-4: a kind that only degrades is a warning even while firing). */`.
  - In `buildProductRows`, replace `const firing = own.find((a) => a.state === "firing");` with

```ts
    const firing = own.find((a) => openAlertHealth(a) === "error");
    const degraded = own.find(
      (a) => a.state === "firing" && DEGRADED_ALERT_KINDS.has(a.kind),
    );
```

  and replace

```ts
    if (firing) {
      health = "error";
      reason = alertKindLabel(firing.kind);
    } else if (acknowledged) {
```

  with

```ts
    if (firing) {
      health = "error";
      reason = alertKindLabel(firing.kind);
    } else if (degraded) {
      health = "warn";
      reason = alertKindLabel(degraded.kind);
    } else if (acknowledged) {
```

- [ ] **Step 5: Run the tests.** Rerun the Step 1 command. Expected: PASS. (`npm run verify` in Step 6 runs every other consumer of these helpers.)

- [ ] **Step 6: Gates and commit.** `npm run verify`.

```bash
git add app/src/components/monitoring/shared.ts app/src/components/processes/health.ts \
  app/src/components/layout/overview.ts app/src/__tests__/processes-health.test.ts \
  app/src/__tests__/overview.test.ts
git commit -m "feat(health): a flagged image degrades its process, never fails it (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: Scan history with diffs and drift, and the "expired" wording

**Files:**
- Modify: `app/src/components/images/format.ts`
- Modify: `app/src/components/images/ImagesPage.tsx`
- Modify: `app/src/components/images/ImageDetailSheet.tsx`
- Modify: `app/src/lib/images/verdict.ts` (the `exceptionLapsed` doc comment only)
- Test: `app/src/__tests__/images-history.test.ts` (new), `app/src/__tests__/images-page.test.tsx`

**Interfaces:**
- Consumes: Task 1's `readScanDiff` and `type ImageScanDiff`.
- Produces (`@/components/images/format`): `isExceptionExpired(expiresAt: string, now: Date): boolean`; `formatCountsDelta(delta: ImageScanDiff["counts_delta"]): string`; `scanDiffSummary(scan: ImageScan, scans: readonly ImageScan[]): string | null`; `scanTagDrift(scan: ImageScan): string | null`.

This task fixes the display bug carried from C-3 (#53 comment 2). The list and the sheet chose "expired" or "until/expires" from `exceptionLapsed`, which is verdict-aware, so an expired exception whose latest verdict passes rendered "until <past date>". The word is now keyed on the DATE alone. `exceptionLapsed` stays for the "new deploys are refused" warning and for the deploy gate, where it is now defence in depth behind Task 6's tick. The history line is spec §9.2's "+2 high, −1 medium since 2026-09-12".

- [ ] **Step 1: Write the failing tests.** Create `app/src/__tests__/images-history.test.ts`:

```ts
/**
 * C-4 presentation helpers: the exception wording keyed on the date alone,
 * and the scan-history line (spec §9.2: "+2 high, −1 medium since
 * 2026-09-12"), built from the diff the drain stores (spec §8.2).
 */
import { describe, expect, it } from "vitest";
import {
  formatCountsDelta,
  isExceptionExpired,
  scanDiffSummary,
  scanTagDrift,
} from "@/components/images/format";
import type { ImageScan } from "@/lib/images/types";

const NOW = new Date("2026-09-27T12:00:00.000Z");
const ZERO = { critical: 0, high: 0, medium: 0, low: 0, negligible: 0, unknown: 0 };

function scan(overrides: Partial<ImageScan> = {}): ImageScan {
  return {
    id: "s-2",
    image_id: "img-1",
    kind: "rescan",
    status: "done",
    requested_by: "pipeline",
    requested_at: "2026-09-27T03:15:00.000Z",
    started_at: null,
    finished_at: "2026-09-27T03:20:00.000Z",
    result: null,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

function withDiff(diff: Record<string, unknown>): ImageScan {
  return scan({
    result: {
      diff: {
        previous_scan_id: "s-1",
        new: [],
        resolved: [],
        newly_fixed: [],
        new_kev: [],
        verdict_changed: false,
        counts_delta: ZERO,
        ...diff,
      },
    },
  });
}

describe("isExceptionExpired (the date alone)", () => {
  it("is true once the expiry has passed, including exactly now (the gate's boundary)", () => {
    expect(isExceptionExpired("2026-09-27T11:59:59.000Z", NOW)).toBe(true);
    expect(isExceptionExpired(NOW.toISOString(), NOW)).toBe(true);
    expect(isExceptionExpired("2026-09-27T12:00:01.000Z", NOW)).toBe(false);
  });

  it("reads an unparseable date as expired, never as live", () => {
    expect(isExceptionExpired("not a date", NOW)).toBe(true);
  });
});

describe("formatCountsDelta", () => {
  it("names only the severities that moved, worst first, with a real minus sign", () => {
    expect(formatCountsDelta({ ...ZERO, high: 2, medium: -1 })).toBe("+2 high, −1 medium");
    expect(formatCountsDelta({ ...ZERO, critical: 1, low: -3 })).toBe("+1 critical, −3 low");
  });

  it("says so when nothing moved", () => {
    expect(formatCountsDelta(ZERO)).toBe("no change in counts");
  });
});

describe("scanDiffSummary", () => {
  const previous = scan({ id: "s-1", kind: "admission", finished_at: "2026-09-12T08:00:00.000Z" });

  it("dates the comparison by the previous scan when it is listed", () => {
    const s = withDiff({
      counts_delta: { ...ZERO, high: 2, medium: -1 },
      new_kev: ["CVE-2026-0003"],
      verdict_changed: true,
    });
    expect(scanDiffSummary(s, [s, previous])).toBe(
      "+2 high, −1 medium since 2026-09-12 · 1 new KEV, verdict changed",
    );
  });

  it("falls back to 'the previous scan' when it is not in the list", () => {
    const s = withDiff({ newly_fixed: ["CVE-2026-0002"] });
    expect(scanDiffSummary(s, [s])).toBe(
      "no change in counts since the previous scan · 1 newly fixed",
    );
  });

  it("is null for an admission or a scan without a readable diff", () => {
    expect(scanDiffSummary(scan(), [])).toBeNull();
    expect(scanDiffSummary(scan({ result: { diff: { new: "x" } } }), [])).toBeNull();
  });
});

describe("scanTagDrift", () => {
  it("names the digest the tag moved to", () => {
    const s = scan({
      result: { tag_drift: { current_digest: "sha256:" + "b".repeat(64), drifted: true } },
    });
    expect(scanTagDrift(s)).toBe("tag moved to sha256:bbbbbbbbbbbb");
  });

  it("says the tag was not checked when the registry did not answer", () => {
    const s = scan({ result: { tag_drift: { current_digest: null, drifted: false } } });
    expect(scanTagDrift(s)).toBe("tag not checked: the registry did not answer");
  });

  it("says nothing when the tag did not move or no check ran", () => {
    const same = scan({
      result: { tag_drift: { current_digest: "sha256:" + "a".repeat(64), drifted: false } },
    });
    expect(scanTagDrift(same)).toBeNull();
    expect(scanTagDrift(scan({ result: { tag_drift: null } }))).toBeNull();
    expect(scanTagDrift(scan())).toBeNull();
  });
});
```

In `app/src/__tests__/images-page.test.tsx`, append inside `describe("ImagesPage", …)`:

```ts
  it("keys 'expired' on the date alone, even when the latest verdict passes (C-4 fix)", async () => {
    const past = "2026-08-01T00:00:00.000Z";
    const detail = image({
      exception: { reason: "vendor fix pending", by: "admin-1", at: past, expires_at: past },
      verdict: { pass: true, reasons: [] },
    });
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id ? { image: detail, scans: [], in_use_by: [], in_use_elsewhere: 0 } : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText(/^expired /)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));
    expect(await screen.findByText(/Granted by admin-1, expired/)).toBeInTheDocument();
    // The verdict passes on its own: no "deploys are refused" warning.
    expect(screen.queryByText(/new deploys are refused until a rescan passes/)).toBeNull();
  });

  it("shows each rescan's diff, its drift and a findings link in the scan history", async () => {
    const detail = image();
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id
        ? {
            image: detail,
            scans: [
              scan({
                id: "s-2",
                kind: "rescan",
                finished_at: "2026-09-27T03:20:00.000Z",
                findings_ref: "scans/img-1/s-2/findings.grype.json",
                result: {
                  tag_drift: { current_digest: "sha256:" + "b".repeat(64), drifted: true },
                  diff: {
                    previous_scan_id: "s-1",
                    new: ["CVE-2026-0003"],
                    resolved: [],
                    newly_fixed: [],
                    new_kev: ["CVE-2026-0003"],
                    verdict_changed: true,
                    counts_delta: { critical: 0, high: 2, medium: -1, low: 0, negligible: 0, unknown: 0 },
                  },
                },
              }),
              scan({ id: "s-1", finished_at: "2026-09-26T00:05:00.000Z" }),
            ],
            in_use_by: [],
            in_use_elsewhere: 0,
          }
        : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));
    expect(await screen.findByTestId("scan-diff")).toHaveTextContent(
      "+2 high, −1 medium since 2026-09-26 · 1 new KEV, verdict changed",
    );
    expect(screen.getByText("tag moved to sha256:bbbbbbbbbbbb")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "findings" })).toHaveAttribute(
      "href",
      "/api/images/img-1/scans/s-2/findings",
    );
  });
```

The existing test `shows an expired exception as 'expired <date>', a live one as 'until <date>'` keeps passing unchanged.

Run (from `app/`): `npx vitest run src/__tests__/images-history.test.ts src/__tests__/images-page.test.tsx`. Expected: FAIL (the helpers are not exported; the list says `until` for the passing-verdict case).

- [ ] **Step 2: The helpers.** In `app/src/components/images/format.ts`, add below the existing imports:

```ts
import { readScanDiff, type ImageScanDiff } from "@/lib/images/scan-diff";
```

and append:

```ts
/**
 * Has an exception's expiry date passed (C-4)? The DATE alone decides the
 * word "expired"; whether new deploys are refused is `exceptionLapsed`'s
 * (verdict-aware) question, asked separately. Expiry exactly `now` counts
 * as expired (the gate's boundary), and an unparseable date never reads as
 * live.
 */
export function isExceptionExpired(expiresAt: string, now: Date): boolean {
  const t = Date.parse(expiresAt);
  return !Number.isFinite(t) || t <= now.getTime();
}

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "negligible", "unknown"] as const;

/** "+2 high, −1 medium" (U+2212, spec §9.2): only the severities that moved. */
export function formatCountsDelta(delta: ImageScanDiff["counts_delta"]): string {
  const parts = SEVERITY_ORDER.filter((s) => delta[s] !== 0).map(
    (s) => `${delta[s] > 0 ? "+" : "−"}${Math.abs(delta[s])} ${s}`,
  );
  return parts.length > 0 ? parts.join(", ") : "no change in counts";
}

/**
 * One history line (spec §9.2): the count change since the previous scan,
 * dated by that scan when it is listed, then new KEVs, newly fixed findings
 * and a verdict flip. Null for an admission or a scan without a diff.
 */
export function scanDiffSummary(scan: ImageScan, scans: readonly ImageScan[]): string | null {
  const diff = readScanDiff(scan.result);
  if (!diff) return null;
  const previous = diff.previous_scan_id
    ? scans.find((s) => s.id === diff.previous_scan_id)
    : undefined;
  const since = previous?.finished_at
    ? ` since ${previous.finished_at.slice(0, 10)}`
    : diff.previous_scan_id
      ? " since the previous scan"
      : "";
  const extras: string[] = [];
  if (diff.new_kev.length > 0) extras.push(`${diff.new_kev.length} new KEV`);
  if (diff.newly_fixed.length > 0) extras.push(`${diff.newly_fixed.length} newly fixed`);
  if (diff.verdict_changed) extras.push("verdict changed");
  return `${formatCountsDelta(diff.counts_delta)}${since}${extras.length > 0 ? ` · ${extras.join(", ")}` : ""}`;
}

/** A rescan's drift note (spec §8.2, informational): where the tag points
 * now when it moved, or that the registry did not answer the HEAD. */
export function scanTagDrift(scan: ImageScan): string | null {
  const raw = scan.result?.tag_drift;
  if (raw === null || typeof raw !== "object") return null;
  const drift = raw as { current_digest?: unknown; drifted?: unknown };
  if (drift.current_digest === null) return "tag not checked: the registry did not answer";
  if (drift.drifted === true && typeof drift.current_digest === "string") {
    return `tag moved to ${shortDigest(drift.current_digest)}`;
  }
  return null;
}
```

- [ ] **Step 3: The list.** In `app/src/components/images/ImagesPage.tsx`:
  - Change `import { exceptionLapsed, readVerdict } from "@/lib/images/verdict";` to `import { readVerdict } from "@/lib/images/verdict";`.
  - Change `import { dbAgeDays, shortDigest } from "./format";` to `import { dbAgeDays, isExceptionExpired, shortDigest } from "./format";`.
  - Replace the `exceptionExpired` computation with:

```tsx
                // C-4: the word follows the DATE alone; whether new deploys
                // are refused (verdict-aware) is the detail sheet's warning.
                const exceptionExpired =
                  image.exception !== null && isExceptionExpired(image.exception.expires_at, now);
```

- [ ] **Step 4: The detail sheet.** In `app/src/components/images/ImageDetailSheet.tsx`:
  - In the module docstring, replace `Scan-history diffs and the admin exception form are C-4; a live exception is shown read-only here.` with `C-4 adds each rescan's diff and tag drift to the history, and the admin verbs.`
  - Change the `./format` import to `import { configWarning, isExceptionExpired, latestScanResult, scanDiffSummary, scanError, scanTagDrift, shortDigest, sortFindings } from "./format";`.
  - Replace `{lapsed ? "expired" : "expires"}{" "}` with `{isExceptionExpired(image.exception.expires_at, now) ? "expired" : "expires"}{" "}`. Keep the `{lapsed && (…)}` warning below it unchanged.
  - Replace the whole `<section>` whose heading is `Scan history` with:

```tsx
            <section className="grid gap-1">
              <h3 className="font-semibold">Scan history</h3>
              {data.scans.length === 0 ? (
                <p className="text-muted-foreground">No scans yet.</p>
              ) : (
                <ul className="grid gap-2">
                  {data.scans.map((s) => {
                    const summary = scanDiffSummary(s, data.scans);
                    const drift = scanTagDrift(s);
                    const failure = s.status === "failed" ? scanError(s) : null;
                    return (
                      <li key={s.id} className="grid gap-0.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={s.status === "failed" ? "destructive" : "secondary"}>
                            {s.status}
                          </Badge>
                          <span>{s.kind}</span>
                          <span className="text-muted-foreground">
                            requested {timeAgo(s.requested_at)}
                            {s.finished_at ? `, finished ${timeAgo(s.finished_at)}` : ""}
                          </span>
                          {s.findings_ref && (
                            <a
                              className="text-primary hover:underline"
                              href={`/api/images/${image.id}/scans/${s.id}/findings`}
                            >
                              findings
                            </a>
                          )}
                        </div>
                        {summary && (
                          <p className="text-xs" data-testid="scan-diff">
                            {summary}
                          </p>
                        )}
                        {drift && <p className="text-xs text-muted-foreground">{drift}</p>}
                        {failure && <p className="text-xs text-destructive">{failure}</p>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
```

- [ ] **Step 5: `exceptionLapsed`'s role.** In `app/src/lib/images/verdict.ts`, append to the `exceptionLapsed` doc comment: ` Since C-4 the pipeline's hourly tick clears an expired exception (and flags a failing image), so at the deploy gate this rule is defence in depth; the UI no longer uses it to pick the word "expired" (\`isExceptionExpired\` in components/images/format.ts does).`

- [ ] **Step 6: Run the tests.** Rerun the Step 1 command. Expected: PASS.

- [ ] **Step 7: Gates and commit.** `npm run verify`.

```bash
git add app/src/components/images/format.ts app/src/components/images/ImagesPage.tsx \
  app/src/components/images/ImageDetailSheet.tsx app/src/lib/images/verdict.ts \
  app/src/__tests__/images-history.test.ts app/src/__tests__/images-page.test.tsx
git commit -m "feat(images): scan history with diffs and drift; 'expired' keyed on the date (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: The admin verbs on the detail sheet: exception (grant or replace) and revoke

**Files:**
- Modify: `app/src/components/images/format.ts` (two pure helpers)
- Create: `app/src/components/images/ImageAdminActions.tsx`
- Modify: `app/src/components/images/ImageDetailSheet.tsx` (a `canAdmin` prop)
- Modify: `app/src/components/images/ImagesPage.tsx` (pass `canAdmin`)
- Test: `app/src/__tests__/images-history.test.ts` (append), `app/src/__tests__/images-page.test.tsx`

**Interfaces:**
- Consumes: Task 8's `useGrantImageException`, `useRevokeImage`; `useImagePolicy` (C-3); `IMAGE_EXCEPTION_REASON_MIN` (C-3, `@/lib/images/schemas`).
- Produces (`@/components/images/format`): `canTakeException(image: Pick<Image, "status" | "exception">): boolean` and `exceptionExpiry(days: number, now: Date): string` (ISO). The component is `ImageAdminActions({ image }: { image: Image })`; the section carries `data-testid="image-admin-actions"`. `ImageDetailSheet` gains a required prop `canAdmin: boolean`.

An exception is offered on `rejected` and `flagged` images, and on an `approved` image that carries one (Task 8: the grant replaces it). The expiry is entered as whole days, capped by the policy's `exception_max_days` (90 while the policy is still loading). The route re-checks it. Revoke is terminal and asks for confirmation, naming how many processes use the image. Both render only for admins, and neither renders on a revoked image.

- [ ] **Step 1: Write the failing tests.** Append to `app/src/__tests__/images-history.test.ts` (and add `canTakeException, exceptionExpiry` to its `@/components/images/format` import):

```ts
describe("the exception verbs' helpers", () => {
  it("offers an exception on rejected and flagged images, and on an approved image that carries one", () => {
    const live = { reason: "r", by: "a", at: "x", expires_at: "2026-10-01T00:00:00.000Z" };
    expect(canTakeException({ status: "rejected", exception: null })).toBe(true);
    expect(canTakeException({ status: "flagged", exception: null })).toBe(true);
    expect(canTakeException({ status: "approved", exception: live })).toBe(true);
    expect(canTakeException({ status: "approved", exception: null })).toBe(false);
    for (const status of ["pending", "scanning", "revoked", "scan_failed"] as const) {
      expect(canTakeException({ status, exception: null }), status).toBe(false);
    }
  });

  it("sends an expiry whole days from now", () => {
    expect(exceptionExpiry(30, NOW)).toBe("2026-10-27T12:00:00.000Z");
  });
});
```

In `app/src/__tests__/images-page.test.tsx`:
- Add `grantMutate` and `revokeMutate` to the `vi.hoisted` object (`grantMutate: vi.fn(), revokeMutate: vi.fn(),`), and to the `@/lib/images/queries` mock add:

```ts
  useGrantImageException: () => ({ mutateAsync: grantMutate, isPending: false }),
  useRevokeImage: () => ({ mutateAsync: revokeMutate, isPending: false }),
```

- Change the testing-library import to `import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";`.
- In `beforeEach`, add `grantMutate.mockReset(); revokeMutate.mockReset();`.
- Append inside `describe("ImagesPage", …)`:

```ts
  function openDetail(detail: Image) {
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id ? { image: detail, scans: [], in_use_by: [], in_use_elsewhere: 0 } : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: `${detail.reference}:${detail.tag_at_add}` }));
  }

  it("lets an admin grant an exception on a rejected image, with a reason and a bounded expiry", async () => {
    roles.value = ["admin"];
    grantMutate.mockResolvedValue({ image: image() });
    openDetail(image({ status: "rejected", verdict: { pass: false, reasons: ["kev:CVE-2026-9"] } }));
    fireEvent.click(await screen.findByRole("button", { name: /Grant exception/ }));
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "vendor fix lands next sprint" },
    });
    fireEvent.change(screen.getByLabelText("Lasts (days)"), { target: { value: "10" } });
    const before = Date.now();
    fireEvent.click(screen.getByRole("button", { name: "Save exception" }));
    await waitFor(() => expect(grantMutate).toHaveBeenCalledTimes(1));
    const [{ id, body }] = grantMutate.mock.calls[0];
    expect(id).toBe("img-1");
    expect(body.reason).toBe("vendor fix lands next sprint");
    const expires = Date.parse(body.expires_at);
    expect(expires).toBeGreaterThanOrEqual(before + 10 * 86_400_000);
    expect(expires).toBeLessThanOrEqual(Date.now() + 10 * 86_400_000);
  });

  it("refuses a thin reason before anything is sent", async () => {
    roles.value = ["admin"];
    openDetail(image({ status: "flagged" }));
    fireEvent.click(await screen.findByRole("button", { name: /Grant exception/ }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "ok" } });
    fireEvent.click(screen.getByRole("button", { name: "Save exception" }));
    expect(await screen.findByText(/At least 10 characters/)).toBeInTheDocument();
    expect(grantMutate).not.toHaveBeenCalled();
  });

  it("offers to replace the exception an approved image carries, and none when it carries none", async () => {
    roles.value = ["admin"];
    const live = new Date(Date.now() + 5 * 86_400_000).toISOString();
    openDetail(
      image({ exception: { reason: "vendor fix pending", by: "admin-1", at: live, expires_at: live } }),
    );
    expect(await screen.findByRole("button", { name: /Replace exception/ })).toBeInTheDocument();
  });

  it("an approved image without an exception offers revoke only", async () => {
    roles.value = ["admin"];
    openDetail(image());
    expect(await screen.findByRole("button", { name: /Revoke image/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Grant exception|Replace exception/ })).toBeNull();
  });

  it("revokes only after the confirmation", async () => {
    roles.value = ["admin"];
    revokeMutate.mockResolvedValue({ image: image({ status: "revoked" }) });
    openDetail(image());
    fireEvent.click(await screen.findByRole("button", { name: /Revoke image/ }));
    expect(screen.getByText(/Revoking is final/)).toHaveTextContent(/2 processes use it/);
    expect(revokeMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(revokeMutate).toHaveBeenCalledWith("img-1"));
  });

  it("shows no admin verbs to an operator", async () => {
    openDetail(image({ status: "rejected" }));
    expect(await screen.findByText("Scan history")).toBeInTheDocument();
    expect(screen.queryByTestId("image-admin-actions")).toBeNull();
  });

  it("shows no admin verbs on a revoked image, even to an admin", async () => {
    roles.value = ["admin"];
    openDetail(image({ status: "revoked" }));
    expect(await screen.findByText("Scan history")).toBeInTheDocument();
    expect(screen.queryByTestId("image-admin-actions")).toBeNull();
  });
```

Run (from `app/`): `npx vitest run src/__tests__/images-history.test.ts src/__tests__/images-page.test.tsx`. Expected: FAIL.

- [ ] **Step 2: The helpers.** Append to `app/src/components/images/format.ts` (and add `import type { Image } from "@/lib/images/types";`. The file already imports `type ImageScan` from there, so make it `import type { Image, ImageScan } from "@/lib/images/types";`):

```ts
/** Spec §4.4 + C-4: an exception approves a rejected or flagged image, and a
 * new grant replaces the one an approved image carries. An approved image
 * without one passed on its own. */
export function canTakeException(image: Pick<Image, "status" | "exception">): boolean {
  return (
    image.status === "rejected" ||
    image.status === "flagged" ||
    (image.status === "approved" && image.exception !== null)
  );
}

/** The `expires_at` a grant of `days` sends. The server measures its cap
 * from its own, later "now", so a whole-day grant at the cap stays inside. */
export function exceptionExpiry(days: number, now: Date): string {
  return new Date(now.getTime() + days * 86_400_000).toISOString();
}
```

- [ ] **Step 3: The component.** Create `app/src/components/images/ImageAdminActions.tsx`:

```tsx
/**
 * The admin verbs on one image (C-4, container-images spec §4.4, §9.2):
 * grant an expiring exception (or replace the one an approved image
 * carries), and revoke. Rendered only for admins; the routes re-check the
 * role and the policy's `exception_max_days`. An exception never covers
 * staleness, and every grant is audited. Revoke is terminal and ends any
 * exception.
 */
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, Input, Label, Textarea } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Ban, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { useGrantImageException, useImagePolicy, useRevokeImage } from "@/lib/images/queries";
import { IMAGE_EXCEPTION_REASON_MIN } from "@/lib/images/schemas";
import type { Image } from "@/lib/images/types";
import { canTakeException, exceptionExpiry } from "./format";

/** Spec §7.1's default `exception_max_days`, used until the policy loads. */
const FALLBACK_MAX_DAYS = 90;
const DEFAULT_DAYS = 30;

function exceptionFormSchema(maxDays: number) {
  return z.object({
    reason: z
      .string()
      .trim()
      .min(IMAGE_EXCEPTION_REASON_MIN, `At least ${IMAGE_EXCEPTION_REASON_MIN} characters`)
      .max(2000),
    days: z
      .number({ error: "Enter a number of days" })
      .int("Whole days only")
      .min(1, "At least one day")
      .max(maxDays, `At most ${maxDays} days (the policy's exception_max_days)`),
  });
}
type ExceptionForm = z.infer<ReturnType<typeof exceptionFormSchema>>;

function ExceptionDialog({ image, onClose }: { image: Image; onClose: () => void }) {
  const { data: policy } = useImagePolicy();
  const maxDays = policy?.exception_max_days ?? FALLBACK_MAX_DAYS;
  const grant = useGrantImageException();
  const replacing = image.exception !== null;
  const form = useForm<ExceptionForm>({
    // Zod v4 inference vs zodResolver: the repo's known cast (project-conventions).
    resolver: zodResolver(exceptionFormSchema(maxDays)) as any,
    defaultValues: { reason: "", days: Math.min(DEFAULT_DAYS, maxDays) },
  });
  const days = form.watch("days");
  const errors = form.formState.errors;

  const submit = form.handleSubmit(async (values) => {
    try {
      await grant.mutateAsync({
        id: image.id,
        body: { reason: values.reason.trim(), expires_at: exceptionExpiry(values.days, new Date()) },
      });
      toast.success(replacing ? "Exception replaced" : "Exception granted");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not grant the exception");
    }
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{replacing ? "Replace exception" : "Grant exception"}</DialogTitle>
          <DialogDescription>
            {`${image.reference}:${image.tag_at_add}`} is approved despite its policy reasons until
            the exception expires. It never covers a stale image, and every grant is audited.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="exception-reason">Reason</Label>
            <Textarea id="exception-reason" rows={3} {...form.register("reason")} />
            {errors.reason && <p className="text-xs text-destructive">{errors.reason.message}</p>}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="exception-days">Lasts (days)</Label>
            <Input
              id="exception-days"
              type="number"
              min={1}
              max={maxDays}
              {...form.register("days", { valueAsNumber: true })}
            />
            <p className="text-xs text-muted-foreground">
              {Number.isFinite(days) && days >= 1
                ? `Expires ${new Date(exceptionExpiry(days, new Date())).toLocaleDateString()}; `
                : ""}
              at most {maxDays} days.
            </p>
            {errors.days && <p className="text-xs text-destructive">{errors.days.message}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={grant.isPending}>
              Save exception
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RevokeDialog({ image, onClose }: { image: Image; onClose: () => void }) {
  const revoke = useRevokeImage();
  const users =
    image.in_use_by === 0
      ? "No process uses it."
      : `${image.in_use_by} ${image.in_use_by === 1 ? "process uses" : "processes use"} it.`;

  const confirm = async () => {
    try {
      await revoke.mutateAsync(image.id);
      toast.success("Image revoked");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not revoke the image");
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke image</DialogTitle>
          <DialogDescription>
            Revoking is final: new deploys are refused, every run of a process whose revision
            uses {`${image.reference}:${image.tag_at_add}`} dies at launch, and any exception
            ends. {users}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirm} disabled={revoke.isPending}>
            Revoke
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ImageAdminActions({ image }: { image: Image }) {
  const [granting, setGranting] = useState(false);
  const [revoking, setRevoking] = useState(false);
  if (image.status === "revoked") return null;
  return (
    <section className="grid gap-2" data-testid="image-admin-actions">
      <h3 className="font-semibold">Admin</h3>
      <div className="flex flex-wrap gap-2">
        {canTakeException(image) && (
          <Button variant="outline" onClick={() => setGranting(true)}>
            <ShieldCheck className="h-4 w-4" />
            {image.exception ? "Replace exception" : "Grant exception"}
          </Button>
        )}
        <Button variant="destructive" onClick={() => setRevoking(true)}>
          <Ban className="h-4 w-4" />
          Revoke image
        </Button>
      </div>
      {granting && <ExceptionDialog image={image} onClose={() => setGranting(false)} />}
      {revoking && <RevokeDialog image={image} onClose={() => setRevoking(false)} />}
    </section>
  );
}
```

If `z.number({ error: … })` does not type-check against the installed Zod v4 (check `grep '"zod"' app/package.json`), use `z.number({ message: "Enter a number of days" })`. Keep the rest.

- [ ] **Step 4: Wire it.** In `app/src/components/images/ImageDetailSheet.tsx`:
  - Add `import { ImageAdminActions } from "./ImageAdminActions";`.
  - Add `canAdmin` to the props: the destructure becomes `{ imageId, onClose, canOperate, canAdmin }`, and the type gains `canAdmin: boolean;`.
  - Directly after the `{canOperate && image.status !== "revoked" && (…Rescan now…)}` block, add `{canAdmin && <ImageAdminActions image={image} />}`.

In `app/src/components/images/ImagesPage.tsx`, add `const canAdmin = roles.includes("admin");` below `canOperate`, and pass `canAdmin={canAdmin}` to `<ImageDetailSheet …/>`.

`grep -rn "ImageDetailSheet" app/src --include=*.tsx` must list no other renderer. If it does, pass `canAdmin={false}` there.

- [ ] **Step 5: Run the tests.** Rerun the Step 1 command. Expected: PASS.

- [ ] **Step 6: Gates and commit.** `npm run verify`.

```bash
git add app/src/components/images/format.ts app/src/components/images/ImageAdminActions.tsx \
  app/src/components/images/ImageDetailSheet.tsx app/src/components/images/ImagesPage.tsx \
  app/src/__tests__/images-history.test.ts app/src/__tests__/images-page.test.tsx
git commit -m "feat(images): the admin exception form (grant or replace) and revoke on the detail sheet (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 12: e2e coverage for the changed flow, and the docs

**Files:**
- Modify: `app/e2e/processes.spec.ts` (the teammate edits it; only the lead runs it)
- Modify: `docs/FEATURES.md`, `docs/ISSUES.md`, `docs/processes.md`, `docs/backend.md`, `docs/monitoring.md` (the retention paragraph), `services/pipeline/README.md`

The `processes` e2e spec covers `/images` read-only (C-3: sidebar link, list or empty state, the Add-image preview). C-4 changes the detail sheet: the history lines and admin verbs. No existing selector breaks. The dev-bypass identity is an operator, so the admin verbs must stay hidden in e2e, which is itself worth pinning. The addition is read-only, in the suite's style, and a bare database passes it.

- [ ] **Step 1: e2e.** In `app/e2e/processes.spec.ts`, insert directly after the closing `});` of `test.describe("Images (C-3)", …)`:

```ts
test.describe("Images (C-4)", () => {
  test("an image's detail sheet shows its scan history, and no admin verbs to an operator", async ({
    page,
  }) => {
    await page.goto("/images");
    await expect(
      page.getByText(/No images yet|\d+ images? in the registry/).first(),
    ).toBeVisible();
    const first = page.getByRole("row").nth(1).getByRole("button").first();
    // A bare database has no image to open: a legal state for this suite.
    if ((await first.count()) === 0) return;
    await first.click();
    await expect(page.getByRole("heading", { name: "Scan history" })).toBeVisible();
    // The dev-bypass identity is an operator: exception and revoke are admin.
    await expect(page.getByTestId("image-admin-actions")).toHaveCount(0);
  });
});
```

- [ ] **Step 2: `docs/FEATURES.md`.** Replace the row `| C-4 · Rescans, drift, flagged/stale, exceptions, retention | ⬜ | #53 |` with:

```markdown
| C-4 · Rescans, drift, flagged/stale, exceptions, retention | ✅ | `pipeline.image_rescan_tick` (`15 * * * *`, `images/lifecycle.py`): audited exception expiry (the latest scan re-evaluated against the current policy; pass stays approved, fail or unreadable → flagged; the four `exception_*` columns cleared; one `audit_log` row `exception_expired` by `pipeline`, compare-and-set) and one `rescan` row per approved/flagged image older than `rescan_interval_hours`. The drain stores each rescan's diff against the previous scan (`images/diff.py`, fixture `image-scan-diff.json`) and the pipeline's own tag-drift HEAD (`images/drift.py`, through `resolve_pinned`, the pull credential; the scanner stays credential-free on rescans). `process_image_flagged` has its writer (`images/alerts.py`, `alert-kinds.json` `image_kinds`): one alert per live enabled process whose current revision uses a flagged, revoked, gone or stale image, reconciled every minute; process-anchored alerts now route to the process's group webhooks; process health and the overview read it as degraded. Retention leg in `history_retention` (`images/retention.py`): each image's current SBOM pair and ten newest scans' objects kept, everything else under `scans/` older than 24 h deleted (dedup orphans included), refs of older rows nulled, rows past `HISTORY_RETENTION_DAYS` pruned. `/images`: scan history with diffs, drift and findings links; admin exception form (grant, or replace a live one) and revoke; "expired" keyed on the date. No DDL |
```

- [ ] **Step 3: `docs/ISSUES.md`.** Under `## Container images + scanning (C queue, epic #56)`:

(a) Replace the I-137 paragraph's final sentence (starting `Deployment-checklist item for compose hosts`) with:

```markdown
Deployment-checklist item for compose hosts: `docker image prune` on a
schedule, never while a run is starting. C-4 considered and deferred it: a
safe daemon delete needs a reference count across in-flight launches and
every revision still pinning the digest, and a delete racing a launch's
`_ensure_image` would fail that run. K-4's reconcile loop (and the kubelet's
own image GC on Kubernetes) is the right home.
```

(b) Append after the I-139 entry:

```markdown
### I-140 · The rescan diff compares summaries, not full findings 🟡
`image_scans.result.diff` (C-4, spec §8.2) is computed over the two stored
summaries (`top`, the ≤ 25 findings chosen policy-first, plus the complete
`kev` list). A finding that drops below the top-25 cut reads as `resolved`,
and one that rises into it as `new`, although the scanner saw it both
times. `counts_delta` comes from the complete counts and is exact. The
alert's message quotes `new`/`new_kev` counts, so on a very noisy image it
can overstate churn; the verdict (which drives `flagged`) is unaffected.
Exact diffs need both full Grype JSON files (large, untrusted,
object-stored).
- Tracked in: `services/pipeline/src/pipeline/images/diff.py`.

### I-141 · A flagged image that gets worse does not re-notify 🟡
`process_image_flagged` is a state-observed condition (C-4): it fires once
when the image goes flagged (or revoked, or stale) and stays open. A later
rescan that adds CRITICALs or a KEV to an image that is already flagged
updates the open alert's message (`last_seen` bump), but ADR 0010 notifies
only on a new alert row, so webhooks hear nothing. The dashboard's scan
history shows each rescan's diff. A resolve-and-re-raise on a worsening
diff would re-notify, at the cost of alert churn.
- Tracked in: `services/pipeline/src/pipeline/images/alerts.py`.
```

- [ ] **Step 4: `docs/processes.md`.** In "Bring your own image", directly after the paragraph that ends `a scan that cannot finish leaves the image \`scan_failed\`.`, insert:

```markdown
Approvals are kept honest over time (C-4). Every approved or flagged image
is rescanned against a fresh vulnerability database once its
`rescan_interval_hours` (24 by default) are up. A rescan reads the stored
SBOM and pulls nothing. Each rescan records what changed since the
previous one ("+2 high, −1 medium since …") and whether the tag you added
now points at another digest. A moved tag is informational: your runs keep
the digest that was scanned, and adding the reference again scans the new
one. If a rescan newly fails an image, it goes **flagged**: new deploys are
refused, triggered runs keep launching, and each process whose current
revision uses it gets a `process_image_flagged` alert (degraded, not
failing). An image not scanned for `scan_window_days` (30) is **stale**,
and its runs are refused until a rescan passes. An admin exception (a
reason and at most `exception_max_days`, 90) approves a rejected or flagged
image. A new grant replaces a live exception, and revoking the image ends
it. When an exception expires, the latest scan is re-checked against the
current policy: the image stays approved if it passes and goes flagged if
not. Every grant and every expiry is in the audit log.
```

- [ ] **Step 5: `docs/backend.md`.** In the `/api/images/[id]/exception` row, replace `rejected/flagged, or approved with an expired exception → approved;` with `rejected/flagged → approved, or REPLACES the exception an approved image carries (live or expired, C-4);`.

- [ ] **Step 6: `docs/monitoring.md`.** In the paragraph that ends `the hourly \`history_retention\` sweep prunes them conservatively.`, append: ` Since C-4 it also runs the scan retention leg (\`pipeline/images/retention.py\`): each image's current SBOM pair and its ten newest scans' objects stay, every other object under \`scans/\` older than 24 h goes (dedup orphans included), older rows' refs are nulled, and \`image_scans\` rows past \`HISTORY_RETENTION_DAYS\` are pruned, never an image's \`last_scan_id\`.`

- [ ] **Step 7: `services/pipeline/README.md`.** At the end of the `## Image scans (C-2)` section (after the paragraph ending `… rather than the \`done\` one that actually produced it.`), add:

```markdown
**C-4.** `pipeline.image_rescan_tick` (`15 * * * *`, `jobs/image_scans.py`,
`images/lifecycle.py`) first expires admin exceptions: it re-evaluates the
latest stored scan against the current policy, clears the four
`exception_*` columns, and on a fail goes `flagged`. The UPDATE and its
`audit_log` row (`exception_expired`, actor `pipeline`) are one statement,
compare-and-set on `(status, exception_expires_at)`. It then inserts one
`rescan` row (`requested_by = pipeline`) per approved/flagged image older
than `rescan_interval_hours` with no open scan (`FOR UPDATE SKIP LOCKED`
against the app's "Rescan now"). On a rescan the drain stores the diff
against the image's previous scan (`images/diff.py`) and HEADs the tag
itself (`images/drift.py`: `resolve_pinned`, the image's pull credential,
HTTPS only, never fatal), writing `tag_current_digest`/`tag_checked_at` when
the registry answered. Before each drain tick, `images/alerts.py`
reconciles `process_image_flagged`. `history_retention` runs the scan
retention leg (`images/retention.py`). Pulled user images are still never
removed from the daemon (I-137).
```

- [ ] **Step 8: Gates and commit.** `npm run verify`. `verify` does not run Playwright specs, and the teammate must NOT run them: the lead runs `processes.spec.ts` in Task 13. Re-read the inserted e2e block once for balanced braces and the exact strings.

```bash
git add app/e2e/processes.spec.ts docs/FEATURES.md docs/ISSUES.md docs/processes.md \
  docs/backend.md docs/monitoring.md services/pipeline/README.md
git commit -m "docs+e2e: rescans, drift, the image alert, exceptions and retention (C-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Rebase, full gates, e2e, PR, smoke (LEAD ONLY)

- [ ] `git fetch origin main && git rebase origin/main`. Expected overlaps:
  - K-3 (#11, migration 029) or K-4 (#12, migration 031) may have merged. This slice has no migration, so nothing collides in `migrate.ts`.
  - `docs/FEATURES.md`, `docs/ISSUES.md` (renumber I-140/I-141 if taken), `docs/monitoring.md`, `docs/backend.md`, `services/pipeline/README.md`: keep both sides.
  - If `services/pipeline/src/pipeline/jobs/image_scans.py` or `images/drain.py` changed on `main` (a C-2 follow-up), re-apply Tasks 3, 4 and 6's edits by hand and re-run `tests/test_image_scan_drain.py`.
  - `package-lock.json`: `git checkout --theirs package-lock.json && npm install && git add package-lock.json`.
- [ ] Gates: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . ../image-scanner/stac_higher_scanner`. All green.
- [ ] e2e (Docker stack up, `astro dev stop` first, `CREDENTIALS_MASTER_KEY` sourced by absolute path from the main checkout's `.env`; see the `run-e2e` skill): `cd app && npm run test:e2e:ci -- processes` and `npm run test:e2e:ci -- monitoring` (the alert-kind labels and the process verdict feed `/monitoring`). The new `Images (C-4)` block is read-only and passes on a bare database.
- [ ] `git push -u origin feat/c4-rescans-drift`, then `gh pr create --base main --title "C-4: rescans, drift, the image alert, exceptions and retention"`. The body starts `Closes #53`. It lists the gates and the e2e run, and copies "Decisions made in this plan" below as deviations and choices. It calls out Decision 7 (existing `process_*` alerts now reach webhooks) and Decision 3 (hourly tick), states "no DDL; migration 032 not used", and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] After CI is green, squash-merge, then `git worktree remove .claude/worktrees/c4-rescans-drift`.
- [ ] **Smoke (Docker policy `smoke`: rebuild/restart and the GOES canary; no measurements).** From the main checkout at the merged `main`:
  1. `docker compose build pipeline && docker compose up -d pipeline`. Hit any app route once (no migration this time, but it reconciles partitions).
  2. `docker compose logs pipeline --since 5m | grep -E "image_rescan_tick|image alerts|scan retention|Traceback"` shows the new jobs registered and no tracebacks. A `pipeline.image_rescan_tick` run appears at the next `:15`.
  3. On any approved image from C-2's smoke: `UPDATE stac_higher.container_images SET last_scanned_at = now() - interval '25 hours' WHERE id = '<id>'`. After the next `:15` tick, a `rescan` row with `requested_by = 'pipeline'` exists. Once the drain finishes it, `result->'diff'` and `result->'tag_drift'` are set, and `tag_checked_at` is set when the registry answered.
  4. The GOES canary still publishes (kind-1 runs are untouched).
- [ ] **Live checks beyond smoke:** open ONE lead-only issue, "C-4 live checks beyond smoke", in the C queue, linked from #54 (C-5), listing:
  - (a) a kind-2 process on an image that a tightened policy then flags. Check the `process_image_flagged` alert, a webhook delivery to the group's channel, a refused redeploy, and a triggered run that still launches.
  - (b) an exception granted, then its `exception_expires_at` set into the past by SQL. The next tick flags or keeps it and writes the `exception_expired` audit row.
  - (c) a re-grant over a live exception from the UI as an admin.
  - (d) a private GHCR image's drift HEAD through its group credential.
  - (e) the retention leg on a stack with more than ten scans of one image and a folded dedup's orphaned prefix.
  - (f) a stale image (`last_scanned_at` older than 30 days) raising the alert, and a rescan resolving it.

  C-5 (#54) may absorb it.
- [ ] Follow-ups to record: C-5 (#54) runs the spec §15 gate. I-137 stays deferred to K-4. I-140/I-141 are accepted limitations.

---

## Decisions made in this plan

Each resolves a C-4 question the spec leaves open, choosing the option most consistent with spec §14 and the C-1/C-2/C-3 decisions. They go into the PR body verbatim.

1. **No DDL; migration 032 is not used.** Migration 030 already has `tag_current_digest`/`tag_checked_at`, the exception columns, `container_images_rescan_idx`, `image_scans_image_idx` and the alert anchors (024). Rescan requests, expiry and retention are row writes. The expiry audit row is an ordinary `audit_log` INSERT (append-only, partition-reconciled by the app).
2. **The drift HEAD is the pipeline's, not the scanner's.** Spec §5 says drift HEADs go through `resolve_pinned` "when the pipeline touches it", while §6.3 sketches a best-effort HEAD inside the rescan scanner. The pipeline-side reading keeps C-2 Decision 25 (a rescan scanner never receives a registry credential) and uses the egress policy. It needs no scanner image change, and C-2's `check_identity` stays as it is. The HEAD runs in the drain, for rescans only. It uses the image's pull credential when it can be resolved and goes anonymous otherwise, and it never fails the scan. The scanner's `tag_drift` stays null and is overwritten in the stored result. `tag_current_digest`/`tag_checked_at` move only when the registry answered.
3. **The rescan tick is hourly (`15 * * * *`) with an interval check,** not spec §8.2's daily `15 3 * * *`. With a daily tick and a 24-hour interval, an image whose previous rescan finished after 03:15 is not due at the next 03:15, which means a rescan every other day. Hourly also enforces an expiring exception within the hour. An image is due at `last_scanned_at <= now - rescan_interval_hours`.
4. **The diff extends spec §8.2's four keys** (`new`, `resolved`, `newly_fixed`, `verdict_changed`) with `new_kev`, `counts_delta` and `previous_scan_id`. The alert and the "+2 high, −1 medium since …" history line need them. It is computed over the stored summaries (`top` plus the complete `kev`), not the full Grype JSON (I-140), for rescans only (an admission, including a digest fold, stores `null`), against the image's `last_scan_id` as read at claim time. It is a new cross-runtime fixture, `image-scan-diff.json`.
5. **`process_image_flagged` gets its own writer list, `image_kinds`,** rather than joining `monitor_kinds`. Monitor ownership would give the flow monitor auto-resolve authority over it. `images/alerts.py` reconciles it through `sync_alerts` scoped to that list, every minute, from the image-drain job, before the drain, so a 15-minute scan never delays it. Source is `health`. The condition covers flagged, revoked, gone, any other non-approved status, and stale (the gate's boundary). Only live, **enabled** processes are considered, since a disabled one cannot run. `declared_kinds` is empty again.
6. **No re-notification on a worsening flagged image** (I-141). ADR 0010 notifies only on a new alert row; the open alert's message carries the reasons and the latest diff. The alert-fatigue rule of spec §8.2 holds: raw count changes on a passing image raise nothing.
7. **Process-anchored alerts now route to the process's group.** The notify fan-out's group derivation never had a process leg, so `process_failed`, `process_stalled` and `process_rate_limited` reached no webhook (group NULL, marked notified). "Routing treats it like `process_failed`" (spec §10) is meaningful only once process anchors route, so all four process kinds route now. **This changes behaviour for the existing process kinds**: groups with webhook channels will start receiving them. The webhook payload gains `process_id` (additive).
8. **Health:** `DEGRADED_ALERT_KINDS` and `openAlertHealth` live in `components/monitoring/shared.ts`, and both `processVerdict` and the overview (process nodes and product rows) use them, so `/processes` and the home page agree. A failing alert on the same process still wins.
9. **Exception expiry re-evaluates rather than reading the stored verdict.** It parses the latest stored scan result and runs `evaluate()` against the CURRENT policy, so a policy tightened since the scan counts. A result that cannot be read fails closed (`flagged`). A row the drain already flagged only loses the columns. The UPDATE and its audit row are one statement, compare-and-set on `(status, exception_expires_at)`, so an admin who re-grants or revokes in between wins and nothing is audited for a write that did not happen. The audit row is action `exception_expired`, actor `pipeline`, on `container_image`, with the previous/new status, expiry, grantor, and the verdict's pass and reasons in `detail`. C-3's `exceptionLapsed` gate rule stays as defence in depth.
10. **A new grant replaces a live exception** (spec §4.4: "revoking an exception early = image.revoke or a new grant"). C-3 refused it with a 409. An approved image without an exception still gets the 409 (nothing to replace, and it passed on its own). The UI takes the expiry as whole days, capped by the policy's `exception_max_days`, and the route re-checks it.
11. **Retention.** The row window is `HISTORY_RETENTION_DAYS` (365), because spec §8.3's `PROCESS_RUN_RETENTION_DAYS` does not exist. Objects are pruned by keep-set over a `scans/` listing rather than per row: each image's current SBOM pair, its ten newest scans' `findings.grype.json`/`result.json`/`log`, and everything under a pending or running scan are kept. Everything else older than a 24-hour grace goes, which is what catches the dedup's orphaned `scans/{provisional_id}/…` objects and the objects of rows already gone. Then the refs of rows outside the ten are nulled (the findings route answers 404, not a dangling redirect). Then old rows are pruned, never an image's `last_scan_id`. The object leg runs first, and a failure there skips the rest. Storage faults are logged, and the table legs of `history_retention` are unaffected.
12. **I-137 (daemon image removal) stays out of C-4.** A safe delete needs a reference count across in-flight launches and every pinning revision, and a delete racing `_ensure_image` would fail a run. K-4's reconcile loop, or the kubelet on Kubernetes, is its home. The ISSUES entry now says so.
13. **The display word keys on the date alone** (`isExceptionExpired`). `exceptionLapsed` keeps the "new deploys are refused" warning and the gate.
14. **The e2e addition is read-only.** It opens a detail sheet if an image exists, sees the scan history, and pins that the operator dev-bypass identity sees no admin verbs. No existing selector changes.

## Self-review

- **Spec coverage.**
  - §4.3 status machine: flagged on a failing rescan (C-2's drain, unchanged). Stale is computed (alerts: Task 4, the gate and launch already enforce it). Exception expiry re-evaluates, pass → approved, fail → flagged, never rejected (Task 6).
  - §4.4 exceptions: one live exception per image, columns cleared on expiry (Task 6). A new grant replaces a live one (Task 8). Grants and expiries are audited (C-3, Task 6). The form, capped by `exception_max_days` (Tasks 8, 11). Revoke (Task 11).
  - §8.2: the tick with the interval, requested by `pipeline`, one open scan (Task 6). The diff computed by the drain, with the four spec keys plus three (Tasks 1, 3). The drift HEAD, informational, with the dashboard note (Tasks 2, 3, 10; the C-3 list already shows "tag moved").
  - §8.3 retention: latest SBOM pair, last ten findings and logs, objects before rows, not `asset_gc`, rows except `last_scan_id` (Task 7).
  - §10: kind out of `declared_kinds` into a writer set (Task 4). Process anchor, flagged/revoked/stale, auto-resolve on return or move-off (Task 4). Routing like `process_failed` (Task 5). Degraded health (Task 9).
  - §9.2 history with diffs and the exception form (Tasks 10, 11). Issue #53's comments: the column clear on a pass, the display fix, re-grant over a live exception, the tick, the diff, drift, the alerts module, expiry, retention with dedup orphans, and I-137 (all covered above).
- **Dry run.** Before this plan was committed, every task's code was applied mechanically from the plan's own code blocks. The pipeline tasks (1-7) went into a scratch copy of `services/` + `tests/contract-fixtures/` + `infra/`: the whole pipeline suite passed (1670 passed; the only 2 failures need `.github/`, which the scratch copy lacks) and `ruff check . ../image-scanner/stac_higher_scanner` was clean. The app tasks (1, 4, 8-11) went into this worktree, where `npm run verify` passed (astro check 0 errors, 1938 tests); they were then reverted. That run found and fixed two plan bugs: an unused unpacked variable (RUF059) and a reused `Response` body in the client test. The docs edits (Task 12), the e2e block and the Pg SQL (DB-gated) were not exercised.
- **Placeholders.** None. Every file is given in full or as an exact replacement with its anchor text. Where a pasted Python line might exceed 100 columns, the Global Constraints say to wrap it without changing content.
- **Type consistency.**
  - `ScanDiff.as_json()` keys equal the fixture's `document` keys and `imageScanDiffSchema`'s. `readScanDiff` reads `result.diff`, which Task 3 writes as `{**stored, "verdict": …, "diff": diff}`.
  - `CheckDrift` returns the `tag_drift` dict that Task 2's `tag_drift()` produces. `record_rescan(tag_current_digest=…)` is the same keyword in the ABC, Pg and the fake.
  - `ImageRow.last_scan_id` is the 14th column in `_IMAGE_COLUMNS` and `row[13]` in `_to_image`.
  - `IMAGE_ALERT_KINDS == ("process_image_flagged",)` equals the fixture's `image_kinds`, and `sync_image_alerts(repo, sync_alerts, *, scan_window_days, now)` matches the job's call.
  - `ExpiredException` and `ImageLifecycleRepo` method names match the fake in the test. `RESCAN_JOB_NAME` is imported by `test_main_jobs.py`.
  - `KeptScan`/`ScanRetentionRepo` (`list_kept_scans`, `list_sbom_refs`, `detach_pruned_refs`, `prune_scan_rows`) match the test repo. `list_objects`/`delete_keys` signatures match the history job's closures.
  - `grantImageException(id, body)`/`revokeImage(id)` (api) and `useGrantImageException` (`{id, body}`)/`useRevokeImage` (`id`) match the component and the page test's mocks. `canAdmin` is added to the sheet's props and passed by `ImagesPage`.
- **Review Focus.** Line 1 is Task 6's "an admin acting between the tick's read and write wins". Line 2 is Task 10's "keys 'expired' on the date alone" plus Task 6's "a passing re-evaluation stays approved and clears the exception". Line 3 is Task 7's "the keep set and what it deletes" (current SBOM, in-flight prefix, grace, dedup orphan). Line 4 is Task 2's failure, realm and credential-in-logs tests. Line 5 is Task 4's stale-boundary, never-scanned and live-enabled-current-revision SQL pin tests, plus the sync test that always reconciles (the auto-resolve when a process moves off the image); the lead's live check (f) covers it end to end.
