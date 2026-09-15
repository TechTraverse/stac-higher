# M3-F · GC at Rate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `asset_collect` — the platform's only byte-deletion path — can retire the 2.6M marks/day the M3 envelope produces instead of 144k, and an open-mark backlog raises an alert instead of quietly becoming a storage bill.

**Architecture:** The collect tick stops deleting prefix-by-prefix. It claims up to `GC_COLLECT_MARKS` (10 000) due marks, lists each mark's keys with a bounded thread pool (`GC_COLLECT_LIST_CONCURRENCY`, 8), plans `DeleteObjects` batches of ≤1000 keys that are **grouped per collection** (a batch never spans two collections — the wrong-prefix bulk-delete risk the spec names), executes them, maps per-key failures back to their marks, and writes the outcomes in two batched statements. The job drains rounds until a round comes back short or a tick time budget (240 s of the 300 s cadence) is spent, so capacity is self-sizing. Prefix construction stays in `storage/keys.py`. The flow monitor gains a per-collection `asset_gc_backlog` condition (collection-anchored, like `push_rejected`) observed from `asset_gc` rows due for longer than an hour, auto-resolving when the backlog clears. ADR 0011's mark-first-then-collect ordering and the grace window are unchanged; only the batch mechanics move.

**Tech Stack:** Python 3.12, boto3 `DeleteObjects` (Quiet mode, ≤1000 keys), psycopg 3, prometheus_client, pytest; one contract fixture edit (`alert-kinds.json`) consumed by vitest and pytest; one app label.

**Spec:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §3 (M3-F row; "M3-F deletes real bytes at a raised rate" risk note: keep prefix construction in `storage/keys.py`, add a test that a batch never spans two collections; backlog assertion); `docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` M3-S-B "GC and retention are NOT bulk paths today"; ADR 0011; `TODO.md` M3 queue "M3-F · GC at rate".

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/m3-f-gc-at-rate -b ai/m3-f-gc-at-rate ai/main`. Pipeline + one fixture + one app label: `npm install` at the worktree root is needed only for Task 4's app gate.
- **Gates:** pipeline tasks `uv run pytest -q` and `uv run ruff check .` from `services/pipeline/`; Task 4 also `npm run verify` from the worktree root. Teammates never run e2e, the dev server, or Docker.
- **ADR 0011 is unchanged:** marks are written before catalog deletes; nothing is collected before `collect_after`; nothing is deleted on an unconfigured platform. **ADR 0001:** the pipeline runs no DDL — the existing partial index `asset_gc_due_idx (collect_after) WHERE collected_at IS NULL` serves both the claim and the backlog count.
- **Settled numbers:** `GC_COLLECT_MARKS` default **10000** (marks per round; 2.6M/day ÷ 288 ticks = 9 028 needed), `GC_COLLECT_LIST_CONCURRENCY` default **8** (concurrent `list_objects_v2` calls), `GC_COLLECT_TICK_BUDGET_SECONDS` default **240** (drain rounds stop after this; the cron is `*/5`), `GC_BACKLOG_ALERT_MARKS` default **1000** (per collection), `GC_BACKLOG_ALERT_SECONDS` default **3600** (a mark counts as backlogged when `collect_after` is older than this). `DeleteObjects` batch size is the API's **1000**. `GC_BATCH_ITEMS` (500) keeps governing the retention leg only.
- **Memory envelope:** one round holds ≤ `GC_COLLECT_MARKS` × ~1.5 keys × ~120 B ≈ 2 MB — recorded in the README's "Memory envelope (M3-C)" section as a fixed term.
- **Batch invariant (spec §3 risk note):** every `DeleteBatch` carries one `collection_id`, every key in it starts with `assets/{collection_id}/`, and a batch has ≤1000 keys. `plan_delete_batches` is the single place batches are built; a mark whose keys fall outside its own collection prefix is recorded as an error, never deleted.
- **Failure semantics:** a mark stays open (with `error`) when its listing raised, when any of its keys failed in `DeleteObjects` (the response's `Errors`), or when the whole batch call raised; `collected_at` is stamped only for marks whose every key was deleted (or that had no keys — a reference-mode item, a normal zero).
- **Alert kind `asset_gc_backlog`:** `source="job_failure"`, collection-anchored (`collection_id`), monitor-owned (auto-resolves), message `"{count} asset prefixes have waited over {hours} h for collection (open-mark backlog)"`; added to `tests/contract-fixtures/alert-kinds.json` `kinds` + `monitor_kinds` and to the app's `ALERT_KIND_LABEL`.
- **Metrics:** `pipeline_asset_gc_objects_deleted_total` (Counter), `pipeline_asset_gc_marks_collected_total` (Counter), `pipeline_asset_gc_due_marks` (Gauge — open marks past `collect_after`, set every collect tick), all in `metrics.py` and in `__all__`.
- **No new dependency.** Structured logging: data in `extra={...}`, messages constant; never `filename`, `module`, `name`, `msg`, `args`, `levelname` as `extra` keys. Blocking boto3 calls run under `asyncio.to_thread`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL or name>
  ```

---

### Task 1: Prefix helpers in `storage/keys.py`, the mark carries its collection

**Files:**
- Modify: `services/pipeline/src/pipeline/storage/keys.py` (append two functions)
- Modify: `services/pipeline/src/pipeline/gc/sweep.py` (`item_prefix` delegates)
- Modify: `services/pipeline/src/pipeline/gc/repo.py` (`DueMark.collection_id`; `list_due_marks` SELECT; two batch writes; `count_due_marks`)
- Modify: `services/pipeline/tests/test_gc_sweep.py` (`FakeGcRepo` + `DueMark(...)` constructions gain the collection; new tests)
- Test: `services/pipeline/tests/test_storage_keys.py` (append — the file exists; read its imports)

**Interfaces:**
- Produces:
  ```python
  # storage/keys.py
  def item_asset_prefix(collection: str, item_id: str) -> str          # "assets/{collection}/{item_id}/" (the §5.3 prefix holding all of one item's objects)
  def key_within_collection(key: str, collection: str) -> bool        # key.startswith(f"{CANONICAL_PREFIX}/{collection}/") — the bulk-delete guard

  # gc/repo.py
  @dataclass(frozen=True)
  class DueMark:
      id: str
      object_key: str
      collection_id: str
  class GcRepo(abc.ABC):
      async def list_due_marks(self, limit: int) -> list[DueMark]                       # now returns collection_id too
      async def record_collected_many(self, mark_ids: Sequence[str]) -> None            # one UPDATE ... WHERE id = ANY(%s)
      async def record_collect_errors(self, errors: Sequence[tuple[str, str]]) -> None  # one executemany
      async def count_due_marks(self) -> int                                             # open marks with collect_after <= now()
  ```
  `record_collected` / `record_collect_error` (singular) stay for callers/tests that use them.

