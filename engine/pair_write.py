# Project: xc-predictor
# Subset:  Pair Engine -- database writer
#
# ★ WRITES ONLY TO NEW TABLES, PREFIXED pair_. Nothing existing is touched: not
#   athlete_ratings, not course_difficulties, not results.speed_rating. The
#   website keeps running on the ALS output until you choose to switch, and a bad
#   run here cannot cost you a 20-minute heap rebuild or an orphaned results_old.
#
# THE GRAIN PROBLEM, AND WHY TWO TABLES
#   athlete_ratings is one row per (athlete_id, pool). The pair engine produces
#   one per (person_id, pool, SEASON), because alpha is per athlete-season -- a
#   high schooler improving 4%/year cannot be one number.
#
#   Collapsing season -> career destroys information, and which collapse is right
#   is a product decision, not a technical one:
#       best     -- peak season. What "how good was this athlete" usually means.
#       recent   -- latest season. What a current-form leaderboard wants.
#       weighted -- race-count weighted mean across seasons. Most stable.
#   So pair_athlete_season keeps the full grain and pair_athlete carries ALL
#   THREE collapses as separate columns. Nothing is decided here; the choice
#   moves to a SQL column name.
#
# ERA
#   rating_seasonal divides by pool_mean within (pool, season), so any era error
#   common to a season cancels exactly -- it appears in numerator and
#   denominator. Immune by construction, at the cost of NOT being comparable
#   across years. rating_career divides by an all-years pool_mean and therefore
#   inherits whatever era_curve.pkl gets wrong. Both are written; they answer
#   different questions.

import io
import os
import sys

import numpy as np

_TABLES = ("pair_athlete_season", "pair_athlete", "pair_course_difficulty")


# ------------------------------------------------------------------ #
# CHUNK 1 -- INPUTS
# ------------------------------------------------------------------ #

# loadRatings
# Purpose:   the arrays pair_ratings.py wrote.
def loadRatings(path):
    with np.load(path, allow_pickle=False) as f:
        return {k: f[k] for k in f.files}


