# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "icechunk==2.2.2", "zarr>=3,<4", "xarray", "xpublish",
#   "xpublish-tiles==0.9.2", "xpublish-edr==0.11.1", "uvicorn",
# ]
# ///
"""Z-1 cube server (#84 Q1-Q3): opens the static Icechunk repo on z1-silo itself
(Silo endpoint + anonymous virtual-chunk access to s3://noaa-goes19/) and serves
it with xpublish.Rest + TilesPlugin + CfEdrPlugin.

Datasets:
  goes-c13     as stored: x/y are scan angles (rad) -> tiles' Geostationary grid
  goes-c13-m   x/y multiplied by perspective_point_height (metres) -> the EDR fix candidate

    uv run cube_server.py [--prefix static-c13] [--port 9100]
"""
import argparse

import icechunk as ic
import uvicorn
import xarray as xr
import xpublish
from fastapi.responses import FileResponse
from xpublish_edr.plugin import CfEdrPlugin
from xpublish_tiles.xpublish.tiles import TilesPlugin

ap = argparse.ArgumentParser()
ap.add_argument("--prefix", default="static-c13")
ap.add_argument("--port", type=int, default=9100)
ap.add_argument("--load", action="store_true", help="materialize into memory: compute-only floor, NOT the product path")
ap.add_argument("--cache", action="store_true", help="1 GiB Icechunk chunk cache (xpublish-tiles CLI config)")
args = ap.parse_args()

storage = ic.s3_storage(bucket="probe", prefix=args.prefix, endpoint_url="http://localhost:19000", region="us-east-1",
                        allow_http=True, force_path_style=True, access_key_id="minioadmin", secret_access_key="minioadmin")
creds = ic.containers_credentials({"s3://noaa-goes19/": ic.s3_credentials(anonymous=True)})
config = None
if args.cache:
    config = ic.RepositoryConfig(caching=ic.CachingConfig(num_bytes_chunks=2**30, num_chunk_refs=2**30))
repo = ic.Repository.open(storage, config=config, authorize_virtual_chunk_access=creds)
session = repo.readonly_session("main")
ds = xr.open_zarr(session.store, zarr_format=3, consolidated=False, chunks=None, decode_coords="all")
ds = ds[["CMI", "DQF", "goes_imager_projection"]]
if args.load:
    ds = ds.load()
ds.attrs["_xpublish_id"] = f"{args.prefix}:{session.snapshot_id}"

h = float(ds.goes_imager_projection.attrs["perspective_point_height"])
ds_m = ds.assign_coords(x=(ds.x * h).assign_attrs(ds.x.attrs, units="m"), y=(ds.y * h).assign_attrs(ds.y.attrs, units="m"))
ds_m.attrs["_xpublish_id"] = f"{args.prefix}-m:{session.snapshot_id}"

rest = xpublish.Rest({"goes-c13": ds, "goes-c13-m": ds_m},
                     plugins={"tiles": TilesPlugin(), "edr": CfEdrPlugin()})
app = rest.app


@app.get("/map")
def map_page():
    return FileResponse(__file__.rsplit("/", 1)[0] + "/map.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
