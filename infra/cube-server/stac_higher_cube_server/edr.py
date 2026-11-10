"""A per-step tolerant EDR ``position`` route (spec §8.4, spike Q5).

A virtual cube reads each step from its NODD source file. When a source is
gone, xpublish-edr's one-shot load fails the whole time series. This route
reuses xpublish-edr 0.11.1's selection pipeline (``_run_position_query`` in
``xpublish_edr/plugin.py``), then loads each step on its own: a step whose
source raises ``icechunk.StorageError`` comes back as NaN (an empty CSV
field, a JSON ``null``) and is counted in ``x-cube-missing-steps``.

Every EDR route (this one and xpublish-edr's ``area`` / ``cube``) sees the
dataset through ``metres_view``: xpublish-edr needs projected metres, while
the registered dataset keeps the GOES scan angles that xpublish-tiles' own
geostationary grid needs (``datasets.rescale_geostationary``).

GET only (plan Decision 3): xpublish-edr registers GET+POST as one route, and
this replaces it. ``area`` and ``cube`` stay xpublish-edr's own and fail on a
missing step. Re-check this module whenever the xpublish-edr pin moves.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import icechunk as ic
import numpy as np
import pandas as pd
import xarray as xr
from fastapi import Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from shapely.errors import GEOSException
from xpublish import Dependencies, hookimpl
from xpublish_edr.format import position_formats
from xpublish_edr.formats.to_covjson import to_cf_covjson
from xpublish_edr.geometry.common import prepare_spatial_grid, project_dataset, project_geometry
from xpublish_edr.geometry.position import select_by_position
from xpublish_edr.plugin import CfEdrPlugin
from xpublish_edr.query import EDRPositionQuery

from stac_higher_cube_server.datasets import rescale_geostationary

MISSING_STEPS_HEADER = "x-cube-missing-steps"
APPEND_DIM_ATTR = "_cube_append_dim"


async def _load_one(step: xr.Dataset) -> xr.Dataset:
    try:
        return await step.load_async()
    except NotImplementedError:
        return step.load()


def _as_missing(step: xr.Dataset, dim: str) -> xr.Dataset:
    """``step`` with every ``dim``-dimensioned data variable set to NaN.

    Built from shapes alone: touching the variable's data would read the
    missing source again (``xr.full_like`` does)."""
    filled = step.copy()
    for name, var in step.data_vars.items():
        if dim in var.dims or dim in var.coords:
            filled[name] = xr.DataArray(
                np.full(var.shape, np.nan), dims=var.dims, coords=var.coords, attrs=var.attrs
            )
    return filled


def _naive_utc(value: str) -> str:
    """``value`` as a naive nanosecond UTC string; anything else is returned as is.

    The writer's ``stac_higher:time_values`` end in ``Z``. xpublish-edr hands a
    ``Z`` string to ``DatetimeIndex.get_indexer``, which raises ``TypeError``
    (a 500), so the append dimension's selector is normalised first."""
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError):
        return value
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return np.datetime_as_string(np.datetime64(stamp.value, "ns"), unit="ns")


def normalise_time_selector(params: dict[str, str], dataset: xr.Dataset, dim: str) -> None:
    """Rewrite ``params[dim]`` (``a`` or ``a/b``) in place for a datetime ``dim``."""
    value = params.get(dim)
    if value is None or dim not in dataset.coords:
        return
    if not np.issubdtype(dataset[dim].dtype, np.datetime64):
        return
    params[dim] = "/".join(_naive_utc(part) for part in value.split("/"))


