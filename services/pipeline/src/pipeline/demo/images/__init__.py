"""The container-images live-gate demo (C-5): a kind-2 twin of the GOES
GeoColor process, plus a canary process, both running on images added
through the APP's HTTP API rather than through `pipeline.demo.platform`'s
direct SQL writers.

`seed.py` carries the logic; this module holds the one piece of process code
the demo deploys (parallel to `pipeline.demo.goes`'s `extractor_code()` and
`geocolor_code()`, which load real files next to that package; the canary
is a single line, so it lives here as a literal instead).
"""

from __future__ import annotations


def canary_code() -> str:
    return (
        'import os\n'
        'print("images-canary: launched on", os.environ.get("STAC_HIGHER_RUN_ID", "?"))\n'
    )
