"""college_goal.py -- the college rungs of /goal (owner, 2026-11-03: "What if
I want collegiate goals?" ... "yes").

    conf7    top 7 at your conference championship
    allconf  all-conference: top 14 there
    nat40    top 40 at your division's nationals (All-American in the NCAA)

★ THE SAME RULE AS THE STATE MARKS (cuts.MARK_Q). A rung's mark is the
  season rating that 90% of the runners who finished in those places had
  reached, over the last SEASONS championships on record -- the place lines
  of /what-it-takes, read off a conference's own meet instead of a state's.
  Three seasons, because seven places of one meet are seven numbers: a
  percentile needs 1/MARK_Q of them before it is one (cuts' THIN rule).

★ THE MEETS ARE THE ONES THE SITE ALREADY FILES (meet_unit, build_meet_units):
  a conference's championship is a championship meet whose name names the
  conference (any spelling, unit_board.normKey); a division's nationals is
  one naming the division and no region or conference. Per season the
  championship race is the race of that meet with the most rated runners of
  the gender (the varsity race, not the JV or the open).

★ COLLEGE SCALE IN, HS-EQUIVALENT ON THE PAGE. The marks are season ratings
  on the college pool (athlete_season), converted to times on that pool;
  the page prints the rating through rv(), so it reads on the HS scale like
  every other rating (CLAUDE.md).
"""
import cuts

SEASONS = 3
RUNGS = (("conf7", 7, "Top 7 at my conference meet"),
         ("allconf", 14, "All-conference (top 14)"),
         ("nat40", 40, "Top 40 at nationals"))
RUNG_KEYS = tuple(k for k, _n, _l in RUNGS)
_PLACES = {k: n for k, n, _l in RUNGS}
POOLS = {"boys": "college_m", "girls": "college_f"}
WORDS = {"boys": "Men", "girls": "Women"}
# the race a college rung reads at, with nothing chosen: the championship
# distance of each (NCAA: 8K / 10K men, 6K women; 8K is the conference one)
DEFAULT_DIST = {"college_m": 8000.0, "college_f": 6000.0}


def isCollegeGoal(key):
    return key in RUNG_KEYS


def label(key):
    return next((lab for k, _n, lab in RUNGS if k == key), key)


# ------------------------------------------------------------------ #
#  the arithmetic (pure: tests/test_college_goal.py)
# ------------------------------------------------------------------ #

def championshipRaces(rows):
    """rows: dicts (year, meet_id, source, div_id, person_id, time, rating)
    -> {year: [rows of that year's championship race, fastest first]}. The
    race is the (meet, source, division) of that year with the most rows."""
    by = {}
    for r in rows:
        by.setdefault(r["year"], {}).setdefault(
            (r["meet_id"], r.get("source"), r.get("div_id")), []).append(r)
    out = {}
    for year, races in by.items():
        race = max(races.values(), key=len)
        out[year] = sorted((r for r in race if r.get("time")), key=lambda r: float(r["time"]))
    return out


def markFrom(races, places, seasons=SEASONS, q=None):
    """{rating, n, years, thin} from championshipRaces' answer: the
    q-quantile of the season ratings of each season's first `places`
    finishers, pooled over the newest `seasons`; None with no rating."""
    q = cuts.MARK_Q if q is None else q
    years = sorted(races, reverse=True)[:seasons]
    vals = []
    for y in years:
        seen = set()
        for r in races[y]:
            if r.get("person_id") in seen:
                continue                       # both feeds' copies of one runner
            seen.add(r.get("person_id"))
            if len(seen) > places:
                break
            if r.get("rating") is not None:
                vals.append(float(r["rating"]))
    if not vals:
        return None
    return {"rating": cuts.quantile(vals, q), "n": len(vals), "years": years,
            "thin": len(vals) < round(1 / q)}


# ------------------------------------------------------------------ #
#  the database
# ------------------------------------------------------------------ #