- [ ] **Step 1: Failing tests**

Append to `services/pipeline/tests/test_storage_keys.py`:
```python
def test_item_asset_prefix_is_the_canonical_item_prefix():
    from pipeline.storage.keys import item_asset_prefix

    assert item_asset_prefix("sentinel-2", "item-1") == "assets/sentinel-2/item-1/"


def test_key_within_collection_guards_the_bulk_delete():
    from pipeline.storage.keys import key_within_collection

    assert key_within_collection("assets/sentinel-2/item-1/B02.tif", "sentinel-2")
    assert not key_within_collection("assets/sentinel-2x/item-1/B02.tif", "sentinel-2")
    assert not key_within_collection("assets/other/item-1/B02.tif", "sentinel-2")
    assert not key_within_collection("staging/sentinel-2/item-1/B02.tif", "sentinel-2")
    assert not key_within_collection("", "sentinel-2")
```
In `test_gc_sweep.py`: every `DueMark("m1", "assets/sentinel-2/item-1/")` becomes `DueMark("m1", "assets/sentinel-2/item-1/", "sentinel-2")` (and likewise for the others — the third positional is the collection); `FakeGcRepo` gains
```python
    async def record_collected_many(self, mark_ids):
        self.collected.extend(mark_ids)

    async def record_collect_errors(self, errors):
        self.errors.extend(errors)

    async def count_due_marks(self) -> int:
        return len(self.due)
```
and a test that `item_prefix` and `item_asset_prefix` agree:
```python
    def test_item_prefix_delegates_to_storage_keys(self):
        from pipeline.storage.keys import item_asset_prefix

        assert item_prefix("c", "i") == item_asset_prefix("c", "i")
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_storage_keys.py tests/test_gc_sweep.py -q` → ImportError / TypeError on `DueMark`.

- [ ] **Step 3: Implement**

