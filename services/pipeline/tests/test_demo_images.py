"""The images live-gate seeder (C-5, GitHub #54): demo images added through
the app's HTTP API, a kind-2 twin of the GOES GeoColor process and a canary.

No Docker, no dev server, no network: `AppClient` is monkeypatched out (a
`FakeClient`) and the platform writers (`install_process`, `put_collection`,
`enable_serving`, `check_migrations`, `psycopg.connect`) are stubbed the same
way `test_demo_goes.py` stubs `pipeline.demo.goes.seed`.

The `_FakeConn`-backed tests exercise the SEQUENCE and PARAMETERS of the SQL
`teardown()`/its `--images`/in-use-check statements build; they run no real
query. The SQL's actual behaviour (the `(reference, tag_at_add)` match, the
`process_revisions` in-use join, the queued/running check) was verified live
against the DB during the C-5 walk-through, not by these fakes.
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
    KEV_IMAGE,
    LARGE_IMAGE,
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
    pick_image,
    require_deployable,
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
    created_at: str = "2026-09-30T00:00:00Z",
) -> dict:
    return {
        "id": image_id,
        "reference": reference,
        "tag_at_add": tag,
        "status": status,
        "digest": digest,
        "verdict": {"reasons": reasons or []} if reasons is not None else None,
        "exception": exception,
        "created_at": created_at,
        "last_scanned_at": None,
    }


def test_pick_image_prefers_the_newest_live_row():
    ref, tag = "ghcr.io/techtraverse/stac-higher-process-runtime", "latest"
    old = _image(
        "old", ref, tag, "approved", digest="sha256:old",
        exception={"reason": "x"}, created_at="2026-09-30T10:00:00Z",
    )
    new = _image(
        "new", ref, tag, "scanning", digest="sha256:new", created_at="2026-10-01T10:00:00Z"
    )
    # Whatever order the API answers in, the newest live row wins, even
    # while it is still scanning, so the seed waits for it instead of
    # deploying on the old excepted digest.
    assert pick_image([old, new], ref, tag)["id"] == "new"
    assert pick_image([new, old], ref, tag)["id"] == "new"


def test_pick_image_skips_revoked_and_other_tags():
    ref = "ghcr.io/techtraverse/stac-higher-process-runtime"
    revoked = _image("rev", ref, "latest", "revoked", created_at="2026-10-02T00:00:00Z")
    other_tag = _image("v1", ref, "v1", "approved", created_at="2026-10-03T00:00:00Z")
    live = _image("live", ref, "latest", "approved", created_at="2026-09-01T00:00:00Z")
    assert pick_image([revoked, other_tag, live], ref, "latest")["id"] == "live"
    assert pick_image([revoked], ref, "latest") is None
    # tag=None matches any tag: the newest live row of the reference.
    assert pick_image([revoked, other_tag, live], ref, None)["id"] == "v1"


def test_split_reference_matches_what_the_app_actually_stores():
    # Verified live against the DB (not just this module's own logic): the
    # app's normalizeImageInput() (app/src/lib/images/normalize.ts) produced
    # exactly these (reference, tag_at_add) pairs for the four demo images.
    assert _split_reference(RUNTIME_IMAGE) == (
        "ghcr.io/techtraverse/stac-higher-process-runtime",
        "latest",
    )
    assert _split_reference(SLIM_IMAGE) == ("docker.io/library/python", "3.12-slim")
    assert _split_reference(LARGE_IMAGE) == ("docker.io/library/python", "3.12")
    assert _split_reference(KEV_IMAGE) == ("docker.io/vulnerables/cve-2014-6271", "latest")


class FakeClient:
    """A dict of images keyed by id. Records every call; `add_image` mints a
    new (normalized) row (or answers `add_status` if set); `grant_exception`
    can be told to answer `exception_status`."""

    def __init__(
        self,
        images: dict[str, dict] | None = None,
        *,
        exception_status: int | None = None,
        add_status: int | None = None,
    ):
        self.images: dict[str, dict] = images or {}
        self.calls: list[str] = []
        self.exception_status = exception_status
        self.add_status = add_status
        self.grant_calls: list[dict] = []
        self._next_id = itertools.count(1)

    def find_image(self, reference: str, tag: str | None) -> dict | None:
        self.calls.append("find_image")
        return pick_image(list(self.images.values()), reference, tag)

    def add_image(self, reference: str) -> dict:
        self.calls.append("add_image")
        if self.add_status is not None:
            raise AppError(self.add_status, "unauthorized")
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


def test_wait_for_scans_refinds_a_folded_row_by_reference_and_tag_after_a_404():
    # The drain de-duplicated the provisional admission onto an existing row
    # by digest and deleted the provisional one (spec §9.1): `get_image` on
    # the ORIGINAL id 404s forever after, but the reference/tag still finds
    # the surviving (now terminal) row under a DIFFERENT id.
    winner = {"id": "winner-1", "status": "approved", "reference": "docker.io/library/python"}

    def get_image(image_id):
        if image_id == "provisional-1":
            raise AppError(404, "not found")
        return winner

    client = SimpleNamespace(
        get_image=get_image,
        find_image=lambda reference, tag: winner if (reference, tag) == ("r", "t") else None,
    )

    result = wait_for_scans(
        client, ["provisional-1"], timeout_s=30,
        find_by={"provisional-1": ("r", "t")},
    )

    # keyed by the ORIGINAL id, so the caller's `images[demo.key]["id"]`
    # lookup still resolves even though the winner's own id differs.
    assert result["provisional-1"] == winner


def test_wait_for_scans_raises_a_clear_error_when_the_folded_row_cannot_be_refound():
    def get_image(image_id):
        raise AppError(404, "not found")

    client = SimpleNamespace(get_image=get_image, find_image=lambda reference, tag: None)

    with pytest.raises(SeedError) as excinfo:
        wait_for_scans(
            client, ["provisional-1"], timeout_s=30, find_by={"provisional-1": ("r", "t")}
        )

    assert "provisional-1" in str(excinfo.value)


def test_wait_for_scans_turns_a_404_into_a_clear_error_without_find_by():
    def get_image(image_id):
        raise AppError(404, "not found")

    client = SimpleNamespace(get_image=get_image)

    with pytest.raises(SeedError):
        wait_for_scans(client, ["a"], timeout_s=30)


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
# require_deployable: still scanning, stale and scan_failed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["pending", "scanning"])
def test_require_deployable_points_a_still_scanning_image_to_no_wait(status):
    image = _image("img-1", "docker.io/library/python", "3.12-slim", status)

    with pytest.raises(SeedError) as excinfo:
        require_deployable(image, "slim image")

    message = str(excinfo.value)
    assert "still scanning" in message
    assert "--no-wait" in message
    assert "exception" not in message


def test_require_deployable_refuses_a_stale_image():
    image = _image("img-1", "docker.io/library/python", "3.12-slim", "approved")
    image["stale"] = True

    with pytest.raises(SeedError) as excinfo:
        require_deployable(image, "slim image")

    assert "stale" in str(excinfo.value) and "/images" in str(excinfo.value)


def test_require_deployable_points_a_scan_failed_image_to_rescan_now():
    image = _image("img-1", "docker.io/library/python", "3.12-slim", "scan_failed")

    with pytest.raises(SeedError) as excinfo:
        require_deployable(image, "slim image")

    assert "Rescan now" in str(excinfo.value) and "/images" in str(excinfo.value)
    assert "exception" not in str(excinfo.value)


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
        "yes": False,
        "force": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class _FakeConn:
    def __init__(self):
        self.statements: list[str] = []
        self.params: list[object] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        self.params.append(params)
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


def test_seed_prints_ui_links_using_the_given_app_url(images_seed_module, monkeypatch):
    module = images_seed_module
    said: list[str] = []
    monkeypatch.setattr(module, "say", said.append)
    client = _runtime_slim_client("approved")
    monkeypatch.setattr(module, "AppClient", lambda base_url, bearer=None: client)

    # A trailing slash on --app-url must not leak a double slash into a link.
    assert module.seed(_images_args(app_url="http://127.0.0.1:4399/")) == 0

    assert any(line == "UI:    http://127.0.0.1:4399/images" for line in said)
    assert any(line == "       http://127.0.0.1:4399/processes" for line in said)
    assert any(
        line == f"       http://127.0.0.1:4399/collections/{OUTPUT_COLLECTION}/items"
        for line in said
    )
    assert not any("localhost:4321" in line for line in said)


def test_a_403_on_the_exception_names_the_admin_identity(images_seed_module, monkeypatch):
    module = images_seed_module
    client = _runtime_slim_client("rejected", exception_status=403)
    monkeypatch.setattr(module, "AppClient", lambda base_url, bearer=None: client)

    with pytest.raises(SeedError) as excinfo:
        module.seed(_images_args(exception_days=14))

    message = str(excinfo.value)
    assert "DEV_AUTH_IDENTITY" in message
    # the (truncated) response body rides along too, so a CSRF 403 from a
    # wrong --app-url reads as what it is, not as "needs admin".
    assert "forbidden" in message
    assert module._test_installed == []


def test_a_401_on_add_names_the_dev_auth_identity_or_bearer(images_seed_module, monkeypatch):
    module = images_seed_module
    client = FakeClient(add_status=401)
    monkeypatch.setattr(module, "AppClient", lambda base_url, bearer=None: client)

    with pytest.raises(SeedError) as excinfo:
        module.seed(_images_args())

    message = str(excinfo.value)
    assert "DEV_AUTH_IDENTITY" in message
    assert "--bearer" in message
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


def test_app_client_omits_authorization_header_without_a_bearer(monkeypatch):
    from pipeline.demo.images.seed import AppClient

    calls: list[dict] = []

    def fake_json_request(url, *, method="GET", body=None, headers=None):
        calls.append(headers)
        return 200, json.dumps({"images": []}).encode()

    monkeypatch.setattr("pipeline.demo.images.seed.json_request", fake_json_request)

    client = AppClient("http://127.0.0.1:4321")
    client.find_image("docker.io/library/python", "3.12-slim")

    assert "Authorization" not in calls[0]


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

    conn = module._test_conn
    idx = next(i for i, stmt in enumerate(conn.statements) if "collection_settings" in stmt)
    assert conn.params[idx] == (OUTPUT_COLLECTION,)

    # Verified live against the DB (not just these fakes): the GOES demo's
    # own processes, collections and ids must never appear anywhere this
    # teardown touches.
    goes_process_ids = {
        "60e50000-0000-4000-8000-000000000001",
        "60e50000-0000-4000-8000-000000000002",
        "60e50000-0000-4000-8000-000000000003",
        "60e50000-0000-4000-8000-000000000004",
    }
    goes_names = ('"goes-geocolor"', "'goes-geocolor'", "goes-abi-mcmipc", "goes-abi-metadata")
    haystacks = [str(stmt) for stmt in conn.statements]
    haystacks += [str(params) for params in conn.params]
    haystacks += [str(entry) for entry in deleted]
    haystacks += [str(pid) for pid in removed_ids]
    blob = " ".join(haystacks)
    assert not any(name in blob for name in goes_names)
    assert not any(pid in blob for pid in goes_process_ids)


def test_teardown_keeps_images_without_the_flag(images_seed_module, monkeypatch):
    module = images_seed_module
    monkeypatch.setattr(module, "remove_process", lambda *a, **k: None)
    monkeypatch.setattr(module, "request", lambda *a, **k: (204, b""))

    module.teardown(_images_args(images=False))

    assert not any("container_images" in stmt for stmt in module._test_conn.statements)


class _QueuedRunFakeConn(_FakeConn):
    def fetchall(self):
        return [("proc-1",)]


def test_teardown_refuses_while_a_run_is_queued_or_running(images_seed_module, monkeypatch):
    module = images_seed_module
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: _QueuedRunFakeConn())

    with pytest.raises(SeedError) as excinfo:
        module.teardown(_images_args(force=False))

    assert "queued" in str(excinfo.value) or "progress" in str(excinfo.value)


class _ImagesFlagFakeConn(_FakeConn):
    """Answers the `--images` deletion query with one demo row not in use
    (id-slim, approved) and one in use (id-runtime, approved); every other
    query behaves like the plain `_FakeConn` (empty)."""

    def fetchall(self):
        last_sql = self.statements[-1]
        if "unnest" in last_sql:
            return [
                ("id-slim", "docker.io/library/python", "3.12-slim", "approved", False),
                (
                    "id-runtime",
                    "ghcr.io/techtraverse/stac-higher-process-runtime",
                    "latest",
                    "approved",
                    True,
                ),
            ]
        return []


def test_teardown_images_flag_alone_lists_rows_and_deletes_nothing(
    images_seed_module, monkeypatch
):
    module = images_seed_module
    conn = _ImagesFlagFakeConn()
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(module, "remove_process", lambda *a, **k: None)
    monkeypatch.setattr(module, "request", lambda *a, **k: (204, b""))
    said: list[str] = []
    monkeypatch.setattr(module, "say", said.append)

    assert module.teardown(_images_args(images=True, yes=False)) == 0

    assert not any(
        "DELETE FROM stac_higher.container_images" in stmt for stmt in conn.statements
    )
    assert any("id-slim" in line for line in said)
    assert any("re-run with --yes" in line for line in said)
    assert any("stac-higher-process-runtime" in line and "in use" in line for line in said)


def test_teardown_images_flag_matches_reference_and_tag_pairs_and_skips_in_use(
    images_seed_module, monkeypatch
):
    module = images_seed_module
    conn = _ImagesFlagFakeConn()
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(module, "remove_process", lambda *a, **k: None)
    monkeypatch.setattr(module, "request", lambda *a, **k: (204, b""))

    assert module.teardown(_images_args(images=True, yes=True)) == 0

    unnest_idx = next(i for i, stmt in enumerate(conn.statements) if "unnest" in stmt)
    references, tags = conn.params[unnest_idx]
    assert set(zip(references, tags, strict=True)) == set(module._demo_reference_tag_pairs())

    delete_idx = next(
        i
        for i, stmt in enumerate(conn.statements)
        if "DELETE FROM stac_higher.container_images" in stmt
    )
    (deleted_ids,) = conn.params[delete_idx]
    assert deleted_ids == ["id-slim"]  # the in-use row (id-runtime) was skipped


def test_teardown_images_yes_without_images_flag_deletes_nothing(images_seed_module, monkeypatch):
    module = images_seed_module
    monkeypatch.setattr(module, "remove_process", lambda *a, **k: None)
    monkeypatch.setattr(module, "request", lambda *a, **k: (204, b""))

    module.teardown(_images_args(images=False, yes=True))

    assert not any("container_images" in stmt for stmt in module._test_conn.statements)


# --------------------------------------------------------------------------- #
# status()
# --------------------------------------------------------------------------- #


def test_status_reports_images_runs_and_item_count(images_seed_module, monkeypatch):
    module = images_seed_module
    said: list[str] = []
    monkeypatch.setattr(module, "say", said.append)

    runtime_ref, runtime_tag = module._split_reference(RUNTIME_IMAGE)
    client = FakeClient(
        {
            "runtime-1": _image(
                "runtime-1", runtime_ref, runtime_tag, "approved", digest="sha256:" + "a" * 64
            )
        }
    )
    monkeypatch.setattr(module, "AppClient", lambda base_url, bearer=None: client)
    monkeypatch.setattr(module, "request", lambda *a, **k: (200, b'{"numberMatched": 3}'))

    assert module.status(_images_args()) == 0

    assert any(runtime_ref in line for line in said)
    assert any("no runs yet" in line for line in said)
    assert any(f"{OUTPUT_COLLECTION}: 3 item(s)" in line for line in said)


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


def test_the_cli_turns_an_uncaught_app_error_into_a_clean_message(monkeypatch):
    from pipeline.demo import __main__ as demo_main
    from pipeline.demo.images import seed as images_seed

    said: list[str] = []
    monkeypatch.setattr(demo_main, "say", said.append)
    monkeypatch.setattr(
        images_seed, "status", lambda args: (_ for _ in ()).throw(AppError(500, "boom"))
    )

    assert demo_main.main(["images-status"]) == 1
    assert any("500" in line and "boom" in line for line in said)
