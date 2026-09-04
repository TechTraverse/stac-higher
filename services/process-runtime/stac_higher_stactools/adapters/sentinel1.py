"""stactools-sentinel1: a GRD ``.SAFE`` product (``supports: grouped``).

The package has no top-level entry point — ``grd``, ``rtc`` and ``slc`` are
separate — and this adapter names GRD, the public product (spec §14). The
ingest stages the product's files flat; the SAFE tree is rebuilt from the
draft's hrefs before the package sees it.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import rebuild_tree


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.sentinel1.grd.stac import create_item

    scratch = Path(tempfile.mkdtemp(prefix="safe-"))
    root = rebuild_tree(paths, draft, ".SAFE", scratch)
    return create_item(str(root))
