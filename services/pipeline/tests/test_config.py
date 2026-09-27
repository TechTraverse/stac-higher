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


def test_hardware_profiles_file_setting():
    """K-1: unset means the repo checkout's infra/hardware-profiles/local.json."""
    assert Settings.from_env(env={}).process_hardware_profiles_file is None
    settings = Settings.from_env(env={"PROCESS_HARDWARE_PROFILES_FILE": "/app/share/hp.json"})
    assert settings.process_hardware_profiles_file == "/app/share/hp.json"


def test_worker_concurrency_defaults_to_the_settled_split():
    """M3-D (spec §7 decision 2): 12 slots total, 4 of them for the bytes queue."""
    from pipeline.config import (
        DEFAULT_FLOW_STATS_FLUSH_SECONDS,
        DEFAULT_WORKER_BYTES_CONCURRENCY,
        DEFAULT_WORKER_CONCURRENCY,
        Settings,
    )

    settings = Settings.from_env(env={})
    assert settings.worker_concurrency == DEFAULT_WORKER_CONCURRENCY == 12
    assert settings.worker_bytes_concurrency == DEFAULT_WORKER_BYTES_CONCURRENCY == 4
    assert settings.default_queue_concurrency == 8
    assert settings.flow_stats_flush_seconds == DEFAULT_FLOW_STATS_FLUSH_SECONDS == 2.0
    # The README invariant M3-B documented: the pool clears the slots + ticks.
    assert settings.db_pool_max >= settings.worker_concurrency + 4


def test_worker_concurrency_reads_its_env_names():
    settings = Settings.from_env(
        env={
            "WORKER_CONCURRENCY": "6",
            "WORKER_BYTES_CONCURRENCY": "2",
            "FLOW_STATS_FLUSH_SECONDS": "0.5",
        }
    )
    assert settings.worker_concurrency == 6
    assert settings.worker_bytes_concurrency == 2
    assert settings.default_queue_concurrency == 4
    assert settings.flow_stats_flush_seconds == 0.5


def test_worker_concurrency_rejects_an_impossible_split():
    import pytest

    with pytest.raises(ValueError, match="WORKER_BYTES_CONCURRENCY"):
        Settings.from_env(env={"WORKER_CONCURRENCY": "4", "WORKER_BYTES_CONCURRENCY": "4"})
    with pytest.raises(ValueError, match="WORKER_BYTES_CONCURRENCY"):
        Settings.from_env(env={"WORKER_BYTES_CONCURRENCY": "0"})
    with pytest.raises(ValueError, match="WORKER_CONCURRENCY"):
        Settings.from_env(env={"WORKER_CONCURRENCY": "0"})
    with pytest.raises(ValueError, match="FLOW_STATS_FLUSH_SECONDS"):
        Settings.from_env(env={"FLOW_STATS_FLUSH_SECONDS": "0"})


def test_sizing_warnings_flag_an_undersized_pool():
    from pipeline.config import sizing_warnings

    assert sizing_warnings(Settings.from_env(env={})) == []
    warnings = sizing_warnings(Settings.from_env(env={"DB_POOL_MAX": "10"}))
    assert len(warnings) == 1
    assert "DB_POOL_MAX" in warnings[0].message
    assert warnings[0].extra == {"db_pool_max": 10, "worker_concurrency": 12, "required": 16}


# ---------------------------------------------------------------------------
# C-2: the scanner, the scan drain and the Docker Hub credential
# ---------------------------------------------------------------------------


def test_c2_settings_defaults():
    s = Settings.from_env({})
    assert s.image_scanner_image == "stac-higher-image-scanner:local"
    # No scanner network in code: a deployment that has not decided on
    # scanner egress gets scans that fail, never scans on the default bridge.
    assert s.process_scanner_network == "none"
    assert s.image_scanner_db_update is True
    assert s.grype_db_update_url is None
    assert s.registry_dockerhub_user is None and s.registry_dockerhub_token is None
    assert s.image_scan_concurrency == 1


def test_c2_settings_parse_and_blank_means_unset():
    s = Settings.from_env(
        {
            "IMAGE_SCANNER_IMAGE": "ghcr.io/org/scanner:20260927",
            "PROCESS_SCANNER_NETWORK": "stac-higher_scanner-egress",
            "IMAGE_SCANNER_DB_UPDATE": "false",
            "GRYPE_DB_UPDATE_URL": " https://mirror.example/listing.json ",
            "REGISTRY_DOCKERHUB_USER": "robot",
            "REGISTRY_DOCKERHUB_TOKEN": "dckr_pat_x",
            "IMAGE_SCAN_CONCURRENCY": "2",
        }
    )
    assert s.image_scanner_image == "ghcr.io/org/scanner:20260927"
    assert s.process_scanner_network == "stac-higher_scanner-egress"
    assert s.image_scanner_db_update is False
    assert s.grype_db_update_url == "https://mirror.example/listing.json"
    assert (s.registry_dockerhub_user, s.registry_dockerhub_token) == ("robot", "dckr_pat_x")
    assert s.image_scan_concurrency == 2
    blank = Settings.from_env(
        {
            "REGISTRY_DOCKERHUB_USER": "  ",
            "GRYPE_DB_UPDATE_URL": "",
            "PROCESS_SCANNER_NETWORK": "",
            "IMAGE_SCANNER_IMAGE": "",
        }
    )
    assert blank.registry_dockerhub_user is None
    assert blank.grype_db_update_url is None
    assert blank.process_scanner_network == "none"
    # Item 4: blank means unset, same as its siblings above -- a deployment
    # passing `${IMAGE_SCANNER_IMAGE:-}` must get the default image, not an
    # empty reference the executor would then refuse to launch.
    assert blank.image_scanner_image == "stac-higher-image-scanner:local"


def test_the_docker_hub_token_never_prints():
    s = Settings.from_env(
        {"REGISTRY_DOCKERHUB_USER": "robot", "REGISTRY_DOCKERHUB_TOKEN": "dckr_pat_x"}
    )
    assert "dckr_pat_x" not in repr(s)


def test_image_scan_concurrency_must_be_positive():
    with pytest.raises(ValueError, match="IMAGE_SCAN_CONCURRENCY"):
        Settings.from_env({"IMAGE_SCAN_CONCURRENCY": "0"})
