"""
record_books.py -- the history pages: state all-time lists, school record
books, course records by era, and "on this day". The shapes, the pure
arithmetic and the one-row loader. The SQL lives in build_record_books.py.

★ PRECOMPUTED, ALWAYS (owner, 2026-10-10: "evergreen, shareable pages built
  from data already there"). Every page here is one primary-key read of
  record_books, written by build_record_books.py (a background pipeline
  step). A page whose row is not built yet SAYS SO; it never falls back to
  a live scan of 60M rows, which is exactly what an all-time list would be.

★ ERA-ADJUSTED BECAUSE THE RATINGS ARE. A speed rating is solved against
  each course's difficulty in its own two-year era (engine/joint_solve.py,
  docs/ENGINE.md), so a 2003 rating and a 2025 rating sit on one scale.
  The lists rank on ratings for that reason, and the pages say so
  (ERA_NOTE). Times are shown, never ranked across courses -- except on a
  course's own record list, where one course at one distance IS one scale.

! THE FILTERS ARE THE BOARDS', NOT NEW ONES. Every row comes from
  ranking_results / athlete_season / team_season (condemned races, twins,
  pro pools and rating outliers never reach them), sliced with
  rankings._whereClauses / season_floor.floorSql / teams._where. See
  build_record_books.py.
"""

# ★ 2026-10-10: one sentence, printed on every page, so nobody has to ask
ERA_NOTE = ("Ratings are era-adjusted: every course is re-measured in each "
            "two-year era, so a 2004 rating and a 2025 rating are on one "
            "scale. Times are as run.")

KINDS = ("state", "school", "course", "otd")

LIST_N = 100            # state all-time lists
GRADE_N = 10            # per grade, state page
SCHOOL_EVENT_N = 10     # per event, school record book
SCHOOL_GRADE_N = 5      # per grade, school record book
IMPROVED_N = 10
TEAM_N = 25             # state page: best team seasons
SCHOOL_TEAM_N = 5
DECADE_N = 3            # course: best per decade
COURSE_DISTS = 4        # course: the most-raced distances kept
OTD_N = 6               # on this day: candidates stored per (day, scope)

# level slug -> (pool prefix, words, grades in order)
LEVELS = {
    "hs":      ("hs",      "High School",   ("9", "10", "11", "12")),
    "ms":      ("ms",      "Middle School", ("6", "7", "8")),
    "college": ("college", "College",       ("fr", "so", "jr", "sr")),
}
BUILD_LEVELS = ("hs", "ms")          # the default build; --levels adds college

GENDERS = {"m": ("boys", "men"), "f": ("girls", "women")}

GRADE_WORDS = {"9": "Freshman", "10": "Sophomore", "11": "Junior",
               "12": "Senior", "6": "6th grade", "7": "7th grade",
               "8": "8th grade", "fr": "Freshman", "so": "Sophomore",
               "jr": "Junior", "sr": "Senior"}


def poolFor(level, gender):
    """('hs', 'f') -> 'hs_f'; None for an unknown level or gender."""
    if level not in LEVELS or gender not in GENDERS:
        return None
    return f"{LEVELS[level][0]}_{gender}"


def genderWord(level, gender):
    adult = level == "college"
    return GENDERS.get(gender, ("", ""))[1 if adult else 0]


def parseGender(raw):
    """'boys' / 'men' / 'm' -> 'm'; 'girls' / 'women' / 'f' -> 'f'; else 'm'."""
    v = (raw or "").strip().lower()
    if v in ("f", "girls", "women", "w", "g"):
        return "f"
    return "m"


# ------------------------------------------------------------------ #
#  KEYS -- one string per page, the table's primary key with the kind
# ------------------------------------------------------------------ #

def stateKey(sport, pool, state):
    return f"{sport.upper()}|{pool}|{state.upper()}"


def schoolKey(school, state, sport):
    # ! the state may be None (a name with no identity cluster): '' keeps
    #   the key a plain string and the PK unique
    return f"{school}|{(state or '').upper()}|{sport.upper()}"


def courseKey(course):
    return course


def otdKey(mmdd, scope=None):
    """'10-10', 'CA' -> '10-10|CA'; no scope is the national card."""
    return f"{mmdd}|{(scope or 'US').upper()}"


# ------------------------------------------------------------------ #
#  PURE ARITHMETIC -- testable without a database
# ------------------------------------------------------------------ #

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bestPerPerson(rows, key, higher=True, person="person_id"):
    """One row per person, their best by `key` (higher or lower is better),
    then ordered best first. Rows with no value or no person are dropped.
    Ties keep the earlier row (the SQL hands them over in a stable order)."""
    best = {}
    for r in rows or ():
        v = _num(r.get(key))
        p = r.get(person)
        if v is None or p is None:
            continue
        cur = best.get(p)
        if cur is None or (v > cur[0] if higher else v < cur[0]):
            best[p] = (v, r)
    out = [r for _v, r in best.values()]
    out.sort(key=lambda r: (-_num(r[key]) if higher else _num(r[key])))
    return out


def gradeBests(rows, grades, n, key="rating"):
    """[(grade, words, rows)] in the level's grade order: each grade's best
    `n` SEASONS by `key`, one per athlete (a runner's two junior seasons
    are one, the better). `rows` carry grade_key from the SQL's
    rankings.gradeKeySql -- the boards' own reading of "Sr" / "12" / "SR-4".
    A grade nobody filled is left out rather than shown empty."""
    by = {}
    for r in rows or ():
        g = str(r.get("grade_key") or "").strip().lower()
        if g in grades:
            by.setdefault(g, []).append(r)
    out = []
    for g in grades:
        got = bestPerPerson(by.get(g, ()), key)[:n]
        if got:
            out.append((g, GRADE_WORDS.get(g, g), got))
    return out