def unitChoices(cur):
    """(conferences, divisions) a college goal can name: the college units
    school_unit holds, one spelling per unit (unit_board.normKey), the
    spelling most schools carry, meet labels filed as conferences left out."""
    import unit_board as UB
    from unit_names import notAConference
    out = []
    for col in ("conference", "division"):
        cur.execute(f"""SELECT {col} AS v, count(*) AS n FROM school_unit
                        WHERE is_college AND NULLIF(btrim({col}), '') IS NOT NULL
                        GROUP BY 1 ORDER BY 2 DESC""")
        best = {}
        for r in cur.fetchall():
            v = r["v"] if isinstance(r, dict) else r[0]
            if col == "conference" and notAConference(v):
                continue
            best.setdefault(UB.normKey(v), v)
        out.append(sorted(best.values()))
    return out[0], out[1]


def _meetsFor(cur, kind, unit):
    """{(meet_id, source)} of the championships filed under this unit, any
    spelling; nationals (kind division) leave out regionals and conference
    meets, which name the division too."""
    import unit_board as UB
    key = UB.normKey(unit)
    cur.execute("""SELECT DISTINCT unit FROM meet_unit WHERE sport = 'XC' AND kind = %s""", (kind,))
    spell = [r["unit"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
    spell = [s for s in spell if s and UB.normKey(s) == key]
    if not spell:
        return set()
    extra = ("AND NOT EXISTS (SELECT 1 FROM meet_unit o WHERE o.sport = 'XC' "
             "AND o.meet_id = mu.meet_id AND o.kind IN ('region', 'conference'))"
             if kind == "division" else "")
    cur.execute(f"""SELECT DISTINCT mu.meet_id, mu.source FROM meet_unit mu
                    WHERE mu.sport = 'XC' AND mu.kind = %s AND mu.unit = ANY(%s) {extra}""",
                (kind, spell))
    return {((r["meet_id"], r["source"]) if isinstance(r, dict) else (r[0], r[1]))
            for r in cur.fetchall()}


_ROWS = """
    SELECT k.year, r.meet_id, r.source, r.div_id, r.person_id,
           r.time_seconds AS time, s.mean_rating AS rating
    FROM   results r
    JOIN   ranking_results k ON k.result_id = r.result_id AND k.sport = 'XC'
    LEFT   JOIN athlete_season s ON s.person_id = r.person_id AND s.sport = 'XC'
                                AND s.year = k.year AND s.pool = k.pool
    WHERE  r.meet_id = ANY(%(ids)s) AND split_part(k.pool, '|', 1) = %(pool)s
      AND  r.time_seconds > 0
"""


def collegeGoal(cur, rung, gender, conference=None, division=None):
    """The rung's target {rating, n, years, thin, where, whose, href, places}
    or (None, why)."""
    pool = POOLS[gender]
    places = _PLACES[rung]
    if rung == "nat40":
        if not division:
            return None, "Pick a division."
        kind, unit = "division", division
    else:
        if not conference:
            return None, "Pick a conference."
        kind, unit = "conference", conference
    meets = _meetsFor(cur, kind, unit)
    if not meets:
        return None, (f"No {unit} championship is on record." if kind == "conference"
                      else f"No {unit} nationals are on record.")
    cur.execute(_ROWS, {"ids": sorted({m for m, _s in meets}), "pool": pool})
    rows = [dict(r) for r in cur.fetchall()]
    rows = [r for r in rows if (r["meet_id"], r["source"]) in meets]
    got = markFrom(championshipRaces(rows), places)
    if not got:
        return None, f"No rated {WORDS[gender].lower()}'s results at the {unit} championship yet."
    import unit_board as UB
    got.update(places=places, where=unit, kind=kind,
               whose=(f"the top {places} at the {unit} championship" if kind == "conference"
                      else f"the top {places} at the {unit} nationals"),
               href=UB.chipHref(kind, unit, None, True))
    return got, None


def athleteCollege(cur, person_id):
    """The athlete's newest college cross country season: {rating, pool,
    year, school, conference, division, gender} or None."""
    cur.execute("""SELECT mean_rating, pool, year, school, conference, division
                   FROM   athlete_season
                   WHERE  person_id = %s AND sport = 'XC' AND pool LIKE 'college%%'
                     AND  mean_rating IS NOT NULL
                   ORDER  BY year DESC LIMIT 1""", (int(person_id),))
    r = cur.fetchone()
    if not r:
        return None
    r = dict(r)
    r["gender"] = "girls" if str(r.get("pool") or "").endswith("_f") else "boys"
    r["rating"] = r.pop("mean_rating")
    return r
