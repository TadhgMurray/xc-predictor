# Project: xc-predictor / racecast
# File:    school_units.py
# Purpose: Read what build_school_units wrote, and turn it into the chips
#          the pages show. One reader, so every page says the same thing.
#
# ★ THE CHIPS ARE ORDERED BY SCOPE, WIDEST LAST -- league, then section,
#   then the division within it. That is the order somebody says it out
#   loud ("we're SFL, Sac-Joaquin D2"), and for college it is the
#   owner's hierarchy read the same way: conference, region, division.
#
# ⚠ A CONFLICTED UNIT STILL SHOWS, MARKED. build_school_units records a
#   latest-season disagreement rather than resolving it; the page keeps
#   the unit and adds an amber "?" whose tooltip says what else was
#   voted. Hiding it would quietly answer a question we said we could
#   not answer.

_SHORT_TO_LONG = {
    "NCS": "North Coast Section", "CCS": "Central Coast Section",
    "SJS": "Sac-Joaquin Section", "SDS": "San Diego Section",
    "CIF-SS": "Southern Section",
}

# (column, prefix, long-form suffix) in display order.
# ★ A DIVISION IS NOT ITS OWN UNIT (owner, 2026-08-27). You are not in
#   "Division 1", you are in NCS Division 1 -- and separately in CA
#   Division 4. The division is always spoken with the unit it belongs
#   to, and the order runs widest-first down each branch: state, state's
#   division, section, section's division.
#
#   So section_div carries its section in the label and section itself is
#   NOT a separate chip: "NCS" and "NCS D1" side by side says NCS twice.
#   The RANK LINE still ranks both, because placing 12th in the section
#   and 4th in your division are different facts.
# ★ THE AREA SITS BETWEEN LEAGUE AND SECTION (owner, 2026-09-02): EBAL,
#   Tri-Valley, NCS D1 -- spoken smallest first, like the rest.
_HS_CHIPS = ("league", "area", "state_div", "section_div", "section",
             "district", "county", "class")

# ! AND COLLEGE IS THE OPPOSITE CASE. A division IS its own unit here,
#   and it is the TOP of the hierarchy: division, region, conference.
#   Nothing above applies -- no parent qualifies it, and it is never
#   collapsed away. Guarded by tests/test_school_units.py.
_COLLEGE_CHIPS = ("division", "region", "conference")


def _pretty(value):
    """Acronyms stay shouting; real names stop shouting."""
    if " " not in value and len(value) <= 6 and value.isupper():
        return value
    return " ".join(w if w.isdigit() else w.capitalize()
                    for w in value.split())


# ⚠ LONG FORM ONLY SAYS WHAT IT KNOWS. Appending the kind is safe for a
#   section, a region, a county and a district -- those words ARE the
#   kind. It is NOT safe for a league or conference: the corpus stores
#   LIBERTY, and "Liberty Conference" would be wrong, because it is the
#   Liberty League. Those show as stored.
_LONG_SUFFIX = {"section": "Section", "region": "Region",
                "county": "County"}


def _label(kind, value, long, row=None):
    row = row or {}
    if kind in ("section_div", "state_div"):
        parent = (row.get("section") if kind == "section_div"
                  else (row.get("state_unit") or row.get("state")))
        if kind == "section_div" and parent and long:
            parent = _SHORT_TO_LONG.get(parent, parent)
        if long:
            return (f"{parent} Division {value}" if parent
                    else f"Division {value}")
        # short form rides the athlete meta line and the rank line, where
        # it sits beside "CA #55" and must not dwarf it
        return f"{parent} D{value}" if parent else f"D{value}"
    if kind == "class":
        return f"Class {value}" if long else value
    if kind == "district":
        return f"District {value}"
    # NCAA DIII is an org code, not a name -- never title-cased
    if kind == "division":
        return value
    if not long:
        return value
    if kind == "section" and value in _SHORT_TO_LONG:
        return _SHORT_TO_LONG[value]
    suffix = _LONG_SUFFIX.get(kind)
    return f"{_pretty(value)} {suffix}" if suffix else _pretty(value)


# ! CURSOR-AGNOSTIC ON PURPOSE. The pages hand us a RealDictCursor, the
#   scripts a plain one, and row[0] raises KeyError on the first. Every
#   read here goes through these two helpers.
def _scalar(row):
    if row is None:
        return None
    return row[0] if isinstance(row, (tuple, list)) else list(row.values())[0]


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return _scalar(cur.fetchone()) is not None


def homeStateOf(cur, person_id):
    """The state this athlete races in most (person_home_state, 10b), or
    None. It is what narrows a shared school name to THEIR school."""
    if not _exists(cur, "person_home_state"):
        return None
    cur.execute("SELECT state FROM person_home_state WHERE person_id = %s",
                (person_id,))
    return _scalar(cur.fetchone())