def mostImproved(seasons, n=IMPROVED_N, key="rating"):
    """The biggest season-on-season jumps: one athlete, one pool, year Y
    against year Y-1, both seasons on file. Best jump per athlete, then the
    `n` biggest. Same pool on both sides on purpose: a rating is a place in
    its own pool's scale, so a middle-school 120 to a high-school 105 is
    not a fall."""
    by = {}
    for s in seasons or ():
        v = _num(s.get(key))
        if v is None or s.get("person_id") is None or s.get("year") is None:
            continue
        by[(s["person_id"], s.get("pool"), int(s["year"]))] = s
    jumps = []
    for (pid, pool, year), s in by.items():
        prev = by.get((pid, pool, year - 1))
        if prev is None:
            continue
        d = _num(s[key]) - _num(prev[key])
        if d <= 0:
            continue
        jumps.append(dict(s, prev_rating=_num(prev[key]),
                          prev_year=prev.get("year"), gain=round(d, 1)))
    best = {}
    for j in jumps:
        p = j["person_id"]
        if p not in best or j["gain"] > best[p]["gain"]:
            best[p] = j
    out = sorted(best.values(), key=lambda j: (-j["gain"], str(j.get("name") or "")))
    return out[:n]


def progression(rows, time_key="time_seconds", date_key="race_date"):
    """The course record as it stood over time: every run that beat every
    earlier one, oldest first, each with the date it fell (`until`) and how
    much faster it was than the record before it (`by`). Ties do not break
    a record. Rows need a time and a date; the rest rides along."""
    rows = [r for r in rows or ()
            if _num(r.get(time_key)) and r.get(date_key)]
    rows.sort(key=lambda r: (str(r[date_key]), _num(r[time_key]),
                             r.get("result_id") or 0))
    out, best = [], None
    for r in rows:
        t = _num(r[time_key])
        if best is None or t < best:
            e = dict(r)
            e["by"] = round(best - t, 1) if best is not None else None
            out.append(e)
            best = t
    for a, b in zip(out, out[1:]):
        a["until"] = b[date_key]
    if out:
        out[-1]["until"] = None              # still standing
    return out


def decadeOf(date_or_year):
    s = str(date_or_year or "")[:4]
    return (int(s) // 10) * 10 if s.isdigit() else None


def decadeBests(rows, n=DECADE_N, time_key="time_seconds",
                date_key="race_date"):
    """[(decade, rows)] newest decade first: each decade's `n` fastest,
    one per athlete. The decade is the calendar one of the race's date
    ('2010s' = 2010-2019), which is what a reader means by it."""
    by = {}
    for r in rows or ():
        d = decadeOf(r.get(date_key))
        if d is not None and _num(r.get(time_key)):
            by.setdefault(d, []).append(r)
    out = []
    for d in sorted(by, reverse=True):
        out.append((d, bestPerPerson(by[d], time_key, higher=False)[:n]))
    return out


def otdPick(rows, today_year):
    """The card's line: the best-rated row from a PAST year (the build stores
    candidates from every year; this year's race is news, not history).
    Rows arrive best first. None when nothing qualifies."""
    for r in rows or ():
        y = str(r.get("race_date") or "")[:4]
        if y.isdigit() and int(y) < int(today_year):
            return dict(r, years_ago=int(today_year) - int(y))
    return None


def distanceLabel(meters):
    """5000 -> '5000m', 4828 -> '3 Mile' -- school_prs's own names."""
    try:
        from school_prs import _distLabel
        return _distLabel(int(round(float(meters))))
    except Exception:                                   # noqa: BLE001
        return f"{meters}m" if meters else ""


# ------------------------------------------------------------------ #
#  THE ONE READ
# ------------------------------------------------------------------ #

def loadBook(cur, kind, key):
    """The stored ctx for one page, or None (not built, or no table yet).
    One primary-key lookup; never raises -- a missing table is a page that
    says "being compiled", not a 500."""
    try:
        cur.execute("SAVEPOINT record_book")
        cur.execute("SELECT ctx, built_at FROM record_books "
                    "WHERE kind = %s AND key = %s", (kind, key))
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT record_book")
    except Exception:                                   # noqa: BLE001
        try:
            cur.execute("ROLLBACK TO SAVEPOINT record_book")
        except Exception:                               # noqa: BLE001
            try:
                cur.connection.rollback()
            except Exception:                           # noqa: BLE001
                pass
        return None
    if row is None:
        return None
    ctx = row["ctx"] if isinstance(row, dict) else row[0]
    built = row["built_at"] if isinstance(row, dict) else row[1]
    if isinstance(ctx, dict):
        ctx = dict(ctx)
        ctx.setdefault("built_at", str(built)[:10] if built else None)
    return ctx


def onThisDay(cur, today, state=None):
    """The card's row for `today` (a date), the state's when one is given
    and has a past-year race that day, else the nation's. None if neither."""
    mmdd = today.strftime("%m-%d")
    for scope in ([state, None] if state else [None]):
        ctx = loadBook(cur, "otd", otdKey(mmdd, scope))
        pick = otdPick((ctx or {}).get("rows"), today.year)
        if pick:
            pick["scope"] = (scope or "US").upper()
            return pick
    return None
