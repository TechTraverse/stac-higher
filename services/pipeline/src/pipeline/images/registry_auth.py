"""Which credential pulls a user image (C-2, container-images spec §5, §8.4).

Resolved at launch, handed only to the daemon (``X-Registry-Auth``) or the
scanner's environment, never to user code (ADR 0021):

1. The image row names a group ``registry`` connection: that connection's
   ``{username, password}``, decrypted here. A deleted connection, one of
   another protocol, one with no stored credentials at all, one configured
   for a DIFFERENT registry host, or one without both secrets is
   :class:`RegistryCredentialGone` -- never a silent fall-back to an
   anonymous pull (C-1 Review Focus 4), and never a credential sent to a
   host it was not configured for. A registry connection can exist with no
   credentials ever having been stored on it; that is reported the same way
   as a deleted one (the run dies ``image_group_mismatch``), not as a
   transient fault that requeues.
2. Otherwise a ``docker.io`` image uses the optional deployment credential
   (``REGISTRY_DOCKERHUB_USER`` / ``_TOKEN``, ISSUES I-125).
3. Otherwise anonymous (``None``).

A missing master key or an undecryptable envelope is
:class:`RegistryAuthUnavailable`: infrastructure, not the run's fault.
"""

from __future__ import annotations

from pipeline.config import Settings
from pipeline.connections.build import AdapterBuildError, decrypt_credentials
from pipeline.connections.registry import (
    REGISTRY_PROTOCOL,
    RegistryConfigError,
    parse_registry_config,
    registry_api_host,
)
from pipeline.connections.repo import ConnectionRow
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
            secrets = decrypt_credentials(
                ConnectionRow(
                    id=row.connection_id,
                    name="",
                    protocol=row.protocol,
                    config=row.config,
                    credentials=row.credentials,
                    host_key=None,
                ),
                master_key,
            )
        except AdapterBuildError as err:
            raise RegistryAuthUnavailable(
                f"registry credential {connection_id}: {err}"
            ) from err
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
