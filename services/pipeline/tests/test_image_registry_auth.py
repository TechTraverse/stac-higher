"""Pull-credential resolution (C-2, container-images spec §5, §8.4).

The credential that reaches the daemon or the scanner is the image's own
group credential, or the deployment's Docker Hub one for a docker.io image
that has none, or nothing (anonymous). A credential is never sent to a host
it was not configured for."""

from __future__ import annotations

import json

import pytest

from _images_fake import FakeImagesRepo
from pipeline.config import Settings
from pipeline.connections.envelope import load_master_key, seal
from pipeline.images.registry_auth import (
    RegistryAuthUnavailable,
    RegistryCredentialGone,
    resolve_registry_auth,
)
from pipeline.images.repo import ImageRow, RegistryCredentialRow

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
CONN = "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d"


def image(reference="ghcr.io/example/tool", connection=None) -> ImageRow:
    return ImageRow(
        id="7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
        reference=reference,
        tag_at_add="1.0",
        status="approved",
        registry_connection_id=connection,
    )


def credential(*, host="ghcr.io", deleted=False, secrets=None) -> RegistryCredentialRow:
    payload = secrets if secrets is not None else {"username": "robot", "password": "s3cret"}
    return RegistryCredentialRow(
        connection_id=CONN,
        protocol="registry",
        config={"host": host},
        credentials=seal(json.dumps(payload), KEY),
        deleted=deleted,
    )


def repo_with(row: RegistryCredentialRow | None) -> FakeImagesRepo:
    repo = FakeImagesRepo()
    if row is not None:
        repo.add_credential(row)
    return repo


@pytest.mark.asyncio
async def test_a_group_credential_is_decrypted_for_its_own_host():
    auth = await resolve_registry_auth(
        image(connection=CONN),
        repo=repo_with(credential()),
        settings=Settings.from_env({}),
        master_key=KEY,
    )
    assert (auth.username, auth.password, auth.server) == ("robot", "s3cret", "ghcr.io")


@pytest.mark.asyncio
async def test_a_docker_hub_credential_matches_every_hub_alias():
    auth = await resolve_registry_auth(
        image(reference="docker.io/org/tool", connection=CONN),
        repo=repo_with(credential(host="index.docker.io")),
        settings=Settings.from_env({}),
        master_key=KEY,
    )
    assert auth is not None and auth.server == "docker.io"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        None,
        credential(deleted=True),
        credential(host="quay.io"),
        credential(secrets={"username": "x"}),
    ],
    ids=["missing", "soft-deleted", "another-host", "no-password"],
)
async def test_an_unusable_group_credential_is_gone_never_anonymous(row):
    """C-1 Review Focus 4, at launch: a deleted credential must not turn a
    private image into a public pull, and a credential for another host must
    never be sent to this one."""
    with pytest.raises(RegistryCredentialGone):
        await resolve_registry_auth(
            image(connection=CONN),
            repo=repo_with(row),
            settings=Settings.from_env({}),
            master_key=KEY,
        )


@pytest.mark.asyncio
async def test_a_connection_with_no_stored_credentials_is_gone_not_transient():
    """S3: a registry connection can exist (not deleted, right protocol, right
    host) and still have never had a credential stored on it. That must fail
    the same way a deleted credential does -- image_group_mismatch, a dead
    run -- never RegistryAuthUnavailable, which would make the caller
    requeue a run that can never succeed."""
    row = RegistryCredentialRow(
        connection_id=CONN,
        protocol="registry",
        config={"host": "ghcr.io"},
        credentials=None,
        deleted=False,
    )
    with pytest.raises(RegistryCredentialGone):
        await resolve_registry_auth(
            image(connection=CONN),
            repo=repo_with(row),
            settings=Settings.from_env({}),
            master_key=KEY,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    ["not json at all", "[1, 2, 3]"],
    ids=["not-json", "not-an-object"],
)
async def test_a_corrupt_decrypted_payload_is_gone_not_transient(payload):
    """Ruling: a payload that decrypts fine (the key and the envelope are
    both good) but is not JSON, or is JSON that is not an object, can never
    become usable by retrying -- it is the same permanent verdict as a
    missing credential, never RegistryAuthUnavailable."""
    row = RegistryCredentialRow(
        connection_id=CONN,
        protocol="registry",
        config={"host": "ghcr.io"},
        credentials=seal(payload, KEY),
        deleted=False,
    )
    with pytest.raises(RegistryCredentialGone):
        await resolve_registry_auth(
            image(connection=CONN),
            repo=repo_with(row),
            settings=Settings.from_env({}),
            master_key=KEY,
        )


@pytest.mark.asyncio
async def test_a_wrong_or_rotated_master_key_is_infrastructure_not_gone():
    """Ruling: EnvelopeError (the key cannot open this envelope) stays
    RegistryAuthUnavailable -- the credential itself may be perfectly fine,
    just not decryptable with the key this worker currently has."""
    other_key = load_master_key(
        {"CREDENTIALS_MASTER_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    )
    row = credential()  # sealed with KEY
    with pytest.raises(RegistryAuthUnavailable):
        await resolve_registry_auth(
            image(connection=CONN),
            repo=repo_with(row),
            settings=Settings.from_env({}),
            master_key=other_key,
        )


@pytest.mark.asyncio
async def test_no_master_key_is_infrastructure_not_a_verdict():
    with pytest.raises(RegistryAuthUnavailable, match="CREDENTIALS_MASTER_KEY"):
        await resolve_registry_auth(
            image(connection=CONN),
            repo=repo_with(credential()),
            settings=Settings.from_env({}),
            master_key=None,
        )


@pytest.mark.asyncio
async def test_the_deployment_docker_hub_credential_covers_only_docker_io():
    hub = Settings.from_env({"REGISTRY_DOCKERHUB_USER": "robot", "REGISTRY_DOCKERHUB_TOKEN": "pat"})
    repo = repo_with(None)
    auth = await resolve_registry_auth(
        image(reference="docker.io/library/python"), repo=repo, settings=hub, master_key=None
    )
    assert (auth.username, auth.password, auth.server) == ("robot", "pat", "docker.io")
    assert (
        await resolve_registry_auth(image(), repo=repo, settings=hub, master_key=None) is None
    )
    assert (
        await resolve_registry_auth(
            image(reference="docker.io/library/python"),
            repo=repo,
            settings=Settings.from_env({}),
            master_key=None,
        )
        is None
    )
