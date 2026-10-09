"""Write one batch of parsed steps into a cube in ONE commit (spec §6.2 steps 5-7).

Blocking (Icechunk and zarr), so the job runs it through ``asyncio.to_thread``.

Each attempt works in a fresh session on ``main``:
1. Read the tip.
2. Classify every step: duplicate, late, layout mismatch, or accepted.
3. Write the accepted steps with ``last_updated_at`` = the source's LastModified.
4. Trim the window in the same session.
5. Commit.

A step whose write raises is failed, and the attempt is redone without it in a
new session; the half-written one is dropped. A ``ConflictError`` means someone
committed since the session opened (a double run). It redoes the attempt once
from the new tip, where steps already written read as duplicates. A second
conflict propagates, and the job retries. Never a rebase or a conflict solver
(ADR 0022).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import icechunk as ic
import numpy as np
import xarray as xr
import zarr

from pipeline.cubes.config import CubeSinkConfig
from pipeline.cubes.icerepo import BRANCH, CubeState, read_state
from pipeline.cubes.steps import (
    LayoutError,
    StaticSpec,
    check_layout,
    check_statics,
    step_specs,
    step_statics,
    trim_count,
)

logger = logging.getLogger(__name__)

REASON_DUPLICATE = "duplicate"
REASON_LATE = "late"
REASON_UNSUPPORTED_LAYOUT = "unsupported_layout"
#: a failed row's reason is free text; keep it short enough to show in the UI
MAX_ERROR_CHARS = 500

Outcome = tuple[str, str | None]


@dataclass(frozen=True)
class ParsedStep:
    row_id: int
    item_id: str
    step: xr.Dataset
    #: the step's ``append_dim`` value
    value: np.generic
    #: the source object's LastModified, taken before the parse
    last_modified: dt.datetime


@dataclass(frozen=True)
class BatchResult:
    #: row id -> (status, reason); appended rows take ``snapshot_id``
    outcomes: dict[int, Outcome]
    #: the branch tip after the batch: the new commit, or the unchanged tip
    snapshot_id: str
    committed: bool
    #: ``append_dim`` values after the batch
    values: np.ndarray
    trimmed: int
    #: the cube has its ``append_dim`` array (False only if nothing was ever written)
    initialised: bool
    #: the cube's variables without the append axis (``x``, ``y``, the grid
    #: mapping) and its data variables' other dimensions, for the collection
    #: asset writer (Z-5). Empty when the caller did not read them (Z-6's
    #: trim may not): the writer then keeps the document's spatial dimensions.
    statics: Mapping[str, StaticSpec] = field(default_factory=dict)
    spatial_dims: tuple[str, ...] = ()


def error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]


def write_step(session: ic.Session, parsed: ParsedStep, append_dim: str | None) -> None:
    """One step's virtual references (and its native arrays) into the session.
    ``append_dim=None`` writes a new cube's first step."""
    parsed.step.vz.to_icechunk(
        session.store, append_dim=append_dim, last_updated_at=parsed.last_modified
    )


def commit_session(session: ic.Session, message: str) -> str:
    return session.commit(message)


class _StepWriteFailed(Exception):
    def __init__(self, row_id: int, message: str) -> None:
        super().__init__(message)
        self.row_id = row_id
        self.message = message


def write_batch(
    repo: ic.Repository,
    parsed: Sequence[ParsedStep],
    config: CubeSinkConfig,
    now: dt.datetime,
) -> BatchResult:
    try:
        return _attempt(repo, parsed, config, now)
    except ic.ConflictError:
        logger.warning(
            "cube commit conflicted; redoing the batch once from the new tip",
            extra={"steps": len(parsed)},
        )
        return _attempt(repo, parsed, config, now)


