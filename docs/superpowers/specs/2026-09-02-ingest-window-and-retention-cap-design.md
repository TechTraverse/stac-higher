# Ingest date window + retention cap — design

**Date:** 2026-09-02
**Status:** **approved** (lead sign-off 2026-09-02). Implementation queue:
`TODO.md` "W queue"; plans in `docs/superpowers/plans/2026-09-02-w{1,2}-*.md`.
**W-1 implemented 2026-09-02** (`ai/w1-window` — §3 in full; the §2 live gate
against `noaa-goes19` is lead-only and still owed). W-2 open.
**Scope source:** the lead's question after measuring what an unbounded NODD
association would do: "is there something we can do to configure a date window
(begin/end, or begin and an open ended future time), and a max number of assets
to retain?"
**Related:** ADR 0011 (retention & GC — the mark-then-collect queue this
reuses), Phase 4 ingest (`docs/FEATURES.md`), the GOES loop spec
(`2026-09-01-goes-geocolor-loop-design.md`) whose §5 named the missing
date-window concept as a gap to settle before G-7.
**Issues to open:** see §10.

---

## 1. Why

Measured on 2026-09-02 against `noaa-goes19`, for a single product
(`ABI-L2-MCMIPC`):

| Scope | Objects | Bytes |
|---|---|---:|
| One hour | 12 | 0.58 GB |
| One day | 288 | 14.2 GB |
| One year | ~102,000 | ~5 TB |
| 2024 → now | ~250,000 | ~12 TB |

The bucket holds 103 products at its root, several with far larger files.

Nothing in the ingest path bounds any of that. `S3Adapter.list` pages an entire
prefix into a Python list with no `MaxKeys`; DISCOVER reconciles everything it
returns; there is no date filter, no count cap, and no notion of "recent". The
only lever an operator has is the prefix string, and NODD's prefixes are
date-partitioned, so a static prefix either covers a fixed past window or the
whole archive. **There is no configuration that means "keep up with the last
few hours"** — which is precisely what a live GOES feed is.

Two other facts sharpen it. Since G-3, s3 sources settle on **first sight**, so
everything the first listing returns is immediately eligible to fetch — the
change made this more eager, not less. And in copy mode FETCH stores bytes
*before* ITEMIZE runs, so a misconfigured association fills the disk even when
every item then fails to itemize.

This spec adds three knobs, in two independent slices:

- **what comes in** — a date window, plus optional prefix expansion so the
  listing itself is bounded;
- **how fast it comes in** — a per-poll ceiling, so a wide window is a paced
  backfill rather than a stampede;
- **what is kept** — a count cap alongside the existing age-based retention.

## 2. Gate (done-when)

1. An association with `window: {begin: "-1h"}` against `noaa-goes19` lists and
   ingests ~12 objects and stops, on a stack with no other configuration.
2. Adding `path_template: "{Y}/{j}/{H}/"` makes the same association issue one
   listing per hour in the window instead of one over the whole product prefix,
   demonstrated by the adapter's recorded list calls.
3. `max_files_per_poll: 4` turns a 1-hour window into three polls, oldest
   first, with the ledger showing the pacing.
4. A collection with `retention_max_items: 24` settles at 24 items, the excess
   marked with the ordinary `retention` reason and collected after the grace
   window — no new deletion path.
5. `npm run verify`, `uv run pytest`, `uv run ruff check .` green; the ingest
   config fixture carries every new shape; docs in §9 updated.

## 3. The ingest window

Three new fields on the ingest association's `config` — a **cross-runtime
contract** (`app/src/lib/associations/schemas.ts` ↔
`services/pipeline/src/pipeline/ingest/config.py` ↔
`tests/contract-fixtures/ingest-config.json` move together):

```json
{
  "source_path": "ABI-L2-MCMIPC/",
  "path_template": "{Y}/{j}/{H}/",
  "window": { "begin": "-6h", "end": null },
  "max_files_per_poll": 200
}
```

All three are optional; omitting them preserves today's behaviour exactly.

### 3.1 `window`

`begin` and `end` each accept either an **RFC3339 timestamp** or a **relative
offset** matching `-<n>[smhd]` (`-90s`, `-30m`, `-6h`, `-7d`). Relative values
are resolved against "now" **on every poll**, which is the whole mechanism by
which a window rolls. `end` absent or null means open — up to now.

| Intent | Config |
|---|---|
| Live feed | `{ "begin": "-6h" }` |
| Live, skipping partially-landed objects | `{ "begin": "-2h", "end": "-10m" }` |
| One-off backfill | `{ "begin": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z" }` |

Validation: `window` present ⇒ `begin` required; offsets must be zero or
negative (a window into the future has no meaning here); if both bounds are
absolute, `begin` < `end`.

**What it matches.** `FileEntry.mtime` — the object's last-modified time. This
is a deliberate approximation and §10 records it: for GOES, mtime is when NOAA
*uploaded* the file, minutes after the instrument scanned it. The scan time
lives in the filename and is not available until G-6's extractors run, which is
far too late to gate a download on.

