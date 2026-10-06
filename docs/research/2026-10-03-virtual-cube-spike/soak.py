# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "icechunk==2.2.2", "virtualizarr[hdf]==2.7.3", "zarr>=3,<4", "xarray",
#   "obstore", "h5netcdf", "numpy", "psutil",
# ]
# ///
"""Z-1 soak (#84 Q4/Q8): poll NODD for GOES-19 CMIPC C13, append each new file
virtually to an Icechunk repo on the throwaway z1-silo, roll a fixed window in
the same commit, expire + GC hourly. One JSONL line per event.

    uv run soak.py --prefix soak-c13 --max-steps 72            # the real soak
    uv run soak.py --prefix smoke-1 --max-steps 3 --backfill 6 --once   # smoke
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import traceback

import icechunk as ic
import numpy as np
import obstore as obs
import psutil
import xarray as xr
import zarr
from obstore.store import S3Store
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
from virtualizarr.registry import ObjectStoreRegistry

SILO = dict(endpoint="http://localhost:19000", key="minioadmin", secret="minioadmin", bucket="probe")
SRC_BUCKET = "noaa-goes19"
PRODUCT = "ABI-L2-CMIPC"
BAND = "M6C13"
T_ARRAYS = ("CMI", "DQF", "t")
KEEP = ["CMI", "DQF", "t", "x", "y", "goes_imager_projection"]
SCAN_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_e(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_")

ap = argparse.ArgumentParser()
ap.add_argument("--prefix", required=True)
ap.add_argument("--max-steps", type=int, default=72)
ap.add_argument("--poll", type=int, default=60)
ap.add_argument("--gc-every", type=int, default=3600)
ap.add_argument("--retention", type=int, default=3600, help="expire snapshots older than this many seconds")
ap.add_argument("--backfill", type=int, default=0, help="seed with the newest N existing files instead of 1")
ap.add_argument("--backfill-hours", type=int, default=2, help="how many hour prefixes to list for --backfill")
ap.add_argument("--updates-per-file", type=int, default=None, help="RepositoryConfig.num_updates_per_repo_info_file")
ap.add_argument("--once", action="store_true", help="exit after seeding + one GC (smoke test)")
ap.add_argument("--log", default=None)
args = ap.parse_args()
LOG = open(args.log or os.path.expanduser(f"~/stac-higher-z1-spike/{args.prefix}.jsonl"), "a", buffering=1)
PROC = psutil.Process()


def now():
    return dt.datetime.now(dt.timezone.utc)


def emit(event, **kw):
    rec = {"ts": now().isoformat(timespec="milliseconds"), "event": event, "rss_mb": round(PROC.memory_info().rss / 2**20, 1), **kw}
    LOG.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def scan_times(key):
    m = SCAN_RE.search(key)
    g = m.groups()

    def mk(y, j, h, mi, s, tenth):
        return dt.datetime(int(y), 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(
            days=int(j) - 1, hours=int(h), minutes=int(mi), seconds=int(s) + int(tenth) / 10)

    return mk(*g[:6]), mk(*g[6:])


src = S3Store(bucket=SRC_BUCKET, region="us-east-1", skip_signature=True)
reg = ObjectStoreRegistry({f"s3://{SRC_BUCKET}": src})
silo_obj = S3Store(bucket=SILO["bucket"], endpoint=SILO["endpoint"], region="us-east-1",
                   access_key_id=SILO["key"], secret_access_key=SILO["secret"],
                   virtual_hosted_style_request=False, client_options={"allow_http": True})
storage = ic.s3_storage(bucket=SILO["bucket"], prefix=args.prefix, endpoint_url=SILO["endpoint"], region="us-east-1",
                        allow_http=True, force_path_style=True,
                        access_key_id=SILO["key"], secret_access_key=SILO["secret"])
creds = ic.containers_credentials({f"s3://{SRC_BUCKET}/": ic.s3_credentials(anonymous=True)})


def open_repo():
    if ic.Repository.exists(storage):
        conf = None
        if args.updates_per_file:
            conf = ic.RepositoryConfig(num_updates_per_repo_info_file=args.updates_per_file)
        return ic.Repository.open(storage, config=conf, authorize_virtual_chunk_access=creds), False
    cfg = ic.RepositoryConfig.default()
    if args.updates_per_file:
        cfg.num_updates_per_repo_info_file = args.updates_per_file
    cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(f"s3://{SRC_BUCKET}/", ic.s3_store(region="us-east-1", anonymous=True)))
    return ic.Repository.create(storage, cfg, authorize_virtual_chunk_access=creds), True


def list_hour(t):
    prefix = f"{PRODUCT}/{t:%Y/%j/%H}/"
    out = {}
    for batch in obs.list(src, prefix=prefix, chunk_size=1000):
        for o in batch:
            if BAND in o["path"]:
                out[o["path"]] = o["last_modified"]
    return out


def list_recent(hours):
    t = now()
    out = {}
    for h in range(hours):
        out.update(list_hour(t - dt.timedelta(hours=h)))
    return out


def repo_stats():
    counts, total = {}, 0
    for batch in obs.list(silo_obj, prefix=f"{args.prefix}/", chunk_size=1000):
        for o in batch:
            kind = o["path"][len(args.prefix) + 1:].split("/", 1)[0]
            counts[kind] = counts.get(kind, 0) + 1
            total += o["size"]
    return counts, total


def times_in(repo):
    try:
        g = zarr.open_group(repo.readonly_session("main").store, mode="r")
        t = g["t"]
    except Exception:
        return None
    # GOES t: seconds since 2000-01-01 12:00:00 (J2000), float64
    raw = t[:]
    epoch = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)
    return [epoch + dt.timedelta(seconds=float(v)) for v in raw]


def vds_for(url):
    vds = open_virtual_dataset(url, registry=reg, parser=HDFParser(),
                               loadable_variables=["t", "x", "y", "goes_imager_projection"])
    # GOES ABI files carry a scalar `t`; make it the append dimension (time chunk of 1).
    # The projection stays a scalar (grid_mapping for the tiler), not expanded along t.
    out = vds[["CMI", "DQF", "t", "x", "y"]].expand_dims("t")
    out["goes_imager_projection"] = vds["goes_imager_projection"]
    return out


def append(repo, key, first):
    url = f"s3://{SRC_BUCKET}/{key}"
    t0 = time.perf_counter()
    vds = vds_for(url)
    t_parse = time.perf_counter() - t0
    s = repo.writable_session("main")
    if first:
        vds.vz.to_icechunk(s.store)
    else:
        vds.vz.to_icechunk(s.store, append_dim="t")
    t_write = time.perf_counter() - t0 - t_parse
    g = zarr.open_group(s.store, mode="r+")
    n = g["t"].shape[0]
    trimmed = 0
    t_trim = 0.0
    if n > args.max_steps:
        k = n - args.max_steps
        t1 = time.perf_counter()
        for name in T_ARRAYS:
            arr = g[name]
            s.shift_array(f"/{name}", (-k,) + (0,) * (arr.ndim - 1))
        for name in T_ARRAYS:
            arr = g[name]
            arr.resize((arr.shape[0] - k,) + arr.shape[1:])
        trimmed = k
        t_trim = time.perf_counter() - t1
        n -= k
    t2 = time.perf_counter()
    sid = s.commit(f"append {key.rsplit('/', 1)[1]}")
    t_commit = time.perf_counter() - t2
    return dict(parse_s=round(t_parse, 3), write_s=round(t_write, 3), trim_s=round(t_trim, 3),
                commit_s=round(t_commit, 3), append_s=round(t_parse + t_write + t_trim, 3),
                total_s=round(time.perf_counter() - t0, 3), snapshot=sid, t_len=n, trimmed=trimmed)


def gc(repo):
    cutoff = now() - dt.timedelta(seconds=args.retention)
    t0 = time.perf_counter()
    expired = repo.expire_snapshots(older_than=cutoff)
    t1 = time.perf_counter()
    summary = repo.garbage_collect(cutoff)
    t2 = time.perf_counter()
    counts, total = repo_stats()
    emit("gc", cutoff=cutoff, expired=len(expired), expire_s=round(t1 - t0, 3), gc_s=round(t2 - t1, 3),
         gc_summary=repr(summary), objects=counts, repo_bytes=total,
         ancestry=sum(1 for _ in repo.ancestry(branch="main")))


def read_check(repo):
    t0 = time.perf_counter()
    ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, zarr_format=3)
    first = float(ds.CMI.isel(t=0, y=750, x=1250).values)
    last = float(ds.CMI.isel(t=-1, y=750, x=1250).values)
    emit("read_check", t_len=ds.sizes["t"], t0=ds.t.values[0], t_last=ds.t.values[-1],
         cmi_first=first, cmi_last=last, read_s=round(time.perf_counter() - t0, 3),
         ok=bool(np.isfinite(first) and np.isfinite(last)))


def main():
    repo, created = open_repo()
    tip_times = times_in(repo) or []
    emit("start", prefix=args.prefix, created=created, max_steps=args.max_steps, pid=os.getpid(),
         versions=dict(icechunk=ic.__version__, zarr=zarr.__version__, xarray=xr.__version__),
         t_len=len(tip_times), tip=tip_times[-1] if tip_times else None)
    seen = set()
    listing = list_recent(args.backfill_hours if not tip_times else 2)
    if not tip_times:
        seeds = sorted(listing, key=lambda k: scan_times(k)[0])[-max(1, args.backfill):]
        for i, key in enumerate(seeds):
            try:
                r = append(repo, key, first=(i == 0))
                emit("append", key=key, scan_start=scan_times(key)[0], seed=True, **r)
            except Exception as e:
                emit("error", key=key, where="seed", error=repr(e), tb=traceback.format_exc())
                raise
        tip_times = times_in(repo)
    # Keys at or before the tip are in the cube or pre-start backlog; newer ones (a restart
    # after downtime) are appended by the loop.
    seen.update(k for k in listing if scan_times(k)[1] <= tip_times[-1])
    counts, total = repo_stats()
    emit("stats", objects=counts, repo_bytes=total)
    last_gc = time.monotonic()
    last_beat = 0.0
    last_sweep = time.monotonic()
    if args.once:
        gc(repo)
        read_check(repo)
        return
    while True:
        loop_t0 = time.monotonic()
        try:
            listing = list_recent(2)
            new = sorted((k for k in listing if k not in seen), key=lambda k: scan_times(k)[0])
            for key in new:
                seen.add(key)
                s, e = scan_times(key)
                detected = now()
                lag = dict(scan_end=e, nodd_last_modified=listing[key],
                           publish_lag_s=round((listing[key] - e).total_seconds(), 1),
                           detect_lag_s=round((detected - e).total_seconds(), 1))
                tip = tip_times[-1]
                if any(s <= t <= e for t in tip_times[-args.max_steps:]):
                    emit("duplicate", key=key, scan_start=s, **lag)
                    continue
                if e <= tip:
                    emit("late", key=key, scan_start=s, tip=tip, behind_s=round((tip - s).total_seconds(), 1), **lag)
                    continue
                gap_s = (s - tip).total_seconds()
                if gap_s > 7.5 * 60:
                    emit("gap", after=tip, before=s, gap_s=round(gap_s, 1), missing_steps=round(gap_s / 300) - 1)
                try:
                    r = append(repo, key, first=False)
                except ic.ConflictError as ce:
                    emit("error", key=key, where="append-conflict", error=repr(ce), tb=traceback.format_exc())
                    repo, _ = open_repo()
                    r = append(repo, key, first=False)
                counts, total = repo_stats()
                tip_times = times_in(repo)
                emit("append", key=key, scan_start=s, objects=counts, manifests=counts.get("manifests", 0),
                     repo_bytes=total, **lag, **r)
            if time.monotonic() - last_sweep >= 3600:
                # Late-arrival sweep: anything in the last 6 h we never listed before.
                sweep = list_recent(7)
                missed = sorted(k for k in sweep if k not in seen)
                for key in missed:
                    seen.add(key)
                    s, e = scan_times(key)
                    emit("late", key=key, scan_start=s, tip=tip_times[-1], via="sweep",
                         nodd_last_modified=sweep[key], publish_lag_s=round((sweep[key] - e).total_seconds(), 1))
                emit("sweep", listed=len(sweep), missed=len(missed))
                last_sweep = time.monotonic()
            if time.monotonic() - last_gc >= args.gc_every:
                gc(repo)
                read_check(repo)
                last_gc = time.monotonic()
            if time.monotonic() - last_beat >= 600:
                emit("heartbeat", t_len=len(tip_times), tip=tip_times[-1], seen=len(seen))
                last_beat = time.monotonic()
        except Exception as e:
            emit("error", where="loop", error=repr(e), tb=traceback.format_exc())
        time.sleep(max(1.0, args.poll - (time.monotonic() - loop_t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        emit("stop", reason="interrupt")
    except Exception as e:
        emit("fatal", error=repr(e), tb=traceback.format_exc())
        sys.exit(1)