def classify(
    parsed: Sequence[ParsedStep],
    state: CubeState,
    config: CubeSinkConfig,
    exclude: frozenset[int] = frozenset(),
) -> tuple[dict[int, Outcome], list[ParsedStep]]:
    """Each step's outcome against the cube, and the accepted steps in time order."""
    outcomes: dict[int, Outcome] = {}
    accepted: list[ParsedStep] = []
    present = set(state.values)
    tip = state.values[-1] if len(state.values) else None
    specs = dict(state.specs) if state.initialised else None
    statics = dict(state.statics) if state.initialised else None
    for p in sorted(parsed, key=lambda p: (p.value, p.row_id)):
        if p.row_id in exclude:
            continue
        if p.value in present:
            outcomes[p.row_id] = ("appended", REASON_DUPLICATE)
            continue
        if tip is not None and p.value <= tip:
            outcomes[p.row_id] = ("skipped", REASON_LATE)
            continue
        try:
            mine, grid = step_specs(p.step, config), step_statics(p.step, config)
            if specs is None or statics is None:
                specs, statics = mine, grid  # the first step of a new cube sets its layout
            else:
                check_layout(mine, specs)
                check_statics(grid, statics)
        except LayoutError as exc:
            logger.warning(
                "cube step skipped: unsupported layout",
                extra={"item_id": p.item_id, "detail": str(exc)},
            )
            outcomes[p.row_id] = ("skipped", REASON_UNSUPPORTED_LAYOUT)
            continue
        accepted.append(p)
        present.add(p.value)
        tip = p.value
        outcomes[p.row_id] = ("appended", None)
    return outcomes, accepted


def _attempt(
    repo: ic.Repository,
    parsed: Sequence[ParsedStep],
    config: CubeSinkConfig,
    now: dt.datetime,
) -> BatchResult:
    dim = config.append_dim
    failed: dict[int, str] = {}
    while True:
        session = repo.writable_session(BRANCH)
        state = read_state(session, dim)
        outcomes, accepted = classify(parsed, state, config, frozenset(failed))
        try:
            for i, step in enumerate(accepted):
                try:
                    write_step(session, step, dim if state.initialised or i > 0 else None)
                except Exception as exc:
                    # Decision 9: a platform-store outage fails the job (retry),
                    # not the row; the rows append once the store returns.
                    if _is_storage_error(exc):
                        raise
                    raise _StepWriteFailed(step.row_id, error_text(exc)) from exc
        except _StepWriteFailed as err:
            logger.warning(
                "cube step write failed; redoing the batch without it",
                extra={"row_id": err.row_id, "error": err.message},
            )
            failed[err.row_id] = err.message
            continue
        break
    for row_id, message in failed.items():
        outcomes[row_id] = ("failed", message)
    after = read_state(session, dim)
    trimmed = trim_count(after.values, config.window, now)
    if trimmed:
        trim_steps(session, after, trimmed)
    if session.has_uncommitted_changes:
        snapshot_id = commit_session(session, _message(accepted, trimmed))
        committed = True
    else:
        snapshot_id = repo.lookup_branch(BRANCH)
        committed = False
    return BatchResult(
        outcomes=outcomes,
        snapshot_id=snapshot_id,
        committed=committed,
        values=after.values[trimmed:],
        trimmed=trimmed,
        initialised=after.initialised,
        statics=after.statics,
        spatial_dims=after.spatial_dims,
    )


def _is_storage_error(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ic.StorageError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def trim_steps(session: ic.Session, state: CubeState, k: int) -> None:
    """Drop the ``k`` oldest steps of every time-dimensioned array (spike
    soak.py): shift the chunk grid down, then shrink. Every time array has
    time chunk 1, so a shift of ``k`` chunks is ``k`` steps."""
    group = zarr.open_group(session.store, mode="r+")
    for name in state.time_arrays:
        if group[name].chunks[0] != 1:
            raise RuntimeError(
                f"{name} has time chunk {group[name].chunks[0]}; the window shift needs 1"
            )
    for name in state.time_arrays:
        array = group[name]
        session.shift_array(f"/{name}", (-k,) + (0,) * (array.ndim - 1))
    for name in state.time_arrays:
        array = group[name]
        array.resize((array.shape[0] - k, *array.shape[1:]))


def _message(accepted: Sequence[ParsedStep], trimmed: int) -> str:
    if accepted:
        return f"append {len(accepted)} items: {accepted[0].item_id}…{accepted[-1].item_id}"
    return f"trim {trimmed} steps"
