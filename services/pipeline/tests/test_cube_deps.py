"""Z-4 dependencies (spec §11): the cube libraries import and round-trip."""

import icechunk as ic
import numpy as np
import zarr


def test_cube_libraries_import():
    import h5py  # noqa: F401
    import obstore  # noqa: F401
    import xarray  # noqa: F401
    from obspec_utils.registry import ObjectStoreRegistry  # noqa: F401
    from virtualizarr import open_virtual_dataset  # noqa: F401
    from virtualizarr.parsers import HDFParser  # noqa: F401

    assert ic.__version__.split(".")[0] == "2"
    assert zarr.__version__.split(".")[0] == "3"


def test_icechunk_round_trips_in_memory():
    repo = ic.Repository.create(ic.in_memory_storage())
    session = repo.writable_session("main")
    array = zarr.group(store=session.store).create_array("a", shape=(2,), dtype="i4")
    array[:] = np.array([1, 2], dtype="i4")
    snapshot = session.commit("one")
    assert repo.lookup_branch("main") == snapshot
    readback = zarr.open_group(repo.readonly_session("main").store, mode="r")
    assert readback["a"][:].tolist() == [1, 2]
