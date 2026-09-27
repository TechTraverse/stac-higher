"""Compose wiring for C-2 (container-images spec §8.5, §11), pinned as text:
a boundary that silently widens is the failure these catch."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
COMPOSE = (REPO / "docker-compose.yml").read_text()


def _service(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^[a-z])", COMPOSE, re.M | re.S)
    assert match, name
    return match.group(1)


def test_the_socket_proxy_allows_images_and_nothing_else_new():
    proxy = _service("docker-socket-proxy")
    assert "- IMAGES=1" in proxy and "IMAGES=0" not in proxy
    for still_off in ("EXEC=0", "VOLUMES=0", "NETWORKS=0", "BUILD=0", "SYSTEM=0"):
        assert still_off in proxy
    assert "scanner-egress" not in proxy


def test_the_scanner_network_is_egress_capable_and_minio_is_on_it():
    networks = COMPOSE.split("\nnetworks:\n", 1)[1]
    block = re.search(r"^  scanner-egress:\n((?:    .*\n|\s*#.*\n)*)", networks, re.M)
    assert block, "scanner-egress network is not declared"
    assert "internal: true" not in block.group(1)
    assert "- scanner-egress" in _service("minio")


def test_the_pipeline_names_the_scanner_network_and_the_hub_credential():
    pipeline = _service("pipeline")
    network = "PROCESS_SCANNER_NETWORK=${PROCESS_SCANNER_NETWORK:-stac-higher_scanner-egress}"
    assert network in pipeline
    assert "IMAGE_SCANNER_IMAGE=${IMAGE_SCANNER_IMAGE:-stac-higher-image-scanner:local}" in pipeline
    assert "REGISTRY_DOCKERHUB_USER=${REGISTRY_DOCKERHUB_USER:-}" in pipeline
    assert "REGISTRY_DOCKERHUB_TOKEN=${REGISTRY_DOCKERHUB_TOKEN:-}" in pipeline
    # The pipeline itself never joins the scanner's network.
    assert "scanner-egress\n" not in pipeline.split("environment:", 1)[0]
