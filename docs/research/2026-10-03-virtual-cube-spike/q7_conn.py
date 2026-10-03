# /// script
# requires-python = ">=3.12"
# dependencies = ["icechunk==2.2.2", "virtualizarr[hdf]==2.7.3", "zarr>=3,<4", "xarray", "obstore", "numpy"]
# ///
"""Q7b/c: build obstore + Icechunk S3 options from an `s3` connection config (the
contract fixture's shapes, parsed by the pipeline's own parse_s3_config), append
virtually and read back; pre-check the endpoint host with the pipeline's
resolve_pinned. Pipeline code is loaded READ-ONLY from the main checkout."""
import ast
import importlib.util
import json
import sys
import time
import traceback
from dataclasses import dataclass
from urllib.parse import urlparse

import icechunk as ic
import obstore as obs
import xarray as xr
from obstore.store import S3Store
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
from virtualizarr.registry import ObjectStoreRegistry

MAIN = "/Users/caesterlein/Projects/TechTraverse/stac-higher/services/pipeline/src/pipeline/connections"
spec = importlib.util.spec_from_file_location("egress", f"{MAIN}/egress.py")
egress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(egress)
# Pull S3Config / parse_s3_config / _endpoint_host out of s3.py without its rasterio/boto imports.
tree = ast.parse(open(f"{MAIN}/adapters/s3.py").read())
wanted = [n for n in tree.body if getattr(n, "name", None) in ("S3Config", "parse_s3_config", "_endpoint_host")]
ns = {"dataclass": dataclass, "urlparse": urlparse, "EgressBlocked": egress.EgressBlocked, "Mapping": dict, "Any": object}
exec("from __future__ import annotations\nfrom collections.abc import Mapping\nfrom typing import Any\n" +
     ast.unparse(ast.Module(body=wanted, type_ignores=[])), ns)
parse_s3_config, endpoint_host = ns["parse_s3_config"], ns["_endpoint_host"]

SILO_KEYS = dict(access_key_id="minioadmin", secret_access_key="minioadmin")


def libs_from_connection(cfg, creds):
    """The mapping the sink would own: connection config -> (obstore store, icechunk container store, icechunk creds)."""
    region = cfg.region or "us-east-1"
    ep = cfg.endpoint
    allow_http = bool(ep and ep.startswith("http://"))
    o_kw = dict(bucket=cfg.bucket, region=region)
    i_kw = dict(region=region)
    if ep:
        o_kw.update(endpoint=ep, virtual_hosted_style_request=not cfg.force_path_style, client_options={"allow_http": allow_http})
        i_kw.update(endpoint_url=ep, allow_http=allow_http, force_path_style=cfg.force_path_style)
    if cfg.anonymous:
        o_kw["skip_signature"] = True
        i_kw["anonymous"] = True
        ic_creds = ic.s3_credentials(anonymous=True)
    else:
        o_kw.update(creds)
        ic_creds = ic.s3_credentials(**creds)
    return S3Store(**o_kw), ic.s3_store(**i_kw), ic_creds


cases = [
    ("fixture: anonymous public bucket (NODD)", {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True}, {},
     "ABI-L2-CMIPC/2026/276/17/OR_ABI-L2-CMIPC-M6C13_G19_s20262761721174_e20262761723559_c20262761724044.nc"),
    ("NODD with the endpoint pinned explicitly (checked host == dialed host)",
     {"bucket": "noaa-goes19", "region": "us-east-1", "endpoint": "https://s3.us-east-1.amazonaws.com",
      "force_path_style": True, "anonymous": True}, {},
     "ABI-L2-CMIPC/2026/276/17/OR_ABI-L2-CMIPC-M6C13_G19_s20262761721174_e20262761723559_c20262761724044.nc"),
    ("fixture shape: custom endpoint, path style, signed (z1-silo copy)",
     {"bucket": "z1src", "endpoint": "http://localhost:19000", "force_path_style": True, "anonymous": False}, SILO_KEYS,
     "goes/A2.nc"),
]
# Put a copy for the signed case (Q5 deleted its object).
import glob
silo_src = S3Store(bucket="z1src", endpoint="http://localhost:19000", region="us-east-1", virtual_hosted_style_request=False,
                   client_options={"allow_http": True}, **SILO_KEYS)
obs.put(silo_src, "goes/A2.nc", open(glob.glob("local/*s20262761721174*.nc")[0], "rb").read())

out = []
for label, raw, creds, key in cases:
    rec = dict(case=label)
    cfg = parse_s3_config(raw)
    host = endpoint_host(cfg.endpoint, cfg.region)
    rec["endpoint_host"] = host
    for allow in ((), (host,)):
        try:
            rec[f"resolve_pinned{'_allowlisted' if allow else ''}"] = egress.resolve_pinned(host, allow)
        except egress.EgressBlocked as e:
            rec[f"resolve_pinned{'_allowlisted' if allow else ''}"] = f"EgressBlocked: {e}"
    try:
        o_store, i_store, i_creds = libs_from_connection(cfg, creds)
        prefix = f"s3://{cfg.bucket}/"
        reg = ObjectStoreRegistry({f"s3://{cfg.bucket}": o_store})
        t0 = time.perf_counter()
        vds = open_virtual_dataset(f"{prefix}{key}", registry=reg, parser=HDFParser(), loadable_variables=["t", "x", "y"])
        vds = vds[["CMI", "t", "x", "y"]].expand_dims("t")
        conf = ic.RepositoryConfig.default()
        conf.set_virtual_chunk_container(ic.VirtualChunkContainer(prefix, i_store))
        st = ic.s3_storage(bucket="probe", prefix=f"q7-{int(time.time()*1000)}", endpoint_url="http://localhost:19000",
                           region="us-east-1", allow_http=True, force_path_style=True, **SILO_KEYS)
        repo = ic.Repository.create(st, conf, authorize_virtual_chunk_access=ic.containers_credentials({prefix: i_creds}))
        s = repo.writable_session("main")
        vds.vz.to_icechunk(s.store)
        s.commit("q7")
        ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, zarr_format=3)
        rec.update(ok=True, seconds=round(time.perf_counter() - t0, 2), cmi_mean=round(float(ds.CMI.isel(t=0).mean()), 3))
    except Exception as e:
        rec.update(ok=False, error=repr(e)[:300], tb=traceback.format_exc()[-600:])
    out.append(rec)
    print(json.dumps(rec, default=str), flush=True)
# Hosts the pre-check must cover in the compose stack
for h in ("minio", "localhost", "169.254.169.254", "noaa-goes19.s3.amazonaws.com"):
    try:
        print(h, "->", egress.resolve_pinned(h))
    except egress.EgressBlocked as e:
        print(h, "-> EgressBlocked:", str(e)[:120])
json.dump(out, open("q7_conn_results.json", "w"), indent=1, default=str)
