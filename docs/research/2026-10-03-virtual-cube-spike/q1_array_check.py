# /// script
# requires-python = ">=3.12"
# dependencies = ["icechunk==2.2.2", "zarr>=3,<4", "xarray", "h5netcdf", "h5py", "numpy"]
# ///
"""Q1: whole-frame equality of the virtual cube vs a direct h5netcdf read of the same file."""
import glob, time, json
import icechunk as ic, numpy as np, xarray as xr
st = ic.s3_storage(bucket="probe", prefix="static-c13", endpoint_url="http://localhost:19000", region="us-east-1",
                   allow_http=True, force_path_style=True, access_key_id="minioadmin", secret_access_key="minioadmin")
creds = ic.containers_credentials({"s3://noaa-goes19/": ic.s3_credentials(anonymous=True)})
repo = ic.Repository.open(st, authorize_virtual_chunk_access=creds)
cube = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, zarr_format=3)
path = glob.glob("local/*s20262761721174*.nc")[0]
nc = xr.open_dataset(path, engine="h5netcdf")
t0 = time.perf_counter()
c = cube.CMI.sel(t=nc.t.values).values
el = time.perf_counter() - t0
d = nc.CMI.values
q = cube.DQF.sel(t=nc.t.values).values
print(json.dumps(dict(
    frame_read_s=round(el, 3), cube_dtype=str(c.dtype), direct_dtype=str(d.dtype),
    equal_nan=bool(np.array_equal(c, d, equal_nan=True)), max_abs=float(np.nanmax(np.abs(c - d))),
    nan_cube=int(np.isnan(c).sum()), nan_direct=int(np.isnan(d).sum()),
    dqf_equal=bool(np.array_equal(q, nc.DQF.values, equal_nan=True)),
    cube_attrs={k: str(cube.CMI.encoding.get(k)) for k in ("scale_factor", "add_offset", "_FillValue", "dtype", "_Unsigned")},
    x_equal=bool(np.array_equal(cube.x.values, nc.x.values)), y_equal=bool(np.array_equal(cube.y.values, nc.y.values)),
    x_units=cube.x.attrs.get("units"), t_in_cube=str(nc.t.values) in [str(v) for v in cube.t.values],
)))
print("x maxdiff", float(np.abs(cube.x.values - nc.x.values).max()), cube.x.dtype, nc.x.dtype, "pixel", float(nc.x.values[1]-nc.x.values[0]))
print("y maxdiff", float(np.abs(cube.y.values - nc.y.values).max()))
