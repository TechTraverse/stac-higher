# ADR 0003 — Default ownership for pre-existing collections

Status: accepted (Phase 1) · Date: 2026-07-14

## Context

Phase 1 introduces `stac_higher.collection_settings` (ROADMAP §5): per-
collection group ownership, external writability, and retention. ROADMAP §10
flags an open question: what do collections that already exist when Phase 1
lands (and, more generally, collections created outside the settings UI)
default to?

Constraints that shape the answer:

- **Phase 1 compatibility is a hard requirement**: anonymous catalog reads
  through the auth proxy keep working (`DEFAULT_PUBLIC=true` this phase), and
  local dev / unit tests / e2e must not start failing because collections
  suddenly acquired owners.
- Collections are created in **pgstac, out of band** from the app (stac-
  fastapi transactions, pypgstac loads, future ingest). The app cannot
  guarantee a settings row exists for every collection at any point in time —
  the "no row" case is permanent, not a one-time migration artifact.
- §7 gives `operator` the "manage collection exposure" capability and `admin`
  cross-group everything.

## Decision

**Pre-existing (and otherwise unconfigured) collections are UNOWNED and
PUBLIC: the absence of a `collection_settings` row — equivalently a row with
`group_id NULL` — means visible to all users and mutable by operators of any
group as well as admins.** Remaining defaults: `externally_writable = false`,
`retention_days = NULL` (keep forever), `gc_grace_days = 30`.

Mechanically:

- The table is **sparse** and defaults are **applied on read**
  (`app/src/lib/collections/settings.ts` — `defaultCollectionSettings` is the
  single source of truth). No backfill runs: a backfill would race with
  out-of-band collection creation and still leave the no-row case to define.
- Claiming a collection later = an operator/admin inserting or updating its
  row with a `group_id` (Collection Settings UI, later phase); that mutation
  is audited like any other.

## Alternatives considered

- **Assign a bootstrap/default group** — rejected: it invents ownership
  nobody chose, silently hides pre-existing collections from every other
  group the moment visibility filtering turns on, and requires the app to
  know a group name that lives in the IdP.
- **Invisible-until-claimed (private by default)** — rejected for Phase 1: it
  directly contradicts the compatibility requirement (anonymous reads keep
  working, `DEFAULT_PUBLIC=true`) and would blank the built-in catalog UX on
  upgrade. Deployments that want deny-by-default can claim every collection
  and flip the proxy default when visibility enforcement lands.
- **One-time backfill of explicit rows** — rejected: racy against out-of-band
  creation and redundant once defaults-on-read exists.

## Consequences

- Retention/GC (Phase 6) treats no-row collections as "keep forever" — safe.
- `externally_writable = false` by default means push ingest (Phase 7) is
  opt-in per collection, as intended.
- When per-collection visibility filtering arrives at the auth proxy,
  `group_id NULL` must translate to "visible to all" in the CQL2 filter
  derivation, keeping today's behavior for unclaimed collections.
