"""The GOES worked example (GOES spec §9, G-7): an extractor and a process.

The two scripts next to this file are ORDINARY process code — written
against `docs/processes.md`, nothing privileged — and are the single source
of truth: `pipeline.demo goes-seed` deploys them, `app/e2e/goes-loop.spec.ts`
deploys them, and the docs quote them. Loaded as text here because that is
what a revision carries.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: The geocolor script names its output collection through this token, which
#: the seeder substitutes — a revision's code is a string, not a module.
OUTPUT_COLLECTION_TOKEN = "__OUTPUT_COLLECTION__"


def extractor_code() -> str:
    return (_HERE / "extractor.py").read_text()


def geocolor_code(output_collection: str) -> str:
    source = (_HERE / "geocolor.py").read_text()
    return source.replace(OUTPUT_COLLECTION_TOKEN, output_collection)
