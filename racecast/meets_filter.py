"""
meets_filter.py -- the filtered mode of /meets: every meet matching a state,
a school, a name or a season, rather than only the rolling recent list.

    from meets_filter import parseFilters, filteredMeets, describe

★ WHY THIS IS A THIRD MODE AND NOT A WHERE CLAUSE ON THE EXISTING ONE. The
  default /meets serves `homepage_recent`, a ~30-row nightly precompute of the
  last year above a results floor. It exists because "newest meets" needs a
  max(date) GROUP BY over 39M rows with no index on date, which panels.py
  calls "fine in this nightly build and unacceptable per page view". Filtering
  that precompute would answer questions about thirty meets and silently claim
  to answer them about the corpus. So a filter runs its own query, against the
  tables that ARE indexed for it, and the precompute is left alone.

★ AND WHY THERE ARE TWO DRIVING TABLES. The cheap path depends on which
  filter you gave:

    school  -> drive from results/results_tf. `school` is indexed there
               (idx_results_school, idx_results_tf_school) and a school's
               whole history is a few hundred meets. The meets tables do not
               carry a school at all -- only the results do.
    else    -> drive from meets/meets_tf. State and name live there, the
               tables are small next to results, and the per-meet result
               count is one indexed probe each for the page's worth of rows
               that survive.

  Choosing wrong is not a style question: driving the state filter from
  results would be a sequential scan of 39M rows per page view.

! EVERY FILTER IS OPTIONAL AND THEY COMPOSE. The one required decision is the
  sport, because XC and TF live in different tables with different columns.

⚠ THE CAP IS A CAP, NOT A PAGE. This returns at most MAX_MEETS rows and says
  so on the page when it bites. Real pagination belongs with the rankings
  pager (see the consistency issue in docs/ISSUES-2026-08-24.md); shipping a
  silent truncation would be the worse half of that.
"""

import re

from season_year import seasonYearSqlInt
from rankings import US_STATES
from school import storedYear

# The most meets one filtered view will return. A school's entire history is
# ~300 for a twenty-year programme; a whole state's is unbounded, which is
# what this protects the page from.
MAX_MEETS = 400

# A meet name search is a substring match, not a prefix -- "invitational"
# should find "Woodbridge Invitational". Bounded so a pathological pattern
# cannot be pasted into the query planner.
MAX_Q = 60

_SPORTS = ("XC", "TF")
_YEAR_RX = re.compile(r"^(19|20)[0-9]{2}$")

# The date guard panels.py uses, for the same reason: the corpus holds junk
# years (0023, 2223) that a text comparison would sort into the future.
_SANE_DATE = r"^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}$"


# parseFilters
# Purpose:   read the query string into a validated dict, rejecting rather
#            than passing through anything the query cannot use.
# Arguments: args -- request.args (any mapping with .get)
# Output:    {sport, state, school, q, year, active} where `active` is True if
#            any filter beyond sport was given -- the flag the route uses to
#            decide between the precompute and this module.
# Detail:
#   ! VALIDATED AGAINST US_STATES, NOT PASSED THROUGH. Every other value is
#     parameterised, so this is belt-and-braces rather than the only guard --
#     but an unknown state silently returning nothing is a worse page than one
#     that ignores the typo, and the caller can tell the difference.
def parseFilters(args):
    sport = (args.get("sport") or "XC").strip().upper()
    if sport not in _SPORTS:
        sport = "XC"

    state = (args.get("state") or "").strip().upper()
    if state and state not in US_STATES:
        state = ""

    school = (args.get("school") or "").strip()

    q = (args.get("q") or "").strip()[:MAX_Q]

    # ⚠ THE URL CARRIES THE LABEL, THE QUERY TAKES THE STORED YEAR. A track
    #   season is stored under the year it OPENS in and named year + 1, so
    #   ?year=2026 on TF means stored 2025. This is the school page's own
    #   rule (school.py storedYear/seasonLabel) and the link from its meet
    #   table carries a label, so converting here is what keeps the two pages
    #   describing the same season. Both are kept: `year` is what the query
    #   compares, `year_label` is what the form and the sentence show.
    year = (args.get("year") or "").strip()
    label = int(year) if _YEAR_RX.match(year) else None

    # ★ CHAMPIONSHIPS, AND THE CHAMPIONSHIPS OF ONE UNIT (issue 34). Both
    #   read meet_unit (build_meet_units, step 10e). A unit alone implies
    #   championships: the table only holds championship meets.
    champ = (args.get("champ") or "").strip().lower() in ("1", "on", "true")
    unit = (args.get("unit") or "").strip().upper()[:40]

    return {"sport": sport, "state": state, "school": school, "q": q,
            "year": storedYear(sport, label), "year_label": label,
            "champ": champ or bool(unit), "unit": unit,
            "active": bool(state or school or q or label is not None
                           or champ or unit)}


