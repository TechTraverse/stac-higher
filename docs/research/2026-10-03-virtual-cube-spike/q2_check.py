# /// script
# requires-python = ">=3.12"
# dependencies = ["xarray", "h5netcdf", "h5py", "numpy", "pyproj", "httpx", "icechunk==2.2.2", "zarr>=3,<4"]
# ///
"""Q2: EDR position series (metres view) vs direct reads; area + cube usability."""
import glob, io, json, time
import httpx, numpy as np, pyproj, xarray as xr, icechunk as ic
B = "http://localhost:9100/datasets"
c = httpx.Client(timeout=300)
path = glob.glob("local/*s20262761721174*.nc")[0]
nc = xr.open_dataset(path, engine="h5netcdf")
gp = nc.goes_imager_projection.attrs; H = float(gp["perspective_point_height"])
tr = pyproj.Transformer.from_crs(4326, pyproj.CRS.from_cf(gp), always_xy=True)
st = ic.s3_storage(bucket="probe", prefix="static-c13", endpoint_url="http://localhost:19000", region="us-east-1",
                   allow_http=True, force_path_style=True, access_key_id="minioadmin", secret_access_key="minioadmin")
repo = ic.Repository.open(st, authorize_virtual_chunk_access=ic.containers_credentials({"s3://noaa-goes19/": ic.s3_credentials(anonymous=True)}))
cube = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, zarr_format=3)
out = {}
for name, (lon, lat) in {"kansas": (-97.0, 38.5), "dc": (-77.0, 38.9), "seattle": (-122.3, 47.6)}.items():
    gx, gy = tr.transform(lon, lat)
    ix = int(np.abs(nc.x.values - gx / H).argmin()); iy = int(np.abs(nc.y.values - gy / H).argmin())
    direct = float(nc.CMI.values[iy, ix])
    series_truth = cube.CMI.isel(y=iy, x=ix).values
    t0 = time.perf_counter()
    r = c.get(f"{B}/goes-c13-m/edr/position", params={"coords": f"POINT({lon} {lat})", "parameter-name": "CMI", "f": "csv"})
    el = time.perf_counter() - t0
    lines = r.text.strip().splitlines()[1:]
    vals = np.array([float(l.split(",")[1]) for l in lines])
    t0 = time.perf_counter()
    rc = c.get(f"{B}/goes-c13-m/edr/position", params={"coords": f"POINT({lon} {lat})", "parameter-name": "CMI"})
    el_cj = time.perf_counter() - t0
    rr = c.get(f"{B}/goes-c13/edr/position", params={"coords": f"POINT({lon} {lat})", "parameter-name": "CMI", "f": "csv"})
    out[name] = dict(status=r.status_code, seconds_csv=round(el, 3), seconds_covjson=round(el_cj, 3), covjson_status=rc.status_code,
                     n=len(vals), direct_1722=direct, edr_1722=float(vals[4]),
                     series_max_abs_vs_cube=float(np.nanmax(np.abs(vals - series_truth))),
                     rad_view_status=rr.status_code, rad_view_first_row=rr.text.splitlines()[1] if rr.status_code == 200 else rr.text[:200])
    print(name, json.dumps(out[name]))
# area: small polygon around Kansas, one time step
poly = "POLYGON((-98 38,-96 38,-96 39,-98 39,-98 38))"
for d in ("goes-c13", "goes-c13-m"):
    t0 = time.perf_counter()
    r = c.get(f"{B}/{d}/edr/area", params={"coords": poly, "parameter-name": "CMI", "datetime": "2026-10-03T17:22:36.734883968", "f": "csv"})
    print("area", d, r.status_code, round(time.perf_counter() - t0, 3), "rows", len(r.text.splitlines()) - 1, r.text[:160].replace("\n", " | "))
for d in ("goes-c13", "goes-c13-m"):
    t0 = time.perf_counter()
    r = c.get(f"{B}/{d}/edr/cube", params={"bbox": "-98,38,-96,39", "parameter-name": "CMI", "f": "csv"})
    print("cube", d, r.status_code, round(time.perf_counter() - t0, 3), "rows", len(r.text.splitlines()) - 1, r.text[:160].replace("\n", " | "))
    t0 = time.perf_counter()
    r = c.get(f"{B}/{d}/edr/cube", params={"bbox": "-98,38,-96,39", "parameter-name": "CMI", "f": "netcdf"})
    print("cube nc", d, r.status_code, round(time.perf_counter() - t0, 3), len(r.content), "bytes")
    if r.status_code == 200 and d.endswith("-m"):
        ds = xr.open_dataset(io.BytesIO(r.content))
        print("  cube nc dims", dict(ds.sizes), "finite", int(np.isfinite(ds.CMI.values).sum()))