**An entry with no `mtime` cannot be gated.** S3 always reports one; an FTP
server without MLSD may not. When a window is configured, such entries are
**skipped and counted** (`undateable`), never admitted — the same posture the
stage already takes for unfingerprintable files. The counter and a log line
exist so "my FTP source ingests nothing" is diagnosable rather than mysterious.

### 3.2 `path_template` — bounding the listing, not just the result

The window alone filters *after* listing, so the listing cost is unchanged: a
quarter-million keys paged into memory every five minutes to keep six hours.
`path_template` fixes that by expanding the window into the prefixes worth
listing at all.

Tokens, all zero-padded, resolved from each instant in the window:

| Token | Meaning | Width |
|---|---|---|
| `{Y}` | year | 4 |
| `{m}` | month | 2 |
| `{d}` | day of month | 2 |
| `{j}` | day of year | 3 |
| `{H}` | hour | 2 |

The template is appended to `source_path`. Expansion granularity is the
**finest token present**: `{H}` → hourly, `{d}` or `{j}` → daily, `{m}` →
monthly, `{Y}` → yearly. DISCOVER then lists each expanded prefix and unions
the results.

```
source_path   "ABI-L2-MCMIPC/"
path_template "{Y}/{j}/{H}/"
window        {begin: "-6h"}
→ ABI-L2-MCMIPC/2026/244/18/ … /2026/244/23/     6 listings, ~72 keys
  (without the template: 1 listing, ~250,000 keys)
```

Guards: a template must contain at least one known token and may contain no
unknown ones; `path_template` requires `window` (there is nothing to expand
against otherwise); and an expansion exceeding `max_prefixes`
(`INGEST_MAX_WINDOW_PREFIXES`, default 1000) **fails the tick loudly** rather
than quietly listing for an hour — a 90-day window at hourly granularity is a
configuration mistake, not a workload.

### 3.3 `max_files_per_poll`

