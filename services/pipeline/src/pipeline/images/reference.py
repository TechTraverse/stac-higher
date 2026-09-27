"""The image reference + digest grammar (C-1, container-images spec section 3/4.1).

Pinned by ``tests/contract-fixtures/image-reference.json`` against
``app/src/lib/images/reference.ts``. A STORED reference is normalized: it is
lowercase, has an explicit registry host and at least one path component, and
carries no tag and no digest.
"""

from __future__ import annotations

import re

_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_HOST = rf"(?:localhost|{_LABEL}(?:\.{_LABEL})+)(?::[0-9]{{1,5}})?"
_COMPONENT = r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*"

IMAGE_REFERENCE_RE = re.compile(rf"{_HOST}(?:/{_COMPONENT})+")
IMAGE_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
IMAGE_REFERENCE_MAX_LENGTH = 255


def is_image_reference(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= IMAGE_REFERENCE_MAX_LENGTH
        and IMAGE_REFERENCE_RE.fullmatch(value) is not None
    )


def is_image_digest(value: str) -> bool:
    return isinstance(value, str) and IMAGE_DIGEST_RE.fullmatch(value) is not None


def registry_host(reference: str) -> str:
    """The registry host of a normalized reference (its first component)."""
    return reference.split("/", 1)[0]
