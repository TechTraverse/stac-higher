"""The images live-gate seeder (C-5, GitHub #54): demo images added through
the app's HTTP API, a kind-2 twin of the GOES GeoColor process and a canary.

No Docker, no dev server, no network: `AppClient` is monkeypatched out (a
`FakeClient`) and the platform writers (`install_process`, `put_collection`,
`enable_serving`, `check_migrations`, `psycopg.connect`) are stubbed the same
way `test_demo_goes.py` stubs `pipeline.demo.goes.seed`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
from types import SimpleNamespace

import pytest

from pipeline.demo.images.seed import (
    CANARY_ID,
    GEOCOLOR_IMG_ID,
    OUTPUT_COLLECTION,
    RUNTIME_IMAGE,
    SLIM_IMAGE,
    AppError,
    DemoImage,
    SeedError,
    _split_reference,
    ensure_image,
    image_snapshot,
    kind2_runtime,
    wait_for_scans,
)


def _image(
    image_id: str,
    reference: str,
    tag: str,
    status: str,
    *,
    digest: str | None = None,
    reasons: list[str] | None = None,
    exception: dict | None = None,
) -> dict:
    return {
        "id": image_id,
        "reference": reference,
        "tag_at_add": tag,
        "status": status,
        "digest": digest,
        "verdict": {"reasons": reasons or []} if reasons is not None else None,
        "exception": exception,
        "last_scanned_at": None,
    }


class FakeClient:
    """A dict of images keyed by id. Records every call; `add_image` mints a
    new (normalized) row; `grant_exception` can be told to answer 403."""

    def __init__(
        self, images: dict[str, dict] | None = None, *, exception_status: int | None = None
    ):
        self.images: dict[str, dict] = images or {}
        self.calls: list[str] = []
        self.exception_status = exception_status
        self.grant_calls: list[dict] = []
        self._next_id = itertools.count(1)

    def find_image(self, reference: str, tag: str | None) -> dict | None:
        self.calls.append("find_image")
        for image in self.images.values():
            if image["reference"] != reference:
                continue
            if tag is not None and image["tag_at_add"] != tag:
                continue
            if image["status"] == "revoked":
                continue
            return image
        return None

    def add_image(self, reference: str) -> dict:
        self.calls.append("add_image")
        reference_norm, tag = _split_reference(reference)
        image_id = f"img-{next(self._next_id)}"
        self.images[image_id] = _image(image_id, reference_norm, tag, "pending")
        return {
            "id": image_id,
            "reference": reference_norm,
            "tag": tag,
            "scan_id": "scan-1",
            "deduplicated": False,
        }

    def get_image(self, image_id: str) -> dict:
        self.calls.append("get_image")
        return self.images[image_id]

    def grant_exception(self, image_id: str, reason: str, expires_at: str) -> dict:
        self.calls.append("grant_exception")
        if self.exception_status is not None:
            raise AppError(self.exception_status, "forbidden")
        self.images[image_id] = {**self.images[image_id], "status": "approved"}
        self.grant_calls.append({"image_id": image_id, "reason": reason, "expires_at": expires_at})
        return self.images[image_id]


# --------------------------------------------------------------------------- #
# ensure_image / find-or-add (Review Focus #1, #4)
# --------------------------------------------------------------------------- #


def test_an_existing_non_revoked_image_is_reused_not_re_added():
    client = FakeClient(
        {"img-1": _image("img-1", "docker.io/library/python", "3.12-slim", "approved")}
    )
    demo = DemoImage("slim", SLIM_IMAGE)

    image = ensure_image(client, demo)

    assert image["id"] == "img-1"
    assert "add_image" not in client.calls


def test_a_revoked_row_is_not_reused():
    client = FakeClient(
        {"img-1": _image("img-1", "docker.io/library/python", "3.12-slim", "revoked")}
    )
    demo = DemoImage("slim", SLIM_IMAGE)

    ensure_image(client, demo)

    assert client.calls.count("add_image") == 1


# --------------------------------------------------------------------------- #
# wait_for_scans
# --------------------------------------------------------------------------- #


def test_wait_for_scans_returns_when_all_are_terminal():
    responses = {
        "a": iter([{"id": "a", "status": "pending"}, {"id": "a", "status": "approved"}]),
        "b": iter([{"id": "b", "status": "approved"}, {"id": "b", "status": "approved"}]),
    }
    client = SimpleNamespace(get_image=lambda image_id: next(responses[image_id]))
    sleeps: list[float] = []
    clock_iter = itertools.count(0.0, 1.0)

    result = wait_for_scans(
        client, ["a", "b"], timeout_s=30, poll_s=1.0,
        sleep=sleeps.append, clock=lambda: next(clock_iter),
    )

    assert result["a"]["status"] == "approved"
    assert result["b"]["status"] == "approved"
    assert sleeps == [1.0]


def test_wait_for_scans_stops_at_the_timeout():
    client = SimpleNamespace(get_image=lambda image_id: {"id": image_id, "status": "pending"})
    sleeps: list[float] = []
    # The second clock() call (the deadline check) jumps well past any timeout.
    clock_iter = itertools.count(0.0, 20.0)

    result = wait_for_scans(
        client, ["a"], timeout_s=10, poll_s=1.0, sleep=sleeps.append, clock=lambda: next(clock_iter)
    )

    assert result["a"]["status"] == "pending"
    assert sleeps == []


# --------------------------------------------------------------------------- #
# kind2_runtime / image_snapshot
# --------------------------------------------------------------------------- #


def test_kind2_runtime_snapshots_the_digest_and_keeps_the_base():
    base = {
        "kind": "inline_python",
        "image": None,
        "memory_mb": 2048,
        "timeout_seconds": 600,
        "retry": {"max_attempts": 2, "backoff": "exponential"},
        "network": {"level": "isolated", "hosts": []},
    }
    image = _image(
        "img-1", "ghcr.io/techtraverse/stac-higher-process-runtime", "latest", "approved",
        digest="sha256:" + "a" * 64,
    )

    runtime = kind2_runtime(image, base)

    assert runtime["kind"] == "inline_python_on_image"
    assert runtime["image"] == image_snapshot(image)
    assert runtime["image"] == {
        "id": "img-1", "reference": image["reference"], "digest": image["digest"],
    }
    assert runtime["memory_mb"] == 2048
    assert runtime["network"] == base["network"]
    # the base dict passed in is never mutated
    assert base["kind"] == "inline_python" and base["image"] is None


# --------------------------------------------------------------------------- #
# seed(): the rejected-image / exception path (Review Focus #2)
# --------------------------------------------------------------------------- #


def _images_args(**overrides) -> argparse.Namespace:
    defaults = {
        "database_url": "postgresql://u:p@localhost:5433/postgis",
        "stac_url": "http://stac.invalid",
        "app_url": "http://app.invalid",
        "bearer": None,
        "with_large": False,
        "with_kev": False,
        "exception_days": None,
        "no_wait": True,
        "scan_timeout": 1800,
        "images": False,
        "force": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class _FakeConn:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        return self

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def images_seed_module(monkeypatch):
    from pipeline.demo.images import seed as module

    conn = _FakeConn()
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(module, "check_migrations", lambda *a, **k: None)
    monkeypatch.setattr(module, "say", lambda *a, **k: None)
    monkeypatch.setattr(module, "request", lambda *a, **k: (200, b"{}"))
    monkeypatch.setattr(module, "put_collection", lambda *a, **k: None)
    monkeypatch.setattr(module, "enable_serving", lambda *a, **k: None)
    installed: list[dict] = []
    monkeypatch.setattr(module, "install_process", lambda conn, **kw: installed.append(kw))
    module._test_conn = conn  # type: ignore[attr-defined]
    module._test_installed = installed  # type: ignore[attr-defined]
    return module


def _runtime_slim_client(runtime_status: str, **kwargs) -> FakeClient:
    from pipeline.demo.images.seed import _split_reference

    runtime_ref, runtime_tag = _split_reference(RUNTIME_IMAGE)
    slim_ref, slim_tag = _split_reference(SLIM_IMAGE)
    return FakeClient(
        {
            "runtime-1": _image(
                "runtime-1", runtime_ref, runtime_tag, runtime_status,
                digest="sha256:" + "a" * 64, reasons=["CVE-1", "CVE-2"],
            ),
            "slim-1": _image(
                "slim-1", slim_ref, slim_tag, "approved", digest="sha256:" + "b" * 64, reasons=[],
            ),
        },
        **kwargs,
    )


def test_a_rejected_image_blocks_the_install_unless_an_exception_is_requested(
    images_seed_module, monkeypatch
):
    module = images_seed_module
    client = _runtime_slim_client("rejected")
    monkeypatch.setattr(module, "AppClient", lambda base_url, bearer=None: client)

    assert module.seed(_images_args(exception_days=None)) == 2
    assert module._test_installed == []

    assert module.seed(_images_args(exception_days=14)) == 0
    assert len(module._test_installed) == 2
    assert len(client.grant_calls) == 1
    grant = client.grant_calls[0]
    assert grant["image_id"] == "runtime-1"
    expires = dt.datetime.fromisoformat(grant["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.UTC)
    delta_days = (expires - dt.datetime.now(dt.UTC)).total_seconds() / 86400
    assert 13.9 <= delta_days <= 14.1


def test_a_403_on_the_exception_names_the_admin_identity(images_seed_module, monkeypatch):
    module = images_seed_module
    client = _runtime_slim_client("rejected", exception_status=403)
    monkeypatch.setattr(module, "AppClient", lambda base_url, bearer=None: client)

    with pytest.raises(SeedError) as excinfo:
        module.seed(_images_args(exception_days=14))

    assert "DEV_AUTH_IDENTITY" in str(excinfo.value)
    assert module._test_installed == []


def test_a_missing_goes_source_collection_stops_before_any_write(images_seed_module, monkeypatch):
    module = images_seed_module
    monkeypatch.setattr(module, "request", lambda *a, **k: (404, b"{}"))
    touched: list[str] = []
    monkeypatch.setattr(
        module,
        "AppClient",
        lambda base_url, bearer=None: SimpleNamespace(
            find_image=lambda *a, **k: touched.append("find_image") or None,
            add_image=lambda *a, **k: touched.append("add_image") or {"id": "x"},
            get_image=lambda *a, **k: touched.append("get_image") or {},
        ),
    )

    with pytest.raises(SeedError) as excinfo:
        module.seed(_images_args())

    assert "goes-abi-mcmipc" in str(excinfo.value)
    assert touched == []
    assert module._test_installed == []


# --------------------------------------------------------------------------- #
# AppClient
# --------------------------------------------------------------------------- #


def test_app_client_sends_origin_and_bearer(monkeypatch):
    from pipeline.demo.images.seed import AppClient

    calls: list[tuple] = []

    def fake_json_request(url, *, method="GET", body=None, headers=None):
        calls.append((url, method, body, headers))
        return 200, json.dumps({"images": []}).encode()

    monkeypatch.setattr("pipeline.demo.images.seed.json_request", fake_json_request)

    client = AppClient("http://127.0.0.1:4321", bearer="tok")
    client.find_image("docker.io/library/python", "3.12-slim")

    assert len(calls) == 1
    _, _, _, headers = calls[0]
    assert headers["Origin"] == "http://127.0.0.1:4321"
    assert headers["Authorization"] == "Bearer tok"


# --------------------------------------------------------------------------- #
# images-teardown (Review Focus #5)
# --------------------------------------------------------------------------- #


def test_teardown_removes_only_its_own_processes_and_collection(images_seed_module, monkeypatch):
    module = images_seed_module
    removed_ids: list[str] = []
    monkeypatch.setattr(
        module, "remove_process", lambda conn, process_id: removed_ids.append(process_id)
    )
    deleted: list[tuple[str, str]] = []

    def fake_request(url, *, method="GET", body=None):
        deleted.append((url, method))
        return 204, b""

    monkeypatch.setattr(module, "request", fake_request)

    assert module.teardown(_images_args()) == 0

    assert set(removed_ids) == {CANARY_ID, GEOCOLOR_IMG_ID}
    assert deleted == [(f"http://stac.invalid/collections/{OUTPUT_COLLECTION}", "DELETE")]


def test_teardown_keeps_images_without_the_flag(images_seed_module, monkeypatch):
    module = images_seed_module
    monkeypatch.setattr(module, "remove_process", lambda *a, **k: None)
    monkeypatch.setattr(module, "request", lambda *a, **k: (204, b""))

    module.teardown(_images_args(images=False))

    assert not any("container_images" in stmt for stmt in module._test_conn.statements)


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #


def test_the_cli_registers_the_three_subcommands(monkeypatch):
    from pipeline.demo import __main__ as demo_main
    from pipeline.demo.images import seed as images_seed

    seen: list[argparse.Namespace] = []
    monkeypatch.setattr(images_seed, "seed", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(images_seed, "status", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(images_seed, "teardown", lambda args: seen.append(args) or 0)

    assert demo_main.main(["images-seed", "--exception-days", "14", "--with-kev"]) == 0
    args = seen[-1]
    assert args.exception_days == 14
    assert args.with_kev is True
    assert args.with_large is False
    assert args.func is images_seed.seed

    assert demo_main.main(["images-status"]) == 0
    assert seen[-1].func is images_seed.status

    assert demo_main.main(["images-teardown"]) == 0
    assert seen[-1].func is images_seed.teardown
