"""The image policy loader and its packaging (C-1, container-images spec §7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.images.policy import (
    ImagePolicyError,
    image_policy_path,
    load_image_policy,
)

REPO = Path(__file__).resolve().parents[3]


def test_unset_env_reads_the_checkout_default():
    assert image_policy_path({}) == REPO / "infra" / "image-policy" / "default.json"
    policy = load_image_policy(image_policy_path({}))
    assert policy.scan_window_days == 30
    assert policy.max_image_bytes == 4096 * 1024 * 1024
    assert policy.block.high_fixed_epss_at_least == 0.1


def test_missing_file_fails_closed_naming_the_path(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(ImagePolicyError, match=r"nope\.json"):
        load_image_policy(missing)


def test_the_pipeline_image_ships_the_policy():
    dockerfile = (REPO / "services" / "pipeline" / "Dockerfile").read_text()
    assert "COPY --from=imagepolicy default.json /app/share/image-policy/default.json" in dockerfile
    assert "PROCESS_IMAGE_POLICY_FILE=/app/share/image-policy/default.json" in dockerfile


def test_every_pipeline_build_carries_the_imagepolicy_context():
    # docker-compose.yml's header records why the build is an anchor: a copy
    # that drifted resolved `--from=hardware` as a Docker Hub image.
    compose = (REPO / "docker-compose.yml").read_text()
    assert "imagepolicy: ./infra/image-policy" in compose
    workflow = (REPO / ".github" / "workflows" / "containers.yml").read_text()
    assert workflow.count("imagepolicy=infra/image-policy") == 2  # app + pipeline
