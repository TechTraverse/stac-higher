"""Finalize (Phase 7 spec §6, ADR 0014): the producer-parameterized
staging→canonical seam. ``seam`` holds the request/result shapes and hook
interfaces, ``steps`` the neutral chain, ``push`` the push-ingest producer
layer, ``repo`` the DB seam, ``sweep`` the §6.4 recovery/expiry tick, and
``status`` the staged_uploads writer contract."""

from pipeline.finalize.seam import (
    PRODUCER_PROCESS_RUN,
    PRODUCER_PUSH_INGEST,
    FinalizeRequest,
    FinalizeResult,
    ItemRef,
    ProducerHooks,
    RejectedItem,
    UpsertedItem,
)
from pipeline.finalize.steps import run_finalize

__all__ = [
    "PRODUCER_PROCESS_RUN",
    "PRODUCER_PUSH_INGEST",
    "FinalizeRequest",
    "FinalizeResult",
    "ItemRef",
    "ProducerHooks",
    "RejectedItem",
    "UpsertedItem",
    "run_finalize",
]
