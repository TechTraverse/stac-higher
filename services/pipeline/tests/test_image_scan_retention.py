"""Scan retention (container-images spec §8.3): the latest SBOM pair and each
image's ten newest scans stay; everything else under scans/ ages out, objects
before rows."""

from __future__ import annotations

import datetime as dt

import pytest

from pipeline.images.retention import (
    DETACH_PRUNED_REFS_SQL,
    KEEP_SCANS_PER_IMAGE,
    KEPT_SCANS_SQL,
    PRUNE_SCAN_ROWS_SQL,
    KeptScan,
    ScanRetentionRepo,
    keep_set,
    keys_to_delete,
    scan_prefix,
    scan_retention_tick,
)
from pipeline.storage.platform import delete_keys, list_objects

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
OLD = NOW - dt.timedelta(days=3)
IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
PROV = "99999999-8888-4777-8666-555555555555"
S_ADM = "11111111-2222-4333-8444-555555555555"
S_NEW = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"
S_FOLD = "22222222-3333-4444-8555-666666666666"
S_RUN = "33333333-4444-4555-8666-777777777777"


def keys(prefix: str, *names: str) -> list[str]:
    return [f"{prefix}{n}" for n in names]


def test_a_folded_scan_keeps_the_prefix_its_refs_name():
    folded = KeptScan(S_FOLD, IMG, "done", f"scans/{PROV}/{S_FOLD}/findings.grype.json", None)
    assert scan_prefix(folded) == f"scans/{PROV}/{S_FOLD}/"
    bare = KeptScan(S_NEW, IMG, "failed", None, None)
    assert scan_prefix(bare) == f"scans/{IMG}/{S_NEW}/"
    foreign = KeptScan(S_NEW, IMG, "done", f"scans/{IMG}/{S_ADM}/findings.grype.json", None)
    assert scan_prefix(foreign) == f"scans/{IMG}/{S_NEW}/"  # a ref naming another scan


def test_the_keep_set_and_what_it_deletes():
    kept_scans = [
        KeptScan(S_NEW, IMG, "done", f"scans/{IMG}/{S_NEW}/findings.grype.json",
                 f"scans/{IMG}/{S_NEW}/log"),
        KeptScan(S_RUN, IMG, "running", None, None),
    ]
    sboms = [f"scans/{IMG}/{S_ADM}/sbom.syft.json"]
    keep, whole = keep_set(kept_scans, sboms)
    objects = [
        # the newest scan: findings/result/log kept, a stray SBOM there is not current
        *[(k, OLD) for k in keys(f"scans/{IMG}/{S_NEW}/", "findings.grype.json",
                                 "result.json", "log", "sbom.syft.json")],
        # the admission scan fell out of the ten: only its SBOM pair stays
        *[(k, OLD) for k in keys(f"scans/{IMG}/{S_ADM}/", "findings.grype.json",
                                 "result.json", "log", "sbom.syft.json", "sbom.cdx.json")],
        # a dedup's orphan under a deleted provisional image
        *[(k, OLD) for k in keys(f"scans/{PROV}/{S_FOLD}/", "sbom.syft.json", "result.json")],
        # a scan in flight: everything under it stays
        (f"scans/{IMG}/{S_RUN}/partial.tmp", OLD),
        # written in the last hour: the grace keeps it whatever it is
        (f"scans/{PROV}/{S_FOLD}/log", NOW - dt.timedelta(hours=1)),
    ]
    doomed = keys_to_delete(objects, keep_keys=keep, keep_prefixes=whole, now=NOW)
    assert sorted(doomed) == sorted(
        [
            f"scans/{IMG}/{S_NEW}/sbom.syft.json",
            f"scans/{IMG}/{S_ADM}/findings.grype.json",
            f"scans/{IMG}/{S_ADM}/result.json",
            f"scans/{IMG}/{S_ADM}/log",
            f"scans/{PROV}/{S_FOLD}/sbom.syft.json",
            f"scans/{PROV}/{S_FOLD}/result.json",
        ]
    )
    # never the current SBOM pair
    assert f"scans/{IMG}/{S_ADM}/sbom.syft.json" not in doomed
    assert f"scans/{IMG}/{S_ADM}/sbom.cdx.json" not in doomed


