"""Settings env contract."""

from pipeline.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_HEALTH_PORT,
    DEFAULT_QUEUE_SCHEMA,
    Settings,
)


def test_defaults():
    settings = Settings.from_env(env={})
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.database_url == "postgresql://username:password@localhost:5433/postgis"
    assert settings.health_port == DEFAULT_HEALTH_PORT == 8083
    assert settings.queue_schema == DEFAULT_QUEUE_SCHEMA == "procrastinate"
    assert settings.log_level == "INFO"
    # connection-job settings default to absent/empty (jobs fail their tick
    # loudly rather than crash the process at startup).
    assert settings.credentials_master_key is None
    assert settings.egress_allow_hosts == frozenset()


def test_connection_env_overrides():
    settings = Settings.from_env(
        env={
            "CREDENTIALS_MASTER_KEY": "abc123==",
            "EGRESS_ALLOW_HOSTS": "MinIO, sftp-test ,ftp-test,",
        }
    )
    assert settings.credentials_master_key == "abc123=="
    # comma-split, trimmed, lowercased, empties dropped.
    assert settings.egress_allow_hosts == frozenset({"minio", "sftp-test", "ftp-test"})


def test_blank_credentials_key_is_none():
    settings = Settings.from_env(env={"CREDENTIALS_MASTER_KEY": ""})
    assert settings.credentials_master_key is None


def test_env_overrides():
    settings = Settings.from_env(
        env={
            "DATABASE_URL": "postgresql://username:password@database:5432/postgis",
            "HEALTH_PORT": "9999",
            "QUEUE_SCHEMA": "queue",
            "LOG_LEVEL": "debug",
        }
    )
    assert settings.database_url == "postgresql://username:password@database:5432/postgis"
    assert settings.health_port == 9999
    assert settings.queue_schema == "queue"
    assert settings.log_level == "DEBUG"


def test_asset_href_base_defaults_and_env(monkeypatch):
    from pipeline.config import Settings

    assert Settings.from_env({}).asset_href_base == "/api/assets"
    assert Settings.from_env({"ASSET_HREF_BASE": "/assets"}).asset_href_base == "/assets"