# describe
# Purpose:   the one-line English sentence above the table.
# Detail:    Built here rather than in the template because a Jinja
#            conditional five clauses deep is unreadable, and because the
#            empty-result message needs the same sentence.
def describe(f):
    bits = []
    if f["school"]:
        bits.append(f"raced by {f['school']}")
    if f["state"]:
        bits.append(f"in {f['state']}")
    if f["q"]:
        bits.append(f"matching “{f['q']}”")
    if f["year_label"] is not None:
        bits.append(f"in the {f['year_label']} season")
    if f.get("unit"):
        bits.insert(0, f"{f['unit']} championships")
    elif f.get("champ"):
        bits.insert(0, "championships only")
    return " ".join(bits)


# ------------------------------------------------------------------ #
#  THE SCHOOL PATH -- driven from results, where `school` is indexed
# ------------------------------------------------------------------ #
#
# ⚠ ONE ROW PER MEET, NOT PER DIVISION. schoolMeets on the school page groups
#   by (meet_id, div_id) because it is listing that school's RACES. This page
#   lists MEETS, so a school that entered a JV and a varsity race at one
#   invitational is one row here and two there. Same data, different question.
_SCHOOL_SQL = """
    WITH mine AS (
        SELECT r.meet_id,
               max(r.date)     AS date,
               count(*)        AS n_results
        FROM   {table} r
        WHERE  r.school = %(school)s
          AND  r.date ~ %(sane)s
          {year_clause}
        GROUP  BY r.meet_id
        ORDER  BY max(r.date) DESC
        LIMIT  %(lim)s
    )
    SELECT x.meet_id, x.date, x.n_results,
           {name_expr}  AS meet_name,
           {course_expr} AS course_name,
           m.state
           {unit_cols}
    FROM   mine x
    {meet_join}
    WHERE  TRUE {unit_clause}
    ORDER  BY x.date DESC
"""

# ------------------------------------------------------------------ #
#  THE BROWSE PATH -- driven from meets, where state and name live
# ------------------------------------------------------------------ #
#
# ! GROUPED BY meet_id BEFORE THE COUNT. `meets` carries one row per
#   (meet_id, div_id, source), so a 12-division invitational is twelve rows
#   and would be listed twelve times. min() over the name and course picks one
#   deterministically -- the same "one named division stands in for the meet"
#   choice panels.py and the meet header both make.
_BROWSE_SQL = """
    WITH picked AS (
        SELECT m.meet_id,
               min(m.meet_name)   AS meet_name,
               {course_pick}      AS course_name,
               min(m.state)       AS state
        FROM   {meets_table} m
        WHERE  m.meet_name IS NOT NULL
          {state_clause}
          {q_clause}
          {unit_clause}
        GROUP  BY m.meet_id
        LIMIT  %(scan)s
    )
    SELECT p.meet_id, p.meet_name, p.course_name, p.state,
           agg.date, agg.n_results
           {unit_cols}
    FROM   picked p
    JOIN   LATERAL (
        SELECT max(r.date) AS date, count(*) AS n_results
        FROM   {table} r
        WHERE  r.meet_id = p.meet_id
          AND  r.date ~ %(sane)s
          {year_clause}
    ) agg ON agg.date IS NOT NULL
    ORDER  BY agg.date DESC
    LIMIT  %(lim)s
"""


# filteredMeets
# Purpose:   the rows for one filtered view of /meets.
# Arguments: cur    -- a RealDictCursor
#            f      -- the dict from parseFilters
# Output:    [{meet_id, meet_name, course_name, state, date, n_results}],
#            newest first, at most MAX_MEETS.
_MEET_UNIT = {"checked": False, "present": False}


def hasMeetUnits(cur):
    """Does meet_unit exist yet? Probed once per process. Absent, the
    championship and unit filters are simply not applied."""
    if not _MEET_UNIT["checked"]:
        try:
            cur.execute("SELECT to_regclass('meet_unit')")
            row = cur.fetchone()
            v = row[0] if isinstance(row, (tuple, list)) else list(row.values())[0]
            _MEET_UNIT["present"] = v is not None
        except Exception:                            # noqa: BLE001
            cur.connection.rollback()
            _MEET_UNIT["present"] = False
        _MEET_UNIT["checked"] = True
    return _MEET_UNIT["present"]


def unitSql(f, alias, params, present=True):
    """(clause, columns) for meet_unit against `alias`.meet_id.

    clause   -- "AND EXISTS (...)" narrowing to championships, or to one
                unit's championships, when the filter asks; else "".
    columns  -- ", units" listing the meet's parsed units, so the table can
                say what each championship is of; empty when the table
                is absent."""
    if not present:
        return "", ""
    params["sport"] = f["sport"]
    cols = (f", (SELECT string_agg(DISTINCT u.unit, ' · ') FROM meet_unit u "
            f"WHERE u.sport = %(sport)s AND u.meet_id = {alias}.meet_id "
            f"AND u.unit IS NOT NULL) AS units")
    if not f.get("champ"):
        return "", cols
    narrow = ""
    if f.get("unit"):
        params["unit"] = f["unit"]
        narrow = " AND u.unit = %(unit)s"
    clause = (f"AND EXISTS (SELECT 1 FROM meet_unit u WHERE u.sport = %(sport)s "
              f"AND u.meet_id = {alias}.meet_id{narrow})")
    return clause, cols


