# Project: xc-predictor / racecast
# File:    unit_board.py
# Purpose: conferences, leagues, sections and divisions RANKED AGAINST EACH
#          OTHER (owner, 2026-11-02: "I'd like to be able to rank conferences
#          and divisions and such"; the measure: "maybe 1 and 3?").
#
#   The team board already ranks the teams INSIDE a unit (its unit filter).
#   This ranks the units themselves, from the season team board
#   (team_season, span 'season', scope 'usa'), two ways side by side:
#
#     top_avg   the mean top-5 average of the unit's best N teams (N on the
#               page, default 5). A big conference is not dragged down by
#               its weakest members, a small one not padded: a unit with
#               fewer than N rated teams is averaged over what it has, and
#               says so.
#     raced     the unit's seven best runners -- from its teams' stored top
#               sevens, which hold every runner who could be in it -- entered
#               as one team in a meet against every other unit of the kind,
#               scored by the team boards' own rules (team_rank.raceStored).
#               Points, low wins: the top end of a unit, raced.
#
# ★ A HIGH SCHOOL UNIT IS A NAME INSIDE A STATE. "Central" is a section in
#   several states and "D2" a division in all of them; the key is (unit,
#   state) for the high school kinds. A college conference, division or
#   region spans states and is keyed by its name alone.
# ! A STATE DIVISION ("D2") RANKED ACROSS STATES is fifty different things
#   with one name; it is only offered with a state picked (KINDS[...][2]).

KINDS = {
    # kind: (label, college?, needs a state)
    "conference":  ("Conferences", True, False),
    "division":    ("Divisions", True, False),
    "region":      ("Regions", True, False),
    "league":      ("Leagues", False, False),
    "section":     ("Sections", False, False),
    "section_div": ("Section divisions", False, True),
    "state_div":   ("State divisions", False, True),
    "area":        ("Areas", False, True),
}
# ★ RANKED INSIDE A PARENT (owner, 2026-11-02: "you cannot find like the
#   best conference in just d3"): the units a board can be narrowed by, per
#   level. A conference board inside NCAA DIII; a league board inside a
#   section.
WITHIN = {True: ("division", "region"), False: ("section", "state_div")}
DEFAULT_N = 5
MIN_TEAMS = 2          # a unit of one team is that team, not a unit
SQUAD = 7


def unitKey(kind, unit, state):
    """The identity of a unit: its name, and its state for a high school
    kind. Pure."""
    return (unit, None) if KINDS[kind][1] else (unit, state)


def rankUnits(rows, kind, n=DEFAULT_N):
    """team_season rows (dicts: school, state, top5_mean, ratings, and the
    unit column `kind`) -> [unit dict], ranked by top_avg, with the raced
    rank and points of each. Pure."""
    from team_rank import raceStored
    units = {}
    for r in rows:
        u = (r.get(kind) or "").strip()
        if not u:
            continue
        units.setdefault(unitKey(kind, u, r.get("state")), []).append(r)
    out = []
    for (name, state), teams in units.items():
        if len(teams) < MIN_TEAMS:
            continue
        rated = sorted((t for t in teams if t.get("top5_mean") is not None),
                       key=lambda t: -float(t["top5_mean"]))
        top = rated[:n]
        pool = sorted((float(x) for t in teams for x in (t.get("ratings") or [])), reverse=True)
        out.append({
            "unit": name, "state": state, "n_teams": len(teams), "n_rated": len(rated),
            "top_avg": round(sum(float(t["top5_mean"]) for t in top) / len(top), 2) if top else None,
            "top_n": len(top),
            "best_team": top[0]["school"] if top else None,
            "best_state": top[0].get("state") if top else None,
            "best_avg": round(float(top[0]["top5_mean"]), 2) if top else None,
            "ratings": pool[:SQUAD],
            "teams": [{"school": t["school"], "state": t.get("state"),
                       "top5_mean": round(float(t["top5_mean"]), 2)} for t in top],
        })
    # race the units: each one entrant, its seven best runners
    entrants = [{"school": f"{u['unit']}|{u['state'] or ''}", "ratings": u["ratings"], "_u": u}
                for u in out if len(u["ratings"]) >= 5]
    raced = raceStored(entrants) or []
    for r in raced:
        r["_u"]["raced_rank"] = r["rank"]
        r["_u"]["raced_points"] = r["points"]
    out.sort(key=lambda u: (u["top_avg"] is None, -(u["top_avg"] or 0), u["unit"]))
    for i, u in enumerate(out, 1):
        u["rank"] = i
        u.pop("ratings", None)
    return out


def sortUnits(units, by):
    """By the page's sort: 'top' (top_avg, the default) or 'raced' (points,
    low first; units too small to race last). Pure."""
    if by == "raced":
        return sorted(units, key=lambda u: (u.get("raced_rank") is None,
                                            u.get("raced_rank") or 0, u["unit"]))
    return sorted(units, key=lambda u: u["rank"])


_SQL = """
    SELECT school, state, rank, points, n_athletes, top5_mean, best_rating,
           best_person_id, ratings, {cols}
    FROM   team_season
    WHERE  span = 'season' AND scope = 'usa'
      AND  pool = %(pool)s AND sport = %(sport)s AND year = %(year)s
      AND  NULLIF(btrim({col}), '') IS NOT NULL
      {where}
"""

_ALL_COLS = ("division", "region", "conference", "league", "section", "section_div",
             "state_div", "area")


def _where(state, within, args):
    out = ""
    if state:
        out += " AND state = %(state)s"
        args["state"] = state
    for i, (col, val) in enumerate(sorted((within or {}).items())):
        if col not in _ALL_COLS or not val:
            continue
        out += f" AND upper({col}) = upper(%(w{i})s)"
        args[f"w{i}"] = val
    return out


