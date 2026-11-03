"""The cube-server app (virtual cube spec §8).

xpublish serves the registry's datasets by cube collection id:
``/datasets/{cube_collection}/tiles/…`` (xpublish-tiles) and
``/datasets/{cube_collection}/edr/…`` (xpublish-edr, with the per-step
tolerant ``position`` of ``edr.py``). A background task polls the registry
every ``CUBE_POLL_SECONDS`` and renders one warm-up tile per (re)opened cube to
absorb the Numba JIT (spike Q3: 2.4-2.6 s cold vs 0.44 s warm).

Auth posture: anonymous, like titiler, until #34. The server reaches the
virtual sources (NODD) directly; network egress rides #68.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator

import httpx
import numpy as np
import uvicorn
import xarray as xr
import xpublish
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ConfigDict
from xpublish import Plugin, hookimpl
from xpublish_tiles.xpublish.tiles import TilesPlugin

from stac_higher_cube_server.caching import cache_control
from stac_higher_cube_server.edr import APPEND_DIM_ATTR, MISSING_STEPS_HEADER, TolerantEdrPlugin
from stac_higher_cube_server.registry import CubeRegistry, S3RepoOpener
from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import PgSinkSource

log = logging.getLogger("stac_higher_cube_server")

_RECORD_KEYS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class ExtraFormatter(logging.Formatter):
    """The standard line plus the record's ``extra={...}`` fields as JSON.

    Errors reach /health as a type name only; their text lives here."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extra = {k: v for k, v in vars(record).items() if k not in _RECORD_KEYS}
        return f"{line} {json.dumps(extra, default=str)}" if extra else line


#: the warm-up tile's rendering (the spike's map.html query, z0). The range is
#: explicit because xpublish-tiles 422s without one on a variable lacking
#: valid_min/valid_max (the real GOES CMI); the warm-up only absorbs the JIT,
#: so the colours it draws don't matter.
WARM_UP_QUERY = "style=raster/gray&colorscalerange=0,1&width=256&height=256&f=png"


class CubeRegistryProvider(Plugin):
    """xpublish dataset provider over the in-memory registry. Ids are cube
    collection ids the registry loaded from the DB, never a client's input."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "cube-registry"
    registry: CubeRegistry

    @hookimpl
    def get_datasets(self) -> list[str]:
        return self.registry.ids()

    @hookimpl
    def get_dataset(self, dataset_id: str) -> xr.Dataset | None:
        return self.registry.get(dataset_id)


def _exact_t(value: np.generic) -> str:
    if np.issubdtype(np.asarray(value).dtype, np.datetime64):
        return np.datetime_as_string(value, unit="ns")
    return str(value)


async def warm_up(app: FastAPI, collection_id: str, ds: xr.Dataset) -> int | None:
    """Render one z0 tile of the latest step through the app itself.

    Returns the status code, or ``None`` when the request itself failed.
    Never raises: a failed warm-up only costs the first visitor the JIT.
    """
    variables = [str(v) for v, da in ds.data_vars.items() if da.ndim >= 2]
    dim = ds.attrs.get(APPEND_DIM_ATTR, "t")
    if not variables or dim not in ds.coords or ds.sizes.get(dim, 0) == 0:
        return None
    url = (
        f"/datasets/{collection_id}/tiles/WebMercatorQuad/0/0/0"
        f"?variables={variables[0]}&{WARM_UP_QUERY}&t={_exact_t(ds[dim].values[-1])}"
    )
    started = time.monotonic()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cube-server") as c:
            response = await c.get(url, timeout=60)
    except Exception as err:
        log.warning("cube warm-up failed",
                    extra={"cube_collection_id": collection_id, "error": repr(err)})
        return None
    log.info("cube warm-up",
             extra={"cube_collection_id": collection_id, "status": response.status_code,
                    "seconds": round(time.monotonic() - started, 3)})
    return response.status_code


async def _poll(app: FastAPI, registry: CubeRegistry, settings: Settings) -> None:
    while True:
        try:
            opened = await asyncio.to_thread(registry.refresh)
        except Exception:  # refresh() already contains its own failures
            log.exception("cube registry refresh crashed")
            opened = []
        for collection_id in opened:
            ds = registry.get(collection_id)
            if ds is not None:
                registry.record_warm_up(collection_id, await warm_up(app, collection_id, ds))
        await asyncio.sleep(settings.poll_seconds)


def create_app(registry: CubeRegistry, settings: Settings, *, poll: bool = True) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_poll(app, registry, settings)) if poll else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    rest = xpublish.Rest(
        None,
        plugins={"tiles": TilesPlugin(), "edr": TolerantEdrPlugin()},
        app_kws={"title": "stac-higher cube-server", "lifespan": lifespan},
    )
    rest.register_plugin(CubeRegistryProvider(registry=registry))
    app = rest.app

    # On the app itself: xpublish's `routers=` are per-dataset routers.
    @app.get("/health")
    def get_health() -> dict:
        return {"status": "ok", **registry.health()}

    @app.middleware("http")
    async def tile_cache_headers(request: Request, call_next):
        response = await call_next(request)
        value = cache_control(request.url.path, request.query_params, response.status_code)
        if value is not None:
            response.headers["cache-control"] = value
        return response

    # Added last, so it is outermost: preflights never reach xpublish.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "HEAD"],
        allow_headers=["*"],
        expose_headers=[MISSING_STEPS_HEADER],
    )
    return app


def main() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ExtraFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    settings = Settings.from_env()
    registry = CubeRegistry(PgSinkSource(settings.db_dsn), S3RepoOpener(settings))
    uvicorn.run(
        create_app(registry, settings), host="0.0.0.0", port=settings.port, log_level="info"
    )


if __name__ == "__main__":
    main()