def filteredMeets(cur, f):
    sport = f["sport"]
    table = "results" if sport == "XC" else "results_tf"
    params = {"sane": _SANE_DATE, "lim": MAX_MEETS}
    units_present = hasMeetUnits(cur)

    # The season, not substring(date, 1, 4) -- a track season crosses New
    # Year, so a calendar year puts December in the season before its own.
    # Same expression build_ranking_results and schoolMeets group by.
    year_clause = ""
    if f["year"] is not None:
        year_clause = f"AND {seasonYearSqlInt(sport, 'r.date')} = %(year)s"
        params["year"] = f["year"]

    if f["school"]:
        params["school"] = f["school"]
        # ⚠ THE STATE OF THE MEET, NOT OF THE SCHOOL. On this path state can
        #   only be applied after the meets join, and `meets` is anet-only --
        #   a tfrrs meet has no state and would vanish. So the filter goes in
        #   the outer query as a nullable test the join can satisfy, and a
        #   school+state combination simply returns the meets we know the
        #   state of.
        if sport == "XC":
            meet_join = """
    LEFT JOIN LATERAL (
        SELECT meet_name, course_name, state
        FROM   meets mm
        WHERE  mm.meet_id = x.meet_id AND mm.meet_name IS NOT NULL
        LIMIT  1
    ) m ON TRUE
    LEFT JOIN LATERAL (
        SELECT venue_name
        FROM   meets_tfrrs t
        WHERE  t.meet_id = x.meet_id AND t.sport = 'XC'
        LIMIT  1
    ) mt ON TRUE"""
            name_expr = "COALESCE(m.meet_name, mt.venue_name)"
            course_expr = "m.course_name"
        else:
            meet_join = """
    LEFT JOIN LATERAL (
        SELECT meet_name, state, is_indoor
        FROM   meets_tf mm
        WHERE  mm.meet_id = x.meet_id AND mm.meet_name IS NOT NULL
        LIMIT  1
    ) m ON TRUE"""
            name_expr = "m.meet_name"
            course_expr = ("CASE WHEN COALESCE(m.is_indoor, 0) = 1 "
                           "THEN 'Indoor' ELSE 'Outdoor' END")

        unit_clause, unit_cols = unitSql(f, "x", params, units_present)
        sql = _SCHOOL_SQL.format(table=table,
                                 year_clause=year_clause,
                                 meet_join=meet_join, name_expr=name_expr,
                                 course_expr=course_expr,
                                 unit_clause=unit_clause, unit_cols=unit_cols)
        cur.execute(sql, params)
        rows = cur.fetchall()
        # State is applied here, in Python, over a few hundred rows -- see the
        # note above. Doing it in SQL would drop every tfrrs meet.
        if f["state"]:
            rows = [r for r in rows if (r["state"] or "") == f["state"]]
        return [r for r in rows if r["meet_name"]]

    meets_table = "meets" if sport == "XC" else "meets_tf"
    course_pick = ("min(m.course_name)" if sport == "XC" else
                   "CASE WHEN COALESCE(min(m.is_indoor), 0) = 1 "
                   "THEN 'Indoor' ELSE 'Outdoor' END")
    state_clause = q_clause = ""
    if f["state"]:
        state_clause = "AND m.state = %(state)s"
        params["state"] = f["state"]
    if f["q"]:
        q_clause = "AND m.meet_name ILIKE %(q)s"
        params["q"] = f"%{f['q']}%"

    # ! A SCAN CAP ABOVE THE ROW CAP. The LATERAL count runs once per surviving
    #   meet, so the number of meets entering it has to be bounded too -- a
    #   bare state filter matches tens of thousands. Set well above MAX_MEETS
    #   because the outer ORDER BY date needs more candidates than it keeps.
    params["scan"] = MAX_MEETS * 8

    unit_clause, unit_cols = unitSql(f, "m", params, units_present)
    # the columns read the picked meet, the clause narrows the scan
    sql = _BROWSE_SQL.format(meets_table=meets_table, table=table,
                             course_pick=course_pick,
                             state_clause=state_clause, q_clause=q_clause,
                             year_clause=year_clause,
                             unit_clause=unit_clause,
                             unit_cols=unit_cols.replace("m.meet_id", "p.meet_id"))
    cur.execute(sql, params)
    return cur.fetchall()


# groupByYear
# Purpose:   [(label, rows)] for the template, newest year first.
# Detail:    ★ BY YEAR, NOT BY MONTH, AND DELIBERATELY. The default /meets
#            groups by month because it shows one rolling year. A filtered
#            view is a history -- a school's twenty seasons, a state's whole
#            record -- and months would give it two hundred headings. This is
#            the same choice the ?course= view already makes, for the same
#            reason, and matching it keeps the page's two history modes
#            reading alike.
def groupByYear(rows):
    out, current = [], None
    for m in rows:
        d = m.get("date") or ""
        label = d[:4] if len(d) >= 4 and d[:4].isdigit() else "Undated"
        if current is None or current[0] != label:
            current = (label, [])
            out.append(current)
        current[1].append(m)
    return out
