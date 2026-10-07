"""above_level.py -- "Ran above their level" on a race page (owner,
2026-10-05): each runner's rating in THIS race against the level they show
the rest of the season, and the runners who beat it by more than a normal
day explains.

★ THEIR LEVEL COMING IN (owner, 2026-10-07: "ran above their level only
  takes into account current season ratings, should prolly be career rating
  before the race"). The yardstick is the site's own season statistic --
  the 80th percentile of rated races (build_ranking_results._SEASON_Q),
  after dropping races far under the median (_SEASON_OUTLIER_PTS) -- over
  the athlete's races in the 365 DAYS BEFORE this one, same sport, same
  pool. It used to be the other races of this season, which included races
  run AFTER this one: a breakthrough in September was judged against the
  level it led to in November. A year back is one full season cycle -- the
  span a season rating covers -- so it carries last season's form into an
  opener without letting a four-year-old freshman season drag it down. A
  runner with no race in that year has no level and no gap.

★ IN PERCENT. The gap is 100 * (race / level - 1). A rating is a speed, so
  a percent is the same whether the page shows pool ratings or the
  HS-equivalent scale, and it reads the same for a 100 and a 150 runner.

★ "ABOVE" MEANS MORE THAN A NORMAL DAY'S SWING. sigma is measured from
  these same runners: the pooled spread of their races in that year, in
  percent. Beating your level by more than that is the surprise; anything
  smaller is a good day. Nothing here is a chosen threshold.

! THE SAME RACE TWICE IS ONE RACE. Both feeds can carry one physical race,
  so a row on the same day at the same distance is this race, not another.

! NEVER A 500: a failed query leaves the rows unstamped and returns None.
"""
import math

SEASON_Q = 0.80            # build_ranking_results._SEASON_Q
LOOKBACK_DAYS = 365        # one season cycle before the race
OUTLIER_PTS = 20.0         # build_ranking_results._SEASON_OUTLIER_PTS


def _quantile(vals, q):
    """percentile_cont: linear interpolation, as Postgres computes it."""
    v = sorted(vals)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _seasonRows(rows):
    """The season's aggregate rows: the outlier rule the season rating uses."""
    if not rows:
        return []
    med = _quantile(rows, 0.5)
    return [r for r in rows if r >= med - OUTLIER_PTS]


def stampAboveLevel(cur, sport, rows, race_date, distance):
    """Stamp each rated row with `level` and `vs_level` (percent) and return
    {"sigma": pct, "surprises": [rows, biggest first], "n": rows with a
    level}, or None when nothing could be computed."""
    if not race_date or not rows:
        return None
    rated = [r for r in rows if r.get("person_id") and r.get("speed_rating")]
    if not rated:
        return None
    try:
        cur.execute("SAVEPOINT above_level")
        # ★ ONE INDEX PROBE PER RUNNER. `person_id = ANY(...)` leaves the
        #   planner free to read the board index for the whole season and
        #   filter; the lateral pins it to the person index, a few dozen
        #   rows each.
        cur.execute("""
            SELECT r.person_id, r.pool, r.speed_rating, r.race_date::text AS day, r.distance
            FROM   unnest(%(pids)s::bigint[]) AS p(pid)
            CROSS  JOIN LATERAL (
                   SELECT person_id, pool, speed_rating, race_date, distance
                   FROM   ranking_results
                   WHERE  person_id = p.pid AND sport = %(sport)s
                     AND  race_date <  %(day)s::date
                     AND  race_date >= %(day)s::date - %(back)s
                     AND  speed_rating IS NOT NULL) r
        """, {"pids": sorted({int(r["person_id"]) for r in rated}), "sport": sport,
              "day": str(race_date)[:10], "back": LOOKBACK_DAYS})
        season = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT above_level")
    except Exception as exc:                     # noqa: BLE001 -- UndefinedTable et al.
        try:
            cur.execute("ROLLBACK TO SAVEPOINT above_level")
        except Exception:                        # noqa: BLE001
            cur.connection.rollback()
        print(f"above_level: {sport} {race_date} skipped: {type(exc).__name__}: {exc}",
              flush=True)
        return None

    day = str(race_date)[:10]
    dist = float(distance) if distance else None
    by = {}
    for s in season:
        by.setdefault((s["person_id"], (s["pool"] or "").split("|")[0]), []).append(s)

    # sigma: pooled spread of 100*ln(rating) over each runner's year, every runner here
    ss, dof = 0.0, 0
    for races in by.values():
        logs = [100.0 * math.log(float(x)) for x in _seasonRows(
            [float(s["speed_rating"]) for s in races]) if x > 0]
        if len(logs) >= 2:
            m = sum(logs) / len(logs)
            ss += sum((x - m) ** 2 for x in logs)
            dof += len(logs) - 1
    sigma = math.sqrt(ss / dof) if dof else None

    n = 0
    for r in rated:
        pool = (r.get("rating_pool") or r.get("pool") or "").split("|")[0]
        races = by.get((r["person_id"], pool)) or []
        others = [float(s["speed_rating"]) for s in races
                  if not (s["day"] == day and (dist is None or s["distance"] is None
                                                or abs(float(s["distance"]) - dist) < 1))]
        others = _seasonRows(others)
        if not others:
            continue
        level = _quantile(others, SEASON_Q)
        if not level:
            continue
        r["level"] = level
        r["vs_level"] = 100.0 * (float(r["speed_rating"]) / level - 1.0)
        n += 1
    if not n:
        return None
    surprises = sorted((r for r in rated if r.get("vs_level") is not None
                        and sigma and r["vs_level"] > sigma),
                       key=lambda r: -r["vs_level"])
    for r in surprises:
        r["above_level"] = True
    return {"sigma": sigma, "surprises": surprises, "n": n}