def load_steps_tolerant(
    ds: xr.Dataset, dim: str, *, concurrency: int = 16
) -> tuple[xr.Dataset, int]:
    """Load ``ds``; if a source is missing, one ``dim`` step at a time, and a
    missing source is a NaN step.

    Returns the loaded dataset (same steps, same order) and the count of
    steps whose source raised ``icechunk.StorageError``. Any other error
    propagates. Runs its own event loop: call it from a worker thread (FastAPI
    runs sync routes in its threadpool), never from a running loop.
    """
    collapsed = dim not in ds.dims
    # One load first: nothing is usually missing, and a per-step load of up to
    # max_steps (10,000) steps is slow. Only a missing source needs the fallback.
    try:
        return ds.load(), 0
    except ic.StorageError:
        pass
    steps = [ds] if collapsed else [ds.isel({dim: [i]}) for i in range(ds.sizes[dim])]

    async def gather() -> list[xr.Dataset | BaseException]:
        gate = asyncio.Semaphore(concurrency)

        async def one(step: xr.Dataset) -> xr.Dataset:
            async with gate:
                return await _load_one(step)

        return await asyncio.gather(*(one(s) for s in steps), return_exceptions=True)

    loaded: list[xr.Dataset] = []
    missing = 0
    for step, result in zip(steps, asyncio.run(gather()), strict=True):
        if isinstance(result, ic.StorageError):
            missing += 1
            loaded.append(_as_missing(step, dim).load())
        elif isinstance(result, BaseException):
            raise result
        else:
            loaded.append(result)
    if collapsed:
        return loaded[0], missing
    return xr.concat(loaded, dim=dim), missing


def metres_view(dataset_dependency):
    """A FastAPI dependency: the resolved dataset with geostationary scan
    angles rescaled to metres (lazy: only the 1-D x/y coordinates change)."""

    def dataset(ds: xr.Dataset = Depends(dataset_dependency)) -> xr.Dataset:  # noqa: B008
        return rescale_geostationary(ds)

    return dataset


class TolerantEdrPlugin(CfEdrPlugin):
    """xpublish-edr with ``position`` replaced by the per-step tolerant route."""

    name: str = "cf_edr"

    @hookimpl
    def dataset_router(self, deps: Dependencies):
        deps = deps.model_copy(update={"dataset": metres_view(deps.dataset)})
        router = super().dataset_router(deps)
        router.routes = [
            r for r in router.routes
            if not (isinstance(r, APIRoute) and r.path.endswith("/position"))
        ]

        @router.get("/position", summary="Position query (missing steps are null)")
        def position(
            request: Request,
            query: Annotated[EDRPositionQuery, Query()],
            dataset: xr.Dataset = Depends(deps.dataset),  # noqa: B008 (xpublish-edr's own form)
        ) -> Response:
            """A point time series. A step whose source file is gone is ``null``
            and counted in the ``x-cube-missing-steps`` header."""
            if query.coords is None:
                raise HTTPException(422, "coords query parameter is required")
            try:
                geometry = query.geometry
            except GEOSException as err:
                raise HTTPException(
                    422, "Could not parse coordinates to geometry, "
                    "check the format of the 'coords' query parameter",
                ) from err
            if query.method != "nearest":
                raise HTTPException(
                    422, f"method={query.method!r} is not supported on a cube; use method=nearest"
                )
            append_dim = dataset.attrs.get(APPEND_DIM_ATTR, "t")
            params = dict(request.query_params)
            normalise_time_selector(params, dataset, append_dim)
            try:
                ds = query.select(dataset, params)
            except ValueError as err:
                raise HTTPException(404, f"Error selecting from query: {err.args[0]}") from err
            try:
                grid = prepare_spatial_grid(ds, require_regular=True)
                projected = project_geometry(grid.ds, query.crs, geometry, grid.spatial_ref)
                ds = select_by_position(grid.ds, projected, query.method, grid.spatial_ref)
            except GEOSException as err:
                raise HTTPException(
                    422, "Could not parse coordinates to geometry, "
                    "check the format of the 'coords' query parameter",
                ) from err
            except KeyError as err:
                raise HTTPException(404, f"Error selecting by position: {err}") from err
            try:
                ds = project_dataset(ds, query.crs, grid.spatial_ref)
            except Exception as err:
                raise HTTPException(404, "Error projecting dataset") from err

            ds, missing = load_steps_tolerant(ds, append_dim)

            if query.format:
                try:
                    format_fn = position_formats()[query.format]
                except KeyError as err:
                    raise HTTPException(
                        404, f"{query.format} is not a valid format for EDR position queries. "
                        "Get `./position/formats` for valid formats",
                    ) from err
                result = format_fn(ds)
            else:
                result = to_cf_covjson(ds)
            response = (
                result if isinstance(result, Response)
                else JSONResponse(jsonable_encoder(result))
            )
            response.headers[MISSING_STEPS_HEADER] = str(missing)
            return response

        return router
