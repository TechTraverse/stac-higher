# /// script
# requires-python = ">=3.12"
# dependencies = ["icechunk==2.2.2", "virtualizarr[hdf]==2.7.3", "zarr>=3,<4", "xarray", "obstore", "numpy"]
# ///
"""Q5b: one source of a 3-step cube is deleted. What do a frame read and a point time series do?"""
import glob, json, time
import icechunk as ic, obstore as obs, xarray as xr
from obstore.store import S3Store
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
from virtualizarr.registry import ObjectStoreRegistry
EP = "http://localhost:19000"; K = dict(access_key_id="minioadmin", secret_access_key="minioadmin")
src = S3Store(bucket="z1src", endpoint=EP, region="us-east-1", virtual_hosted_style_request=False, client_options={"allow_http": True}, **K)
files = sorted(glob.glob("local/*.nc"))[:2]
keys = []
for i, f in enumerate(files + files[:1]):
    k = f"series/{i}.nc"; obs.put(src, k, open(f, "rb").read()); keys.append(k)
reg = ObjectStoreRegistry({"s3://z1src": src})
cfg = ic.RepositoryConfig.default()
cfg.set_virtual_chunk_container(ic.VirtualChunkContainer("s3://z1src/", ic.s3_store(region="us-east-1", endpoint_url=EP, allow_http=True, force_path_style=True)))
creds = ic.containers_credentials({"s3://z1src/": ic.s3_credentials(**K)})
st = ic.s3_storage(bucket="probe", prefix=f"q5b-{int(time.time())}", endpoint_url=EP, region="us-east-1", allow_http=True, force_path_style=True, **K)
repo = ic.Repository.create(st, cfg, authorize_virtual_chunk_access=creds)
for i, k in enumerate(keys):
    v = open_virtual_dataset(f"s3://z1src/{k}", registry=reg, parser=HDFParser(), loadable_variables=["t", "x", "y"])
    v = v[["CMI", "t", "x", "y"]].expand_dims("t")
    if i == 2:  # third file reuses file 0's bytes; give it a later time so append order holds
        v = v.assign_coords(t=v.t + 600_000_000_000 if v.t.dtype.kind == "M" else v.t + 600)
    s = repo.writable_session("main"); v.vz.to_icechunk(s.store, **({} if i == 0 else {"append_dim": "t"})); s.commit(str(i))
obs.delete(src, keys[1])
ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, zarr_format=3)
def tryit(label, f):
    try: print(label, "OK", f())
    except Exception as e: print(label, "FAIL", type(e).__name__, str(e).splitlines()[0][:160])
tryit("frame t=0", lambda: float(ds.CMI.isel(t=0).mean()))
tryit("frame t=1 (deleted source)", lambda: float(ds.CMI.isel(t=1).mean()))
tryit("point series over all t", lambda: ds.CMI.isel(y=750, x=1250).values.tolist())
