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
    SELECT school, state, top5_mean, ratings, {col} AS {col}
    FROM   team_season
    WHERE  span = 'season' AND scope = 'usa'
      AND  pool = %(pool)s AND sport = %(sport)s AND year = %(year)s
      AND  NULLIF(btrim({col}), '') IS NOT NULL
      {state}
"""


def load(cur, kind, pool, sport, year, state=None):
    """The season board's teams that carry this unit kind."""
    if kind not in KINDS:
        raise ValueError(kind)
    col = '"class"' if kind == "class" else kind
    cur.execute(_SQL.format(col=col, state="AND state = %(state)s" if state else ""),
                {"pool": pool, "sport": sport, "year": int(year), "state": state})
    return [dict(r) for r in cur.fetchall()]
