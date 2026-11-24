import json
from pathlib import Path

import pytest

from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import BadSink, SinkRow, parse_sink_config, rows_to_sinks

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
    for var in ("CUBE_DB_DSN", "CUBE_S3_ENDPOINT", "PLATFORM_ASSET_BUCKET"):
        assert var in str(err.value)
    assert "CUBE_S3_ACCESS_KEY_ID" not in str(err.value)


BASE_ENV = {
    "CUBE_DB_DSN": "postgresql://x", "CUBE_S3_ENDPOINT": "http://minio:9000",
    "PLATFORM_ASSET_BUCKET": "stac-higher",
}


def test_settings_without_keys_use_the_default_chain():
    for extra in ({}, {"CUBE_S3_ACCESS_KEY_ID": "", "CUBE_S3_SECRET_ACCESS_KEY": " "}):
        settings = Settings.from_env({**BASE_ENV, **extra})
        assert settings.s3_access_key_id is None
        assert settings.s3_secret_access_key is None


@pytest.mark.parametrize("half", ["CUBE_S3_ACCESS_KEY_ID", "CUBE_S3_SECRET_ACCESS_KEY"])
def test_settings_keys_must_be_set_together(half):
    with pytest.raises(ValueError, match="CUBE_S3_ACCESS_KEY_ID and CUBE_S3_SECRET_ACCESS_KEY"):
        Settings.from_env({**BASE_ENV, half: "v"})


def test_settings_secret_never_prints():
    settings = Settings.from_env({
        **BASE_ENV, "CUBE_S3_ACCESS_KEY_ID": "k", "CUBE_S3_SECRET_ACCESS_KEY": "hunter2-secret",
    })
    assert settings.s3_access_key_id == "k"
    assert settings.s3_secret_access_key == "hunter2-secret"
    assert "hunter2-secret" not in repr(settings)


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


def test_rejected_config_becomes_a_bad_sink_not_an_absence():
    rows = [
        ("s1", "good", {"append_dim": "t", "variables": ["CMI"]}, ["s3://b/"]),
        ("s2", "bad", {"append_dim": "t", "variables": []}, ["s3://b/"]),
        ("s3", "none", {"append_dim": "t", "variables": ["CMI"]}, None),
    ]
    good, bad, none = rows_to_sinks(rows)
    assert good == SinkRow("s1", "good", "t", ("CMI",), ("s3://b/",))
    assert bad == BadSink(id="s2", cube_collection_id="bad", error="InvalidSinkConfig")
    assert none.source_prefixes == ()
