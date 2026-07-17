"""Poll-based ingest pipeline (ROADMAP §6.1, Phase 4 Slice B2+B3).

The scheduler enqueues a DISCOVER job per due association; DISCOVER → GROUP →
FETCH chain through the queue, each stage idempotent against the
``stac_higher.ingest_files`` ledger. This slice takes a settled source file all
the way into canonical object storage (status ``stored``); EXTRACT + ITEMIZE
(Slice B4) turn stored files into STAC items.
"""