class Repo(ScanRetentionRepo):
    def __init__(self, order):
        self.order = order
        self.prune_args = None

    async def list_kept_scans(self, *, keep):
        self.order.append(("kept", keep))
        return []

    async def list_sbom_refs(self):
        return [f"scans/{IMG}/{S_ADM}/sbom.syft.json"]

    async def detach_pruned_refs(self, *, keep):
        self.order.append(("detach", keep))
        return 2

    async def prune_scan_rows(self, *, older_than, keep):
        self.order.append("prune")
        self.prune_args = (older_than, keep)
        return 4


async def test_objects_go_before_rows_and_rows_use_the_history_window():
    order: list = []

    async def listing():
        order.append("list")
        return [(f"scans/{PROV}/{S_FOLD}/result.json", OLD)]

    async def delete(found):
        order.append(("delete", tuple(found)))
        return len(found)

    repo = Repo(order)
    result = await scan_retention_tick(
        repo, list_objects=listing, delete_keys=delete, history_days=365, now=NOW
    )
    assert order == [
        ("kept", KEEP_SCANS_PER_IMAGE),
        "list",
        ("delete", (f"scans/{PROV}/{S_FOLD}/result.json",)),
        ("detach", KEEP_SCANS_PER_IMAGE),
        "prune",
    ]
    assert repo.prune_args == (NOW - dt.timedelta(days=365), KEEP_SCANS_PER_IMAGE)
    assert (result.objects_deleted, result.rows_deleted) == (1, 4)


async def test_a_failed_object_leg_prunes_no_rows():
    order: list = []

    async def listing():
        raise OSError("storage down")

    async def delete(found):  # pragma: no cover - never reached
        return 0

    repo = Repo(order)
    with pytest.raises(OSError):
        await scan_retention_tick(
            repo, list_objects=listing, delete_keys=delete, history_days=365, now=NOW
        )
    assert "prune" not in order
    assert not any(isinstance(step, tuple) and step[0] == "detach" for step in order)


def test_a_pending_scan_also_keeps_its_whole_prefix():
    """Item 4's pending-scan case: keep_set's whole-prefix rule covers
    `pending`, not only `running` (the retention sweep's own test above only
    exercises `running`)."""
    keep, whole = keep_set([KeptScan(S_RUN, IMG, "pending", None, None)], [])
    assert whole == {f"scans/{IMG}/{S_RUN}/"}
    assert keep == frozenset()


def test_the_sql_keeps_the_newest_ten_the_last_scan_and_scans_in_flight():
    # Item 4 (Minor 3): a requested_at tie-break makes the three windows
    # provably agree -- ties are unreachable in practice (one scan per image
    # per transaction), but the tie-break is free insurance.
    window = "row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC, id DESC)"
    assert window in KEPT_SCANS_SQL
    assert "s.rn <= %s OR s.status IN ('pending', 'running') OR s.id = i.last_scan_id" in (
        KEPT_SCANS_SQL
    )
    assert "SET findings_ref = NULL, log_ref = NULL" in DETACH_PRUNED_REFS_SQL
    assert "s.id IS DISTINCT FROM i.last_scan_id" in DETACH_PRUNED_REFS_SQL
    for fragment in (
        "r.rn > %s",
        "s.requested_at < %s",
        "s.status IN ('done', 'failed')",
        "s.id IS DISTINCT FROM i.last_scan_id",
    ):
        assert fragment in PRUNE_SCAN_ROWS_SQL, fragment


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.delete_calls: list[list[str]] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        found = [{"Key": k, "LastModified": m} for k, m in self.objects if k.startswith(Prefix)]
        yield {"Contents": found[:1]}
        yield {"Contents": found[1:]}
        yield {}

    def delete_objects(self, Bucket, Delete):
        self.delete_calls.append([o["Key"] for o in Delete["Objects"]])


def test_list_objects_pages_and_scopes_to_the_prefix():
    s3 = FakeS3([("scans/a", OLD), ("scans/b", NOW), ("logs/c", OLD)])
    assert list_objects(s3, "bucket", "scans/") == [("scans/a", OLD), ("scans/b", NOW)]


def test_delete_keys_batches_by_a_thousand():
    s3 = FakeS3([])
    assert delete_keys(s3, "bucket", [f"scans/{i}" for i in range(2500)]) == 2500
    assert [len(c) for c in s3.delete_calls] == [1000, 1000, 500]
    assert delete_keys(s3, "bucket", []) == 0
