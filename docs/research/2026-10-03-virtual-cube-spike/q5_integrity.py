# /// script
# requires-python = ">=3.12"
# dependencies = ["icechunk==2.2.2", "virtualizarr[hdf]==2.7.3", "zarr>=3,<4", "xarray", "obstore", "h5py", "numpy"]
# ///
"""Q5: what does a reader do when a virtual source object changes or disappears?
Everything here runs on COPIES in the throwaway z1-silo bucket `z1src`, never NODD.

Two repos over the same source object:
  plain  - default to_icechunk (no checksum)
  dated  - to_icechunk(last_updated_at=<object LastModified>)
Then tamper with the object four ways and read both repos after each."""
import datetime as dt
import glob
import hashlib
import json
import time
import traceback

import icechunk as ic
import numpy as np
import obstore as obs
import xarray as xr
from obstore.store import S3Store
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
from virtualizarr.registry import ObjectStoreRegistry

EP = "http://localhost:19000"
K = dict(access_key_id="minioadmin", secret_access_key="minioadmin")
silo = lambda b: S3Store(bucket=b, endpoint=EP, region="us-east-1", virtual_hosted_style_request=False,
                         client_options={"allow_http": True}, **K)
# the source bucket for copies
import subprocess
subprocess.run(["aws", "--endpoint-url", EP, "s3", "mb", "s3://z1src"], env={"AWS_ACCESS_KEY_ID": "minioadmin",
               "AWS_SECRET_ACCESS_KEY": "minioadmin", "AWS_DEFAULT_REGION": "us-east-1", "PATH": "/usr/local/bin:/usr/bin"},
               capture_output=True)
src = silo("z1src")
NODD = S3Store(bucket="noaa-goes19", region="us-east-1", skip_signature=True)

A = glob.glob("local/*s20262761721174*.nc")[0]
B_KEY = "ABI-L2-CMIPC/2026/276/17/OR_ABI-L2-CMIPC-M6C13_G19_s20262761726174_e20262761728559_c20262761729044.nc"
B = "local/" + B_KEY.rsplit("/", 1)[1]
try:
    open(B, "rb").close()
except FileNotFoundError:
    open(B, "wb").write(bytes(obs.get(NODD, B_KEY).bytes()))
a_bytes, b_bytes = open(A, "rb").read(), open(B, "rb").read()
KEY = "goes/A.nc"
obs.put(src, KEY, a_bytes)
lm = obs.head(src, KEY)["last_modified"]

reg = ObjectStoreRegistry({"s3://z1src": src})
vds = open_virtual_dataset(f"s3://z1src/{KEY}", registry=reg, parser=HDFParser(), loadable_variables=["t", "x", "y"])
vds = vds[["CMI", "t", "x", "y"]].expand_dims("t")

cfg = ic.RepositoryConfig.default()
cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(
    "s3://z1src/", ic.s3_store(region="us-east-1", endpoint_url=EP, allow_http=True, force_path_style=True)))
creds = ic.containers_credentials({"s3://z1src/": ic.s3_credentials(**K)})
repos = {}
stamp = int(time.time())
for name, kw in {"plain": {}, "dated": {"last_updated_at": lm}}.items():
    st = ic.s3_storage(bucket="probe", prefix=f"q5-{name}-{stamp}", endpoint_url=EP, region="us-east-1",
                       allow_http=True, force_path_style=True, **K)
    repo = ic.Repository.create(st, cfg, authorize_virtual_chunk_access=creds)
    s = repo.writable_session("main")
    vds.vz.to_icechunk(s.store, **kw)
    s.commit("init")
    repos[name] = repo

# What did the ref record? Inspect one chunk's virtual ref via the session's chunk locations.
sess = repos["plain"].readonly_session("main")
locs = sorted(sess.all_virtual_chunk_locations())[:2]


def read(name):
    try:
        ds = xr.open_zarr(repos[name].readonly_session("main").store, consolidated=False, zarr_format=3)
        v = ds.CMI.isel(t=0).values
        return dict(ok=True, sha=hashlib.sha1(np.nan_to_num(v, nan=-9).tobytes()).hexdigest()[:12],
                    mean=round(float(np.nanmean(v)), 4))
    except Exception as e:
        return dict(ok=False, error=type(e).__name__, msg=str(e).splitlines()[0][:220])


res = dict(source_last_modified=str(lm), example_locations=locs, a_size=len(a_bytes), b_size=len(b_bytes), steps=[])


def step(label):
    rec = dict(step=label, plain=read("plain"), dated=read("dated"))
    res["steps"].append(rec)
    print(json.dumps(rec), flush=True)


step("baseline")
time.sleep(1.5)
obs.put(src, KEY, a_bytes)
step("re-upload identical bytes (new LastModified)")
time.sleep(1.5)
obs.put(src, KEY, b_bytes)
step("replace with next scan's file (same product, different chunk offsets)")
# same-size, same-layout corruption: flip bytes inside the CMI chunk region of A
bad = bytearray(a_bytes)
mid = len(bad) // 2
for i in range(mid, mid + 4096, 7):
    bad[i] ^= 0xFF
time.sleep(1.5)
obs.put(src, KEY, bytes(bad))
step("A with 585 bytes flipped mid-file (same size)")
obs.delete(src, KEY)
step("deleted")
json.dump(res, open("q5_results.json", "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if k != "steps"}, default=str))
