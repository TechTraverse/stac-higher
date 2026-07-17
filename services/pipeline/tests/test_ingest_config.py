"""parse_ingest_config + glob matching (Python side of the §5.1 contract)."""

from __future__ import annotations

import pytest

from pipeline.ingest.config import (
    IngestConfigError,
    parse_ingest_config,
    path_matches,
)


def test_parse_minimal_applies_defaults():
    cfg = parse_ingest_config({"source_path": "/outgoing"})
    assert cfg.source_path == "/outgoing"
    assert cfg.include == ()
    assert cfg.exclude == ()
    assert cfg.poll_frequency_seconds == 300
    assert cfg.storage_mode == "copy"
    assert cfg.grouping.rule == "none"
    assert cfg.grouping.timeout_seconds == 900
    assert cfg.grouping.on_timeout == "ingest_partial"
    assert cfg.post_ingest == "leave"


def test_parse_full_config():
    cfg = parse_ingest_config(
        {
            "source_path": "/products",
            "include": ["**/*.tif", "**/*.xml"],
            "exclude": ["**/*.tmp"],
            "poll_frequency_seconds": 600,
            "storage_mode": "reference",
            "grouping": {
                "rule": "shared_basename",
                "timeout_seconds": 120,
                "on_timeout": "discard",
            },
            "metadata": {"strategy": "raster_auto"},
            "post_ingest": "delete",
        }
    )
    assert cfg.include == ("**/*.tif", "**/*.xml")
    assert cfg.exclude == ("**/*.tmp",)
    assert cfg.storage_mode == "reference"
    assert cfg.grouping.rule == "shared_basename"
    assert cfg.grouping.timeout_seconds == 120
    assert cfg.grouping.on_timeout == "discard"
    assert cfg.metadata == {"strategy": "raster_auto"}
    assert cfg.post_ingest == "delete"


def test_parse_requires_source_path():
    with pytest.raises(IngestConfigError, match="source_path is required"):
        parse_ingest_config({"include": ["*.tif"]})
    with pytest.raises(IngestConfigError, match="source_path is required"):
        parse_ingest_config({"source_path": "   "})


def test_parse_rejects_bad_enum():
    with pytest.raises(IngestConfigError, match="storage_mode"):
        parse_ingest_config({"source_path": "/x", "storage_mode": "sideways"})
    with pytest.raises(IngestConfigError, match=r"grouping\.rule"):
        parse_ingest_config({"source_path": "/x", "grouping": {"rule": "nope"}})


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("scene.tif", True),
        ("a/b/scene.tif", True),
        ("scene.xml", True),
        ("scene.tmp", False),  # excluded
        ("notes.txt", False),  # not included
        ("deep/nested/x.tmp", False),  # excluded even though under include glob
    ],
)
def test_path_matches_include_exclude(path, expected):
    assert (
        path_matches(path, ["**/*.tif", "**/*.xml"], ["**/*.tmp"]) is expected
    )


def test_path_matches_empty_include_allows_all_but_excluded():
    assert path_matches("anything.bin", [], []) is True
    assert path_matches("skip.tmp", [], ["*.tmp"]) is False


def test_single_star_stays_within_segment():
    assert path_matches("a.tif", ["*.tif"], []) is True
    assert path_matches("sub/a.tif", ["*.tif"], []) is False
