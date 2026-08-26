"""
ensure_weather_table.py -- create the (empty) weather table.

    python scripts/ensure_weather_table.py

feature_extraction LEFT JOINs weather; a database where the table was
never created killed the 2026-08-25 features run six hours in (and the
guard added since survives it with all-zero weather features). This
creates the table empty in seconds -- safe with anything else running.
Filling it is backfill/weather_backfill.py: batched Open-Meteo fetches,
resumable, and a long job you run when you want real weather features.
"""

import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "backfill")

from database import getConn                      # noqa: E402
from weather_backfill import _createWeatherTable  # noqa: E402


def main():
    with getConn() as conn:
        _createWeatherTable(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM weather")
            n = cur.fetchone()[0]
    print(f"weather table ready ({n:,} rows fetched so far)")


if __name__ == "__main__":
    main()