def unitsFor(cur, school, state=None, sport="XC", long=False,
             collapse=True):
    """[{label, kind, conflict}] for one school, or [] when unknown.

    state narrows a shared name to one real school; without it the row
    with the most corroboration wins, which is the same tie-break the
    school pages use for the bare URL."""
    if not _exists(cur, "school_unit"):
        return []
    cols = ("league", "section", "section_div", "district", "county",
            "region", "state_unit", "state_div", "class", "conference",
            "division", "area", "is_college", "conflict", "asof", "state")

    args = [school, sport]
    sql = ("SELECT " + ", ".join('"%s"' % c for c in cols) +
           " FROM school_unit WHERE school = %s AND sport = %s")
    if state:
        sql += " AND state = %s"
        args.append(state.upper())
    sql += " ORDER BY votes DESC LIMIT 1"
    cur.execute(sql, args)
    row = cur.fetchone()
    # ! FALL BACK ACROSS SPORTS. A school's league does not change because
    #   you opened the track page; XC simply has the deeper corpus. Only
    #   the sport flips -- never the state, which would answer about a
    #   different school entirely.
    if row is None:
        args[1] = "TF" if sport == "XC" else "XC"
        cur.execute(sql, args)
        row = cur.fetchone()
    if row is None:
        return []
    row = dict(zip(cols, row)) if isinstance(row, (tuple, list)) else dict(row)
    spec = _COLLEGE_CHIPS if row["is_college"] else _HS_CHIPS
    out = []
    for col in spec:
        value = row.get(col)
        if not value:
            continue
        # ! DISPLAY collapses, RANKING does not. "NCS" adds nothing
        #   beside "NCS D1" on a meta line -- but placing 12th in the
        #   section and 4th in your division are different facts, so the
        #   rank line asks for collapse=False and gets both.
        if collapse and col == "section" and row.get("section_div"):
            continue
        out.append({"kind": col,
                    # raw is what you FILTER on; label is what you show.
                    # The rank line needs both and they are not the same
                    # string once the labeller has been through it.
                    "raw": value,
                    "label": _label(col, value, long, row),
                    "conflict": bool(row["conflict"]),
                    "asof": row["asof"]})
    # the conflict flag is per ROW, so mark only the first chip -- the
    # tooltip explains, and three amber marks would read as three faults
    for chip in out[1:]:
        chip["conflict"] = False
    return out


# ---- filtering a board BY unit --------------------------------------- #
# ★ A UNIT FILTER IS A SCHOOL FILTER. "Every NCS team" resolves to the
#   list of schools whose section is NCS, and then rides the boards'
#   existing `school = ANY(...)` path -- already indexed, already tested,
#   and it composes with every other filter (course included) for free.
#
#   The alternative, an EXISTS against school_unit inside _whereClauses,
#   would have to qualify its outer reference by table, and the boards do
#   not agree on one: the performance boards select from unaliased
#   ranking_results, the ability board from an aliased athlete_season. An
#   unqualified `school` inside the subquery binds to school_unit's OWN
#   column, which is silently always-true rather than an error.
_FILTERABLE = ("league", "area", "section", "section_div", "district",
               "county", "class", "conference", "region", "division")



def hasUnitArgs(args):
    """Did the request name any unit at all? Lets a caller skip opening a
    connection for a fold that would do nothing."""
    return any((args.get(k) or "").strip() for k in _FILTERABLE)


def schoolsInUnits(cur, wanted, state=None):
    """Schools matching {kind: [values]}, as a sorted list of names.

    An empty result is meaningful and returned as [] -- the caller must
    NOT treat it as "no filter", or asking for a unit nobody is in would
    silently return the whole board."""
    if not _exists(cur, "school_unit"):
        return None
    clauses, args = [], []
    for kind, values in wanted.items():
        if kind not in _FILTERABLE or not values:
            continue
        clauses.append('"%s" = ANY(%%s)' % kind)
        args.append([str(v).upper() for v in values])
    if not clauses:
        return None
    sql = ("SELECT DISTINCT school FROM school_unit WHERE "
           + " AND ".join(clauses))
    if state:
        sql += " AND state = ANY(%s)"
        args.append([s.upper() for s in state])
    cur.execute(sql, args)
    return sorted({_scalar(r) if not isinstance(r, (tuple, list)) else r[0]
                   for r in cur.fetchall()})


def applyUnitFilters(cur, f, args):
    """Fold any unit filters in `args` into f["school"]. Returns an error
    string, or None.

    ⚠ INTERSECTS with an explicit school filter rather than replacing it:
      asking for NCS *and* two named schools means those two, if they are
      in NCS -- never all of NCS."""
    wanted = {k: [v.strip() for v in (args.get(k) or "").split(",")
                  if v.strip()]
              for k in _FILTERABLE}
    wanted = {k: v for k, v in wanted.items() if v}
    if not wanted:
        return None
    schools = schoolsInUnits(cur, wanted, f.get("state"))
    if schools is None:
        return ("Units are not built yet -- run the pipeline through "
                "10d_school_units.")
    if not schools:
        return ("No school on record belongs to "
                + ", ".join(f"{k} {'/'.join(v)}" for k, v in wanted.items())
                + ".")
    if f.get("school"):
        keep = set(f["school"]) & set(schools)
        if not keep:
            return "None of those schools are in that unit."
        f["school"] = sorted(keep)
    else:
        f["school"] = schools
    f["unit_filter"] = wanted
    return None
