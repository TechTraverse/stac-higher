"""`pipeline.demo --in-cluster` (R-10, GitHub #146): the seeders run as a
Kubernetes Job against the endpoints a deployment configures.

Compose mode must not move: every default, the MinIO keys and the bucket it
creates stay as they were. In-cluster mode reads its endpoints from the
environment the chart's Job sets, never builds or inspects an image, reaches
the platform bucket the way the pipeline does, and seals the Terraform
demo-destination key into the delivery connection.
"""

from __future__ import annotations

import argparse
import json

import pytest

from pipeline.connections.envelope import decrypt, load_master_key
from pipeline.demo import __main__ as demo_main
from pipeline.demo import platform

KEY_B64 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="

CLUSTER_ENV = {
    "DEMO_STAC_URL": "http://stac-api.stac-higher.svc:8080",
    "DATABASE_URL": "postgresql://db.example.com:5432/stac_higher",
    "STAGING_BUCKET": "stac-higher-example-platform",
    "DEMO_PUBLIC_TITILER_URL": "https://titiler.example.com",
    "DEMO_PUBLIC_APP_URL": "https://app.example.com",
    "DEMO_APP_URL": "http://stac-higher-app",
}

DEST_ENV = {
    "DEMO_DESTINATION_BUCKET": "stac-higher-example-demo",
    "DEMO_DESTINATION_REGION": "us-east-1",
    "DEMO_DESTINATION_ACCESS_KEY_ID": "AKIAEXAMPLEEXAMPLE00",
    "DEMO_DESTINATION_SECRET_ACCESS_KEY": "example/secret/key/0000000000000000000000",
}


def _parsed(argv: list[str], env: dict[str, str]) -> argparse.Namespace:
    """Parse + resolve exactly as `main()` does, without running the command."""
    args = demo_main.build_parser().parse_args(argv)
    demo_main.resolve_endpoints(args, env)
    return args


# --------------------------------------------------------------------------- #
# endpoint resolution
# --------------------------------------------------------------------------- #


def test_compose_defaults_are_unchanged():
    args = _parsed(["seed"], CLUSTER_ENV)  # the env is ignored without --in-cluster

    assert args.in_cluster is False
    assert args.stac_url == "http://localhost:8082"
    assert args.titiler_url == "http://localhost:8084"
    assert args.s3_endpoint == "http://localhost:9000"
    assert args.database_url == "postgresql://username:password@localhost:5433/postgis"
    assert args.bucket == "stac-higher"
    assert args.ui_url == "http://localhost:4321"

    images = _parsed(["images-seed"], CLUSTER_ENV)
    assert images.app_url == "http://127.0.0.1:4321"


def test_cluster_mode_reads_endpoints_from_the_environment():
    args = _parsed(["--in-cluster", "seed", "--no-build-image"], CLUSTER_ENV)

    assert args.in_cluster is True
    assert args.stac_url == CLUSTER_ENV["DEMO_STAC_URL"]
    assert args.database_url == CLUSTER_ENV["DATABASE_URL"]
    assert args.bucket == CLUSTER_ENV["STAGING_BUCKET"]
    assert args.titiler_url == CLUSTER_ENV["DEMO_PUBLIC_TITILER_URL"]
    assert args.ui_url == CLUSTER_ENV["DEMO_PUBLIC_APP_URL"]
    assert args.s3_endpoint is None

    images = _parsed(["--in-cluster", "images-seed"], CLUSTER_ENV)
    assert images.app_url == CLUSTER_ENV["DEMO_APP_URL"]


def test_cluster_mode_lets_an_explicit_flag_win():
    args = _parsed(["--in-cluster", "--stac-url", "http://other:8080", "status"], CLUSTER_ENV)
    assert args.stac_url == "http://other:8080"


@pytest.mark.parametrize("var", ["DEMO_STAC_URL", "DATABASE_URL", "STAGING_BUCKET"])
def test_cluster_mode_names_a_missing_required_variable(var):
    env = {k: v for k, v in CLUSTER_ENV.items() if k != var}
    with pytest.raises(SystemExit) as excinfo:
        _parsed(["--in-cluster", "status"], env)
    assert var in str(excinfo.value)


def test_cluster_mode_needs_the_app_url_only_for_the_images_commands():
    env = {k: v for k, v in CLUSTER_ENV.items() if k != "DEMO_APP_URL"}
    _parsed(["--in-cluster", "status"], env)  # no app_url needed
    with pytest.raises(SystemExit) as excinfo:
        _parsed(["--in-cluster", "images-status"], env)
    assert "DEMO_APP_URL" in str(excinfo.value)


