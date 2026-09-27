"""Which credential pulls a user image (C-2, container-images spec §5, §8.4).

Resolved at launch, handed only to the daemon (``X-Registry-Auth``) or the
scanner's environment, never to user code (ADR 0021):

1. The image row names a group ``registry`` connection: that connection's
   ``{username, password}``, decrypted here. A deleted connection, one of
   another protocol, one with no stored credentials at all, one configured
   for a DIFFERENT registry host, one whose decrypted payload is not a JSON
   object, or one without both secrets is :class:`RegistryCredentialGone`
   -- never a silent fall-back to an anonymous pull (C-1 Review Focus 4),
   and never a credential sent to a host it was not configured for. A
   registry connection can exist with no credentials ever having been
   stored on it; that is reported the same way as a deleted one (the run
   dies ``image_group_mismatch``), not as a transient fault that requeues.
   A corrupt-but-present payload (not JSON, not an object) is the same:
   permanent, not transient -- decrypting it again will not fix it.
2. Otherwise a ``docker.io`` image uses the optional deployment credential
   (``REGISTRY_DOCKERHUB_USER`` / ``_TOKEN``, ISSUES I-125).
3. Otherwise anonymous (``None``).

A missing master key or an envelope that fails to decrypt at all (wrong or
rotated ``CREDENTIALS_MASTER_KEY``) is :class:`RegistryAuthUnavailable`:
infrastructure, not the run's fault -- the same envelope may decrypt
successfully once the key is fixed.
"""

from __future__ import annotations

import json

from pipeline.config import Settings
from pipeline.connections.envelope import EnvelopeError, decrypt
from pipeline.connections.registry import (
    REGISTRY_PROTOCOL,
    RegistryConfigError,
    parse_registry_config,
    registry_api_host,
)
from pipeline.images.reference import registry_host
from pipeline.images.repo import ImageRow, ImagesRepo
from pipeline.process.executor import RegistryAuth

DOCKER_HUB = "docker.io"


class RegistryCredentialGone(Exception):
    """The image's group credential can no longer be used to pull it."""


class RegistryAuthUnavailable(Exception):
    """The credential exists but this worker cannot decrypt it now."""


async def resolve_registry_auth(
    image: ImageRow,
    *,
    repo: ImagesRepo,
    settings: Settings,
    master_key: bytes | None,
) -> RegistryAuth | None:
    host = registry_host(image.reference)
    if image.registry_connection_id:
        connection_id = image.registry_connection_id
        row = await repo.get_registry_credential(connection_id)
        if row is None or row.deleted or row.protocol != REGISTRY_PROTOCOL:
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} for {image.reference} was deleted"
            )
        if row.credentials is None:
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} for {image.reference} has no"
                " stored credentials"
            )
        try:
            configured = parse_registry_config(row.config).host
        except RegistryConfigError as err:
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} is not usable: {err}"
            ) from err
        if registry_api_host(configured) != registry_api_host(host):
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} is for {configured}, "
                f"the image is on {host}"
            )
        if master_key is None:
            raise RegistryAuthUnavailable(
                "CREDENTIALS_MASTER_KEY is not set, so the credential of a private image "
                "cannot be decrypted"
            )
        try:
            plaintext = decrypt(row.credentials, master_key)
        except EnvelopeError as err:
            # A wrong or rotated master key: infrastructure, not a verdict on
            # this credential -- the same bytes may decrypt fine once the key
            # is fixed, so this must never be treated as permanently gone.
            raise RegistryAuthUnavailable(
                f"registry credential {connection_id}: {err}"
            ) from err
        try:
            secrets = json.loads(plaintext)
        except json.JSONDecodeError as err:
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} payload is not valid JSON"
            ) from err
        if not isinstance(secrets, dict):
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} payload is not an object"
            )
        username = secrets.get("username")
        password = secrets.get("password")
        if not (
            isinstance(username, str) and username and isinstance(password, str) and password
        ):
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} has no username and password"
            )
        return RegistryAuth(username=username, password=password, server=host)
    if (
        host == DOCKER_HUB
        and settings.registry_dockerhub_user
        and settings.registry_dockerhub_token
    ):
        return RegistryAuth(
            username=settings.registry_dockerhub_user,
            password=settings.registry_dockerhub_token,
            server=DOCKER_HUB,
        )
    return None
