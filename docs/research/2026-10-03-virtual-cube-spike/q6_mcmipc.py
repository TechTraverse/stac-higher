# /// script
# requires-python = ">=3.12"
# dependencies = ["icechunk==2.2.2", "virtualizarr[hdf]==2.7.3", "zarr>=3,<4", "xarray", "obstore", "numpy"]
# ///
"""Q6: ABI-L2-MCMIPC internal chunking, a 3-file virtual append, and a lazy RGB composite."""
import datetime as dt
import json
import time

import icechunk as ic
import numpy as np
import obstore as obs
import xarray as xr
import zarr
from obstore.store import S3Store
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
from virtualizarr.registry import ObjectStoreRegistry

src = S3Store(bucket="noaa-goes19", region="us-east-1", skip_signature=True)
reg = ObjectStoreRegistry({"s3://noaa-goes19": src})
hour = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
keys = sorted(o["path"] for b in obs.list(src, prefix=f"ABI-L2-MCMIPC/{hour:%Y/%j/%H}/") for o in b)[:3]
print("keys", keys)

t0 = time.perf_counter()
v0 = open_virtual_dataset(f"s3://noaa-goes19/{keys[0]}", registry=reg, parser=HDFParser(),
                          loadable_variables=["t", "x", "y", "goes_imager_projection"])
parse_s = time.perf_counter() - t0
bands = [f"CMI_C{i:02d}" for i in range(1, 17)]
shapes = {}
for b in bands + [f"DQF_C{i:02d}" for i in range(1, 17)]:
    ma = v0[b].data
    md = ma.metadata
    shapes[b] = dict(shape=list(ma.shape), chunks=list(md.chunks), dtype=str(ma.dtype),
                     codecs=[getattr(c, "codec_name", None) or c.__class__.__name__ for c in md.codecs])
uniq = {json.dumps({k: v for k, v in s.items()}) for b, s in shapes.items() if b.startswith("CMI")}
print(json.dumps(dict(parse_s=round(parse_s, 2), n_vars=len(v0.data_vars), unique_cmi_layouts=len(uniq),
                      cmi_c01=shapes["CMI_C01"], cmi_c13=shapes["CMI_C13"], dqf_c01=shapes["DQF_C01"])))

stamp = int(time.time())
st = ic.s3_storage(bucket="probe", prefix=f"q6-mcmipc-{stamp}", endpoint_url="http://localhost:19000", region="us-east-1",
                   allow_http=True, force_path_style=True, access_key_id="minioadmin", secret_access_key="minioadmin")
cfg = ic.RepositoryConfig.default()
cfg.set_virtual_chunk_container(ic.VirtualChunkContainer("s3://noaa-goes19/", ic.s3_store(region="us-east-1", anonymous=True)))
creds = ic.containers_credentials({"s3://noaa-goes19/": ic.s3_credentials(anonymous=True)})
repo = ic.Repository.create(st, cfg, authorize_virtual_chunk_access=creds)
keep = bands + [f"DQF_C{i:02d}" for i in range(1, 17)]
for i, k in enumerate(keys):
    t0 = time.perf_counter()
    v = v0 if i == 0 else open_virtual_dataset(f"s3://noaa-goes19/{k}", registry=reg, parser=HDFParser(),
                                               loadable_variables=["t", "x", "y", "goes_imager_projection"])
    out = v[keep + ["t", "x", "y"]].expand_dims("t")
    out["goes_imager_projection"] = v["goes_imager_projection"]
    s = repo.writable_session("main")
    out.vz.to_icechunk(s.store, **({} if i == 0 else {"append_dim": "t"}))
    sid = s.commit(f"append {k.rsplit('/', 1)[1]}")
    print(json.dumps(dict(append=i, seconds=round(time.perf_counter() - t0, 2), snapshot=sid)))

ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, zarr_format=3)
print(json.dumps(dict(dims=dict(ds.sizes), t=[str(x) for x in ds.t.values])))
# Lazy "true-colour-ish" composite: R=C02 (0.64um, 0.5 km native but 2 km in MCMIPC), G synthetic, B=C01.
t0 = time.perf_counter()
sl = dict(t=-1, y=slice(600, 856), x=slice(1200, 1456))
r, b, nir = (ds[f"CMI_C{n:02d}"].isel(sl) for n in (2, 1, 3))
g = 0.45 * r + 0.1 * nir + 0.45 * b  # the standard simple green approximation
rgb = xr.concat([r, g, b], dim="band").clip(0, 1) ** (1 / 2.2)
arr = rgb.values
print(json.dumps(dict(rgb_256x256_s=round(time.perf_counter() - t0, 3), shape=list(arr.shape),
                      finite=float(np.isfinite(arr).mean()), mean=[round(float(np.nanmean(arr[i])), 3) for i in range(3)])))
json.dump(dict(keys=keys, shapes=shapes), open("q6_results.json", "w"), indent=1)
