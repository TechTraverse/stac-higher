"""Shared helpers for the connection jobs (drain + health sweep)."""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.connections.envelope import CredentialKeyError, load_master_key

logger = logging.getLogger(__name__)


def load_key_or_skip(settings: Settings, job_name: str) -> bytes | None:
    """Load the credential master key, or log and return ``None`` so the caller
    skips this tick. A missing/malformed key must never crash the worker — only
    the connection jobs need it, and they degrade to a no-op tick.
    """
    try:
        return load_master_key({"CREDENTIALS_MASTER_KEY": settings.credentials_master_key})
    except CredentialKeyError as exc:
        logger.error("%s skipped: %s", job_name, exc, extra={"job": job_name})
        return None