# loadDegree
# Purpose:   per-cell degree from pair_validated.npz, if it exists.
# Output:    ndarray or None.
#
# Optional on purpose: degree is useful metadata on a difficulty row (how many
# athlete-seasons pinned it) but the writer must not require a file from a
# different script to have been run first.
def loadDegree(path, n_cells):
    if not os.path.exists(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as f:
            deg = f["degree"]
        return deg if deg.size == n_cells else None
    except Exception:
        return None


# splitCourseKey
# Purpose:   (canonical_id, distance_m) from an engine course key.
# Output:    (id_or_name or None, distance or None)
#
# rpartition(":d") scans from the right, so a venue name containing a colon
# cannot break the parse. Both XC key shapes are handled; TF keys return None
# for the id, which is correct -- they are keyed on a location, not a course.
def splitCourseKey(key):
    if key.startswith("XC:"):
        venue, tag, dist = key[3:].rpartition(":d")
        if tag and dist.isdigit():
            return (venue or None, int(dist))
        return (key[3:] or None, None)
    return (None, None)


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE CAREER COLLAPSE
# ------------------------------------------------------------------ #

# _argmaxBy
# Purpose:   for each group, the index of the row with the largest `value`.
# Arguments: group -- dense codes; value; n_groups; valid.
# Output:    ndarray of row indices, -1 where the group has no valid row.
#
# Syntax: sorting by (group, value) then taking the LAST row of each group is
# one sort instead of a Python loop over 7.4M rows. np.searchsorted finds each
# group's boundary in the sorted order.
def _argmaxBy(group, value, n_groups, valid):
    idx = np.nonzero(valid)[0]
    if idx.size == 0:
        return np.full(n_groups, -1, dtype=np.int64)
    order = idx[np.lexsort((value[idx], group[idx]))]
    out = np.full(n_groups, -1, dtype=np.int64)
    out[group[order]] = order           # later writes win -> largest value
    return out


# collapseToCareer
# Purpose:   one row per (person_id, pool) with all three collapse rules.
# Arguments: r -- the loaded ratings dict.
# Output:    dict of arrays, plus the (person, pool) key arrays.
#
# ★ ALL THREE, NOT ONE. See the module docstring: choosing between peak, recent
#   and weighted is a product decision and this module refuses to make it.
def collapseToCareer(r, min_races_best=3):
    """
    ★ 'BEST' USES ONLY WELL-MEASURED SEASONS. buildRatings now rates 2-race
      athlete-seasons -- coverage went from 73% to near ALS's 87% -- but a peak
      taken as the MAXIMUM over seasons is exactly where a noisy estimate wins.
      A 2-race season is rated and reported at its own grain; it does not get to
      define a career.

      recent and weighted are unaffected: recent is chosen by season, and
      weighted is race-count weighted, so a 2-race season contributes 2/N.
    """
    valid = r["valid"].astype(bool)
    well = valid & (r["races"].astype(np.int64) >= min_races_best)
    if not well.any():
        well = valid
    pid = r["person_id"].astype(str)
    pool = r["pool"].astype(str)
    season = r["season"].astype(np.int64)
    races = r["races"].astype(np.int64)

    combo = np.char.add(np.char.add(pid, "\x1f"), pool)
    uniq, code = np.unique(combo, return_inverse=True)
    n = uniq.size

    best = _argmaxBy(code, r["rating_seasonal"], n, well)
    recent = _argmaxBy(code, season.astype(np.float64), n, valid)

    # Race-count weighted mean of the seasonal rating.
    w = np.where(valid, races.astype(np.float64), 0.0)
    num = np.bincount(code, weights=np.where(valid, r["rating_seasonal"], 0.0) * w,
                      minlength=n)
    den = np.bincount(code, weights=w, minlength=n)
    weighted = np.where(den > 0, num / np.maximum(den, 1e-9), np.nan)

    seasons = np.bincount(code, weights=valid.astype(np.float64), minlength=n)
    total_races = np.bincount(code, weights=w, minlength=n)

    keep = best >= 0
    split = np.array([u.split("\x1f") for u in uniq])
    return {
        "person_id": split[:, 0][keep],
        "pool": split[:, 1][keep],
        "rating_best": r["rating_seasonal"][best[keep]],
        "best_season": season[best[keep]],
        "rating_recent": r["rating_seasonal"][recent[keep]],
        "recent_season": season[recent[keep]],
        "rating_weighted": weighted[keep],
        "n_seasons": seasons[keep].astype(np.int64),
        "total_races": total_races[keep].astype(np.int64),
    }


# ------------------------------------------------------------------ #
# CHUNK 3 -- SQL
# ------------------------------------------------------------------ #

_DDL = {
    "pair_athlete_season": """
        CREATE TABLE pair_athlete_season (
            person_id        text    NOT NULL,
            pool             text    NOT NULL,
            season           int     NOT NULL,
            races            int     NOT NULL,
            ability          double precision,
            rating_seasonal  double precision,
            rating_career    double precision,
            PRIMARY KEY (person_id, pool, season)
        )""",
    "pair_athlete": """
        CREATE TABLE pair_athlete (
            person_id        text    NOT NULL,
            pool             text    NOT NULL,
            rating_best      double precision,
            best_season      int,
            rating_recent    double precision,
            recent_season    int,
            rating_weighted  double precision,
            n_seasons        int,
            total_races      int,
            PRIMARY KEY (person_id, pool)
        )""",
    "pair_course_difficulty": """
        CREATE TABLE pair_course_difficulty (
            course_key       text    NOT NULL PRIMARY KEY,
            canonical_id     text,
            distance_m       int,
            difficulty       double precision,
            degree           int
        )""",
}

_INDEXES = (
    "CREATE INDEX ON pair_athlete_season (pool, season, rating_seasonal DESC)",
    "CREATE INDEX ON pair_athlete_season (person_id)",
    "CREATE INDEX ON pair_athlete (pool, rating_best DESC)",
    "CREATE INDEX ON pair_course_difficulty (canonical_id, distance_m)",
)


# _fmt
# Purpose:   one COPY field.
#
# ⚠ NULL IS "\N", NOT "". COPY's text format treats an empty field as an empty
#   STRING, which is fine for text and fails outright on an integer column
#   ("invalid input syntax for type integer"). A NaN rating and an absent degree
#   must both become a real SQL NULL.
def _fmt(v):
    if v is None:
        return "\\N"
    if isinstance(v, (float, np.floating)):
        return "\\N" if not np.isfinite(v) else f"{float(v):.6f}"
    return str(v)


# copyRows
# Purpose:   bulk-load an iterable of tuples into a table.
# Arguments: cur -- open cursor; table; columns; rows; batch.
#
# ★ COPY, NOT INSERT. 7.4M athlete-seasons through executemany would take tens
#   of minutes; COPY is seconds. psycopg3 and psycopg2 expose it differently, so
#   both are attempted -- this module does not know which driver database.py
#   brings.
def copyRows(cur, table, columns, rows, batch=200_000):
    cols = ", ".join(columns)
    sql = f"COPY {table} ({cols}) FROM STDIN"
    buf, n, total = [], 0, 0

    def flush(text):
        try:                                            # psycopg3
            with cur.copy(sql) as cp:
                cp.write(text)
        except AttributeError:                          # psycopg2
            cur.copy_expert(sql, io.StringIO(text))

    for row in rows:
        buf.append("\t".join(_fmt(v) for v in row))
        n += 1
        if n >= batch:
            flush("\n".join(buf) + "\n")
            total += n
            buf, n = [], 0
            print(f"      {total:,} rows...")
    if buf:
        flush("\n".join(buf) + "\n")
        total += n
    return total


# ------------------------------------------------------------------ #
# CHUNK 4 -- ROW GENERATORS
# ------------------------------------------------------------------ #

def seasonRows(r):
    """One row per rated athlete-season."""
    valid = r["valid"].astype(bool)
    for i in np.nonzero(valid)[0]:
        yield (str(r["person_id"][i]), str(r["pool"][i]),
               int(r["season"][i]), int(r["races"][i]),
               float(r["ability"][i]), float(r["rating_seasonal"][i]),
               float(r["rating_career"][i]))


def careerRows(c):
    """One row per (person_id, pool), all three collapses."""
    for i in range(c["person_id"].size):
        yield (c["person_id"][i], c["pool"][i],
               float(c["rating_best"][i]), int(c["best_season"][i]),
               float(c["rating_recent"][i]), int(c["recent_season"][i]),
               float(c["rating_weighted"][i]), int(c["n_seasons"][i]),
               int(c["total_races"][i]))


def difficultyRows(keys, difficulty, degree):
    """One row per cell, with canonical_id and distance parsed out of the key."""
    for i, key in enumerate(keys):
        cid, dist = splitCourseKey(str(key))
        yield (str(key), cid, dist, float(difficulty[i]),
               int(degree[i]) if degree is not None else None)


# ------------------------------------------------------------------ #
# CHUNK 5 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(ratings_path, validated_path):
    from database import getConn

    r = loadRatings(ratings_path)
    keys = r["course_keys"]
    degree = loadDegree(validated_path, len(keys))
    career = collapseToCareer(r)

    n_season = int(r["valid"].astype(bool).sum())
    print(f"[write] {n_season:,} athlete-seasons, "
          f"{career['person_id'].size:,} athlete careers, "
          f"{len(keys):,} cells"
          f"{'' if degree is not None else '  (no degree file; column left NULL)'}")

    with getConn() as conn:
        with conn.cursor() as cur:
            # Idempotent: a re-run replaces cleanly rather than appending.
            for t in _TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            for t in _TABLES:
                cur.execute(_DDL[t])

            print("    pair_athlete_season...")
            a = copyRows(cur, "pair_athlete_season",
                         ("person_id", "pool", "season", "races", "ability",
                          "rating_seasonal", "rating_career"), seasonRows(r))
            print("    pair_athlete...")
            b = copyRows(cur, "pair_athlete",
                         ("person_id", "pool", "rating_best", "best_season",
                          "rating_recent", "recent_season", "rating_weighted",
                          "n_seasons", "total_races"), careerRows(career))
            print("    pair_course_difficulty...")
            c = copyRows(cur, "pair_course_difficulty",
                         ("course_key", "canonical_id", "distance_m",
                          "difficulty", "degree"),
                         difficultyRows(keys, r["difficulty"], degree))

            for stmt in _INDEXES:
                cur.execute(stmt)
            for t in _TABLES:
                cur.execute(f"ANALYZE {t}")
        conn.commit()

    print(f"\n[write] wrote {a:,} / {b:,} / {c:,} rows")
    print("[write] nothing existing was modified. To see the leaderboard:")
    print("""
    SELECT an.name, s.season, s.races,
           round(s.rating_seasonal::numeric, 1) AS rating
    FROM pair_athlete_season s
    JOIN athlete_named an ON an.person_id::text = s.person_id
    WHERE s.pool = 'hs_m' AND s.races >= 20
    ORDER BY s.rating_seasonal DESC
    LIMIT 25;""")


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    main(sys.argv[1] if len(sys.argv) > 1
         else os.path.join(here, "pair_ratings.npz"),
         sys.argv[2] if len(sys.argv) > 2
         else os.path.join(here, "pair_validated.npz"))