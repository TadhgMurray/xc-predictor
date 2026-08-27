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
_HS_CHIPS = ("league", "section", "section_div", "district", "county",
             "class")
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


def _label(kind, value, long):
    if kind in ("section_div", "state_div"):
        return f"Division {value}"
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


def unitsFor(cur, school, state=None, sport="XC", long=False):
    """[{label, kind, conflict}] for one school, or [] when unknown.

    state narrows a shared name to one real school; without it the row
    with the most corroboration wins, which is the same tie-break the
    school pages use for the bare URL."""
    if not _exists(cur, "school_unit"):
        return []
    cols = ("league", "section", "section_div", "district", "county",
            "region", "state_unit", "state_div", "class", "conference",
            "division", "is_college", "conflict", "asof")
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
        out.append({"kind": col,
                    "label": _label(col, value, long),
                    "conflict": bool(row["conflict"]),
                    "asof": row["asof"]})
    # the conflict flag is per ROW, so mark only the first chip -- the
    # tooltip explains, and three amber marks would read as three faults
    for chip in out[1:]:
        chip["conflict"] = False
    return out
