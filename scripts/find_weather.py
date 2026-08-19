# drop_old_weather.py  (irreversible)
# Drops the retired Open-Meteo weather table. Plain DROP (no CASCADE): if
# something still depends on it, this errors LOUD instead of deleting dependents.
from database import getConn, initPool, closePool

initPool()
try:
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS weather_openmeteo_old")
        conn.commit()
        print("dropped weather_openmeteo_old")
finally:
    closePool()