def load(cur, kind, pool, sport, year, state=None, within=None, unit=None):
    """The season board's teams that carry this unit kind -- inside the
    `within` units ({col: value}), and only `unit` when given."""
    if kind not in KINDS:
        raise ValueError(kind)
    args = {"pool": pool, "sport": sport, "year": int(year)}
    w = dict(within or {})
    if unit:
        w[kind] = unit
    cur.execute(_SQL.format(col=kind, cols=", ".join(_ALL_COLS),
                            where=_where(state, w, args)), args)
    return [dict(r) for r in cur.fetchall()]


def options(cur, col, pool, sport, year, state=None):
    """The values of `col` on this season's board, most teams first: the
    pickers' choices."""
    if col not in _ALL_COLS:
        return []
    args = {"pool": pool, "sport": sport, "year": int(year)}
    cur.execute(f"""SELECT {col} AS v, count(*) AS n FROM team_season
                    WHERE span = 'season' AND scope = 'usa' AND pool = %(pool)s
                      AND sport = %(sport)s AND year = %(year)s
                      AND NULLIF(btrim({col}), '') IS NOT NULL{_where(state, None, args)}
                    GROUP BY 1 ORDER BY 2 DESC, 1""", args)
    return [r["v"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]


# ---- the generated badge -------------------------------------------------- #
# ★ OUR OWN MARK, NOT THEIRS (owner, 2026-11-02: "can you generate some
#   logos?"). A conference's real logo is its trademark; this is a badge
#   drawn from its name -- initials on a colour fixed by the name -- so every
#   unit has a mark and none of them pretends to be the official one.
_PALETTE = ("#1f4e79", "#7a1f2b", "#1d5e3a", "#5b2a86", "#8a4b08", "#0f5f66",
            "#5a5a14", "#2c3e8f", "#7d2e63", "#334155")
_SKIP = {"of", "the", "and", "&", "at", "in", "for", "conference", "league", "athletic",
         "association", "section", "division"}


def initials(name, most=4):
    """'Middlesex League' -> 'ML'; 'NESCAC' -> 'NESC'; 'NCAA DIII' -> 'DIII'
    (a word with a digit names the unit); 'Big Ten' -> 'BT'. Pure."""
    words = [w for w in str(name or "").replace("-", " ").split() if w]
    if not words:
        return "?"
    digit = [w for w in words if any(c.isdigit() for c in w) or w.upper() in ("DI", "DII", "DIII")]
    if digit:
        return digit[-1][:most].upper()
    if len(words) == 1:
        w = words[0]
        return (w if w.isupper() else w[:3])[:most].upper()
    picked = [w for w in words if w.lower() not in _SKIP]
    if len(picked) <= 1:
        picked = words
    return "".join(w[0] for w in picked)[:most].upper()


def badgeSvg(name, size=28):
    """An inline SVG shield with the unit's initials. Markup-safe: the only
    text in it is letters and digits from initials()."""
    import hashlib
    import re as _re
    text = _re.sub(r"[^A-Z0-9]", "", initials(name)) or "?"
    colour = _PALETTE[int(hashlib.sha1(str(name).encode("utf-8")).hexdigest(), 16) % len(_PALETTE)]
    fs = {1: 15, 2: 13, 3: 10.5, 4: 8.5}.get(len(text), 8.5)
    return (f'<svg class="unit-badge" viewBox="0 0 32 32" width="{int(size)}" height="{int(size)}" '
            f'aria-hidden="true"><path d="M16 1.5 L29 6 V15.5 C29 23 23.5 28.5 16 30.5 '
            f'C8.5 28.5 3 23 3 15.5 V6 Z" fill="{colour}"/><path d="M16 4 L26.5 7.7 V15.5 '
            f'C26.5 21.6 22 26.2 16 27.9 C10 26.2 5.5 21.6 5.5 15.5 V7.7 Z" fill="none" '
            f'stroke="#fff" stroke-opacity=".55" stroke-width="1"/><text x="16" y="19.5" '
            f'text-anchor="middle" font-family="system-ui,sans-serif" font-weight="700" '
            f'font-size="{fs}" fill="#fff">{text}</text></svg>')


def unitHref(kind, unit, state=None, pool=None, sport=None):
    """/unit/<kind>/<name>[?state=..&pool=..&sport=..]: a unit's own page."""
    from urllib.parse import quote, urlencode
    q = {k: v for k, v in (("state", state if kind in KINDS and not KINDS[kind][1] else None),
                           ("pool", pool), ("sport", (sport or "").lower() or None)) if v}
    return f"/unit/{kind}/{quote(str(unit), safe='')}" + (f"?{urlencode(q)}" if q else "")


def chipHref(kind, raw, state=None, college=None):
    """The template global `unit_href`: a unit chip's link, or None for a
    kind with no page (county, district, class) or a high school unit with
    no state."""
    if kind not in KINDS or not raw:
        return None
    if not KINDS[kind][1] and not state:
        return None
    pool = "college_m" if KINDS[kind][1] else "hs_m"
    return unitHref(kind, raw, state, pool)


def label(kind, value, state=None):
    """How a unit is NAMED on the page: the chips' own labeller
    (school_units._label, short form) -- 'Middlesex League' for the stored
    'MIDDLESEX LEAGUE', 'NESCAC' left alone, 'NCS D2' for a section division."""
    try:
        from school_units import _label
        return _label(kind, value, False, {"state": state})
    except Exception:                                     # noqa: BLE001
        return value