def test_cluster_mode_print_only_urls_are_optional():
    env = {
        k: v
        for k, v in CLUSTER_ENV.items()
        if k not in ("DEMO_PUBLIC_TITILER_URL", "DEMO_PUBLIC_APP_URL")
    }
    args = _parsed(["--in-cluster", "status"], env)
    assert args.titiler_url is None and args.ui_url is None


def test_cluster_mode_refuses_the_compose_s3_endpoint():
    with pytest.raises(SystemExit) as excinfo:
        _parsed(["--in-cluster", "--s3-endpoint", "http://minio:9000", "status"], CLUSTER_ENV)
    assert "--s3-endpoint" in str(excinfo.value)


def test_in_cluster_reaches_the_subcommand(monkeypatch):
    for key, value in CLUSTER_ENV.items():
        monkeypatch.setenv(key, value)
    seen: list[tuple[bool, str]] = []
    monkeypatch.setattr(
        demo_main.goes, "seed", lambda args: seen.append((args.in_cluster, args.stac_url)) or 0
    )
    assert demo_main.main(["--in-cluster", "goes-seed", "--no-build-image"]) == 0
    assert seen == [(True, CLUSTER_ENV["DEMO_STAC_URL"])]


# --------------------------------------------------------------------------- #
# the runtime image and the platform bucket
# --------------------------------------------------------------------------- #


def _no_docker(*a, **k):
    raise AssertionError("in-cluster mode must never call docker")


def test_cluster_seed_refuses_to_build(monkeypatch):
    monkeypatch.setattr(platform.subprocess, "run", _no_docker)
    monkeypatch.setattr(platform, "say", lambda *a, **k: None)

    with pytest.raises(SystemExit) as excinfo:
        platform.ensure_runtime_image(build=True, in_cluster=True)

    assert "--no-build-image" in str(excinfo.value)


def test_cluster_no_build_never_inspects_the_daemon(monkeypatch):
    monkeypatch.setattr(platform.subprocess, "run", _no_docker)
    monkeypatch.setattr(platform, "say", lambda *a, **k: None)

    platform.ensure_runtime_image(build=False, in_cluster=True)  # no docker call, no error


