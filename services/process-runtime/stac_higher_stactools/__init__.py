"""The platform half of the built-in extractor library (X queue spec §6).

This package ships inside the stactools runtime image
(``services/process-runtime/Dockerfile.stactools``) — never in the pipeline
— and a built-in extractor process is the two-line body

    from stac_higher_stactools import run
    run("stactools-goes")

executed by the runtime entrypoint with the ordinary extractor contract
(``docs/processes.md`` "Extractors"). ``run`` reads the ADR 0018 manifest,
stages each draft's assets to local disk, hands them to the registry entry's
adapter, MERGES the pystac item the package built onto the draft under the
§6.1 immutability rules, and writes ``{item_id}.json`` to the run's output
prefix. Finalize's extract branch stays the gate — the wrapper emits exactly
what a hand-written extractor emits.

Only the standard library, ``boto3`` and ``pystac`` are imported at module
import time; each stactools package is imported by its adapter on first use,
so a registry entry whose package is missing fails THAT run with a clear
reason rather than every run on the image.
"""

from __future__ import annotations

from stac_higher_stactools.merge import MergeResult, merge_item
from stac_higher_stactools.registry import (
    BuiltinExtractor,
    RegistryError,
    load_registry,
    registry_path,
)
from stac_higher_stactools.runner import run

__all__ = [
    "BuiltinExtractor",
    "MergeResult",
    "RegistryError",
    "load_registry",
    "merge_item",
    "registry_path",
    "run",
]
