# G-2 · Process Inputs + Network Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A process run receives the STAC items that triggered it, plus a readable location for every one of their assets, through a manifest staged into its own prefix before the container starts; and every revision carries a `network` profile capped by a deployment maximum (only `isolated` accepted this slice).

**Architecture:** The dispatcher records `collection_id` and `op` per triggering item. At run time, before credentials are minted, a pure planner turns the run's `input_items` plus the items' pgstac documents into an `InputManifest`: canonical `/api/assets/...` hrefs become their `assets/{c}/{i}/{f}` keys (no copy; the run's STS session policy gains read access to each source collection's prefix), any other absolute href is fetched — through a matching reference-mode ingest association's adapter when one exists, else an egress-policy-checked public HTTPS GET — into `staging/runs/{run_id}/inputs/{batch_id}/{item_id}/{filename}`. The manifest is written to `inputs/{batch_id}/manifest.json`, two env vars name it, the finalize resolver skips `inputs/`, and the write gate, Python mirror, fixture and UI gain the `network` block.

**Tech Stack:** Python 3.12 (psycopg, boto3, urllib), pytest; Zod v4 + React (app), vitest; contract fixtures in `tests/contract-fixtures/`.

**Spec:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §3, §4, §14; ADR 0018 (written in Task 10).

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/goes-g2 -b ai/goes-g2 ai/main`; `npm install` at the worktree root.
- Verify gates: `npm run verify` (root) and `uv run pytest` + `uv run ruff check .` (from `services/pipeline/`).
- ADR 0013 invariants hold: the run's environment is assembled only in `build_run_spec`; the platform's own keys never reach the run; STS policy is the boundary. Read grants are on **source collections' canonical prefixes only** (`assets/{collection}/*`), never on `staging/` outside the run's own prefix.
- Staging happens BEFORE `executor.launch`; a staging failure means no container exists.
- Keys are built only by `pipeline/storage/keys.py` builders; every segment passes `_safe_segment`/`sanitize_filename`.
- The manifest is a producer-golden contract: `tests/contract-fixtures/process-input-manifest.json`, consumed by pytest (the pipeline writes exactly this) and documented in the fixtures README.
- `runtime.network` is a cross-runtime shape: app schema (strict writer), Python parser (lenient reader), `process-runtime.json` fixture — all three move together.
- Slice 1 write gate accepts `network.level = "isolated"` only; `PROCESS_NETWORK_MAX` defaults to `isolated`.
- Commit messages end with the session's attribution trailer.

---

### Task 1: Dispatcher batch entries carry `collection_id` and `op`

**Files:**
- Modify: `services/pipeline/src/pipeline/dispatcher/loop.py:265`
- Test: `services/pipeline/tests/test_process_triggers.py`

**Interfaces:**
- Produces: each entry appended to a process batch is `{"item_id": str, "collection_id": str, "op": "insert" | "update"}`. Older run rows may still hold `{"item_id"}` only — the planner (Task 4) tolerates that.

- [ ] **Step 1: Write the failing test**

In `services/pipeline/tests/test_process_triggers.py`, find the test that drives the dispatcher loop with `enqueue_process_runs` and asserts on the batch payload (search for `"items"` in that file). Add a sibling:

```python
async def test_process_batch_entries_carry_collection_and_op(...):
    # Arrange exactly like the neighbouring test that asserts one batch per
    # source (same fake repo / fake queue / one 'insert' event for item 'i1'
    # in collection 'c1'), then:
    payload = enqueued_process_batches[0]
    assert payload["items"] == [{"item_id": "i1", "collection_id": "c1", "op": "insert"}]
```

Copy the arrangement from the neighbouring test verbatim; only the assertion is new.

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py -k collection_and_op -v`
Expected: FAIL — the entry is `{"item_id": "i1"}`.

- [ ] **Step 3: Implement**

`services/pipeline/src/pipeline/dispatcher/loop.py` line 265:

```python
                    batch["items"].append(
                        {
                            "item_id": pm.item_id,
                            "collection_id": event.collection_id,
                            "op": event.op,
                        }
                    )
```

- [ ] **Step 4: Run and commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py -v && uv run ruff check .`
Expected: PASS.

```bash
git add services/pipeline/src/pipeline/dispatcher/loop.py services/pipeline/tests/test_process_triggers.py
git commit -m "feat(dispatcher): process batch entries carry collection_id and op (G-2)"
```

---

### Task 2: Key builders for the run's inputs area

**Files:**
- Modify: `services/pipeline/src/pipeline/storage/keys.py`
- Test: `services/pipeline/tests/test_process_executor.py` (it already imports `run_staging_prefix`, `InvalidKeySegment`)

**Interfaces:**
- Produces:
  - `run_inputs_prefix(run_id: str) -> str` → `staging/runs/{run_id}/inputs/`
  - `run_input_manifest_key(run_id: str, batch_id: str) -> str` → `staging/runs/{run_id}/inputs/{batch_id}/manifest.json`
  - `run_input_asset_key(run_id: str, batch_id: str, item_id: str, filename: str) -> str` → `staging/runs/{run_id}/inputs/{batch_id}/{item_id}/{sanitized filename}`
  - `INPUTS_SEGMENT = "inputs"`

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_process_executor.py`:

```python
from pipeline.storage.keys import (
    run_input_asset_key,
    run_input_manifest_key,
    run_inputs_prefix,
)


def test_input_keys_nest_under_the_run_prefix():
    assert run_inputs_prefix(RUN) == f"staging/runs/{RUN}/inputs/"
    assert run_input_manifest_key(RUN, "b1") == f"staging/runs/{RUN}/inputs/b1/manifest.json"
    assert (
        run_input_asset_key(RUN, "b1", "item-1", "OR_ABI.nc")
        == f"staging/runs/{RUN}/inputs/b1/item-1/OR_ABI.nc"
    )


def test_input_asset_key_sanitizes_the_filename_and_refuses_bad_ids():
    assert run_input_asset_key(RUN, "b1", "i", "../x y.nc").endswith("/i/x_y.nc")
    with pytest.raises(InvalidKeySegment):
        run_input_asset_key(RUN, "b1", "a/b", "f.nc")
    with pytest.raises(InvalidKeySegment):
        run_input_manifest_key(RUN, "b 1")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_executor.py -k input_keys -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement** (append to `keys.py` after `run_staging_prefix`)

```python
INPUTS_SEGMENT = "inputs"


def run_inputs_prefix(run_id: str) -> str:
    """``staging/runs/{run_id}/inputs/`` — where the PLATFORM stages what a run
    consumes (GOES spec §3). Inside the run prefix so the run's own credentials
    can read it and the TTL sweep ages it out; finalize skips it."""
    return f"{run_staging_prefix(run_id)}{INPUTS_SEGMENT}/"


def run_input_manifest_key(run_id: str, batch_id: str) -> str:
    """``staging/runs/{run_id}/inputs/{batch_id}/manifest.json``."""
    if not _SAFE_IDENTITY.match(batch_id):
        raise InvalidKeySegment(f"batch id is not a safe path segment: {batch_id!r}")
    return f"{run_inputs_prefix(run_id)}{batch_id}/manifest.json"


def run_input_asset_key(run_id: str, batch_id: str, item_id: str, filename: str) -> str:
    """``staging/runs/{run_id}/inputs/{batch_id}/{item_id}/{filename}`` — a
    remote asset staged for the run. The item id keeps same-named files of
    different items apart; the filename is sanitized like every other key."""
    if not _SAFE_IDENTITY.match(batch_id):
        raise InvalidKeySegment(f"batch id is not a safe path segment: {batch_id!r}")
    item_id = _safe_segment(item_id, field="item_id")
    return f"{run_inputs_prefix(run_id)}{batch_id}/{item_id}/{sanitize_filename(filename)}"
```

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_executor.py -k input -v && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/storage/keys.py services/pipeline/tests/test_process_executor.py
git commit -m "feat(storage): key builders for a run's inputs area (G-2)"
```

---

### Task 3: Session policy read grants for source collections

**Files:**
- Modify: `services/pipeline/src/pipeline/process/credentials.py` (`session_policy`, `mint_run_credentials`)
- Test: `services/pipeline/tests/test_process_executor.py`

**Interfaces:**
- Produces: `session_policy(bucket, prefix, read_prefixes: Sequence[str] = ()) -> dict`; `mint_run_credentials(settings, run_id, timeout_seconds, *, sts_client=None, read_prefixes: Sequence[str] = ())`. `read_prefixes` are full key prefixes such as `assets/goes19-abi/`.
- Constant `MAX_READ_PREFIXES = 8` — more raises `RunCredentialsError` (real STS caps inline policies at 2048 chars; spec §14).

- [ ] **Step 1: Write the failing tests**

```python
def test_session_policy_grants_read_only_on_source_collection_prefixes():
    policy = session_policy("stac-higher", f"staging/runs/{RUN}/", ["assets/goes/", "assets/other/"])
    sids = {s["Sid"]: s for s in policy["Statement"]}
    read = sids["SourceCollectionsRead"]
    assert read["Action"] == ["s3:GetObject"]
    assert read["Resource"] == [
        "arn:aws:s3:::stac-higher/assets/goes/*",
        "arn:aws:s3:::stac-higher/assets/other/*",
    ]
    # The bucket-level ListBucket condition now names the read prefixes too.
    prefixes = sids["RunPrefixList"]["Condition"]["StringLike"]["s3:prefix"]
    assert f"staging/runs/{RUN}/*" in prefixes
    assert "assets/goes/*" in prefixes and "assets/other/*" in prefixes
    # Nothing grants Put/Delete outside the run prefix.
    for s in policy["Statement"]:
        if s["Sid"] != "RunPrefixObjects":
            assert "s3:PutObject" not in s["Action"] and "s3:DeleteObject" not in s["Action"]


def test_session_policy_without_read_prefixes_is_unchanged():
    policy = session_policy("b", "staging/runs/x/")
    assert [s["Sid"] for s in policy["Statement"]] == ["RunPrefixObjects", "RunPrefixList"]


def test_too_many_read_prefixes_refuses_to_mint():
    class Sts:
        def assume_role(self, **kw):  # pragma: no cover - never reached
            raise AssertionError("must not be called")

    with pytest.raises(RunCredentialsError, match="read prefixes"):
        mint_run_credentials(
            settings(), RUN, 60, sts_client=Sts(), read_prefixes=[f"assets/c{i}/" for i in range(9)]
        )


def test_mint_passes_read_prefixes_into_the_policy():
    seen = {}

    class Sts:
        def assume_role(self, **kw):
            seen.update(kw)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "s", "SessionToken": "t"}}

    mint_run_credentials(settings(), RUN, 60, sts_client=Sts(), read_prefixes=["assets/goes/"])
    assert "assets/goes/*" in seen["Policy"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_executor.py -k "session_policy or read_prefixes" -v`
Expected: FAIL (`TypeError: unexpected keyword argument`).

- [ ] **Step 3: Implement**

In `credentials.py`:

```python
from collections.abc import Sequence

#: Real STS caps an inline session policy at 2048 characters; each read
#: prefix costs two ARN-ish strings. Eight keeps a worst-case policy under the
#: cap with room for long collection ids (GOES spec §14).
MAX_READ_PREFIXES = 8


def session_policy(bucket: str, prefix: str, read_prefixes: Sequence[str] = ()) -> dict:
    """The inline policy bounding one run.

    Object actions are scoped to the run's prefix. ``read_prefixes`` — the
    canonical ``assets/{collection}/`` prefixes of the run's SOURCE collections
    (GOES spec §3.2) — get ``s3:GetObject`` only. ListBucket is granted on the
    BUCKET resource — that is where S3 evaluates it — conditioned on the run
    prefix plus the read prefixes, so a run can enumerate its own outputs and
    its inputs without discovering that anything else exists.
    """
    list_prefixes = [f"{prefix}*", *(f"{p}*" for p in read_prefixes)]
    statements = [
        {
            "Sid": "RunPrefixObjects",
            "Effect": "Allow",
            "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
            "Resource": [f"arn:aws:s3:::{bucket}/{prefix}*"],
        },
        {
            "Sid": "RunPrefixList",
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::{bucket}"],
            "Condition": {"StringLike": {"s3:prefix": list_prefixes}},
        },
    ]
    if read_prefixes:
        statements.append(
            {
                "Sid": "SourceCollectionsRead",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/{p}*" for p in read_prefixes],
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}
```

In `mint_run_credentials`, add the keyword `read_prefixes: Sequence[str] = ()` and, before building the client:

```python
    if len(read_prefixes) > MAX_READ_PREFIXES:
        raise RunCredentialsError(
            f"run would need {len(read_prefixes)} read prefixes; the inline session "
            f"policy supports at most {MAX_READ_PREFIXES} source collections"
        )
```

and pass `Policy=json.dumps(session_policy(bucket, prefix, read_prefixes))`.

Update the module docstring: the run "can read its own prefix, read the canonical prefixes of its source collections (read-only), and nothing else".

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_executor.py -v && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/process/credentials.py services/pipeline/tests/test_process_executor.py
git commit -m "feat(process): read-only session-policy grants for source collections (G-2)"
```

---

### Task 4: The input planner (pure) and the manifest contract

**Files:**
- Create: `services/pipeline/src/pipeline/process/inputs.py`
- Create: `services/pipeline/tests/test_process_inputs.py`
- Create: `tests/contract-fixtures/process-input-manifest.json`
- Modify: `tests/contract-fixtures/README.md`
- Modify: `services/pipeline/tests/test_contract_fixtures.py`

**Interfaces:**
- Produces (all in `pipeline.process.inputs`):

```python
MANIFEST_VERSION = 1
KIND_TRANSFORM = "transform"

@dataclass(frozen=True)
class InputAsset:
    bucket: str
    key: str
    staged: bool
    href: str

@dataclass(frozen=True)
class InputItem:
    collection: str
    op: str                      # "insert" | "update" | "unknown"
    item: dict[str, Any]
    assets: dict[str, InputAsset]

@dataclass(frozen=True)
class RemoteFetch:
    href: str
    key: str
    item_id: str
    asset_key: str

@dataclass(frozen=True)
class InputPlan:
    manifest: dict[str, Any]     # exactly what is written to manifest.json
    manifest_key: str
    fetches: tuple[RemoteFetch, ...]
    read_prefixes: tuple[str, ...]

class InputPlanError(Exception): ...

def parse_canonical_href(href: object, base: str = "/api/assets") -> tuple[str, str, str] | None
def plan_inputs(*, run_id, process_id, batch_id, kind, refs, documents, source_collections, bucket, asset_href_base) -> InputPlan
def input_env(run_id: str, manifest_key: str) -> dict[str, str]
```

  - `refs`: the run's `input_items` (`[{"item_id", "collection_id"?, "op"?}]`).
  - `documents`: `dict[(collection_id, item_id) -> item dict]` already fetched.
  - `source_collections`: the process's `process_sources.collection_id` values; used both for the read grants and as the fallback collection for a legacy ref with no `collection_id` (only when there is exactly one source collection; otherwise `InputPlanError`).
  - A ref whose document is missing (item deleted between trigger and run) is **skipped and recorded** under `manifest["skipped"]` as `{"item_id", "collection", "reason": "not_found"}`; it is not an error.
  - `input_env` → `{"STAC_HIGHER_INPUT_PREFIX": run_inputs_prefix(run_id), "STAC_HIGHER_INPUT_MANIFEST": manifest_key}`.

- [ ] **Step 1: Write the fixture** (`tests/contract-fixtures/process-input-manifest.json`)

```json
{
  "style": "producer-golden",
  "$comment": "The manifest the pipeline stages at staging/runs/{run_id}/inputs/{batch_id}/manifest.json before a process run starts (GOES spec §3.1). Producer-golden: the PIPELINE is the only writer (pipeline/process/inputs.py) and user code inside the run is the reader, so only the pytest suite consumes this file — it asserts plan_inputs() produces exactly `expected` from `given`. Version bumps are additive: a reader must ignore unknown keys.",
  "version": 1,
  "given": {
    "run_id": "11111111-1111-4111-8111-111111111111",
    "process_id": "22222222-2222-4222-8222-222222222222",
    "batch_id": "b1",
    "bucket": "stac-higher",
    "asset_href_base": "/api/assets",
    "source_collections": ["goes19-abi-l2-mcmipc", "cloud-masks"],
    "refs": [
      { "item_id": "OR_ABI-L2-MCMIPC_M6_G19_s20262431201", "collection_id": "goes19-abi-l2-mcmipc", "op": "insert" },
      { "item_id": "mask-7", "collection_id": "cloud-masks", "op": "update" },
      { "item_id": "gone", "collection_id": "cloud-masks", "op": "insert" }
    ],
    "documents": {
      "goes19-abi-l2-mcmipc/OR_ABI-L2-MCMIPC_M6_G19_s20262431201": {
        "type": "Feature", "stac_version": "1.0.0",
        "id": "OR_ABI-L2-MCMIPC_M6_G19_s20262431201", "collection": "goes19-abi-l2-mcmipc",
        "geometry": null, "bbox": null, "properties": { "datetime": "2026-08-31T12:01:00Z" }, "links": [],
        "assets": { "data": { "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/2026/243/12/OR_ABI-L2-MCMIPC_M6_G19_s20262431201.nc" } }
      },
      "cloud-masks/mask-7": {
        "type": "Feature", "stac_version": "1.0.0", "id": "mask-7", "collection": "cloud-masks",
        "geometry": null, "bbox": null, "properties": { "datetime": "2026-08-31T00:00:00Z" }, "links": [],
        "assets": {
          "mask": { "href": "/api/assets/cloud-masks/mask-7/mask.tif", "type": "image/tiff" },
          "thumb": { "href": "/api/assets/cloud-masks/mask-7/thumb%20nail.png" }
        }
      }
    }
  },
  "expected": {
    "manifest_key": "staging/runs/11111111-1111-4111-8111-111111111111/inputs/b1/manifest.json",
    "read_prefixes": ["assets/cloud-masks/", "assets/goes19-abi-l2-mcmipc/"],
    "fetches": [
      {
        "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/2026/243/12/OR_ABI-L2-MCMIPC_M6_G19_s20262431201.nc",
        "key": "staging/runs/11111111-1111-4111-8111-111111111111/inputs/b1/OR_ABI-L2-MCMIPC_M6_G19_s20262431201/OR_ABI-L2-MCMIPC_M6_G19_s20262431201.nc",
        "item_id": "OR_ABI-L2-MCMIPC_M6_G19_s20262431201",
        "asset_key": "data"
      }
    ],
    "manifest": {
      "version": 1,
      "run_id": "11111111-1111-4111-8111-111111111111",
      "process_id": "22222222-2222-4222-8222-222222222222",
      "batch_id": "b1",
      "kind": "transform",
      "items": [
        {
          "collection": "goes19-abi-l2-mcmipc",
          "op": "insert",
          "item": { "$ref": "given.documents.goes19-abi-l2-mcmipc/OR_ABI-L2-MCMIPC_M6_G19_s20262431201" },
          "assets": {
            "data": {
              "bucket": "stac-higher",
              "key": "staging/runs/11111111-1111-4111-8111-111111111111/inputs/b1/OR_ABI-L2-MCMIPC_M6_G19_s20262431201/OR_ABI-L2-MCMIPC_M6_G19_s20262431201.nc",
              "staged": true,
              "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/2026/243/12/OR_ABI-L2-MCMIPC_M6_G19_s20262431201.nc"
            }
          }
        },
        {
          "collection": "cloud-masks",
          "op": "update",
          "item": { "$ref": "given.documents.cloud-masks/mask-7" },
          "assets": {
            "mask": { "bucket": "stac-higher", "key": "assets/cloud-masks/mask-7/mask.tif", "staged": false, "href": "/api/assets/cloud-masks/mask-7/mask.tif" },
            "thumb": { "bucket": "stac-higher", "key": "assets/cloud-masks/mask-7/thumb nail.png", "staged": false, "href": "/api/assets/cloud-masks/mask-7/thumb%20nail.png" }
          }
        }
      ],
      "skipped": [ { "item_id": "gone", "collection": "cloud-masks", "reason": "not_found" } ]
    }
  }
}
```

Add to the fixtures README a `### producer-golden — process-input-manifest.json` subsection: what it is, that only pytest consumes it, the `$ref` convention (an `item` in `expected` is the `given.documents` entry it names, substituted by the test), and that `read_prefixes` are sorted.

- [ ] **Step 2: Write the failing tests** (`services/pipeline/tests/test_process_inputs.py`)

```python
"""The input planner (GOES spec §3): pure, no I/O."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pipeline.process.inputs import (
    InputPlanError,
    input_env,
    parse_canonical_href,
    plan_inputs,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures" / "process-input-manifest.json").read_text()
)


def _documents(given):
    return {tuple(k.split("/", 1)): v for k, v in given["documents"].items()}


def _resolve_refs(expected_manifest, given):
    out = copy.deepcopy(expected_manifest)
    for entry in out["items"]:
        ref = entry["item"]["$ref"].removeprefix("given.documents.")
        entry["item"] = given["documents"][ref]
    return out


def test_plan_matches_the_golden_fixture():
    given, expected = FIXTURE["given"], FIXTURE["expected"]
    plan = plan_inputs(
        run_id=given["run_id"],
        process_id=given["process_id"],
        batch_id=given["batch_id"],
        kind="transform",
        refs=given["refs"],
        documents=_documents(given),
        source_collections=given["source_collections"],
        bucket=given["bucket"],
        asset_href_base=given["asset_href_base"],
    )
    assert plan.manifest_key == expected["manifest_key"]
    assert list(plan.read_prefixes) == expected["read_prefixes"]
    assert [f.__dict__ for f in plan.fetches] == expected["fetches"]
    assert plan.manifest == _resolve_refs(expected["manifest"], given)


@pytest.mark.parametrize(
    "href,parsed",
    [
        ("/api/assets/c/i/f.tif", ("c", "i", "f.tif")),
        ("/api/assets/c/i/f%20g.tif", ("c", "i", "f g.tif")),
        ("https://x/y.tif", None),
        ("/api/assets/c/i", None),
        ("/api/assets/c/../f", None),
        (None, None),
    ],
)
def test_parse_canonical_href(href, parsed):
    assert parse_canonical_href(href) == parsed


def test_legacy_ref_without_collection_uses_the_single_source_collection():
    doc = {"id": "i", "collection": "only", "assets": {}}
    plan = plan_inputs(
        run_id=FIXTURE["given"]["run_id"], process_id="p", batch_id="b", kind="transform",
        refs=[{"item_id": "i"}], documents={("only", "i"): doc},
        source_collections=["only"], bucket="b", asset_href_base="/api/assets",
    )
    assert plan.manifest["items"][0]["collection"] == "only"
    assert plan.manifest["items"][0]["op"] == "unknown"


def test_legacy_ref_is_ambiguous_with_two_source_collections():
    with pytest.raises(InputPlanError, match="collection"):
        plan_inputs(
            run_id=FIXTURE["given"]["run_id"], process_id="p", batch_id="b", kind="transform",
            refs=[{"item_id": "i"}], documents={}, source_collections=["a", "b"],
            bucket="b", asset_href_base="/api/assets",
        )


def test_asset_without_a_usable_href_is_omitted_from_assets_but_item_kept():
    doc = {"id": "i", "collection": "c", "assets": {"odd": {"title": "no href"}, "rel": {"href": "a/b.tif"}}}
    plan = plan_inputs(
        run_id=FIXTURE["given"]["run_id"], process_id="p", batch_id="b", kind="transform",
        refs=[{"item_id": "i", "collection_id": "c", "op": "insert"}], documents={("c", "i"): doc},
        source_collections=["c"], bucket="b", asset_href_base="/api/assets",
    )
    assert plan.manifest["items"][0]["assets"] == {}
    assert plan.fetches == ()


def test_input_env_names_prefix_and_manifest():
    env = input_env(FIXTURE["given"]["run_id"], "staging/runs/x/inputs/b/manifest.json")
    assert env == {
        "STAC_HIGHER_INPUT_PREFIX": f"staging/runs/{FIXTURE['given']['run_id']}/inputs/",
        "STAC_HIGHER_INPUT_MANIFEST": "staging/runs/x/inputs/b/manifest.json",
    }
```

Also append to `services/pipeline/tests/test_contract_fixtures.py` a one-liner that loads `process-input-manifest.json` and asserts `fixture["style"] == "producer-golden"` and `fixture["version"] == 1` (the real assertion lives in `test_process_inputs.py`; this keeps the fixture registered where the README says every fixture is loaded).

- [ ] **Step 3: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_inputs.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 4: Implement** (`services/pipeline/src/pipeline/process/inputs.py`)

```python
"""Plan what a run receives (GOES spec §3): the manifest, the remote fetches,
and the read grants — computed purely so it can be tested without storage.

Two kinds of asset location, decided per asset:

- a canonical ``/api/assets/{c}/{i}/{f}`` href → the object already sits at
  ``assets/{c}/{i}/{f}`` in the platform bucket. No copy; the run's session
  policy is granted read on each SOURCE collection's prefix (§3.2).
- any other absolute href → fetched by the launcher into the run's inputs
  area (``RemoteFetch``); the manifest points at the staged copy.

Anything else (a relative href, no href) is not a location the run can use
and is omitted from ``assets`` — the item itself is still delivered, because
its metadata may be the point.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote

from pipeline.storage.keys import (
    CANONICAL_PREFIX,
    canonical_asset_key,
    run_input_asset_key,
    run_input_manifest_key,
    run_inputs_prefix,
)

MANIFEST_VERSION = 1
KIND_TRANSFORM = "transform"
OP_UNKNOWN = "unknown"
SKIP_NOT_FOUND = "not_found"


class InputPlanError(Exception):
    """The run's inputs cannot be described — a contract problem, not I/O."""


@dataclass(frozen=True)
class InputAsset:
    bucket: str
    key: str
    staged: bool
    href: str


@dataclass(frozen=True)
class RemoteFetch:
    href: str
    key: str
    item_id: str
    asset_key: str


@dataclass(frozen=True)
class InputPlan:
    manifest: dict[str, Any]
    manifest_key: str
    fetches: tuple[RemoteFetch, ...]
    read_prefixes: tuple[str, ...]


def parse_canonical_href(href: object, base: str = "/api/assets") -> tuple[str, str, str] | None:
    """``/api/assets/{c}/{i}/{f}`` → (c, i, f), URL-decoded; else None."""
    if not isinstance(href, str):
        return None
    prefix = base.rstrip("/") + "/"
    if not href.startswith(prefix):
        return None
    parts = href[len(prefix):].split("?", 1)[0].split("/")
    if len(parts) != 3:
        return None
    segs = [unquote(p) for p in parts]
    if any(not s or s in (".", "..") or "/" in s or "\\" in s for s in segs):
        return None
    return segs[0], segs[1], segs[2]


def _is_absolute(href: object) -> bool:
    return isinstance(href, str) and "://" in href


def _filename_from_href(href: str) -> str:
    tail = href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return unquote(tail) or "asset"


def input_env(run_id: str, manifest_key: str) -> dict[str, str]:
    return {
        "STAC_HIGHER_INPUT_PREFIX": run_inputs_prefix(run_id),
        "STAC_HIGHER_INPUT_MANIFEST": manifest_key,
    }


def plan_inputs(
    *,
    run_id: str,
    process_id: str,
    batch_id: str,
    kind: str,
    refs: Sequence[Mapping[str, Any]],
    documents: Mapping[tuple[str, str], dict[str, Any]],
    source_collections: Sequence[str],
    bucket: str,
    asset_href_base: str,
) -> InputPlan:
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    fetches: list[RemoteFetch] = []

    for ref in refs:
        item_id = str(ref.get("item_id") or "")
        if not item_id:
            raise InputPlanError("input ref has no item_id")
        collection = ref.get("collection_id")
        if not collection:
            if len(source_collections) != 1:
                raise InputPlanError(
                    f"input ref {item_id!r} names no collection and the process has "
                    f"{len(source_collections)} source collections"
                )
            collection = source_collections[0]
        collection = str(collection)
        op = str(ref.get("op") or OP_UNKNOWN)

        document = documents.get((collection, item_id))
        if document is None:
            skipped.append({"item_id": item_id, "collection": collection, "reason": SKIP_NOT_FOUND})
            continue

        assets: dict[str, dict[str, Any]] = {}
        for asset_key, entry in (document.get("assets") or {}).items():
            href = (entry or {}).get("href") if isinstance(entry, dict) else None
            canonical = parse_canonical_href(href, asset_href_base)
            if canonical is not None:
                c, i, f = canonical
                assets[asset_key] = asdict(
                    InputAsset(bucket=bucket, key=canonical_asset_key(c, i, f), staged=False, href=href)
                )
                continue
            if _is_absolute(href):
                key = run_input_asset_key(run_id, batch_id, item_id, _filename_from_href(href))
                fetches.append(RemoteFetch(href=href, key=key, item_id=item_id, asset_key=asset_key))
                assets[asset_key] = asdict(InputAsset(bucket=bucket, key=key, staged=True, href=href))
            # relative / missing href: not a location the run can use — omitted.

        items.append({"collection": collection, "op": op, "item": document, "assets": assets})

    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "run_id": run_id,
        "process_id": process_id,
        "batch_id": batch_id,
        "kind": kind,
        "items": items,
    }
    if skipped:
        manifest["skipped"] = skipped

    read_prefixes = tuple(sorted(f"{CANONICAL_PREFIX}/{c}/" for c in set(source_collections)))
    return InputPlan(
        manifest=manifest,
        manifest_key=run_input_manifest_key(run_id, batch_id),
        fetches=tuple(fetches),
        read_prefixes=read_prefixes,
    )
```

Note: `canonical_asset_key` validates segments and raises `InvalidKeySegment` for a hostile href; `parse_canonical_href` already filtered those, so a raise here is a bug, not an input condition.

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_inputs.py tests/test_contract_fixtures.py -v && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/process/inputs.py services/pipeline/tests/test_process_inputs.py tests/contract-fixtures/process-input-manifest.json tests/contract-fixtures/README.md services/pipeline/tests/test_contract_fixtures.py
git commit -m "feat(process): pure input planner + producer-golden manifest fixture (G-2)"
```

---

### Task 5: Repo methods — item documents and source collections

**Files:**
- Modify: `services/pipeline/src/pipeline/process/repo.py` (abstract + `PgProcessRepo`)
- Modify: `services/pipeline/tests/_process_fake.py`

**Interfaces:**
- Produces on `ProcessRepo`:
  - `async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None` — `SELECT pgstac.get_item(%s, %s)` with `(item_id, collection_id)` (same as `dispatcher/repo.py:209-216`).
  - `async def list_source_collections(self, process_id: str) -> tuple[str, ...]` — `SELECT DISTINCT collection_id FROM stac_higher.process_sources WHERE process_id = %s ORDER BY collection_id` (mirror `list_output_collections`).
- `FakeProcessRepo` gains `items: dict[tuple[str, str], dict]` and `source_collections: list[str]` fields backing the two methods.

- [ ] **Step 1: Add the abstract methods and Pg implementations** (copy the shape of `list_output_collections` at `repo.py:435-446` and `dispatcher/repo.py`'s `get_item`).

- [ ] **Step 2: Extend the fake**

```python
    items: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    source_collections: list[str] = field(default_factory=list)

    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        return self.items.get((collection_id, item_id))

    async def list_source_collections(self, process_id: str) -> tuple[str, ...]:
        return tuple(sorted(set(self.source_collections)))
```

- [ ] **Step 3: Run the whole pipeline suite** (abstract additions break any other `ProcessRepo` subclass — grep `ProcessRepo)` in tests and add the two methods there too).

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`

- [ ] **Step 4: Commit**

```bash
git add services/pipeline/src/pipeline/process/repo.py services/pipeline/tests/_process_fake.py
git commit -m "feat(process): repo reads for item documents and source collections (G-2)"
```

---

### Task 6: Remote fetch and staging

**Files:**
- Create: `services/pipeline/src/pipeline/process/staging.py`
- Create: `services/pipeline/src/pipeline/connections/http_fetch.py`
- Create: `services/pipeline/tests/test_process_staging.py`

**Interfaces:**
- `pipeline.connections.http_fetch.fetch_public_url(url: str, allow_hosts: frozenset[str] = frozenset(), *, timeout: float = 60.0, max_bytes: int = 2 * 1024**3) -> bytes` — HTTPS only, host vetted by `resolve_pinned` (same rule as adapters; `EgressBlocked` propagates), dials the pinned IP with `Host`/SNI preserved, refuses redirects to other hosts, raises `PublicFetchError` on non-200 or size overflow.
- `pipeline.process.staging`:

```python
class InputStagingError(Exception): ...   # names item_id + asset_key + href in its message

RemoteFetcher = Callable[[str], Awaitable[bytes]]   # href -> bytes

async def stage_inputs(plan: InputPlan, *, storage_client, bucket: str, fetch_remote: RemoteFetcher, concurrency: int = 4) -> None
    # gathers plan.fetches with a semaphore, put_object each; writes the manifest LAST
    # (content_type application/json). Any failure -> InputStagingError (chained).

def build_remote_fetcher(settings: Settings, master_key: bytes | None) -> RemoteFetcher
    # returns an async callable: for a href, load enabled reference-mode ingest
    # associations (PgIngestRepo.list_enabled_ingest_associations, filtered to
    # config.storage_mode == "reference"); for each, build_adapter and check
    # href.startswith(adapter.public_object_url("")); the first match fetches
    # via adapter.get(relative_path). No match -> fetch_public_url(href,
    # settings.egress_allow_hosts). master_key None -> public-only.
```

- [ ] **Step 1: Write the failing tests**

```python
"""Staging a run's remote inputs (GOES spec §3.2)."""

from __future__ import annotations

import json

import pytest

from pipeline.process.inputs import InputPlan, RemoteFetch
from pipeline.process.staging import InputStagingError, stage_inputs

RUN = "11111111-1111-4111-8111-111111111111"


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.order: list[str] = []

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
        self.order.append(Key)


def _plan(fetches):
    return InputPlan(
        manifest={"version": 1, "items": []},
        manifest_key=f"staging/runs/{RUN}/inputs/b/manifest.json",
        fetches=tuple(fetches),
        read_prefixes=(),
    )


async def test_stages_remote_bytes_then_writes_the_manifest_last():
    s3 = FakeS3()
    fetched: list[str] = []

    async def fetch(href: str) -> bytes:
        fetched.append(href)
        return b"nc-bytes"

    plan = _plan([RemoteFetch("https://h/x.nc", f"staging/runs/{RUN}/inputs/b/i/x.nc", "i", "data")])
    await stage_inputs(plan, storage_client=s3, bucket="stac-higher", fetch_remote=fetch)

    assert fetched == ["https://h/x.nc"]
    assert s3.objects[f"staging/runs/{RUN}/inputs/b/i/x.nc"] == b"nc-bytes"
    assert json.loads(s3.objects[plan.manifest_key]) == plan.manifest
    assert s3.order[-1] == plan.manifest_key


async def test_a_failed_fetch_is_an_input_staging_error_naming_the_asset():
    s3 = FakeS3()

    async def fetch(href: str) -> bytes:
        raise OSError("boom")

    plan = _plan([RemoteFetch("https://h/x.nc", f"staging/runs/{RUN}/inputs/b/i/x.nc", "i", "data")])
    with pytest.raises(InputStagingError, match=r"item 'i' asset 'data'"):
        await stage_inputs(plan, storage_client=s3, bucket="b", fetch_remote=fetch)
    assert plan.manifest_key not in s3.objects


async def test_no_fetches_still_writes_the_manifest():
    s3 = FakeS3()

    async def fetch(href: str) -> bytes:  # pragma: no cover
        raise AssertionError("not called")

    plan = _plan([])
    await stage_inputs(plan, storage_client=s3, bucket="b", fetch_remote=fetch)
    assert plan.manifest_key in s3.objects
```

And for `http_fetch` (in the same file):

```python
from pipeline.connections import http_fetch
from pipeline.connections.egress import EgressBlocked


def test_fetch_public_url_refuses_plain_http_and_blocked_hosts(monkeypatch):
    with pytest.raises(http_fetch.PublicFetchError, match="https"):
        http_fetch.fetch_public_url("http://example.com/x")

    def blocked(host, allow_hosts=()):
        raise EgressBlocked("nope")

    monkeypatch.setattr(http_fetch, "resolve_pinned", blocked)
    with pytest.raises(EgressBlocked):
        http_fetch.fetch_public_url("https://169.254.169.254/latest")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_staging.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `http_fetch.py`**

```python
"""Egress-checked GET of a public HTTPS URL (GOES spec §3.2 — the fallback for
staging a remote input asset when no reference-mode association's adapter
claims its href).

Same posture as the adapters: the host goes through ``resolve_pinned`` first
and the socket dials the validated IP, so a DNS rebind between check and
connect cannot reach an internal address. HTTPS only — SNI/certificate
validation is kept by passing the hostname to the TLS layer while connecting
to the pinned IP.
"""

from __future__ import annotations

import http.client
import ssl
from urllib.parse import urlsplit

from pipeline.connections.egress import resolve_pinned

DEFAULT_MAX_BYTES = 2 * 1024**3


class PublicFetchError(Exception):
    """The URL could not be fetched (scheme, status, redirect, size)."""


def fetch_public_url(
    url: str,
    allow_hosts: frozenset[str] = frozenset(),
    *,
    timeout: float = 60.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise PublicFetchError(f"only https URLs can be staged: {url!r}")
    host = parts.hostname
    port = parts.port or 443
    pinned = resolve_pinned(host, allow_hosts)  # raises EgressBlocked
    dial = pinned[0] if pinned else host

    context = ssl.create_default_context()
    conn = http.client.HTTPSConnection(dial, port, timeout=timeout, context=context)
    # server_hostname for SNI + cert validation stays the real host.
    conn._context = context  # noqa: SLF001 - keep default validation
    conn.host = host if pinned else conn.host  # Host header
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    try:
        conn.connect()
        if pinned:
            # Re-wrap with the right SNI when dialling an IP literal.
            conn.sock = context.wrap_socket(conn.sock.unwrap() if hasattr(conn.sock, "unwrap") else conn.sock, server_hostname=host)  # pragma: no cover - exercised live
        conn.request("GET", path, headers={"Host": host, "User-Agent": "stac-higher-pipeline"})
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308):
            raise PublicFetchError(f"redirects are not followed when staging inputs: {url!r}")
        if resp.status != 200:
            raise PublicFetchError(f"GET {url!r} returned {resp.status}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise PublicFetchError(f"{url!r} exceeds the {max_bytes}-byte staging limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        conn.close()
```

Implementer note: the pinned-IP + SNI re-wrap is fiddly in `http.client`; if it fights you, the acceptable simplification is: when `pinned` is non-empty, connect by **hostname** (`HTTPSConnection(host, …)`) after `resolve_pinned` has validated it — the same narrow rebind window `S3Adapter._pinned_endpoint` documents for https endpoints ("keep the hostname so TLS validates; resolve_pinned already rejected internal addresses"). Do that, delete the re-wrap lines, and say so in the docstring. Keep the scheme check, the redirect refusal, the status check and the size cap.

- [ ] **Step 4: Implement `staging.py`**

```python
"""Stage a run's inputs before launch (GOES spec §3.2)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from pipeline.config import Settings
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.connections.http_fetch import fetch_public_url
from pipeline.ingest.repo import PgIngestRepo
from pipeline.process.inputs import InputPlan
from pipeline.storage import platform

logger = logging.getLogger(__name__)

RemoteFetcher = Callable[[str], Awaitable[bytes]]


class InputStagingError(Exception):
    """An input could not be staged — the run must not start."""


async def stage_inputs(
    plan: InputPlan,
    *,
    storage_client,
    bucket: str,
    fetch_remote: RemoteFetcher,
    concurrency: int = 4,
) -> None:
    """Fetch every remote input into the run's inputs area, then write the
    manifest LAST so a partially staged batch never looks complete."""
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(fetch):
        async with gate:
            try:
                data = await fetch_remote(fetch.href)
                await asyncio.to_thread(platform.put_object, storage_client, bucket, fetch.key, data)
            except Exception as err:
                raise InputStagingError(
                    f"could not stage item {fetch.item_id!r} asset {fetch.asset_key!r} "
                    f"from {fetch.href!r}: {type(err).__name__}: {err}"
                ) from err

    await asyncio.gather(*(one(f) for f in plan.fetches))
    try:
        await asyncio.to_thread(
            platform.put_object,
            storage_client,
            bucket,
            plan.manifest_key,
            json.dumps(plan.manifest, separators=(",", ":")).encode(),
            content_type="application/json",
        )
    except Exception as err:
        raise InputStagingError(f"could not write the input manifest: {type(err).__name__}") from err


def build_remote_fetcher(settings: Settings, master_key: bytes | None) -> RemoteFetcher:
    """Prefer the reference-mode association whose connection owns the href
    (credentials/anonymity + egress policy come with it); else a public GET."""
    repo = PgIngestRepo(settings.database_url)

    async def fetch(href: str) -> bytes:
        if master_key is not None:
            for assoc in await repo.list_enabled_ingest_associations():
                if (assoc.config or {}).get("storage_mode") != "reference":
                    continue
                try:
                    adapter = build_adapter(assoc.connection, master_key, settings.egress_allow_hosts)
                except AdapterBuildError:
                    continue
                base = adapter.public_object_url("")
                if href.startswith(base):
                    return await adapter.get(href[len(base):])
        return await asyncio.to_thread(fetch_public_url, href, settings.egress_allow_hosts)

    return fetch
```

Check `public_object_url("")` on `S3Adapter` yields the base with a trailing slash (`.../bucket/` or `https://bucket.s3.region.amazonaws.com/`) — the existing tests at `test_adapters.py:167-191` show the two forms; add one assertion there for the empty-path case if none exists.

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_staging.py tests/test_adapters.py -v && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/process/staging.py services/pipeline/src/pipeline/connections/http_fetch.py services/pipeline/tests/test_process_staging.py services/pipeline/tests/test_adapters.py
git commit -m "feat(process): stage remote inputs via association adapter or egress-checked public GET (G-2)"
```

---

### Task 7: Network profile in the Python runtime + deployment cap

**Files:**
- Modify: `services/pipeline/src/pipeline/process/config.py` (`ProcessRuntime`, `parse_process_runtime`)
- Modify: `services/pipeline/src/pipeline/config.py` (`Settings.process_network_max`, env `PROCESS_NETWORK_MAX`)
- Modify: `services/pipeline/src/pipeline/process/launch.py` (`NetworkCapExceeded`, `check_network_cap`)
- Test: `services/pipeline/tests/test_process_executor.py`, `services/pipeline/tests/test_contract_fixtures.py` (defaults assertion picks up the new field automatically if it iterates `defaults`)

**Interfaces:**
- `NETWORK_LEVELS = ("isolated", "inputs", "hosts", "open")` (ordered, lowest first) in `process/config.py`.
- `ProcessRuntime.network_level: str = "isolated"`, `ProcessRuntime.network_hosts: tuple[str, ...] = ()`.
- Parser rules: `network` absent → defaults; `level` must be in `NETWORK_LEVELS`; `hosts` must be a list of non-blank strings without `/`, `:` or whitespace; `hosts` non-empty ⇔ `level == "hosts"` (else `ProcessConfigError`).
- `Settings.process_network_max: str = "isolated"` from `PROCESS_NETWORK_MAX` (validated against `NETWORK_LEVELS` at `from_env`; invalid → `ValueError`).
- `launch.check_network_cap(runtime, settings)` raises `NetworkCapExceeded(f"revision requests network level {level!r} but this deployment allows at most {cap!r} (PROCESS_NETWORK_MAX)")`.

- [ ] **Step 1: Write the failing tests**

```python
from pipeline.process.config import NETWORK_LEVELS, ProcessConfigError, parse_process_runtime
from pipeline.process.launch import NetworkCapExceeded, check_network_cap


def test_runtime_network_defaults_to_isolated():
    rt = parse_process_runtime({"kind": "inline_python"})
    assert rt.network_level == "isolated" and rt.network_hosts == ()


def test_runtime_network_hosts_level_requires_hosts_and_vice_versa():
    rt = parse_process_runtime({"kind": "inline_python", "network": {"level": "hosts", "hosts": ["api.example.com"]}})
    assert rt.network_hosts == ("api.example.com",)
    with pytest.raises(ProcessConfigError):
        parse_process_runtime({"kind": "inline_python", "network": {"level": "hosts", "hosts": []}})
    with pytest.raises(ProcessConfigError):
        parse_process_runtime({"kind": "inline_python", "network": {"level": "open", "hosts": ["x.y"]}})
    with pytest.raises(ProcessConfigError):
        parse_process_runtime({"kind": "inline_python", "network": {"level": "lan"}})
    with pytest.raises(ProcessConfigError):
        parse_process_runtime({"kind": "inline_python", "network": {"level": "hosts", "hosts": ["https://x.y"]}})


def test_network_levels_are_ordered_lowest_first():
    assert NETWORK_LEVELS == ("isolated", "inputs", "hosts", "open")


def test_network_cap_refuses_a_level_above_the_deployment_maximum():
    rt = parse_process_runtime({"kind": "inline_python", "network": {"level": "open"}})
    with pytest.raises(NetworkCapExceeded, match="PROCESS_NETWORK_MAX"):
        check_network_cap(rt, settings())  # default cap: isolated
    check_network_cap(rt, settings(PROCESS_NETWORK_MAX="open"))
    check_network_cap(parse_process_runtime({"kind": "inline_python"}), settings())


def test_invalid_network_max_env_is_rejected_at_startup():
    with pytest.raises(ValueError, match="PROCESS_NETWORK_MAX"):
        settings(PROCESS_NETWORK_MAX="everything")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_executor.py -k network -v`

- [ ] **Step 3: Implement**

`process/config.py`:

```python
NETWORK_LEVELS = ("isolated", "inputs", "hosts", "open")
DEFAULT_NETWORK_LEVEL = "isolated"
_HOST_FORBIDDEN = set("/: \t\n")

# on ProcessRuntime:
    network_level: str = DEFAULT_NETWORK_LEVEL
    network_hosts: tuple[str, ...] = ()


def _parse_network(raw: Any) -> tuple[str, tuple[str, ...]]:
    if raw is None:
        return DEFAULT_NETWORK_LEVEL, ()
    doc = _obj(raw, "runtime.network")
    level = _enum(doc.get("level"), NETWORK_LEVELS, "runtime.network.level", default=DEFAULT_NETWORK_LEVEL)
    hosts_raw = doc.get("hosts", [])
    if not isinstance(hosts_raw, list) or not all(isinstance(h, str) for h in hosts_raw):
        raise ProcessConfigError("runtime.network.hosts must be a list of strings")
    hosts = tuple(h.strip() for h in hosts_raw)
    if any(not h or _HOST_FORBIDDEN & set(h) for h in hosts):
        raise ProcessConfigError("runtime.network.hosts entries must be bare hostnames")
    if level == "hosts" and not hosts:
        raise ProcessConfigError("runtime.network.level 'hosts' requires a non-empty hosts list")
    if level != "hosts" and hosts:
        raise ProcessConfigError("runtime.network.hosts is only allowed with level 'hosts'")
    return level, hosts
```

and in `parse_process_runtime`: `level, hosts = _parse_network(doc.get("network"))` → pass `network_level=level, network_hosts=hosts`. (Check `_enum`'s signature accepts `default=`; the `backoff` call at the end of the function shows it does.)

`pipeline/config.py`: add `DEFAULT_PROCESS_NETWORK_MAX = "isolated"`, field `process_network_max: str = DEFAULT_PROCESS_NETWORK_MAX`, and in `from_env`:

```python
            process_network_max=_parse_network_max(env.get("PROCESS_NETWORK_MAX", DEFAULT_PROCESS_NETWORK_MAX)),
```

with

```python
def _parse_network_max(value: str) -> str:
    from pipeline.process.config import NETWORK_LEVELS  # local import: config must not import process at module load

    v = (value or "").strip().lower()
    if v not in NETWORK_LEVELS:
        raise ValueError(f"PROCESS_NETWORK_MAX must be one of {NETWORK_LEVELS}, got {value!r}")
    return v
```

(If `pipeline.process.config` already imports `pipeline.config` at module level, keep this import local as shown to avoid a cycle.)

`launch.py`:

```python
from pipeline.process.config import NETWORK_LEVELS


class NetworkCapExceeded(Exception):
    """The revision asks for more network than this deployment permits —
    configuration, so the run dies rather than retries."""


def check_network_cap(runtime: ProcessRuntime, settings: Settings) -> None:
    if NETWORK_LEVELS.index(runtime.network_level) > NETWORK_LEVELS.index(settings.process_network_max):
        raise NetworkCapExceeded(
            f"revision requests network level {runtime.network_level!r} but this "
            f"deployment allows at most {settings.process_network_max!r} (PROCESS_NETWORK_MAX)"
        )
```

Slice 1 realises every accepted level (`isolated`) as today's `settings.process_network`; `build_run_spec` is unchanged. Add a comment there: "network profile levels above `isolated` are refused by `check_network_cap` until the egress proxy (spec §11) exists".

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/process/config.py services/pipeline/src/pipeline/config.py services/pipeline/src/pipeline/process/launch.py services/pipeline/tests/test_process_executor.py
git commit -m "feat(process): runtime.network profile parser and PROCESS_NETWORK_MAX cap (G-2)"
```

---

### Task 8: Wire planning + staging into the run path

**Files:**
- Modify: `services/pipeline/src/pipeline/process/launch.py` (`build_run_spec`, `execute_run`)
- Modify: `services/pipeline/src/pipeline/process/runner.py` (`run_one`)
- Modify: `services/pipeline/src/pipeline/jobs/process.py` (`process_run_tick` builds the fetcher; `Settings` field `process_input_stage_concurrency` from `PROCESS_INPUT_STAGE_CONCURRENCY`, default 4, in `pipeline/config.py`)
- Test: `services/pipeline/tests/test_process_executor.py`, `services/pipeline/tests/test_process_triggers.py` (or wherever `run_one` is tested — `grep -l "run_one" services/pipeline/tests/*.py`)

**Interfaces:**
- `build_run_spec(..., extra_env: Mapping[str, str] | None = None)` — merged AFTER the revision env and BEFORE credentials/code (platform values still win; `extra_env` is the input env).
- `execute_run(..., read_prefixes: Sequence[str] = (), extra_env: Mapping[str, str] | None = None)`.
- `run_one(run, *, repo, executor, settings, storage_client, resolve_secret, now=None, sts_client=None, fetch_remote: RemoteFetcher | None = None)`. Order inside: parse runtime/env → `check_network_cap` → plan (repo reads) → `stage_inputs` → `execute_run(read_prefixes=plan.read_prefixes, extra_env=input_env(...))`.
- Error mapping in `run_one`: `NetworkCapExceeded` → `dead`; `InputPlanError` → `dead`; `InputStagingError` → `outcome_transition(exit_code=1, timed_out=False, …, error=str(err))` (spends an attempt, retries, then dead).
- `batch_id`: `uuid.uuid4().hex` per run in slice 1.

- [ ] **Step 1: Write the failing tests** (in `test_process_executor.py`)

```python
from pipeline.process.inputs import input_env


def test_run_env_includes_the_input_variables_and_platform_values_still_win():
    creds = RunCredentials("a", "s", "t", "stac-higher", f"staging/runs/{RUN}/", None, "us-east-1")
    spec = build_run_spec(
        settings(), run_id=RUN, process_id=PROC,
        runtime=ProcessRuntime(kind="inline_python"), code="print(1)",
        env={"STAC_HIGHER_INPUT_MANIFEST": "spoofed", "MINE": "1"},
        credentials=creds,
        extra_env=input_env(RUN, f"staging/runs/{RUN}/inputs/b/manifest.json"),
    )
    assert spec.env["STAC_HIGHER_INPUT_PREFIX"] == f"staging/runs/{RUN}/inputs/"
    assert spec.env["STAC_HIGHER_INPUT_MANIFEST"] == f"staging/runs/{RUN}/inputs/b/manifest.json"
    assert spec.env["MINE"] == "1"


def test_execute_run_mints_with_read_prefixes():
    seen = {}

    class Sts:
        def assume_role(self, **kw):
            seen.update(kw)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "s", "SessionToken": "t"}}

    class Store:
        def put_object(self, **kw): pass

    executor = MemoryExecutor()  # the existing fake; see how neighbouring tests script it
    execute_run(
        executor, settings(), Store(), run_id=RUN, process_id=PROC,
        runtime=ProcessRuntime(kind="inline_python"), code="pass", env_entries=(),
        resolve_secret=lambda ref: "", sts_client=Sts(), read_prefixes=["assets/goes/"],
    )
    assert "assets/goes/*" in seen["Policy"]
```

And in the `run_one` test module, using `FakeProcessRepo` (items + source_collections from Task 5), a `MemoryExecutor`, a `FakeS3`-style storage client, and a `fetch_remote` stub:

```python
async def test_run_one_plans_stages_and_grants_before_launch():
    repo = FakeProcessRepo(
        source_collections=["src"],
        items={("src", "i1"): {"id": "i1", "collection": "src", "assets": {"d": {"href": "https://h/x.nc"}}}},
    )
    run = QueuedRun(id=RUN, process_id=PROC, revision_id="r", source_id="s", attempts=1,
                    input_items=[{"item_id": "i1", "collection_id": "src", "op": "insert"}],
                    runtime={"kind": "inline_python"}, code="pass", env=[])
    s3 = FakeS3()
    fetched = []

    async def fetch(href):
        fetched.append(href); return b"bytes"

    result = await run_one(run, repo=repo, executor=MemoryExecutor(), settings=settings(),
                           storage_client=s3, resolve_secret=lambda r: "", sts_client=StsOk(),
                           fetch_remote=fetch)
    assert result.status == "succeeded"
    assert fetched == ["https://h/x.nc"]
    assert any(k.endswith("/manifest.json") for k in s3.objects)
    assert MemoryExecutor.last_spec.env["STAC_HIGHER_INPUT_MANIFEST"].endswith("/manifest.json")


async def test_run_one_marks_staging_failure_as_a_failed_attempt_and_never_launches():
    # same arrangement, fetch raises OSError; assert result.status in ("failed", "dead"),
    # "could not stage" in result.error, and the executor recorded NO launch.


async def test_run_one_dies_when_the_network_level_exceeds_the_cap():
    # runtime={"kind": "inline_python", "network": {"level": "open"}} with default settings
    # -> result.status == "dead" and "PROCESS_NETWORK_MAX" in result.error.
```

Fill the two sketched tests in full following the first one's shape (they are the same arrangement with one variable changed); do not leave them as comments. Look at how the existing `MemoryExecutor` exposes launched specs (`grep -n "class MemoryExecutor" -A40 services/pipeline/src/pipeline/process/memory_executor.py`) and use its real attribute instead of `last_spec` if it differs.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_executor.py -k "input_variables or read_prefixes or run_one" -v`

- [ ] **Step 3: Implement `launch.py` changes**

`build_run_spec` gains `extra_env: Mapping[str, str] | None = None`:

```python
    run_env = dict(env)
    run_env.update(extra_env or {})      # platform-provided input pointers
    run_env.update(credentials.as_env())
    run_env[CODE_ENV_VAR] = encode_code(code)
    run_env["STAC_HIGHER_RUN_ID"] = run_id
    run_env["STAC_HIGHER_PROCESS_ID"] = process_id
```

Because a revision could set `STAC_HIGHER_INPUT_*` itself, apply `extra_env` after `env` (as above) so the platform value wins — the docstring already states that ordering rule; extend it to name the input variables.

`execute_run` gains `read_prefixes: Sequence[str] = ()` and `extra_env=None`, passes `read_prefixes=read_prefixes` to `mint_run_credentials` and `extra_env=extra_env` to `build_run_spec`.

- [ ] **Step 4: Implement `run_one` changes**

After the `run.code is None` check:

```python
    try:
        check_network_cap(runtime, settings)
    except NetworkCapExceeded as err:
        await _finish(repo, run, "dead", None, str(err), None, at)
        return RunResult(run.id, "dead", error=str(err))

    # GOES spec §3: describe the inputs, stage the remote ones, THEN mint+launch.
    source_collections = await repo.list_source_collections(run.process_id)
    documents: dict[tuple[str, str], dict] = {}
    for ref in run.input_items:
        coll = ref.get("collection_id") or (source_collections[0] if len(source_collections) == 1 else None)
        item_id = ref.get("item_id")
        if coll and item_id and (coll, item_id) not in documents:
            doc = await repo.get_item(coll, item_id)
            if doc is not None:
                documents[(coll, item_id)] = doc
    try:
        plan = plan_inputs(
            run_id=run.id, process_id=run.process_id, batch_id=uuid.uuid4().hex,
            kind=KIND_TRANSFORM, refs=run.input_items, documents=documents,
            source_collections=source_collections, bucket=settings.staging_bucket,
            asset_href_base=settings.asset_href_base,
        )
    except InputPlanError as err:
        await _finish(repo, run, "dead", None, f"unusable inputs: {err}", None, at)
        return RunResult(run.id, "dead", error=str(err))

    if fetch_remote is None:
        async def fetch_remote(href: str) -> bytes:
            raise InputStagingError(f"no remote fetcher configured for {href!r}")

    try:
        await stage_inputs(
            plan, storage_client=storage_client, bucket=settings.staging_bucket,
            fetch_remote=fetch_remote, concurrency=settings.process_input_stage_concurrency,
        )
    except InputStagingError as err:
        transition = outcome_transition(
            exit_code=1, timed_out=False, attempts=run.attempts, max_attempts=runtime.max_attempts,
            now=at, retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS, error=str(err),
        )
        await _finish(repo, run, transition.status, None, transition.error, transition.next_attempt_at, at)
        return RunResult(run.id, transition.status, error=transition.error)
```

then pass `read_prefixes=plan.read_prefixes, extra_env=input_env(run.id, plan.manifest_key)` into `execute_run`. Imports: `uuid`, `check_network_cap`, `NetworkCapExceeded`, `KIND_TRANSFORM`, `InputPlanError`, `plan_inputs`, `input_env`, `InputStagingError`, `stage_inputs`, `RemoteFetcher`.

- [ ] **Step 5: Wire the fetcher in the job**

`jobs/process.py` `process_run_tick`: build once per tick

```python
        master_key = load_key_or_skip(settings, JOB_RUN_TICK)  # None => public-only staging
        fetch_remote = build_remote_fetcher(settings, master_key)
```

and pass `fetch_remote=fetch_remote` into `run_one`. (`load_key_or_skip` returns `None` and logs when the key is absent — that must NOT skip the tick here; only reference-mode private sources lose their adapter path.) Add `process_input_stage_concurrency` to `Settings` (`PROCESS_INPUT_STAGE_CONCURRENCY`, default 4) and to `docker-compose.yml`'s pipeline env block with a one-line comment, next to `PROCESS_NETWORK_MAX=${PROCESS_NETWORK_MAX:-isolated}` (also added there).

- [ ] **Step 6: Run everything, lint, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/process/launch.py services/pipeline/src/pipeline/process/runner.py services/pipeline/src/pipeline/jobs/process.py services/pipeline/src/pipeline/config.py docker-compose.yml services/pipeline/tests/
git commit -m "feat(process): plan and stage inputs before launch; input env + read grants (G-2)"
```

---

### Task 9: Finalize skips the inputs area

**Files:**
- Modify: `services/pipeline/src/pipeline/finalize/process_run.py` (`ProcessRunResolver.resolve`, ~line 120)
- Test: `services/pipeline/tests/test_process_finalize.py`

- [ ] **Step 1: Failing test**

```python
async def test_resolver_ignores_everything_under_inputs():
    from pipeline.storage.keys import run_inputs_prefix

    inputs = run_inputs_prefix(RUN)
    store = FakeStore(
        {
            f"{inputs}b1/manifest.json": b'{"version": 1, "items": []}',
            f"{inputs}b1/i1/src.json": item_doc(item_id="src", collection="cloud-masks"),
            f"{PREFIX}out.json": item_doc(item_id="out", assets={"data": {"href": "out.tif"}}),
            f"{PREFIX}out.tif": b"tif",
        }
    )
    resolution = await ProcessRunResolver(store=store).resolve(build_process_request(RUN, OUT))
    assert [i.ref.item_id for i in resolution.items] == ["out"]
    assert resolution.rejected == ()


async def test_an_output_cannot_claim_an_input_file_as_its_asset():
    from pipeline.storage.keys import run_inputs_prefix

    store = FakeStore(
        {
            f"{run_inputs_prefix(RUN)}b1/i1/x.nc": b"nc",
            f"{PREFIX}out.json": item_doc(item_id="out", assets={"data": {"href": "inputs/b1/i1/x.nc"}}),
        }
    )
    resolution = await ProcessRunResolver(store=store).resolve(build_process_request(RUN, OUT))
    # A relative href with a separator is refused (never treated as ours), so
    # the item publishes with that asset left as-is — which the docs warn about.
    assert [i.ref.item_id for i in resolution.items] == ["out"]
```

Read the second test's expectation against the current `_relative_filename` behaviour (`/` → `None` → `continue`): the asset is neither staged nor rejected. That is the existing contract; the test pins it and the docs (Task 10) state it. If you judge that a `/`-containing relative href should instead REJECT the item (`REASON_MISSING_ASSET`), make that change and flip the assertion — say which in the commit.

- [ ] **Step 2: Implement**

In `resolve`, after `keys = self.store.list_keys(prefix)`:

```python
        # GOES spec §3.3: the platform stages a run's INPUTS under
        # `inputs/` inside the same prefix. Nothing there is an output — the
        # manifest is JSON and staged item documents may be too.
        inputs_prefix = f"{prefix}{INPUTS_SEGMENT}/"
        keys = [k for k in keys if not k.startswith(inputs_prefix)]
```

with `from pipeline.storage.keys import INPUTS_SEGMENT, run_staging_prefix, sanitize_filename`.

- [ ] **Step 3: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_finalize.py -v && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/finalize/process_run.py services/pipeline/tests/test_process_finalize.py
git commit -m "fix(finalize): process-run resolver skips the staged inputs area (G-2)"
```

---

### Task 10: App — `network` on the runtime schema, the write gate, the route cap, the fixture

**Files:**
- Modify: `app/src/lib/processes/schemas.ts` (`runtimeLimits` ~line 92; write gate ~line 150)
- Modify: `app/src/pages/api/processes/[id]/revisions.ts` (after `safeParse`)
- Create: `app/src/lib/processes/network.ts`
- Modify: `tests/contract-fixtures/process-runtime.json`
- Test: `app/src/__tests__/contract-fixtures.test.ts` (existing union-fixture block picks the new cases up), a new `app/src/__tests__/processes-network.test.ts`

**Interfaces:**
- `PROCESS_NETWORK_LEVELS = ["isolated", "inputs", "hosts", "open"] as const`; `processNetworkSchema` = `{ level (default "isolated"), hosts: string[] (default []) }` strict, refined (`hosts` non-empty ⇔ level `hosts`; each host matches `/^[A-Za-z0-9.-]+$/`).
- `runtimeLimits.network: processNetworkSchema.default(() => processNetworkSchema.parse({}))`.
- Write gate: `processRuntimeSchema` additionally refuses `network.level !== "isolated"` with `NETWORK_LEVEL_NOT_YET_AVAILABLE` ("network levels above 'isolated' arrive with the egress proxy; see the GOES spec §11").
- `app/src/lib/processes/network.ts`: `getNetworkMax(env = process.env): NetworkLevel` (reads `PROCESS_NETWORK_MAX`, default `"isolated"`, throws on an unknown value), `networkLevelWithinCap(level, cap): boolean`, `NETWORK_CAP_MESSAGE(level, cap)`.
- Route: after the schema parse, `if (!networkLevelWithinCap(parsed.data.runtime.network.level, getNetworkMax())) return 400 { error: NETWORK_CAP_MESSAGE(...) }`.

- [ ] **Step 1: Fixture** — in `process-runtime.json` add `"network": { "level": "isolated", "hosts": [] }` to both `defaults` documents and append cases:

```json
    { "name": "network isolated explicitly", "config": { "kind": "inline_python", "network": { "level": "isolated" } }, "app": "accept", "pipeline": "accept" },
    { "name": "network inputs (slice 1: writer refuses, reader accepts)", "config": { "kind": "inline_python", "network": { "level": "inputs" } }, "app": "reject", "pipeline": "accept" },
    { "name": "network hosts with a list (slice 1: writer refuses, reader accepts)", "config": { "kind": "inline_python", "network": { "level": "hosts", "hosts": ["api.example.com"] } }, "app": "reject", "pipeline": "accept" },
    { "name": "network open (slice 1: writer refuses, reader accepts)", "config": { "kind": "inline_python", "network": { "level": "open" } }, "app": "reject", "pipeline": "accept" },
    { "name": "hosts level without hosts", "config": { "kind": "inline_python", "network": { "level": "hosts", "hosts": [] } }, "app": "reject", "pipeline": "reject" },
    { "name": "hosts listed on a non-hosts level", "config": { "kind": "inline_python", "network": { "level": "open", "hosts": ["x.y"] } }, "app": "reject", "pipeline": "reject" },
    { "name": "unknown network level", "config": { "kind": "inline_python", "network": { "level": "lan" } }, "app": "reject", "pipeline": "reject" },
    { "name": "host with a scheme", "config": { "kind": "inline_python", "network": { "level": "hosts", "hosts": ["https://x.y"] } }, "app": "reject", "pipeline": "reject" }
```

Update the fixture's `$comment` with one sentence about `network` and the slice-1 asymmetry.

- [ ] **Step 2: Failing tests**

`app/src/__tests__/processes-network.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { processRuntimeSchema, processRuntimeReadSchema } from "@/lib/processes/schemas";
import { getNetworkMax, networkLevelWithinCap } from "@/lib/processes/network";

describe("runtime.network", () => {
  it("defaults to isolated with no hosts", () => {
    const rt = processRuntimeSchema.parse({ kind: "inline_python" });
    expect(rt.network).toEqual({ level: "isolated", hosts: [] });
  });
  it("read schema accepts every level; write gate accepts only isolated this slice", () => {
    for (const level of ["inputs", "open"]) {
      expect(processRuntimeReadSchema.safeParse({ kind: "inline_python", network: { level } }).success).toBe(true);
      expect(processRuntimeSchema.safeParse({ kind: "inline_python", network: { level } }).success).toBe(false);
    }
  });
});

describe("PROCESS_NETWORK_MAX", () => {
  it("defaults to isolated and validates", () => {
    expect(getNetworkMax({})).toBe("isolated");
    expect(getNetworkMax({ PROCESS_NETWORK_MAX: "hosts" })).toBe("hosts");
    expect(() => getNetworkMax({ PROCESS_NETWORK_MAX: "lan" })).toThrow(/PROCESS_NETWORK_MAX/);
  });
  it("orders levels", () => {
    expect(networkLevelWithinCap("isolated", "isolated")).toBe(true);
    expect(networkLevelWithinCap("open", "hosts")).toBe(false);
    expect(networkLevelWithinCap("inputs", "hosts")).toBe(true);
  });
});
```

Run `cd app && npx vitest run src/__tests__/processes-network.test.ts src/__tests__/contract-fixtures.test.ts` → FAIL.

- [ ] **Step 3: Implement schema + module + route** per the Interfaces block. The route also needs a test: extend the existing revisions-route test file (`grep -l "revisions" app/src/__tests__/*.test.ts`) with a case that POSTs `network: { level: "isolated" }` (201) and asserts a 400 for `level: "open"` mentioning `PROCESS_NETWORK_MAX` when the write gate is bypassed — since the write gate already rejects it with a different message in slice 1, assert on the write-gate message for now and leave a comment that the cap message becomes reachable in slice 2.

- [ ] **Step 4: Run, typecheck, commit**

Run: `cd app && npm test && npm run check`

```bash
git add app/src/lib/processes/schemas.ts app/src/lib/processes/network.ts "app/src/pages/api/processes/[id]/revisions.ts" tests/contract-fixtures/process-runtime.json app/src/__tests__/
git commit -m "feat(processes): runtime.network block, slice-1 write gate, PROCESS_NETWORK_MAX cap (G-2)"
```

---

### Task 11: Deploy-card UI control

**Files:**
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` (deploy card state ~line 214; runtime object ~line 238; inputs grid ~line 277)
- Test: the existing `ProcessDetailPage` test (`grep -l "ProcessDetailPage" app/src/__tests__/*.test.tsx`)

**Interfaces:**
- `PUBLIC_PROCESS_NETWORK_MAX` (client env, default `"isolated"`) — the UI mirror of the pipeline's cap. Read via `import.meta.env.PUBLIC_PROCESS_NETWORK_MAX ?? "isolated"`.

- [ ] **Step 1: Failing test** — render the deploy card and assert a `Network access` select exists whose only enabled option is `Isolated (platform storage only)`, with the others present but disabled and a note "Higher levels arrive with the egress proxy" (or, when the env var is above `isolated`, enabled up to the cap). Then click deploy and assert `deployMutation` was called with `runtime.network = { level: "isolated", hosts: [] }`.

- [ ] **Step 2: Implement** — add `const [networkLevel, setNetworkLevel] = useState<NetworkLevel>("isolated");`, a `Select` (from `@stac-higher/shared`) labelled `Network access` with the four options, each disabled when `!networkLevelWithinCap(level, cap)` or (slice 1) when `level !== "isolated"`; option labels: `Isolated (platform storage only)`, `Inputs (hosts of the input assets)`, `Named hosts`, `Open internet`; help text under it: "Slice 1 runs every process isolated; inputs are staged into the run. Higher levels are enabled per deployment (PROCESS_NETWORK_MAX) once the egress proxy ships." Include `network: { level: networkLevel, hosts: [] }` in the deployed runtime.

- [ ] **Step 3: Run, typecheck, commit**

```bash
git add app/src/components/processes/ProcessDetailPage.tsx app/src/__tests__/
git commit -m "feat(processes-ui): network access control on the deploy card (G-2)"
```

---

### Task 12: ADR 0018, docs, ISSUES, verify, merge

**Files:**
- Create: `docs/decisions/0018-process-inputs-and-network-profiles.md`
- Modify: `docs/decisions/README.md` (index + invariants)
- Modify: `docs/processes.md` (new sections "What your run receives", "Network access"; update "Where your code runs", the env table, "Rules that will surprise you")
- Modify: `docs/FEATURES.md`, `docs/ISSUES.md` (new I-90 policy size, I-91 remote inputs without a matching association use a public GET only), `AGENTS.md` (Processes paragraph: one sentence on staged inputs + network profile), `TODO.md` (tick G-2)

- [ ] **Step 1: ADR 0018** — sections: Context (runs could not see their inputs; the boundaries of ADR 0013), Decision (manifest contract §3.1 verbatim, the two byte cases, read grants by source-collection prefix, staging before launch, the `inputs/` exclusion, the `network` profile with levels + `PROCESS_NETWORK_MAX`, slice-1 `isolated` only), Consequences (copy cost for remote inputs until the `inputs` level; policy size cap; manifest versioning is additive), Alternatives rejected (per-item read grants — STS policy size; copying platform-held bytes — needless churn; opening the network — ADR 0013), Invariants to add to `docs/decisions/README.md`: "A run's read access outside its own prefix is limited to the canonical prefixes of its source collections, granted in the STS session policy, never by platform keys" and "A revision's `network.level` never exceeds `PROCESS_NETWORK_MAX`; the pipeline enforces it at launch independently of the app."

- [ ] **Step 2: `docs/processes.md`** — add after "Where your code runs":

````markdown
## What your run receives

Before your container starts, the platform stages the items that triggered
the run and writes a manifest describing them:

| Variable | Meaning |
|---|---|
| `STAC_HIGHER_INPUT_PREFIX` | `staging/runs/{run_id}/inputs/` — read-only from your point of view |
| `STAC_HIGHER_INPUT_MANIFEST` | the key of this batch's `manifest.json` |

```python
import json, os, boto3
s3 = boto3.client("s3")
bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
manifest = json.loads(
    s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
)
for entry in manifest["items"]:
    item = entry["item"]                      # the full STAC item
    for name, asset in entry["assets"].items():
        obj = s3.get_object(Bucket=asset["bucket"], Key=asset["key"])   # or rasterio: /vsis3/{bucket}/{key}
```

Every asset has a `bucket` and `key` your credentials can read, whether the
bytes are platform-held (`staged: false`, the canonical object) or were
fetched from elsewhere for this run (`staged: true`, a copy under your inputs
prefix). `href` is the catalog's own href, for provenance. `op` is the event
that triggered the run (`insert` | `update`). An item deleted between trigger
and run appears under `skipped`, not `items`. A cron run has an empty
`items` list. Assets with a relative or missing href are omitted from
`assets` — they are not locations you can read.
````

Add a "Network access" section describing the four levels, that slice 1 accepts `isolated` only, and `PROCESS_NETWORK_MAX`. Under "Rules that will surprise you" add: "**Inputs are not outputs.** Nothing under `inputs/` is published, and an output item cannot name an input file (`inputs/…`) as its asset — copy the bytes if you need them published."

- [ ] **Step 3: ISSUES** — add I-90 (STS inline-policy size caps source collections at 8; cloud backend must revisit) and I-91 (remote inputs whose href matches no enabled reference-mode association are fetched by an unauthenticated HTTPS GET; private reference sources need their association enabled or slice 2's `inputs` level).

- [ ] **Step 4: Full verification**

`npm run verify` (root); `cd services/pipeline && uv run pytest && uv run ruff check .`. Lead-only live check with the stack up: deploy a process whose code prints the manifest, trigger it by uploading an item into its source collection, and confirm the run log shows the manifest with a canonical `assets/…` key and the run succeeded.

- [ ] **Step 5: Commit, merge, clean up**

```bash
git add docs/ AGENTS.md TODO.md
git commit -m "docs: ADR 0018 process inputs + network profiles; processes.md contract (G-2)"
git checkout ai/main && git merge ai/goes-g2 --no-ff
git worktree remove .claude/worktrees/goes-g2 && git branch -d ai/goes-g2
```

Do NOT push `ai/main`.
