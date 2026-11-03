# ADR 0023 — Cube maintenance is a second cube writer, for its age trim only

- **Status:** accepted — PR #105 (2026-10-19; implemented by Z-6, issue #92)
- **Amends:** ADR 0022 — "each repository has exactly one writer,
  `pipeline.cube_append`" becomes "one writer **at a time**:
  `pipeline.cube_append`, or `pipeline.cube_maintain_sink` for its age trim".
  The serialization ADR 0022 relies on (one per-sink Procrastinate `lock`) is
  unchanged.
- **Related:** ADR 0022 (virtual cube sink), virtual cube spec
  `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §10, Z-6
  plan `docs/superpowers/plans/2026-10-09-z6-cube-maintain.md`, I-143, I-146.

## Context

ADR 0022 trims a cube's rolling window inside the append's commit, and names
`pipeline.cube_append` as each repository's only writer. Its spike found that
two concurrent appends to one repository always conflict and cannot be
rebased, so the single writer is what keeps commits safe.

A source that stops publishing has no next append. Its cube would never trim
again: the collection would keep advertising steps older than the window
forever. Spec §10 gives the hourly `cube_maintain` the trim for this case, and
a trim is a commit. That makes maintenance a second writer, which ADR 0022's
wording rules out.

## Decision

`pipeline.cube_maintain_sink` may commit to a cube repository, under these
limits:

- **Only an age trim.** It removes steps older than the window
  (`write.trim_steps`, the same code the append uses). It never appends a
  step, never creates a repository and never saves its config.
- **Only when it is the sink's next writer.** It trims only when the sink has
  a window, has no `pending` ledger row, and the branch tip is the snapshot
  recorded on the sink (or a maintenance trim of it, below).
- **Under the same lock.** It holds `lock = cube:{sink_id}`, the append's
  lock, so the two never run together. Its own `queueing_lock`
  (`cube-maintain:{sink_id}`) only stops hourly ticks from piling up behind a
  held lock.
- **Tagged and recorded.** The commit carries snapshot metadata
  `stac_higher:writer = maintenance`, and is recorded on the sink with a
  compare-and-swap from the recorded snapshot. If a crash loses the record,
  the next run recognises its own tagged tip (its parent is the recorded
  snapshot) and records it then.

Expiry and GC are not new writes: ADR 0022 already allows them on a sink with
a window.

## Alternatives considered

- **Leave every trim to `cube_append`.** Rejected: a stopped source never
  trims, which is the case the trim exists for.
- **Have maintenance enqueue a trim-only `cube_append`.** Rejected: the append
  is driven by its ledger rows, and its crash-loop budget and retries count
  per row. A run with no rows has no budget to spend and nothing to record in
  the ledger, so it would need a second mode inside the append job. Expiry
  and GC need the same lock anyway, so maintenance would still hold it.

## Consequences

- "One writer at a time" still holds: both writers take the same lock.
- A maintenance job that crashes holds the lock until `cube_kick` recovers it,
  so the sink's appends wait meanwhile (I-146).
- A tip that is neither recorded nor a tagged maintenance trim is reported as
  attention `unrecorded_tip`, never silently `ok`.
- A later writer of any other kind needs its own ADR.
