# probe_store.py -- why is one point-read 7 seconds? Prints the chunk geometry
# (the deciding fact), the time coverage (explains the 0-row overseas cells),
# and times a single US read. Run:  python scripts\probe_store.py
import time
import pandas as pd
import xarray as xr
import icechunk

storage = icechunk.s3_storage(bucket="earthmover-icechunk-era5", prefix="icechunkV2",
                              region="us-east-1", anonymous=True)
ds = xr.open_zarr(icechunk.Repository.open(storage).readonly_session("main").store,
                  group="single/temporal", consolidated=False, chunks=None)

t2m = ds["t2m"]
print("dims:        ", t2m.dims)
print("shape:       ", t2m.shape)
print("CHUNK shape: ", t2m.encoding.get("chunks"), "  <-- the deciding number")

vt = ds["valid_time"].values
print("coverage:    ", vt[0], "->", vt[-1], f"(n={len(vt)})")

# Time one US point, one day, ONE variable -- isolates per-read overhead.
t = time.time()
_ = (t2m.sel(latitude=42.0, longitude=289.0, method="nearest")
        .sel(valid_time=pd.date_range("2015-09-05", "2015-09-05 23:00", freq="h"))
        .load())
print(f"1 US point, 24h, t2m only: {time.time() - t:.2f}s")