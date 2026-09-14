"""Settings env contract."""

import pytest

from pipeline.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_HEALTH_PORT,
    DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS,
    DEFAULT_PGSTAC_QUEUE_STALE_SECONDS,
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


def test_catalog_href_base_defaults_and_env():
    """D-1: `derived_from` hrefs are root-relative by default (mirroring
    ASSET_HREF_BASE); an absolute base makes them absolute."""
    assert Settings.from_env({}).catalog_href_base == "/"
    assert (
        Settings.from_env({"CATALOG_HREF_BASE": "https://c.example/stac"}).catalog_href_base
        == "https://c.example/stac"
    )


def test_pgstac_queue_defaults():
    settings = Settings.from_env(env={})
    # Local/self-hosted default: the pipeline drains (pg_cron is not in the
    # pgstac image — spec §4.3). Cloud sets "database" once pg_cron owns it.
    assert settings.pgstac_queue_drainer == "pipeline"
    assert settings.pgstac_queue_stale_seconds == DEFAULT_PGSTAC_QUEUE_STALE_SECONDS == 300
    assert settings.pgstac_queue_history_days == DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS == 7


def test_pgstac_queue_env_overrides():
    settings = Settings.from_env(
        env={
            "PGSTAC_QUEUE_DRAINER": " Database ",
            "PGSTAC_QUEUE_STALE_SECONDS": "120",
            "PGSTAC_QUEUE_HISTORY_DAYS": "30",
        }
    )
    assert settings.pgstac_queue_drainer == "database"
    assert settings.pgstac_queue_stale_seconds == 120
    assert settings.pgstac_queue_history_days == 30


def test_pgstac_queue_drainer_rejects_unknown_value():
    with pytest.raises(ValueError, match="PGSTAC_QUEUE_DRAINER"):
        Settings.from_env(env={"PGSTAC_QUEUE_DRAINER": "cron"})


def test_db_pool_defaults():
    """M3-B: the async repo pool is sized from env, with a default that already
    clears M3-D's concurrency (12) plus the periodic ticks that overlap it."""
    from pipeline.config import DEFAULT_DB_POOL_MAX, DEFAULT_DB_POOL_MIN, Settings

    settings = Settings.from_env(env={})
    assert settings.db_pool_min == DEFAULT_DB_POOL_MIN == 2
    assert settings.db_pool_max == DEFAULT_DB_POOL_MAX == 16
    # The sizing invariant the README documents: a pool smaller than the
    # concurrent checkouts makes callers wait and then raise PoolTimeout.
    assert settings.db_pool_max > 12


def test_db_pool_env_overrides():
    from pipeline.config import Settings

    settings = Settings.from_env(env={"DB_POOL_MIN": "1", "DB_POOL_MAX": "32"})
    assert settings.db_pool_min == 1
    assert settings.db_pool_max == 32


def test_memory_envelope_settings_default_to_the_s_e_envelope():
    from pipeline.config import (
        DEFAULT_FETCH_CHUNK_BYTES,
        DEFAULT_FETCH_TRANSFER_CONCURRENCY,
        DEFAULT_GDAL_CACHEMAX_MB,
    )

    settings = Settings.from_env(env={})
    assert settings.gdal_cachemax_mb == DEFAULT_GDAL_CACHEMAX_MB == 64
    assert settings.fetch_chunk_bytes == DEFAULT_FETCH_CHUNK_BYTES == 8 * 1024 * 1024
    assert settings.fetch_transfer_concurrency == DEFAULT_FETCH_TRANSFER_CONCURRENCY == 4


def test_memory_envelope_settings_read_their_env_names():
    settings = Settings.from_env(
        env={
            "GDAL_CACHEMAX": "128",
            "FETCH_CHUNK_BYTES": "16777216",
            "FETCH_TRANSFER_CONCURRENCY": "2",
        }
    )
    assert settings.gdal_cachemax_mb == 128
    assert settings.fetch_chunk_bytes == 16 * 1024 * 1024
    assert settings.fetch_transfer_concurrency == 2
