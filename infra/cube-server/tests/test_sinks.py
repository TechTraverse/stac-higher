import json
from pathlib import Path

import pytest

from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import parse_sink_config

FIXTURE = Path(__file__).resolve().parents[3] / "tests/contract-fixtures/cube-sink-config.json"
#: reject cases that are about the two fields the server reads
SERVER_REJECTS = {"blank append_dim", "no variables", "blank variable name", "variables not a list"}


def _cases():
    return json.loads(FIXTURE.read_text())["cases"]


def test_reads_every_pipeline_accepted_fixture_case():
    accepted = [c for c in _cases() if c["pipeline"] == "accept"]
    assert accepted
    for case in accepted:
        config = case["config"]
        append_dim, variables = parse_sink_config(config)
        assert append_dim == config["append_dim"].strip(), case["name"]
        assert variables == tuple(dict.fromkeys(config["variables"])), case["name"]


def test_rejects_the_fixture_cases_about_its_fields():
    cases = {c["name"]: c for c in _cases()}
    assert cases.keys() >= SERVER_REJECTS
    for name in SERVER_REJECTS:
        with pytest.raises(ValueError):
            parse_sink_config(cases[name]["config"])


def test_settings_names_every_missing_var():
    with pytest.raises(ValueError) as err:
        Settings.from_env({})
    for var in ("CUBE_DB_DSN", "CUBE_S3_ENDPOINT", "CUBE_S3_ACCESS_KEY_ID",
                "CUBE_S3_SECRET_ACCESS_KEY", "PLATFORM_ASSET_BUCKET"):
        assert var in str(err.value)


def test_settings_defaults():
    settings = Settings.from_env({
        "CUBE_DB_DSN": "postgresql://x", "CUBE_S3_ENDPOINT": "http://minio:9000",
        "CUBE_S3_ACCESS_KEY_ID": "k", "CUBE_S3_SECRET_ACCESS_KEY": "s",
        "PLATFORM_ASSET_BUCKET": "stac-higher",
    })
    assert settings.poll_seconds == 15
    assert settings.port == 8086
    assert settings.s3_region == "us-east-1"
    assert settings.cors_origins == ("*",)


def test_settings_splits_cors_origins():
    settings = Settings.from_env({
        "CUBE_DB_DSN": "d", "CUBE_S3_ENDPOINT": "e", "CUBE_S3_ACCESS_KEY_ID": "k",
        "CUBE_S3_SECRET_ACCESS_KEY": "s", "PLATFORM_ASSET_BUCKET": "b",
        "CUBE_CORS_ORIGINS": "http://localhost:4321, https://x.example",
    })
    assert settings.cors_origins == ("http://localhost:4321", "https://x.example")
