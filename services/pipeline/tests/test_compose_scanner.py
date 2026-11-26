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
    # The pipeline itself never joins the scanner's network. The env line
    # ends with `scanner-egress}` (the default value's suffix), so it cannot
    # false-match a `- scanner-egress` networks-list entry.
    assert "- scanner-egress" not in pipeline


def test_the_socket_proxy_environment_is_pinned_exactly():
    """K-3 review: the proxy's whole KEY=VALUE map, so deleting an explicit
    `=0` line (and inheriting an upstream default) fails as loudly as adding
    an `=1`. K-3 added INFO (read-only GET /info, the NVIDIA-runtime and NCPU
    probe); K-4 added EVENTS and pinned the image defaults PING/VERSION off."""
    proxy = _service("docker-socket-proxy")
    env = dict(re.findall(r"^\s*- ([A-Z_]+)=(\S+)\s*$", proxy, re.M))
    assert env == {
        "CONTAINERS": "1",
        "POST": "1",
        "EXEC": "0",
        "VOLUMES": "0",
        "IMAGES": "1",
        "NETWORKS": "0",
        "SERVICES": "0",
        "TASKS": "0",
        "SECRETS": "0",
        "CONFIGS": "0",
        "NODES": "0",
        "SWARM": "0",
        "SYSTEM": "0",
        "INFO": "1",
        "PLUGINS": "0",
        "BUILD": "0",
        "COMMIT": "0",
        "DISTRIBUTION": "0",
        "AUTH": "0",
        "EVENTS": "1",
        "PING": "0",
        "VERSION": "0",
    }


def test_every_image_default_on_section_is_listed():
    """K-4 / #202: tecnativa/docker-socket-proxy:0.3.0 turns EVENTS, PING and
    VERSION on unless told otherwise. Each is written explicitly, so the
    enabled set is exactly what the file says: the watcher's EVENTS on, the
    two nothing uses off."""
    proxy = _service("docker-socket-proxy")
    env = dict(re.findall(r"^\s*- ([A-Z_]+)=(\S+)\s*$", proxy, re.M))
    for key in ("EVENTS", "PING", "VERSION"):
        assert key in env, key
    assert (env["EVENTS"], env["PING"], env["VERSION"]) == ("1", "0", "0")
    assert {k for k, v in env.items() if v == "1"} == {
        "CONTAINERS", "POST", "IMAGES", "INFO", "EVENTS"
    }
