# check_weather_grid.py -- eyeball the units in weather_grid after a run.
# Run once from repo root:  python scripts\check_weather_grid.py
from database import getConn, initPool, closePool


def _fmt(x, nd=1):
    """Format a number to nd decimals, or 'NA' if NULL -- so None never crashes."""
    return f"{x:.{nd}f}" if x is not None else "NA"


def _printCounts(cur):
    """Row + cell-day counts: rows should be ~ cell-days * 24."""
    cur.execute("SELECT count(*), count(DISTINCT (cell_lat, cell_lon, date)) "
                "FROM weather_grid")
    rows, celldays = cur.fetchone()
    print(f"rows = {rows}   cell-days = {celldays}   "
          f"(expect rows ~= cell-days * 24 = {celldays * 24})\n")


def _printOneDay(cur):
    """One cell's full 24h -- the diurnal shape is the best sanity check:
       solar 0 at night and peaking midday, temp rising/falling with it."""
    cur.execute("""
        SELECT hour, temperature_2m, precipitation, cloud_cover, solar_radiation,
               wind_speed_10m, wind_gust, soil_moisture, surface_pressure
        FROM   weather_grid
        ORDER  BY cell_lat, cell_lon, date, hour
        LIMIT  24
    """)
    print("hr   temp  precip  cloud   solar   wind   gust   soil    pres")
    for hr, temp, pr, cl, so, wd, gu, sm, ps in cur.fetchall():
        print(f"{hr:>2}  {_fmt(temp):>5}  {_fmt(pr,3):>6}  {_fmt(cl,0):>4}  "
              f"{_fmt(so,0):>6}  {_fmt(wd):>5}  {_fmt(gu):>5}  "
              f"{_fmt(sm,3):>5}  {_fmt(ps,0):>6}")


def _printRanges(cur):
    """Global min/max: are the extremes physical?"""
    cur.execute("""
        SELECT min(temperature_2m),   max(temperature_2m),
               min(precipitation),    max(precipitation),
               min(solar_radiation),  max(solar_radiation),
               min(soil_moisture),    max(soil_moisture),
               min(surface_pressure), max(surface_pressure)
        FROM weather_grid
    """)
    mm = cur.fetchone()
    print("\nranges:")
    for i, lab in enumerate(["temp degC", "precip mm", "solar W/m2",
                             "soil 0-1", "pres hPa"]):
        print(f"  {lab:11s} min={_fmt(mm[2*i], 3):>9}  max={_fmt(mm[2*i+1], 3):>9}")


def main():
    initPool()
    try:
        with getConn() as conn:
            with conn.cursor() as cur:
                _printCounts(cur)
                _printOneDay(cur)
                _printRanges(cur)
    finally:
        closePool()


if __name__ == "__main__":
    main()