`storage/keys.py`, after `canonical_asset_key`:
```python
def item_asset_prefix(collection: str, item_id: str) -> str:
    """The §5.3 canonical prefix holding ALL of one item's asset objects —
    what an ``asset_gc`` mark names (ADR 0011). Built here, beside
    `canonical_asset_key`, so the mark and the key can never disagree."""
    return f"{CANONICAL_PREFIX}/{_safe_segment(collection, field='collection')}/{_safe_segment(item_id, field='item_id')}/"


def key_within_collection(key: str, collection: str) -> bool:
    """The bulk-delete guard (M3-F, spec §3): a key the collector may delete
    on behalf of ``collection`` must sit under that collection's canonical
    prefix. A mangled mark or a listing that strayed fails this and is
    recorded as an error, never deleted."""
    return bool(key) and key.startswith(f"{CANONICAL_PREFIX}/{collection}/")
```
(Check `_safe_segment`'s behaviour on the ids the existing `item_prefix` accepted — if it would reject an id the retention sweep produces today, use the plain f-string without `_safe_segment` and say so in the report.)

`gc/sweep.py`: `item_prefix` becomes `return item_asset_prefix(collection_id, item_id)` (import from `pipeline.storage.keys`); keep the name — `retention_tick` and tests use it.

`gc/repo.py`: `DueMark` gains `collection_id: str`; ABC gains the three methods (docstrings: "M3-F: the collector writes outcomes in bulk — one statement per tick, not one per mark"); `PgGcRepo`:
```python
    async def list_due_marks(self, limit: int) -> list[DueMark]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, object_key, collection_id FROM stac_higher.asset_gc"
                " WHERE collected_at IS NULL AND collect_after <= now()"
                " ORDER BY collect_after LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [DueMark(id=str(r[0]), object_key=r[1], collection_id=r[2]) for r in rows]

    async def record_collected_many(self, mark_ids: Sequence[str]) -> None:  # pragma: no cover
        if not mark_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.asset_gc SET collected_at = now(), error = NULL"
                " WHERE id = ANY(%s::uuid[])",
                (list(mark_ids),),
            )
            await conn.commit()

    async def record_collect_errors(self, errors: Sequence[tuple[str, str]]) -> None:  # pragma: no cover
        if not errors:
            return
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "UPDATE stac_higher.asset_gc SET error = %s WHERE id = %s",
                    [(error, mark_id) for mark_id, error in errors],
                )
            await conn.commit()

    async def count_due_marks(self) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM stac_higher.asset_gc"
                " WHERE collected_at IS NULL AND collect_after <= now()"
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0
```
(`from collections.abc import Sequence`.)

- [ ] **Step 4: Gates** — `uv run pytest -q`, `uv run ruff check .` → green.

- [ ] **Step 5: Commit**
```bash
git add services/pipeline/src/pipeline/storage/keys.py services/pipeline/src/pipeline/gc/sweep.py services/pipeline/src/pipeline/gc/repo.py services/pipeline/tests/test_storage_keys.py services/pipeline/tests/test_gc_sweep.py
git commit -m "feat(gc): item_asset_prefix / key_within_collection in storage.keys; DueMark carries its collection; batched outcome writes and a due-mark count on GcRepo (M3-F)"
```

### Task 2: `delete_keys` and the batch planner — a batch never spans two collections

**Files:**
- Modify: `services/pipeline/src/pipeline/storage/platform.py` (append `DeleteOutcome`, `delete_keys`)
- Modify: `services/pipeline/src/pipeline/gc/sweep.py` (append `DeleteBatch`, `MAX_DELETE_KEYS`, `plan_delete_batches`)
- Test: `services/pipeline/tests/test_gc_sweep.py` (append `TestPlanDeleteBatches`), `services/pipeline/tests/test_storage_platform.py` (append; the file exists — read its fake-client pattern)

**Interfaces:**
- Produces:
  ```python
  # storage/platform.py
  @dataclass(frozen=True)
  class DeleteOutcome:
      deleted: tuple[str, ...]            # keys the daemon confirmed deleted (or silently accepted in Quiet mode)
      failed: dict[str, str]              # key -> "Code: Message" from the response's Errors
  def delete_keys(client: S3Like, bucket: str, keys: Sequence[str]) -> DeleteOutcome
      # ≤ 1000 keys per call (raises ValueError above), Quiet=True, parses Errors; a missing key is NOT an error (S3 returns success for it)

  # gc/sweep.py
  MAX_DELETE_KEYS = 1000
  @dataclass(frozen=True)
  class DeleteBatch:
      collection_id: str
      keys: tuple[str, ...]               # every key satisfies key_within_collection(key, collection_id)
      mark_ids: tuple[str, ...]           # marks with at least one key in this batch
  def plan_delete_batches(listed: Sequence[tuple[DueMark, Sequence[str]]]) -> tuple[list[DeleteBatch], list[tuple[str, str]]]
      # returns (batches, errors); a mark with a key outside its collection contributes NO keys and one error
  ```

- [ ] **Step 1: Failing tests**

Append to `test_gc_sweep.py`:
```python
class TestPlanDeleteBatches:
    def _mark(self, i, coll="c1"):
        return DueMark(f"m{i}", f"assets/{coll}/i{i}/", coll)

    def test_groups_by_collection_and_never_spans_two(self):
        from pipeline.gc.sweep import plan_delete_batches

        listed = [
            (self._mark(1, "c1"), ["assets/c1/i1/a.tif", "assets/c1/i1/b.tif"]),
            (self._mark(2, "c2"), ["assets/c2/i2/a.tif"]),
            (self._mark(3, "c1"), ["assets/c1/i3/a.tif"]),
        ]
        batches, errors = plan_delete_batches(listed)
        assert errors == []
        assert {b.collection_id for b in batches} == {"c1", "c2"}
        for b in batches:
            assert all(k.startswith(f"assets/{b.collection_id}/") for k in b.keys)
        c1 = next(b for b in batches if b.collection_id == "c1")
        assert set(c1.mark_ids) == {"m1", "m3"}
        assert len(c1.keys) == 3

    def test_splits_at_the_delete_objects_limit(self):
        from pipeline.gc.sweep import MAX_DELETE_KEYS, plan_delete_batches

        keys = [f"assets/c1/i1/{n}.tif" for n in range(MAX_DELETE_KEYS + 5)]
        batches, _ = plan_delete_batches([(self._mark(1), keys)])
        assert [len(b.keys) for b in batches] == [MAX_DELETE_KEYS, 5]
        assert all(b.mark_ids == ("m1",) for b in batches)

    def test_a_key_outside_the_marks_collection_is_an_error_not_a_delete(self):
        from pipeline.gc.sweep import plan_delete_batches

        listed = [(self._mark(1, "c1"), ["assets/c1/i1/a.tif", "assets/c2/i9/z.tif"])]
        batches, errors = plan_delete_batches(listed)
        assert batches == []
        assert errors[0][0] == "m1"
        assert "assets/c2/i9/z.tif" in errors[0][1]

    def test_a_mark_with_no_keys_is_collectable_without_a_batch(self):
        from pipeline.gc.sweep import plan_delete_batches

        batches, errors = plan_delete_batches([(self._mark(1), [])])
        assert batches == [] and errors == []
```
Append to `test_storage_platform.py` (use the file's existing fake S3 client pattern — a class recording `delete_objects(Bucket=, Delete=)` calls and returning a scripted response):
```python
def test_delete_keys_is_quiet_batched_and_maps_errors():
    from pipeline.storage.platform import delete_keys

    class Client:
        calls = []

        def delete_objects(self, **kwargs):
            self.calls.append(kwargs)
            return {"Errors": [{"Key": "assets/c/i/bad.tif", "Code": "AccessDenied", "Message": "nope"}]}

    client = Client()
    out = delete_keys(client, "bucket", ["assets/c/i/a.tif", "assets/c/i/bad.tif"])
    assert client.calls[0]["Bucket"] == "bucket"
    assert client.calls[0]["Delete"]["Quiet"] is True
    assert [o["Key"] for o in client.calls[0]["Delete"]["Objects"]] == ["assets/c/i/a.tif", "assets/c/i/bad.tif"]
    assert out.deleted == ("assets/c/i/a.tif",)
    assert out.failed == {"assets/c/i/bad.tif": "AccessDenied: nope"}


def test_delete_keys_refuses_more_than_the_api_limit_and_empty_input():
    from pipeline.storage.platform import delete_keys

    with pytest.raises(ValueError):
        delete_keys(object(), "bucket", [f"assets/c/i/{n}" for n in range(1001)])
    assert delete_keys(object(), "bucket", []).deleted == ()
```

- [ ] **Step 2: Run to verify they fail** — the two files → ImportError.

- [ ] **Step 3: Implement**

`storage/platform.py`, after `delete_prefix`:
```python
@dataclass(frozen=True)
class DeleteOutcome:
    deleted: tuple[str, ...]
    failed: dict[str, str]


def delete_keys(client: S3Like, bucket: str, keys: Sequence[str]) -> DeleteOutcome:
    """One ``DeleteObjects`` call for up to 1000 keys (M3-F, the collector's
    bulk primitive). Quiet mode: the response lists only failures, which are
    mapped back per key so the caller can keep exactly those marks open. A
    key that no longer exists is a success (S3 semantics), so an
    already-collected prefix is a normal zero. Pure over an injected client;
    synchronous boto3 — wrap in ``asyncio.to_thread``."""
    if len(keys) > 1000:
        raise ValueError(f"DeleteObjects takes at most 1000 keys, got {len(keys)}")
    if not keys:
        return DeleteOutcome(deleted=(), failed={})
    response = client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
    )
    failed = {
        str(e.get("Key")): f"{e.get('Code', 'Error')}: {e.get('Message', '')}"
        for e in (response or {}).get("Errors", [])
        if e.get("Key")
    }
    deleted = tuple(k for k in keys if k not in failed)
    return DeleteOutcome(deleted=deleted, failed=failed)
```
(`from collections.abc import Sequence`, `from dataclasses import dataclass` if not imported.)

`gc/sweep.py`:
```python
#: DeleteObjects' hard limit per call.
MAX_DELETE_KEYS = 1000


@dataclass(frozen=True)
class DeleteBatch:
    """≤1000 keys of ONE collection and the marks they belong to. The
    per-collection grouping is the spec §3 safety rule: a bulk delete can
    never reach across collections, whatever a mark says."""

    collection_id: str
    keys: tuple[str, ...]
    mark_ids: tuple[str, ...]


def plan_delete_batches(
    listed: Sequence[tuple[DueMark, Sequence[str]]],
) -> tuple[list[DeleteBatch], list[tuple[str, str]]]:
    """Group listed keys into per-collection DeleteObjects batches.

    A mark whose listing contains a key outside its own collection prefix is
    refused whole (recorded as an error, none of its keys planned) — the mark
    or the listing is wrong, and the collector never guesses which."""
    errors: list[tuple[str, str]] = []
    per_collection: dict[str, list[tuple[str, str]]] = {}  # coll -> [(key, mark_id)]
    for mark, keys in listed:
        stray = [k for k in keys if not key_within_collection(k, mark.collection_id)]
        if stray:
            errors.append(
                (mark.id, f"listing strayed outside collection {mark.collection_id!r}: {stray[0]}")
            )
            continue
        per_collection.setdefault(mark.collection_id, []).extend((k, mark.id) for k in keys)
    batches: list[DeleteBatch] = []
    for collection_id, pairs in per_collection.items():
        for start in range(0, len(pairs), MAX_DELETE_KEYS):
            chunk = pairs[start : start + MAX_DELETE_KEYS]
            batches.append(
                DeleteBatch(
                    collection_id=collection_id,
                    keys=tuple(k for k, _ in chunk),
                    mark_ids=tuple(dict.fromkeys(m for _, m in chunk)),
                )
            )
    return batches, errors
```
(import `key_within_collection` from `pipeline.storage.keys`, `Sequence` from `collections.abc`.)

- [ ] **Step 4: Gates and commit**
```bash
git add services/pipeline/src/pipeline/storage/platform.py services/pipeline/src/pipeline/gc/sweep.py services/pipeline/tests/test_gc_sweep.py services/pipeline/tests/test_storage_platform.py
git commit -m "feat(gc): delete_keys (Quiet DeleteObjects, per-key errors) and plan_delete_batches — per-collection, ≤1000 keys, strays refused (M3-F)"
```

### Task 3: The batched collect tick, the drain loop, settings, metrics

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py` (three settings + docstring bullets)
- Modify: `services/pipeline/src/pipeline/metrics.py` (three metrics)
- Modify: `services/pipeline/src/pipeline/gc/sweep.py` (`collect_tick` rewritten; `CollectTickResult` gains `marks_seen`)
- Modify: `services/pipeline/src/pipeline/jobs/gc.py` (`asset_collect` drains rounds within the budget)
- Modify: `services/pipeline/README.md` (env rows; envelope term), `docker-compose.yml` (pipeline env lines)
- Test: `services/pipeline/tests/test_gc_sweep.py` (rewrite `TestCollectTick`), `services/pipeline/tests/test_config.py` (append), `services/pipeline/tests/test_gc_jobs.py` (new — the drain loop; if a GC job test file already exists, extend it)

**Interfaces:**
- Consumes: Task 1 repo methods, Task 2 `delete_keys` / `plan_delete_batches`, `list_keys` (existing).
- Produces:
  ```python
  # config.py
  DEFAULT_GC_COLLECT_MARKS = 10000
  DEFAULT_GC_COLLECT_LIST_CONCURRENCY = 8
  DEFAULT_GC_COLLECT_TICK_BUDGET_SECONDS = 240
  Settings.gc_collect_marks: int; Settings.gc_collect_list_concurrency: int; Settings.gc_collect_tick_budget_seconds: int
  # metrics.py
  ASSET_GC_OBJECTS_DELETED = Counter("pipeline_asset_gc_objects_deleted_total", ...)
  ASSET_GC_MARKS_COLLECTED = Counter("pipeline_asset_gc_marks_collected_total", ...)
  ASSET_GC_DUE_MARKS = Gauge("pipeline_asset_gc_due_marks", ...)
  # gc/sweep.py
  @dataclass(frozen=True)
  class CollectTickResult:
      marks_seen: int; collected_marks: int; deleted_objects: int; errors: int
  async def collect_tick(repo, *, list_keys_under: Callable[[str], list[str]], delete_keys: Callable[[Sequence[str]], DeleteOutcome], batch_limit: int, list_concurrency: int) -> CollectTickResult
  ```
  (The old `delete_under_prefix` positional parameter is gone; `jobs/gc.py` is its only caller.)

- [ ] **Step 1: Failing tests**

Replace `TestCollectTick` in `test_gc_sweep.py`:
```python
class TestCollectTick:
    def _repo(self, *marks):
        return FakeGcRepo(due=[DueMark(f"m{i}", f"assets/{c}/i{i}/", c) for i, c in marks])

    def _lister(self, table):
        def list_keys_under(prefix: str) -> list[str]:
            if isinstance(table.get(prefix), Exception):
                raise table[prefix]
            return list(table.get(prefix, []))
        return list_keys_under

    async def test_deletes_in_per_collection_batches_and_stamps_collected(self):
        from pipeline.storage.platform import DeleteOutcome

        repo = self._repo((1, "c1"), (2, "c2"), (3, "c1"))
        listing = {
            "assets/c1/i1/": ["assets/c1/i1/a.tif", "assets/c1/i1/b.tif"],
            "assets/c2/i2/": ["assets/c2/i2/a.tif"],
            "assets/c1/i3/": ["assets/c1/i3/a.tif"],
        }
        calls: list[list[str]] = []

        def deleter(keys):
            calls.append(list(keys))
            return DeleteOutcome(deleted=tuple(keys), failed={})

        result = await collect_tick(
            repo, list_keys_under=self._lister(listing), delete_keys=deleter, batch_limit=10, list_concurrency=2
        )
        assert result.marks_seen == 3 and result.collected_marks == 3 and result.deleted_objects == 4
        assert result.errors == 0
        # two calls, one per collection, each within its own prefix
        assert len(calls) == 2
        for batch in calls:
            colls = {k.split("/")[1] for k in batch}
            assert len(colls) == 1
        assert sorted(repo.collected) == ["m1", "m2", "m3"]

    async def test_a_partial_delete_failure_keeps_only_the_affected_mark_open(self):
        from pipeline.storage.platform import DeleteOutcome

        repo = self._repo((1, "c1"), (2, "c1"))
        listing = {"assets/c1/i1/": ["assets/c1/i1/a.tif"], "assets/c1/i2/": ["assets/c1/i2/a.tif"]}

        def deleter(keys):
            return DeleteOutcome(
                deleted=tuple(k for k in keys if "i1" in k),
                failed={k: "AccessDenied: nope" for k in keys if "i2" in k},
            )

        result = await collect_tick(
            repo, list_keys_under=self._lister(listing), delete_keys=deleter, batch_limit=10, list_concurrency=2
        )
        assert result.collected_marks == 1 and result.errors == 1 and result.deleted_objects == 1
        assert repo.collected == ["m1"]
        assert repo.errors[0][0] == "m2" and "AccessDenied" in repo.errors[0][1]

    async def test_a_listing_error_keeps_that_mark_open_and_the_rest_proceed(self):
        from pipeline.storage.platform import DeleteOutcome

        repo = self._repo((1, "c1"), (2, "c1"))
        listing = {"assets/c1/i1/": RuntimeError("minio down"), "assets/c1/i2/": ["assets/c1/i2/a.tif"]}
        result = await collect_tick(
            repo,
            list_keys_under=self._lister(listing),
            delete_keys=lambda keys: DeleteOutcome(deleted=tuple(keys), failed={}),
            batch_limit=10,
            list_concurrency=2,
        )
        assert result.errors == 1 and result.collected_marks == 1
        assert repo.errors[0][0] == "m1" and "minio down" in repo.errors[0][1]
        assert repo.collected == ["m2"]

    async def test_a_raising_delete_call_keeps_the_whole_batch_open(self):
        repo = self._repo((1, "c1"), (2, "c1"))
        listing = {"assets/c1/i1/": ["assets/c1/i1/a.tif"], "assets/c1/i2/": ["assets/c1/i2/a.tif"]}

        def deleter(keys):
            raise RuntimeError("bucket gone")

        result = await collect_tick(
            repo, list_keys_under=self._lister(listing), delete_keys=deleter, batch_limit=10, list_concurrency=2
        )
        assert result.collected_marks == 0 and result.errors == 2
        assert {e[0] for e in repo.errors} == {"m1", "m2"}

    async def test_empty_prefix_is_a_normal_zero(self):
        from pipeline.storage.platform import DeleteOutcome

        repo = self._repo((1, "ref-coll"))
        result = await collect_tick(
            repo,
            list_keys_under=lambda prefix: [],
            delete_keys=lambda keys: DeleteOutcome(deleted=(), failed={}),
            batch_limit=10,
            list_concurrency=2,
        )
        assert result.collected_marks == 1 and result.deleted_objects == 0
```
Append to `test_config.py`:
```python
def test_gc_collect_settings_defaults_and_env():
    from pipeline.config import (
        DEFAULT_GC_COLLECT_LIST_CONCURRENCY,
        DEFAULT_GC_COLLECT_MARKS,
        DEFAULT_GC_COLLECT_TICK_BUDGET_SECONDS,
        Settings,
    )

    s = Settings.from_env({})
    assert (s.gc_collect_marks, s.gc_collect_list_concurrency, s.gc_collect_tick_budget_seconds) == (
        DEFAULT_GC_COLLECT_MARKS, DEFAULT_GC_COLLECT_LIST_CONCURRENCY, DEFAULT_GC_COLLECT_TICK_BUDGET_SECONDS
    ) == (10000, 8, 240)
    s = Settings.from_env({"GC_COLLECT_MARKS": "500", "GC_COLLECT_LIST_CONCURRENCY": "2", "GC_COLLECT_TICK_BUDGET_SECONDS": "30"})
    assert (s.gc_collect_marks, s.gc_collect_list_concurrency, s.gc_collect_tick_budget_seconds) == (500, 2, 30)
```
New `tests/test_gc_jobs.py` (the drain loop is a pure function so it is testable without Procrastinate):
```python
"""M3-F: the collect job drains rounds until a short round or the budget."""

from __future__ import annotations

import pytest

from pipeline.gc.sweep import CollectTickResult
from pipeline.jobs.gc import drain_collect_rounds


@pytest.mark.asyncio
async def test_drains_until_a_short_round():
    rounds = iter([
        CollectTickResult(marks_seen=10, collected_marks=10, deleted_objects=15, errors=0),
        CollectTickResult(marks_seen=10, collected_marks=9, deleted_objects=12, errors=1),
        CollectTickResult(marks_seen=3, collected_marks=3, deleted_objects=3, errors=0),
    ])

    async def one_round():
        return next(rounds)

    clock = iter([0.0, 1.0, 2.0, 3.0])
    total = await drain_collect_rounds(one_round, batch_limit=10, budget_seconds=240, monotonic=lambda: next(clock))
    assert total.marks_seen == 23 and total.collected_marks == 22 and total.deleted_objects == 30 and total.errors == 1


@pytest.mark.asyncio
async def test_stops_when_the_budget_is_spent_even_if_rounds_are_full():
    calls = 0

    async def one_round():
        nonlocal calls
        calls += 1
        return CollectTickResult(marks_seen=10, collected_marks=10, deleted_objects=10, errors=0)

    clock = iter([0.0, 100.0, 250.0, 400.0])
    total = await drain_collect_rounds(one_round, batch_limit=10, budget_seconds=240, monotonic=lambda: next(clock))
    assert calls == 2  # the third round would start past the budget
    assert total.marks_seen == 20
```

- [ ] **Step 2: Run to verify they fail** — the three files → TypeError (`collect_tick` signature) / ImportError.

- [ ] **Step 3: Implement**

`config.py` — beside `DEFAULT_GC_BATCH_ITEMS`:
```python
# M3-F: the collect leg at rate (spec §3, S-B). Marks per round, concurrent
# listings per round, and how long one tick keeps draining rounds. One round
# holds ≤ GC_COLLECT_MARKS × ~1.5 keys × ~120 B ≈ 2 MB — a fixed term of the
# memory envelope, not a multiple of the backlog.
DEFAULT_GC_COLLECT_MARKS = 10000
DEFAULT_GC_COLLECT_LIST_CONCURRENCY = 8
DEFAULT_GC_COLLECT_TICK_BUDGET_SECONDS = 240
```
three `Settings` fields after `gc_batch_items`, three `from_env` lines (`int(env.get("GC_COLLECT_MARKS", str(DEFAULT_GC_COLLECT_MARKS)))` etc.), three docstring bullets after the `GC_BATCH_ITEMS` bullet.

`metrics.py`:
```python
ASSET_GC_OBJECTS_DELETED = Counter(
    "pipeline_asset_gc_objects_deleted_total",
    "Objects deleted by the asset collector (M3-F)",
    registry=REGISTRY,
)
ASSET_GC_MARKS_COLLECTED = Counter(
    "pipeline_asset_gc_marks_collected_total",
    "asset_gc marks stamped collected (M3-F)",
    registry=REGISTRY,
)
ASSET_GC_DUE_MARKS = Gauge(
    "pipeline_asset_gc_due_marks",
    "Open asset_gc marks past collect_after after the last collect tick (M3-F backlog)",
    registry=REGISTRY,
)
```
plus the three names in `__all__` (alphabetical).

`gc/sweep.py` — replace `collect_tick` and its result:
```python
@dataclass(frozen=True)
class CollectTickResult:
    marks_seen: int
    collected_marks: int
    deleted_objects: int
    errors: int


async def collect_tick(
    repo: GcRepo,
    *,
    list_keys_under: Callable[[str], list[str]],
    delete_keys: Callable[[Sequence[str]], DeleteOutcome],
    batch_limit: int,
    list_concurrency: int,
) -> CollectTickResult:
    """One round: claim ≤ batch_limit due marks, list their keys concurrently,
    delete in per-collection batches, write the outcomes in bulk.

    Both callables are blocking boto3 — they run under ``to_thread``.
    Failure is per mark: a listing that raised, a key the daemon refused, or
    a batch call that raised keeps exactly the affected marks open with the
    error; everything else is stamped collected (ADR 0011's ordering and
    grace are untouched — this only changes how many marks one round moves).
    """
    marks = await repo.list_due_marks(batch_limit)
    if not marks:
        return CollectTickResult(0, 0, 0, 0)
    errors: list[tuple[str, str]] = []

    gate = asyncio.Semaphore(max(1, list_concurrency))

    async def _list(mark: DueMark) -> tuple[DueMark, list[str] | Exception]:
        async with gate:
            try:
                return mark, await asyncio.to_thread(list_keys_under, mark.object_key)
            except Exception as exc:  # storage outage must not kill the round
                return mark, exc

    listed: list[tuple[DueMark, list[str]]] = []
    for mark, keys in await asyncio.gather(*(_list(m) for m in marks)):
        if isinstance(keys, Exception):
            errors.append((mark.id, str(keys)))
            logger.warning(
                "asset collect listing failed; mark stays open",
                extra={"mark_id": mark.id, "object_key": mark.object_key},
            )
        else:
            listed.append((mark, keys))

    batches, stray_errors = plan_delete_batches(listed)
    errors.extend(stray_errors)
    failed_marks: dict[str, str] = {m: e for m, e in errors}
    key_owner = {k: mark.id for mark, keys in listed for k in keys}
    deleted = 0
    for batch in batches:
        try:
            outcome = await asyncio.to_thread(delete_keys, batch.keys)
        except Exception as exc:
            for mark_id in batch.mark_ids:
                failed_marks.setdefault(mark_id, str(exc))
            logger.warning(
                "asset collect batch delete failed; marks stay open",
                extra={"collection_id": batch.collection_id, "marks": len(batch.mark_ids), "keys": len(batch.keys)},
            )
            continue
        deleted += len(outcome.deleted)
        for key, error in outcome.failed.items():
            failed_marks.setdefault(key_owner[key], error)

    collected_ids = [m.id for m, _ in listed if m.id not in failed_marks]
    await repo.record_collected_many(collected_ids)
    await repo.record_collect_errors(sorted(failed_marks.items()))
    ASSET_GC_OBJECTS_DELETED.inc(deleted)
    ASSET_GC_MARKS_COLLECTED.inc(len(collected_ids))
    return CollectTickResult(
        marks_seen=len(marks),
        collected_marks=len(collected_ids),
        deleted_objects=deleted,
        errors=len(failed_marks),
    )
```
(imports: `Sequence`, `DeleteOutcome` from `pipeline.storage.platform`, the two counters from `pipeline.metrics`.) Delete the old `delete_under_prefix` docstring reference in the module docstring and describe the batched shape in one sentence.

`jobs/gc.py`:
```python
async def drain_collect_rounds(
    one_round: Callable[[], Awaitable[CollectTickResult]],
    *,
    batch_limit: int,
    budget_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> CollectTickResult:
    """Run rounds until one comes back short (the backlog is drained) or the
    tick's time budget is spent — capacity sizes itself to the backlog
    instead of to a constant, which is what S-B found 18× short."""
    started = monotonic()
    seen = collected = deleted = errors = 0
    while True:
        result = await one_round()
        seen += result.marks_seen
        collected += result.collected_marks
        deleted += result.deleted_objects
        errors += result.errors
        if result.marks_seen < batch_limit:
            break
        if monotonic() - started >= budget_seconds:
            break
    return CollectTickResult(seen, collected, deleted, errors)
```
and `asset_collect` becomes:
```python
    async def asset_collect(timestamp: int) -> None:
        repo = PgGcRepo(settings.database_url)
        due = await repo.count_due_marks()
        ASSET_GC_DUE_MARKS.set(due)
        if not due:  # don't build an S3 client on an idle tick
            return
        client = build_platform_client(settings)
        result = await drain_collect_rounds(
            lambda: collect_tick(
                repo,
                list_keys_under=partial(list_keys, client, settings.staging_bucket),
                delete_keys=partial(delete_keys, client, settings.staging_bucket),
                batch_limit=settings.gc_collect_marks,
                list_concurrency=settings.gc_collect_list_concurrency,
            ),
            batch_limit=settings.gc_collect_marks,
            budget_seconds=settings.gc_collect_tick_budget_seconds,
        )
        ASSET_GC_DUE_MARKS.set(await repo.count_due_marks())
        logger.info(
            "asset collect swept due marks",
            extra={
                "marks_seen": result.marks_seen,
                "collected_marks": result.collected_marks,
                "deleted_objects": result.deleted_objects,
                "errors": result.errors,
                "due_before": due,
                "scheduled_timestamp": timestamp,
            },
        )
```
(imports: `time`, `Awaitable`/`Callable`, `list_keys`, `delete_keys`, `ASSET_GC_DUE_MARKS`, `CollectTickResult`; `delete_prefix` is no longer imported here — keep the function in `platform.py`, it has other tests.)

`services/pipeline/README.md`: three env-contract rows after `GC_BATCH_ITEMS` (`GC_COLLECT_MARKS` `10000` — marks one collect round claims; the tick drains rounds until short or `GC_COLLECT_TICK_BUDGET_SECONDS`; `GC_COLLECT_LIST_CONCURRENCY` `8`; `GC_COLLECT_TICK_BUDGET_SECONDS` `240`), and one sentence in "Memory envelope (M3-C)": `The asset collector holds one round of listings — ≤ GC_COLLECT_MARKS × ~1.5 keys × ~120 B ≈ 2 MB — a fixed term.` `docker-compose.yml`: three env lines after `GC_BATCH_ITEMS` (if present; else after the M3-D block) with `${VAR:-default}`.

- [ ] **Step 4: Gates and commit**
`uv run pytest -q`, `uv run ruff check .` → green.
```bash
git add services/pipeline/src/pipeline/config.py services/pipeline/src/pipeline/metrics.py services/pipeline/src/pipeline/gc/sweep.py services/pipeline/src/pipeline/jobs/gc.py services/pipeline/README.md docker-compose.yml services/pipeline/tests/test_gc_sweep.py services/pipeline/tests/test_config.py services/pipeline/tests/test_gc_jobs.py
git commit -m "feat(gc): batched collect tick — concurrent listings, per-collection DeleteObjects, bulk outcome writes, drain rounds within a tick budget; GC_COLLECT_* settings; asset_gc metrics (M3-F)"
```

### Task 4: The `asset_gc_backlog` alert

**Files:**
- Modify: `tests/contract-fixtures/alert-kinds.json` (`kinds` + `monitor_kinds` gain `asset_gc_backlog`, appended last; the `description` gains one sentence)
- Modify: `services/pipeline/src/pipeline/flow/monitor.py` (`MONITOR_KINDS`, the condition in `monitor_tick`)
- Modify: `services/pipeline/src/pipeline/flow/repo.py` (`count_backlogged_marks_by_collection`)
- Modify: `services/pipeline/src/pipeline/jobs/monitor.py` (pass the two settings)
- Modify: `services/pipeline/src/pipeline/config.py` (`DEFAULT_GC_BACKLOG_ALERT_MARKS = 1000`, `DEFAULT_GC_BACKLOG_ALERT_SECONDS = 3600`, fields, env, bullets), `services/pipeline/README.md` (two rows), `docker-compose.yml` (two lines)
- Modify: `app/src/components/monitoring/shared.ts` (`ALERT_KIND_LABEL.asset_gc_backlog = "asset GC backlog"`)
- Test: `services/pipeline/tests/test_flow_monitor.py` (fake + two tests), `services/pipeline/tests/test_config.py` (extend the M3-F test), the two fixture consumers run unchanged (`tests/test_contract_fixtures.py`, `app/src/__tests__/contract-fixtures.test.ts`)

**Interfaces:**
- Produces:
  ```python
  # flow/repo.py
  async def count_backlogged_marks_by_collection(self, older_than_seconds: int) -> list[tuple[str, int]]
      # SELECT collection_id, count(*) FROM stac_higher.asset_gc WHERE collected_at IS NULL AND collect_after <= now() - make_interval(secs => %s) GROUP BY collection_id
  # flow/monitor.py
  async def monitor_tick(repo, *, ingest_max_retries, push_lookback_seconds, gc_backlog_marks: int = 1000, gc_backlog_seconds: int = 3600, now=None)
  ```

- [ ] **Step 1: Failing tests**

`test_flow_monitor.py` — `FakeMonitorRepo` gains `backlogged_marks: list[tuple[str, int]] = field(default_factory=list)` and
```python
    async def count_backlogged_marks_by_collection(self, older_than_seconds: int) -> list[tuple[str, int]]:
        self.backlog_queries.append(older_than_seconds)
        return list(self.backlogged_marks)
```
(`backlog_queries: list[int] = field(default_factory=list)`), and after the job_failure tests:
```python
async def test_asset_gc_backlog_fires_per_collection_above_the_threshold():
    repo = FakeMonitorRepo(backlogged_marks=[("sentinel-2", 1500), ("small", 12)])
    raised, _ = await _tick(repo)
    alerts = [a for a in open_alerts(repo) if a["kind"] == "asset_gc_backlog"]
    assert [(a["collection_id"], a["source"]) for a in alerts] == [("sentinel-2", "job_failure")]
    assert "1500 asset prefixes have waited over 1 h" in alerts[0]["message"]
    assert repo.backlog_queries == [3600]


async def test_asset_gc_backlog_auto_resolves_when_the_collector_catches_up():
    repo = FakeMonitorRepo(backlogged_marks=[("sentinel-2", 1500)])
    await _tick(repo)
    repo.backlogged_marks = []
    _, resolved = await _tick(repo)
    assert resolved == 1
    assert not [a for a in open_alerts(repo) if a["kind"] == "asset_gc_backlog"]
```
(read `_tick` and `open_alerts` in the file — if `_tick` passes explicit kwargs, add `gc_backlog_marks=1000, gc_backlog_seconds=3600` there so the defaults are exercised through it.)

Extend the Task 3 config test with `DEFAULT_GC_BACKLOG_ALERT_MARKS == 1000`, `DEFAULT_GC_BACKLOG_ALERT_SECONDS == 3600`, and the env override for both.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_flow_monitor.py tests/test_config.py tests/test_contract_fixtures.py -q` → the fake cannot be instantiated as an ABC subclass without the new method once the ABC has it; before that, the kind test fails on `open_alerts`.

- [ ] **Step 3: Implement**

`alert-kinds.json`: append `"asset_gc_backlog"` to `kinds` and to `monitor_kinds` (both LAST — the fixture's consumers assert `monitor + declared + notify == kinds` in order); append to `description`: ` M3-F added asset_gc_backlog (monitor-owned, collection-anchored): open asset_gc marks past their collect_after for longer than GC_BACKLOG_ALERT_SECONDS, above GC_BACKLOG_ALERT_MARKS per collection.`

`flow/repo.py` — ABC + Pg:
```python
    @abc.abstractmethod
    async def count_backlogged_marks_by_collection(self, older_than_seconds: int) -> list[tuple[str, int]]:
        """M3-F: open asset_gc marks whose collect_after passed more than
        ``older_than_seconds`` ago, per collection — the collector's quiet
        failure made visible."""
```
```python
    async def count_backlogged_marks_by_collection(  # pragma: no cover
        self, older_than_seconds: int
    ) -> list[tuple[str, int]]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT collection_id, count(*) FROM stac_higher.asset_gc"
                " WHERE collected_at IS NULL"
                "   AND collect_after <= now() - make_interval(secs => %s)"
                " GROUP BY collection_id",
                (older_than_seconds,),
            )
            rows = await cur.fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]
```
`flow/monitor.py`: `MONITOR_KINDS` gains `"asset_gc_backlog"` last with a comment (`# M3-F: the collector's backlog, observed from asset_gc rows — auto-resolves when the collector catches up.`); `monitor_tick` gains `gc_backlog_marks: int = 1000, gc_backlog_seconds: int = 3600` and, before `return await repo.sync_alerts(...)`:
```python
    # -- job_failure: the asset collector is behind (M3-F, S-B's quiet failure)
    hours = max(1, round(gc_backlog_seconds / 3600))
    for collection_id, count in await repo.count_backlogged_marks_by_collection(gc_backlog_seconds):
        if count < gc_backlog_marks:
            continue
        conditions.append(
            AlertCondition(
                source="job_failure",
                kind="asset_gc_backlog",
                collection_id=collection_id,
                message=(
                    f"{count} asset prefixes have waited over {hours} h for collection "
                    "(open-mark backlog)"
                ),
            )
        )
```
`jobs/monitor.py`: pass `gc_backlog_marks=settings.gc_backlog_alert_marks, gc_backlog_seconds=settings.gc_backlog_alert_seconds`. `config.py`: the two constants, fields, `from_env` lines, docstring bullets. README two rows; compose two lines. `shared.ts`: the label after `process_rate_limited`.

- [ ] **Step 4: Gates and commit**
From `services/pipeline/`: `uv run pytest -q`, `uv run ruff check .`; from the worktree root: `npm run verify` (the fixture consumer + the label test).
```bash
git add tests/contract-fixtures/alert-kinds.json services/pipeline/src/pipeline/flow/monitor.py services/pipeline/src/pipeline/flow/repo.py services/pipeline/src/pipeline/jobs/monitor.py services/pipeline/src/pipeline/config.py services/pipeline/README.md docker-compose.yml app/src/components/monitoring/shared.ts services/pipeline/tests/test_flow_monitor.py services/pipeline/tests/test_config.py
git commit -m "feat(monitor): asset_gc_backlog alert — per-collection open-mark backlog past an hour, monitor-owned, auto-resolving; fixture + label (M3-F)"
```

### Task 5: Measure, deploy, merge (lead only, Docker)

- [ ] Worktree gates; `git checkout ai/main && git merge ai/m3-f-gc-at-rate --no-ff`; verify + pytest + ruff on `ai/main`; `docker compose build pipeline && docker compose up -d pipeline`.
- [ ] **Backlog assertion (spec §3 "M3-F has a backlog assertion"):** `loadgen --label m3f setup --mode copy` + `feed --profile raster --rate 0 --count 2000 --asset-bytes 1048576` (2000 items, ~1 MB each, copy mode → 2000 canonical prefixes); then seed marks directly (the retention leg is not under test):
  ```sql
  INSERT INTO stac_higher.asset_gc (object_key, collection_id, item_id, reason, collect_after)
  SELECT 'assets/' || collection || '/' || id || '/', collection, id, 'retention', now() - interval '2 hours'
    FROM pgstac.items WHERE collection LIKE '%m3f%';
  ```
  Record `count_due` before; wait for the next collect tick (≤5 min) or `docker compose restart pipeline` to bring it forward; read the tick's log line (`marks_seen`, `collected_marks`, `deleted_objects`, `errors`, elapsed from the two timestamps) and `pipeline_asset_gc_due_marks` after. Expected: all 2000 marks collected in ONE tick (one round), `errors = 0`, `due_marks = 0`; compute marks/s and objects/s; extrapolate to 9 028 marks per tick. RSS during the tick from `docker stats`.
  Then the alert: seed 1 200 marks with `collect_after = now() - interval '2 hours'` on a prefix the collector will fail on (point them at a collection whose bucket ACL refuses, or simplest: stop MinIO for one tick → listing errors keep them open), wait one monitor tick, `GET /api/alerts` shows one `asset_gc_backlog` for the collection; restart MinIO, wait a collect + monitor tick, the alert auto-resolves. Chrome: the alert's label reads "asset GC backlog" on the monitoring page (screenshot noted).
- [ ] `loadgen teardown`; `DELETE FROM stac_higher.asset_gc WHERE collection_id LIKE '%m3f%'` only if teardown leaves rows.
- [ ] `TODO.md` tick + landed note (numbers, the per-collection anchor ruling); `docs/FEATURES.md`; `docs/ISSUES.md` (S-B's retention-leg 18× per-collection ceiling stays open as a note if not already an issue); worktree removal; canary re-check.

## Self-review

- Spec §3 M3-F row: batched `DeleteObjects` (T2/T3), envelope-sized batch (T3 settings + README term), alert on an open-mark backlog (T4); the risk note's two requirements — prefix construction in `storage/keys.py` (T1) and a test that a batch never spans two collections (T2 `test_groups_by_collection_and_never_spans_two` + T3's per-collection call assertion). ADR 0011 ordering/grace untouched (no change to `retention_tick`, `list_due_marks` still filters on `collect_after <= now()`). Backlog assertion: T5.
- Type consistency: `DueMark(id, object_key, collection_id)` used in T1–T4 and the tests; `DeleteOutcome(deleted: tuple, failed: dict)` from T2 consumed in T3's tick and tests; `collect_tick(repo, *, list_keys_under, delete_keys, batch_limit, list_concurrency)` matches the job's call; `CollectTickResult(marks_seen, collected_marks, deleted_objects, errors)` positional order matches `drain_collect_rounds`'s constructor; `count_backlogged_marks_by_collection(older_than_seconds)` identical in ABC, Pg, fake and the tick.
- Placeholders: none.
