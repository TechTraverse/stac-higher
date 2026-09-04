"""stactools-sentinel2: an L1C/L2A ``.SAFE`` product (``supports: grouped``),
rebuilt from the flat group via the draft's hrefs (see ``sentinel1``)."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import rebuild_tree


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.sentinel2.stac import create_item

    scratch = Path(tempfile.mkdtemp(prefix="safe-"))
    root = rebuild_tree(paths, draft, ".SAFE", scratch)
    return create_item(str(root))
