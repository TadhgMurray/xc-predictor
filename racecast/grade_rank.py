# Project: xc-predictor / racecast
# File:    grade_rank.py
# Purpose: The grade line under the athlete header's rating (owner,
#          2026-10-10, item 13): "#212 of HS sophomore boys nationally ·
#          top 3.0% · #14 in CA".
#
# ★ ONE ROW, NEVER A COUNT. build_season_ranks (step 10g) stores each
#   season's place among its own grade, nationally and in its state, with
#   the two totals beside them (season_rank.grade_*); this reads that row on
#   its primary key. The page's percentile already comes from the same
#   table's nation_total, so the two lines agree by construction.
#
# ! A TABLE BUILT BEFORE THE GRADE COLUMNS EXISTED READS AS "NO LINE". The
#   columns are looked up once per process; until step 10g has run with this
#   build the header simply has no grade line -- never an error, and never
#   the slow live count the rank line falls back to.
#
# ! ONLY A GRADE THE POOL ACTUALLY HAS. The key is rankings.gradeKeySql's,
#   which passes junk through ("Class of 2011" keys as 201); a key that is
#   not one of the level's grades names nobody and gets no line.
from season_floor import floorFor, percentileWords

_COLS = {"checked": False, "ready": False}

# key -> the class as a reader says it, per level. The middle-school grades
# are the engine's own (normalize_distance.GRADE_TO_LEVEL: 6-8 pool as ms).
_HS = {"9": "freshman", "10": "sophomore", "11": "junior", "12": "senior"}
try:
    from normalize_distance import GRADE_TO_LEVEL as _G2L
    _MS = {g: f"{g}th-grade" for g, lv in _G2L.items() if lv == "ms" and g.isdigit()}
except Exception:                                       # noqa: BLE001
    _MS = {g: f"{g}th-grade" for g in ("6", "7", "8")}
_COLLEGE = {"fr": "freshman", "so": "sophomore", "jr": "junior", "sr": "senior"}
_SEX = {"m": ("boys", "men"), "f": ("girls", "women")}


def _ready(cur):
    if not _COLS["checked"]:
        try:
            cur.execute("""SELECT count(*) AS n FROM information_schema.columns
                           WHERE table_name = 'season_rank'
                             AND column_name IN ('grade_key', 'grade_nation',
                                                 'grade_nation_total', 'grade_state',
                                                 'grade_state_total')""")
            row = cur.fetchone()
            n = row["n"] if isinstance(row, dict) else row[0]
            _COLS["ready"] = int(n or 0) == 5
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            _COLS["ready"] = False
        _COLS["checked"] = True
    return _COLS["ready"]


def gradeWords(pool, key):
    """'HS sophomore boys', '7th-grade girls', 'college junior men', or None
    for a key the pool's level does not have."""
    bare = (pool or "").split("|", 1)[0]
    level, _, sex = bare.partition("_")
    if sex not in _SEX:
        return None
    k = str(key or "").strip().lower()
    if level == "hs" and k in _HS:
        return f"HS {_HS[k]} {_SEX[sex][0]}"
    if level == "ms" and k in _MS:
        return f"{_MS[k]} {_SEX[sex][0]}"
    if level == "college" and k in _COLLEGE:
        return f"college {_COLLEGE[k]} {_SEX[sex][1]}"
    return None


def boardHref(season, key, state=None):
    """The board the number is read off: the season's ability board, its
    own floor, filtered to the grade (and the state)."""
    q = [("board", "ability"), ("pool", season["pool"]), ("sport", season["sport"]),
         ("year", str(season["year"])), ("min_races", str(floorFor(season["year"]))),
         ("grade", str(key))]
    if state:
        q.append(("state", state))
    return "/rankings?" + "&".join(f"{k}={v}" for k, v in q)


def lineFrom(row, season):
    """The line's pieces from a season_rank row, or None when there is
    nothing real to say."""
    if not row:
        return None
    key = row.get("grade_key")
    words = gradeWords(season.get("pool"), key)
    rank, total = row.get("grade_nation"), row.get("grade_nation_total")
    if not words or not rank or not total:
        return None
    out = {"words": words, "rank": int(rank), "total": int(total),
           "pct": percentileWords(rank, total),
           "href": boardHref(season, key)}
    state = (season.get("state") or "").strip().upper()
    srank, stotal = row.get("grade_state"), row.get("grade_state_total")
    # ★ IN-STATE WHEN IT SAYS SOMETHING: a state with one runner in the grade
    #   would print "#1 in XX", which is a count, not a place.
    if state and srank and stotal and int(stotal) > 1:
        out.update(state=state, state_rank=int(srank), state_total=int(stotal),
                   state_href=boardHref(season, key, state))
    return out


def gradeRank(cur, person_id, season):
    """The grade line for the header season, or None. Never raises."""
    if not season or not season.get("pool"):
        return None
    try:
        if not _ready(cur):
            return None
        cur.execute("""SELECT grade_key, grade_nation, grade_nation_total,
                              grade_state, grade_state_total
                       FROM   season_rank
                       WHERE  person_id = %s AND pool = %s AND sport = %s AND year = %s""",
                    (person_id, season["pool"], season["sport"], season["year"]))
        row = cur.fetchone()
    except Exception as exc:                            # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:                               # noqa: BLE001
            pass
        print(f"grade_rank: read failed ({type(exc).__name__}: {exc})", flush=True)
        return None
    return lineFrom(dict(row) if row else None, season)
