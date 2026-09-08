"""The `process_run` producer hooks (ADR 0014, spec §2 — M5-D).

The pair the Phase 7 seam left as ``NotImplementedError``. Everything
producer-specific lives here; the neutral steps are untouched, which is the
seam's check criterion.

**How a process publishes.** User code writes into its run prefix
(``staging/runs/{run_id}/`` — the only place its credentials permit):

- one ``*.json`` object per output item, a STAC item document;
- the asset files those documents reference, as SIBLINGS in the same prefix.

Asset hrefs are **relative filenames** (``"cloud_mask.tif"``), resolved
against the run's own prefix. Deliberately not the ``staging://`` grammar:
that scheme identifies an upload SESSION and belongs to push ingest, and
overloading it would make one grammar mean two different things depending on
who wrote it. An absolute href (http/s3) passes through untouched, which is
what a reference-style output needs.

**Rejection semantics are NOT push's, and that is the point (ISSUES I-80).**
Push finalizes an item that is ALREADY in pgstac, so its tiers are about
whether to delete it or restore a snapshot. A process output is not in the
catalog until the steps upsert it — so a rejected process item is simply
never published. There is nothing to delete and nothing to restore, and
copying push's delete-on-reject would let a bad run destroy a pre-existing
item that merely shares an id.

**Lineage (D-1).** The run row carries the items that triggered it, and this
resolver sees every output document before the neutral steps do — so it
stamps one ``derived_from`` link per triggering item onto each output that
carries none. That is a BATCH-level default: runs coalesce per source, so
the platform knows the set of inputs and the set of outputs, not which came
from which. One-in/one-out and many-in/one-out are exactly right under it;
a many-in/many-out process writes its own ``derived_from`` links (the
manifest names every input) and the platform leaves the link set alone.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.finalize.seam import (
    REF_STAGED,
    FinalizeOutcome,
    FinalizeRequest,
    ItemRef,
    OutcomeRecorder,
    ProducerResolver,
    RejectedItem,
    Resolution,
    ResolvedItem,
    StagedAsset,
)
from pipeline.finalize.store import ObjectStore
from pipeline.metrics import PROCESS_OUTPUT_ITEMS
from pipeline.process.repo import ProcessRepo
from pipeline.storage.keys import (
    INPUTS_SEGMENT,
    item_href,
    run_staging_prefix,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

#: Rejection reasons this producer writes. Deliberately its own closed set —
#: push's reasons describe upload sessions, which a process run does not have.
REASON_INVALID_ITEM = "invalid_item"
REASON_WRONG_COLLECTION = "wrong_collection"
REASON_MISSING_ASSET = "missing_asset"
REASON_NO_OUTPUTS = "no_outputs"

PROCESS_REJECTION_REASONS = (
    REASON_INVALID_ITEM,
    REASON_WRONG_COLLECTION,
    REASON_MISSING_ASSET,
    REASON_NO_OUTPUTS,
)

#: Item documents are discovered by this suffix.
ITEM_DOCUMENT_SUFFIX = ".json"

#: The link rel finalize stamps for lineage (STAC core relation type).
REL_DERIVED_FROM = "derived_from"
#: Batch manifests the input planner wrote (`inputs/{batch}/manifest.json`).
MANIFEST_SUFFIX = "/manifest.json"
#: Root-relative catalog hrefs unless `CATALOG_HREF_BASE` says otherwise.
DEFAULT_CATALOG_HREF_BASE = "/"


def build_process_request(
    run_id: str,
    output_collections: tuple[str, ...],
    input_items: Sequence[dict[str, Any]] = (),
) -> FinalizeRequest:
    """The §2 request for one process run.

    ``items`` is empty: unlike a push, the outputs are DISCOVERED by the
    resolver from what user code actually wrote, so the caller cannot name
    them in advance. ``input_items`` is the run row's triggering batch — it
    rides in ``provenance`` so the resolver can stamp lineage (D-1) without
    re-reading the row; an empty batch (a cron run) stamps nothing.
    """
    from pipeline.finalize.seam import PRODUCER_PROCESS_RUN

    return FinalizeRequest(
        producer=PRODUCER_PROCESS_RUN,
        staging_prefix=run_staging_prefix(run_id),
        output_collections=output_collections,
        items=(),
        provenance={"run_id": run_id, "input_items": tuple(input_items)},
    )


def _is_relative(href: object) -> bool:
    """A href the run means to resolve against its own prefix: no scheme and
    not root-relative. Anything else is reference-style and passes through."""
    return isinstance(href, str) and bool(href) and "://" not in href and not href.startswith("/")


def _relative_filename(href: object) -> str | None:
    """The sibling filename an href names, or None when it is not one.

    Absolute hrefs (any scheme, or a rooted path) are somebody else's bytes
    and pass through untouched. A relative href with a separator is refused
    rather than normalised — a process must not reach outside its own prefix,
    and `sanitize_filename` is the same guard the rest of the key layer uses.
    """
    if not isinstance(href, str) or not href:
        return None
    if "://" in href or href.startswith("/"):
        return None
    if "/" in href or "\\" in href:
        return None
    try:
        return sanitize_filename(href)
    except Exception:
        return None


@dataclass
class ProcessRunResolver(ProducerResolver):
    """Discover and load the run's output items from its staging prefix."""

    store: ObjectStore
    catalog_href_base: str = DEFAULT_CATALOG_HREF_BASE

    def _skipped_inputs(self, manifest_keys: list[str]) -> set[tuple[str, str]]:
        """The `(collection, item_id)` pairs the input planner recorded as
        skipped (`not_found`, deleted between trigger and run) in ANY batch
        manifest. An item the run never received must not be cited as a
        source. A manifest that cannot be read yields no skips — the stamp
        then errs towards the run row, which is what actually triggered it."""
        skipped: set[tuple[str, str]] = set()
        for key in manifest_keys:
            try:
                manifest = json.loads(self.store.get(key))
            except Exception:
                continue
            if not isinstance(manifest, dict):
                continue
            for entry in manifest.get("skipped") or ():
                if not isinstance(entry, dict):
                    continue
                coll, item_id = entry.get("collection"), entry.get("item_id")
                if isinstance(coll, str) and isinstance(item_id, str):
                    skipped.add((coll, item_id))
        return skipped

    def _lineage_sources(
        self, req: FinalizeRequest, skipped: set[tuple[str, str]]
    ) -> tuple[tuple[str, str], ...]:
        """The triggering items worth citing, deduplicated in batch order."""
        seen: set[tuple[str, str]] = set()
        sources: list[tuple[str, str]] = []
        for ref in req.provenance.get("input_items") or ():
            if not isinstance(ref, dict):
                continue
            coll, item_id = ref.get("collection_id"), ref.get("item_id")
            if not (isinstance(coll, str) and coll and isinstance(item_id, str) and item_id):
                continue
            pair = (coll, item_id)
            if pair in seen or pair in skipped:
                continue
            seen.add(pair)
            sources.append(pair)
        return tuple(sources)

    def _stamp_derived_from(
        self,
        document: dict,
        *,
        collection_id: str,
        item_id: str,
        sources: tuple[tuple[str, str], ...],
    ) -> None:
        """Append a `derived_from` link per source unless the document already
        carries one — an existing link is the author's override (they know the
        real fan-in), and the whole link set is then left untouched. An output
        that is also an input (an in-place update) is never linked to itself."""
        if not sources:
            return
        links = document.get("links")
        if not isinstance(links, list):
            links = []
            document["links"] = links
        if any(isinstance(ln, dict) and ln.get("rel") == REL_DERIVED_FROM for ln in links):
            return
        for coll, src_id in sources:
            if (coll, src_id) == (collection_id, item_id):
                continue
            links.append(
                {
                    "rel": REL_DERIVED_FROM,
                    "href": item_href(coll, src_id, base=self.catalog_href_base),
                    "type": "application/geo+json",
                }
            )

    async def resolve(self, req: FinalizeRequest) -> Resolution:
        prefix = req.staging_prefix
        keys = self.store.list_keys(prefix)
        # GOES spec §3.3: the platform stages a run's INPUTS under
        # `inputs/` inside the same prefix. Nothing there is an output — the
        # manifest is JSON and staged item documents may be too.
        inputs_prefix = f"{prefix}{INPUTS_SEGMENT}/"
        manifest_keys = [
            k for k in keys if k.startswith(inputs_prefix) and k.endswith(MANIFEST_SUFFIX)
        ]
        keys = [k for k in keys if not k.startswith(inputs_prefix)]
        document_keys = sorted(k for k in keys if k.endswith(ITEM_DOCUMENT_SUFFIX))
        available = set(keys)

        if not document_keys:
            # A run that produced nothing is not an error — a filter process
            # legitimately emits nothing for a batch. It is recorded as a
            # no-op so the ledger says "ran, published nothing" rather than
            # inventing a rejection.
            return Resolution(
                skip="no_outputs",
                skip_detail="run wrote no item documents",
                claimed=True,
            )

        items: list[ResolvedItem] = []
        rejected: list[RejectedItem] = []
        # D-1: the lineage every output inherits unless it wrote its own.
        # Manifests are read only when there is a batch to cite.
        sources: tuple[tuple[str, str], ...] = ()
        if req.provenance.get("input_items"):
            sources = self._lineage_sources(req, self._skipped_inputs(manifest_keys))

        for key in document_keys:
            ref = ItemRef(
                kind=REF_STAGED,
                collection_id="",  # filled from the document below
                item_id="",
                key=key,
            )
            try:
                document = json.loads(self.store.get(key))
            except Exception as err:
                rejected.append(
                    RejectedItem(
                        item_ref=ref,
                        reason=REASON_INVALID_ITEM,
                        detail=f"{key} is not readable JSON: {type(err).__name__}",
                    )
                )
                continue
            if not isinstance(document, dict):
                rejected.append(
                    RejectedItem(
                        item_ref=ref,
                        reason=REASON_INVALID_ITEM,
                        detail=f"{key} is not a JSON object",
                    )
                )
                continue

            item_id = document.get("id")
            collection_id = document.get("collection")
            if not isinstance(item_id, str) or not item_id:
                rejected.append(
                    RejectedItem(
                        item_ref=ref, reason=REASON_INVALID_ITEM, detail=f"{key} has no item id"
                    )
                )
                continue
            if not isinstance(collection_id, str) or not collection_id:
                rejected.append(
                    RejectedItem(
                        item_ref=ref,
                        reason=REASON_INVALID_ITEM,
                        detail=f"{key} declares no collection",
                    )
                )
                continue

            resolved_ref = ItemRef(
                kind=REF_STAGED,
                collection_id=collection_id,
                item_id=item_id,
                key=key,
            )

            # The process may only publish into collections an operator wired
            # as OUTPUTS. Without this a run could write items into any
            # collection in the catalog just by naming it in its JSON.
            if collection_id not in req.output_collections:
                rejected.append(
                    RejectedItem(
                        item_ref=resolved_ref,
                        reason=REASON_WRONG_COLLECTION,
                        detail=(
                            f"{collection_id!r} is not an output collection of this process"
                        ),
                    )
                )
                continue

            staged: list[StagedAsset] = []
            missing: list[str] = []
            for asset_key, entry in (document.get("assets") or {}).items():
                href = (entry or {}).get("href")
                if not _is_relative(href):
                    continue  # absolute/external href — not ours to move
                filename = _relative_filename(href)
                if filename is None:
                    # A relative href that is not a plain filename (`a/b.tif`,
                    # `../x`, `inputs/…` — GOES spec §3.3). Publishing it
                    # verbatim would put a dangling href in the catalog, so it
                    # is refused like any other file the run did not write.
                    missing.append(f"{asset_key} -> {href} (not a plain filename)")
                    continue
                staging_key = f"{prefix}{filename}"
                if staging_key not in available:
                    missing.append(f"{asset_key} -> {filename}")
                    continue
                staged.append(
                    StagedAsset(
                        asset_key=asset_key,
                        filename=filename,
                        staging_key=staging_key,
                    )
                )
            if missing:
                # An item referencing bytes the run never wrote would publish
                # a broken asset href. Reject the ITEM, not the run: its
                # siblings may be fine.
                rejected.append(
                    RejectedItem(
                        item_ref=resolved_ref,
                        reason=REASON_MISSING_ASSET,
                        detail=(
                            "item references files the run did not write: "
                            + ", ".join(missing)
                        ),
                    )
                )
                continue

            self._stamp_derived_from(
                document, collection_id=collection_id, item_id=item_id, sources=sources
            )
            items.append(
                ResolvedItem(
                    ref=resolved_ref,
                    document=document,
                    staged_assets=tuple(staged),
                )
            )

        return Resolution(items=tuple(items), rejected=tuple(rejected), claimed=True)