An integer ≥ 1, or absent for unlimited (today's behaviour). It caps how many
**new** files one DISCOVER tick admits to the ledger, applied after window
filtering, taking the **oldest first** so a backfill advances forward in time
and can be watched. The remainder is picked up next poll.

The window bounds the steady state; this bounds the approach to it. Both are
needed: the first poll of a six-hour window still faces six hours of files.

### 3.4 What the window is not

The window governs what comes **in**, never what stays. A file that ages out of
a rolling window simply stops being listed; the item it produced remains in the
catalog and is governed by retention alone (§4). The two rules never contend
for the same object, and neither can resurrect or delete what the other owns.

## 4. The retention cap

`retention_max_items` — a nullable positive integer on
`stac_higher.collection_settings`, beside `retention_days`.

Unlike §3 this is **not** a cross-runtime contract: the pipeline reads these as
typed columns, not jsonb, so there is no fixture (the file's own header says
so).

### 4.1 Semantics

Keep the newest N items by **item datetime**, expire the rest:

```sql
SELECT id FROM pgstac.items
 WHERE collection = %s
 ORDER BY datetime DESC, id DESC
OFFSET %s
```

The `id DESC` tiebreak matters: without it, `OFFSET` is not deterministic
across calls when datetimes collide, and the sweep could mark a different
arbitrary subset each tick.

Datetime, not ingestion time — so once G-6's extractors land, "newest" for GOES
means most recently *scanned*, which is what an operator means.

### 4.2 Composition with what exists

- The rules **union**: an item expires if it is older than `retention_days`
  **or** beyond the newest `retention_max_items`. Either declared alone works;
  both declared is a union, not a precedence.
- `archived = true` still overrides everything and expires the whole
  collection. When archived, `retention_max_items` is **ignored** — otherwise
  the count branch would preserve N items in a collection the operator asked to
  empty.
- Expiry uses the existing `retention` reason, so migration 017's CHECK
  constraint is untouched. A count cap *is* a retention policy; inventing a
  third reason would fragment the queue for no gain.
- ADR 0011's mark-first-then-collect ordering and the `gc_grace_days` window
  are unchanged. This adds a row to an existing query's result, nothing more.

### 4.3 Surfaces

- `collection_settings` migration (§8 on numbering) adds the column with a
  `CHECK (retention_max_items IS NULL OR retention_max_items >= 1)`.
- `list_gc_collections`'s predicate gains `OR retention_max_items IS NOT NULL`,
  and `GcCollection` carries the value.
- `list_expired_items` assembles the branches that apply and unions them.
  Building the SQL conditionally (as the method already does for the
  `retention_days IS NULL` case) is preferred over a single query with NULL
  parameters, because `OFFSET NULL` is an error.
- The Settings tab gains a **Maximum items** input beside retention days, on
  the existing warn-and-proceed path.
- `GET /api/collections/[id]/settings/impact` accepts
  `?retention_max_items=N` and answers with the same `total_items` /
  `expired_items` shape, so the dialog can say what the change would delete
  before it is saved.

## 5. Where the changes land

| Area | Change |
|---|---|
| `associations/schemas.ts` | `window`, `path_template`, `max_files_per_poll` |
| `ingest/config.py` | mirrored fields, offset parser, template validator |
| `ingest/window.py` *(new)* | offset parsing, window resolution, prefix expansion — pure, no I/O |
| `ingest/discover.py` | list per expanded prefix; filter by mtime; apply the poll cap; new counters |
| `IngestFormDialog.tsx` | the three fields, with the window explained |
| `ingest-config.json` | fixture cases for every shape above |
| migration | `collection_settings.retention_max_items` |
| `gc/repo.py`, `gc/sweep.py` | count branch in the expired-items query |
| `settings-schemas.ts`, `SettingsTab.tsx`, `settings/impact.ts` | the cap's write path and its dry run |

`ingest/window.py` exists so the arithmetic — parsing `-6h`, flooring to an
hour boundary, enumerating prefixes — is testable without a stack, an adapter,
or a clock. Everything time-dependent takes `now` as a parameter.

## 6. Testing

- **Pure unit** (`ingest/window.py`): offset grammar including rejects; a
  window whose bounds are mixed absolute/relative; expansion at each
  granularity; a window crossing a year boundary (`{j}` must roll 365→001); the
  `max_prefixes` refusal; a template with an unknown token.
- **DISCOVER**: entries outside the window skipped and counted; an entry with
  no mtime skipped and counted when a window is set, admitted when none is;
  the poll cap admitting the oldest N and leaving the rest; one listing per
  expanded prefix, asserted against `FakeAdapter.list_calls`, which already
  records them.
- **Contract fixture**: accept/reject for each new field on both sides.
- **GC**: count-only, age-only, both, and archived-overrides-count; the
  `OFFSET` tiebreak pinned by two items sharing a datetime.
- **Live** (lead): the §2 gate against `noaa-goes19`.

## 7. What this does NOT do

- **No NRT push.** This makes polling bounded, not instant. NOAA's SNS/SQS
  notification feed remains the cloud answer for latency, and is Phase 8.
- **No change to delivery, extraction, or the process path.**
- **Not retroactive.** Adding a window does not remove items ingested before
  it; retention governs those, which is the §3.4 split.
- **No per-association retention.** The cap is a property of the collection
  (the lead's call), so two associations feeding one collection cannot fight
  over what to delete.
- **No byte-based cap.** "Max N items" is the ask; "max N gigabytes" would need
  per-item size accounting the catalog does not keep.

## 8. Decisions settled with the lead 2026-09-02

- Window filters on **both** mtime (always) and, optionally, expanded key
  prefixes — correctness from the first, reachability from the second.
- `begin`/`end`, each absolute **or** relative, `end` optional and open.
- The retain cap lives on **collection settings**, beside `retention_days`.
- A **per-poll fetch cap** ships alongside, because the window bounds the
  steady state and not the surge toward it.

**Open for the lead — migration numbering.** The K queue's K-3 slice plans to
take migration **026**. This work would land first and claim it, so either this
spec takes 026 and `2026-09-02-process-compute-k8s-kueue-design.md` is bumped
to 027, or this takes 027 and leaves 026 reserved. The plans assume **this
takes 026 and K-3 bumps**, since K-3 has not started; say so if you prefer the
reverse.

## 9. Slices

Independent; may run in parallel in separate worktrees.

- **W-1 · Ingest date window + prefix expansion + per-poll cap.** §3.
  Plan: `2026-09-02-w1-ingest-window.md`.
- **W-2 · Retention count cap.** §4.
  Plan: `2026-09-02-w2-retention-cap.md`.

Docs to update: `docs/FEATURES.md` (both), `docs/connections.md` or the ingest
reference for the config fields, `docs/decisions/README.md` if ADR 0011 gains a
note that retention now has two rules feeding one queue.

## 10. Risks and issues to open

- **The window gates on upload time, not observation time.** For GOES these
  differ by minutes; for a producer that backfills old data under a new
  timestamp they could differ by years, and such a file would be admitted by a
  recent window even though its content is old. Documented, not solved:
  gating on observation time requires reading the file, which is the download
  the window exists to avoid.
- **A source with no modified times ingests nothing once a window is set.**
  Mitigated by a counter and a log line, but an operator who sets a window on
  an MLSD-less FTP server will see silence. Consider surfacing `undateable` on
  the Data flow tab.
- **Prefix expansion assumes a UTC, zero-padded, date-partitioned layout.**
  True for NODD and most public archives; a bucket keyed by something else
  simply cannot use the template and falls back to filtering after listing.
- **The per-poll cap interacts with the settle rule.** Files admitted but not
  yet fetched sit `settled` in the ledger; a very small cap against a very
  large window leaves a long-lived backlog there. The existing stall sweeps
  cover correctness; the backlog's *visibility* is worth a follow-up.
- **`max_prefixes` is a blunt refusal.** A 90-day hourly window fails rather
  than degrading to daily granularity. Deliberate — silently changing the
  granularity would change which files are found.
