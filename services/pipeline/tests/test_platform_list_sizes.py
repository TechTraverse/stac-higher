"""list_sizes: every (key, Size) under a prefix, across pages (Z-6 size readout)."""

from pipeline.storage.platform import list_sizes


def test_list_sizes_reads_every_page():
    class Paginator:
        def paginate(self, Bucket, Prefix):
            assert (Bucket, Prefix) == ("b", "assets/c/_cube/")
            yield {"Contents": [{"Key": "assets/c/_cube/repo", "Size": 3}]}
            yield {}
            yield {"Contents": [{"Key": "assets/c/_cube/snapshots/S", "Size": 4}]}

    class Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return Paginator()

    assert list_sizes(Client(), "b", "assets/c/_cube/") == [
        ("assets/c/_cube/repo", 3),
        ("assets/c/_cube/snapshots/S", 4),
    ]