def test_platform_s3_client_is_the_pipelines_own_in_cluster(monkeypatch):
    built: list[object] = []
    monkeypatch.setattr(platform, "build_platform_client", lambda s: built.append(s) or "pipe")
    monkeypatch.setattr(platform, "s3_client", _no_docker)
    monkeypatch.setenv("STAGING_S3_ENDPOINT", "")
    monkeypatch.delenv("STAGING_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("STAGING_S3_SECRET_ACCESS_KEY", raising=False)

    args = argparse.Namespace(in_cluster=True, s3_endpoint=None)
    assert platform.platform_s3_client(args) == "pipe"
    settings = built[0]
    # blank endpoint + no keys = the AWS default endpoint and credential chain
    assert settings.staging_s3_endpoint is None
    assert settings.staging_s3_access_key is None


def test_platform_s3_client_is_minio_on_compose(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(platform, "s3_client", lambda endpoint: seen.append(endpoint) or "minio")
    args = argparse.Namespace(in_cluster=False, s3_endpoint="http://localhost:9000")
    assert platform.platform_s3_client(args) == "minio"
    assert seen == ["http://localhost:9000"]


def test_teardown_refuses_in_cluster(monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("teardown must refuse before connecting")

    monkeypatch.setattr(demo_main.psycopg, "connect", no_db)
    args = argparse.Namespace(in_cluster=True)
    with pytest.raises(SystemExit) as excinfo:
        demo_main.teardown(args)
    assert "asset_gc" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# goes-seed --deliver: the destination it seals
# --------------------------------------------------------------------------- #


class _Conn:
    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _goes_args(**overrides) -> argparse.Namespace:
    values = {
        "in_cluster": False,
        "database_url": "postgresql://db.invalid/x",
        "stac_url": "http://stac.invalid",
        "s3_endpoint": "http://localhost:9000",
        "titiler_url": "http://tiler.invalid",
        "ui_url": "http://localhost:4321",
        "bucket": "stac-higher",
        "include": [],
        "window": "-1h",
        "max_files": 2,
        "deliver": True,
        "internal_s3_endpoint": "http://minio:9000",
        "build_image": False,
        "force": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def goes(monkeypatch):
    from pipeline.demo.goes import seed as module

    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", KEY_B64)
    for var in DEST_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(module, "check_migrations", lambda *a, **k: None)
    monkeypatch.setattr(module, "ensure_runtime_image", lambda **k: None)
    monkeypatch.setattr(module, "say", lambda *a, **k: None)
    monkeypatch.setattr(module, "competing_ingest_associations", lambda *a, **k: [])
    monkeypatch.setattr(module, "enable_serving", lambda *a, **k: None)
    monkeypatch.setattr(module, "install_process", lambda *a, **k: None)
    monkeypatch.setattr(module, "upsert_association", lambda *a, **k: "assoc")
    module._writes = []  # type: ignore[attr-defined]
    module._connections = []  # type: ignore[attr-defined]
    module._buckets = []  # type: ignore[attr-defined]
    monkeypatch.setattr(
        module, "put_collection", lambda *a, **k: module._writes.append(a)  # type: ignore[attr-defined]
    )
    monkeypatch.setattr(
        module,
        "upsert_connection",
        lambda *a, **k: module._connections.append(k) or "conn",  # type: ignore[attr-defined]
    )
    monkeypatch.setattr(module, "s3_client", lambda endpoint: ("minio", endpoint))
    monkeypatch.setattr(
        module,
        "ensure_bucket",
        lambda client, bucket: module._buckets.append((client, bucket)),  # type: ignore[attr-defined]
    )
    return module


def _dest(module) -> dict:
    return next(c for c in module._connections if c["name"] == module.DEST_CONNECTION_NAME)


def test_cluster_deliver_seals_the_demo_destination_key(goes, monkeypatch):
    from pipeline.connections.adapters.s3 import S3Adapter, parse_s3_config

    for key, value in DEST_ENV.items():
        monkeypatch.setenv(key, value)

    assert goes.seed(_goes_args(in_cluster=True, s3_endpoint=None)) == 0

    dest = _dest(goes)
    assert dest["config"] == {"bucket": "stac-higher-example-demo", "region": "us-east-1"}
    key = load_master_key({"CREDENTIALS_MASTER_KEY": KEY_B64})
    sealed = json.loads(decrypt(dest["credentials"], key))
    assert sealed == {
        "access_key_id": DEST_ENV["DEMO_DESTINATION_ACCESS_KEY_ID"],
        "secret_access_key": DEST_ENV["DEMO_DESTINATION_SECRET_ACCESS_KEY"],
    }
    assert "minioadmin" not in json.dumps(sealed)
    parsed = parse_s3_config(dest["config"])
    assert parsed.endpoint is None and parsed.force_path_style is False
    S3Adapter(dest["config"], sealed)  # the delivery worker can build it
    assert goes._buckets == []  # Terraform made the bucket; the key cannot create one


@pytest.mark.parametrize("missing", sorted(DEST_ENV))
def test_cluster_deliver_refuses_before_any_write(goes, monkeypatch, missing):
    for key, value in DEST_ENV.items():
        if key != missing:
            monkeypatch.setenv(key, value)

    with pytest.raises(SystemExit) as excinfo:
        goes.seed(_goes_args(in_cluster=True, s3_endpoint=None))

    assert missing in str(excinfo.value)
    assert goes._writes == [] and goes._connections == []


def test_cluster_without_deliver_needs_no_destination(goes):
    assert goes.seed(_goes_args(in_cluster=True, s3_endpoint=None, deliver=False)) == 0
    assert all(c["name"] != goes.DEST_CONNECTION_NAME for c in goes._connections)


def test_compose_deliver_still_seals_minio_keys(goes, monkeypatch):
    for key, value in DEST_ENV.items():  # present, and ignored on compose
        monkeypatch.setenv(key, value)

    assert goes.seed(_goes_args()) == 0

    dest = _dest(goes)
    assert dest["config"] == {
        "bucket": goes.DEST_BUCKET,
        "region": "us-east-1",
        "endpoint": "http://minio:9000",
        "force_path_style": True,
    }
    key = load_master_key({"CREDENTIALS_MASTER_KEY": KEY_B64})
    assert json.loads(decrypt(dest["credentials"], key)) == {
        "access_key_id": "minioadmin",
        "secret_access_key": "minioadmin",
    }
    assert goes._buckets == [(("minio", "http://localhost:9000"), goes.DEST_BUCKET)]


def test_goes_seed_passes_cluster_mode_to_the_image_step(goes, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(goes, "ensure_runtime_image", lambda **k: seen.append(k))
    goes.seed(_goes_args(in_cluster=True, s3_endpoint=None, deliver=False))
    assert seen == [{"build": False, "in_cluster": True}]


def test_goes_teardown_refuses_in_cluster(goes, monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("goes-teardown must refuse before connecting")

    monkeypatch.setattr(goes.psycopg, "connect", no_db)
    with pytest.raises(SystemExit) as excinfo:
        goes.teardown(_goes_args(in_cluster=True))
    assert "asset_gc" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# --wait-ready: start-up ordering absorbed inside the pod (the chart's Job runs
# with backoffLimit 0, because a pod retry would redo every completed seed)
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_ready_returns_once_every_url_answers(monkeypatch):
    monkeypatch.setattr(platform, "say", lambda *a, **k: None)
    answers = {"http://stac": [False, False, True], "http://app": [True]}
    probed: list[str] = []

    def probe(url):
        probed.append(url)
        return answers[url].pop(0)

    clock = _Clock()
    platform.wait_ready(
        ["http://stac", "http://app"], timeout_s=60, probe=probe, sleep=clock.sleep, clock=clock
    )
    assert probed == ["http://stac", "http://stac", "http://stac", "http://app"]


def test_wait_ready_gives_up_naming_the_url(monkeypatch):
    monkeypatch.setattr(platform, "say", lambda *a, **k: None)
    clock = _Clock()
    with pytest.raises(SystemExit) as excinfo:
        platform.wait_ready(
            ["http://stac"], timeout_s=30, probe=lambda url: False, sleep=clock.sleep, clock=clock
        )
    assert "http://stac" in str(excinfo.value)
    assert clock.now >= 30


def test_reachable_counts_any_http_answer_and_not_a_refusal(monkeypatch):
    import urllib.error

    def answers(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    def refuses(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(platform.urllib.request, "urlopen", answers)
    assert platform.reachable("http://stac") is True
    monkeypatch.setattr(platform.urllib.request, "urlopen", refuses)
    assert platform.reachable("http://stac") is False


def test_main_waits_for_the_stac_api_and_the_app_before_the_command(monkeypatch):
    for key, value in CLUSTER_ENV.items():
        monkeypatch.setenv(key, value)
    order: list[object] = []
    monkeypatch.setattr(
        demo_main, "wait_ready", lambda urls, *, timeout_s: order.append((list(urls), timeout_s))
    )
    monkeypatch.setattr(demo_main.images, "seed", lambda args: order.append("seed") or 0)
    monkeypatch.setattr(demo_main.goes, "seed", lambda args: order.append("goes") or 0)

    assert demo_main.main(["--in-cluster", "--wait-ready", "600", "images-seed"]) == 0
    assert order == [
        ([CLUSTER_ENV["DEMO_STAC_URL"], CLUSTER_ENV["DEMO_APP_URL"]], 600),
        "seed",
    ]
    order.clear()
    assert demo_main.main(["--in-cluster", "goes-seed", "--no-build-image"]) == 0
    assert order == ["goes"]  # --wait-ready 0 (the default) waits for nothing


# --------------------------------------------------------------------------- #
# the seeds' own in-cluster paths
# --------------------------------------------------------------------------- #


def test_seed_uploads_the_scene_through_the_pipelines_client_in_cluster(monkeypatch):
    uploads: list[dict] = []
    image_steps: list[dict] = []

    class _Bucket:
        def put_object(self, **kwargs):
            uploads.append(kwargs)

    def compose_client(*a, **k):
        raise AssertionError("the compose s3_client must not be used in-cluster")

    clients: list[bool] = []
    monkeypatch.setattr(
        demo_main, "platform_s3_client", lambda args: clients.append(args.in_cluster) or _Bucket()
    )
    monkeypatch.setattr(demo_main, "_s3", compose_client)
    monkeypatch.setattr(demo_main, "s3_client", compose_client)
    monkeypatch.setattr(demo_main, "_check_migrations", lambda *a, **k: None)
    monkeypatch.setattr(demo_main, "ensure_runtime_image", lambda **k: image_steps.append(k))
    monkeypatch.setattr(demo_main, "_put_collection", lambda *a, **k: None)
    monkeypatch.setattr(demo_main, "_put_item", lambda *a, **k: None)
    monkeypatch.setattr(demo_main, "_enable_serving", lambda *a, **k: None)
    monkeypatch.setattr(demo_main, "_install_process", lambda *a, **k: None)
    monkeypatch.setattr(demo_main, "build_scene_cog", lambda: b"cog")
    monkeypatch.setattr(demo_main.psycopg, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(demo_main, "_say", lambda *a, **k: None)

    args = _parsed(["--in-cluster", "seed", "--no-build-image"], CLUSTER_ENV)
    assert demo_main.seed(args) == 0

    assert clients == [True]
    assert image_steps == [{"build": False, "in_cluster": True}]
    assert len(uploads) == 1
    assert uploads[0]["Bucket"] == CLUSTER_ENV["STAGING_BUCKET"]
    assert uploads[0]["Body"] == b"cog"