@dataclass
class ProcessRunRecorder(OutcomeRecorder):
    """Record what the run published on its ledger row.

    No delete, no restore, no snapshot (I-80): a rejected process output was
    never in the catalog, so the only thing to record is that it was not
    published. The run's own status was already decided by its exit code —
    this adds WHAT it produced, and downgrades a run whose every output was
    rejected so "succeeded" cannot mean "published nothing usable".
    """

    repo: ProcessRepo

    async def record(self, req: FinalizeRequest, outcome: FinalizeOutcome) -> None:
        run_id = str(req.provenance["run_id"])
        upserted = [
            {"collection_id": u.collection_id, "item_id": u.item_id}
            for u in outcome.result.upserted
        ]
        rejected = [
            {
                "collection_id": r.item_ref.collection_id,
                "item_id": r.item_ref.item_id,
                "reason": r.reason,
                "detail": r.detail,
            }
            for r in outcome.result.rejected
        ]

        status = "succeeded"
        error = None
        if rejected and not upserted:
            # Every output was refused: the container exited 0, but nothing
            # reached the catalog. Reporting that as success would make the
            # ledger — and the flow_stats built from it — say a flow is
            # healthy while it publishes nothing.
            status = "dead"
            error = f"all {len(rejected)} output items were rejected"
        elif rejected:
            total = len(rejected) + len(upserted)
            error = f"{len(rejected)} of {total} output items were rejected"

        PROCESS_OUTPUT_ITEMS.inc(len(upserted))
        await self.repo.finish_run(
            run_id,
            status=status,
            error=error,
            log_ref=None,
            next_attempt_at=None,
            output_items=upserted,
        )
        logger.info(
            "process run outputs recorded",
            extra={
                "run_id": run_id,
                "upserted": len(upserted),
                "rejected": len(rejected),
                # Reasons are OUR closed set, safe to log; details carry user
                # strings and stay in the ledger.
                "reasons": sorted({r["reason"] for r in rejected}),
            },
        )
