"""Build a ready-to-use adapter from a connection row (decrypt → factory).

This is the shared front half of the probe path, factored out so the ingest
workers (Phase 4) can obtain a live adapter for ``list``/``get`` without running
``test()``. The probe layer (:func:`pipeline.connections.probe.run_adapter_test`)
also builds on it. Credentials are decrypted here and handed to the adapter,
which owns them for the rest of the job; they are never logged.
"""

from __future__ import annotations

import json

from pipeline.connections.adapters import StorageAdapter, adapter_for
from pipeline.connections.envelope import EnvelopeError, decrypt
from pipeline.connections.repo import ConnectionRow


class AdapterBuildError(Exception):
    """A connection could not be turned into an adapter (no/invalid credentials
    or an unsupported protocol). Carries a caller-safe message with no secrets."""


def build_adapter(
    connection: ConnectionRow,
    master_key: bytes,
    allow_hosts: frozenset[str],
) -> StorageAdapter:
    """Decrypt ``connection``'s credentials and construct its protocol adapter.

    Raises :class:`AdapterBuildError` for any expected failure (missing
    credentials, a bad envelope, a non-JSON payload, or a reserved/unknown
    protocol) so callers can surface one uniform error. Egress is still enforced
    later, inside the adapter's own calls.
    """
    if connection.credentials is None:
        raise AdapterBuildError("connection has no stored credentials")

    try:
        credentials = json.loads(decrypt(connection.credentials, master_key))
    except EnvelopeError as exc:
        raise AdapterBuildError(f"credential decryption failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AdapterBuildError("credential payload is not valid JSON") from exc

    try:
        return adapter_for(
            connection.as_adapter_row(), credentials, allow_hosts=allow_hosts
        )
    except (ValueError, NotImplementedError) as exc:
        raise AdapterBuildError(str(exc)) from exc
