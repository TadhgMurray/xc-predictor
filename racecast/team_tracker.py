"""team_tracker.py -- a team's season, week by week, for its coach (owner,
2026-10-10, approved): /school/<name>/season.

    teamSeason(cur, school, state, pool, year=None)

  For every week of the season so far: the team's rating (the mean of its
  top five), its top seven, the 1-5 gap, and where it would place in its
  state meet -- the "Who wins state?" meet (projections.py), held again with
  only the races run by that week.

★ REPLAYED, NOT STORED. team_season keeps one row per team per season, the
  board as of the last pipeline run; there is no history of it. Rather than
  start storing one (and wait a season for a chart), each week is rebuilt
  from ranking_results: every runner in the state meet's field gets the
  rating their races up to that Sunday give them, by the boards' own season
  rule, and the field is raced again.

★ THE BOARDS' SEASON RULE, NOT AN AVERAGE. A runner's rating as of a week
  is what build_ranking_results would have published from the same races:
  the 80th percentile of their rated races after dropping the ones far
  under the median (above_level.py states the same rule for one race;
  the constants are imported from there, not retyped). So the last point on
  the chart is the number the boards show today.

★ THE STATE MEET IS projections.py's. The same field (every school's best
  seven in the state and the school's division), scored by the same
  function (projections.scoreField, which is predict._score). A place here
  and a place on /projections differ only in the week.

! HIGH SCHOOL ONLY: a state meet is a high-school championship. A college
  page gets no tracker link.

! WEEKS END ON SUNDAY. A weekend's meets land inside the week they close,
  so each point is "after that weekend's racing".
"""
import datetime

import ttlcache
from above_level import SEASON_Q, _quantile, _seasonRows

TTL = 6 * 3600.0
POOLS = {"hs_m": "Boys", "hs_f": "Girls"}


# ------------------------------------------------------------------ #
#  pure rules (tests/test_team_tracker.py)
# ------------------------------------------------------------------ #

def seasonRating(ratings):
    """The boards' season number for a list of race ratings, or None."""
    kept = _seasonRows([float(r) for r in ratings if r is not None])
    return _quantile(kept, SEASON_Q) if kept else None


def weekEnds(first, last):
    """Every Sunday from the one closing `first`'s week to the one closing
    `last`'s, as dates. Empty when either is missing."""
    if not first or not last:
        return []
    d0 = datetime.date.fromisoformat(str(first)[:10])
    d1 = datetime.date.fromisoformat(str(last)[:10])
    sun = d0 + datetime.timedelta(days=(6 - d0.weekday()) % 7)
    out = []
    while sun <= d1 + datetime.timedelta(days=(6 - d1.weekday()) % 7):
        out.append(sun)
        sun += datetime.timedelta(days=7)
    return out


def teamWeek(team_ratings):
    """{top5, gap15, top7} for a team's ratings as of one week, best first.
    top5 is the mean of the five scorers (team_season.top5_mean); the 1-5
    gap is first minus fifth in rating points -- the spread a coach means
    by "1-5 gap", on the scale every other number here is on. None where
    the team has fewer than five rated runners."""
    rs = sorted((float(r) for r in team_ratings if r is not None), reverse=True)
    top7 = rs[:7]
    if len(rs) < 5:
        return {"top5": None, "gap15": None, "top7": top7}
    return {"top5": sum(rs[:5]) / 5.0, "gap15": rs[0] - rs[4], "top7": top7}


# ------------------------------------------------------------------ #
#  the database half
# ------------------------------------------------------------------ #

def _division(cur, school, state, pool, year):
    """The school's division as its own runners are stamped this season
    ({value, label}), or None: the field is then the whole state."""
    from projections import _seasonHasUnits, divisionsFor
    cols = _seasonHasUnits(cur)
    if not cols:
        return None, cols
    sel = " , ".join(f'upper(s."{c}") AS "{c}"' for c in cols)
    cur.execute(f"""
        SELECT {sel} FROM athlete_season s
        WHERE  s.school = %(s)s AND s.state = %(st)s AND s.pool = %(p)s
          AND  s.sport = 'XC' AND s.year = %(y)s
    """, {"s": school, "st": state, "p": pool, "y": year})
    votes = {}
    for r in cur.fetchall():
        for c in cols:
            if r.get(c):
                votes[r[c]] = votes.get(r[c], 0) + 1
    if not votes:
        return None, cols
    value = max(votes, key=votes.get)
    from projections import divisionSlug
    div = next((d for d in divisionsFor(cur, state)
                if d["slug"] == divisionSlug(value)), None)
    return ({"value": value, "label": div["label"] if div else f"{state} {value}",
             "slug": divisionSlug(value)}, cols)


def _rows(cur, state, pool, year, division, cols):
    div_sql, p = "", {"st": state, "p": pool, "y": year}
    if division and cols:
        p["v"] = division["value"]
        div_sql = " AND (" + " OR ".join(f'upper(s."{c}") = %(v)s' for c in cols) + ")"
    # ! ONE ROW PER PHYSICAL RACE: anet and tfrrs carry the same race, and a
    #   twin would count twice toward the percentile (breakouts._SEASON_CTE)
    cur.execute(f"""
        WITH f AS (
            SELECT s.person_id, s.school, s.grade FROM athlete_season s
            WHERE  s.pool = %(p)s AND s.sport = 'XC' AND s.year = %(y)s
              AND  s.state = %(st)s AND s.mean_rating IS NOT NULL {div_sql}
        )
        SELECT DISTINCT ON (rr.person_id, rr.race_date, round(rr.time_seconds::numeric, 1))
               rr.person_id, f.school, f.grade, rr.race_date::text AS day,
               rr.speed_rating AS rating, rr.meet_id
        FROM   ranking_results rr JOIN f ON f.person_id = rr.person_id
        WHERE  rr.pool = %(p)s AND rr.sport = 'XC' AND rr.year = %(y)s
          AND  rr.speed_rating IS NOT NULL
          AND  rr.race_date <= current_date
        ORDER  BY rr.person_id, rr.race_date, round(rr.time_seconds::numeric, 1), rr.result_id
    """, p)
    return [dict(r) for r in cur.fetchall()]


def _names(cur, pids):
    if not pids:
        return {}
    from rankings import nameLateral
    cur.execute(f"""
        SELECT s.person_id, a.name
        FROM   unnest(%(p)s::bigint[]) AS s(person_id)
        {nameLateral('s')}
    """, {"p": sorted(pids)})
    return {r["person_id"]: r.get("name") or "Unknown" for r in cur.fetchall()}


def _meetNames(cur, meet_ids):
    if not meet_ids:
        return {}
    cur.execute("""
        SELECT g.meet_id,
               COALESCE((SELECT min(m.meet_name) FROM meets m WHERE m.meet_id = g.meet_id),
                        (SELECT min(mt.meet_name) FROM meets_tfrrs mt
                          WHERE mt.meet_id = g.meet_id AND mt.sport = 'XC')) AS name
        FROM   unnest(%(m)s::bigint[]) AS g(meet_id)
    """, {"m": sorted(meet_ids)})
    return {r["meet_id"]: r.get("name") for r in cur.fetchall()}


def replay(rows, school, weeks):
    """The weekly points for `school` from the field's race rows -- pure,
    so the tests can run a season without a database.

    rows: [{person_id, school, grade, day, rating, meet_id}]."""
    from projections import scoreField, topPerSchool
    rows = sorted(rows, key=lambda r: r["day"])
    out = []
    for wk in weeks:
        cut = wk.isoformat()
        by = {}
        for r in rows:
            if r["day"] > cut:
                break
            by.setdefault(r["person_id"], {"school": r["school"], "grade": r.get("grade"),
                                           "ratings": []})["ratings"].append(float(r["rating"]))
        field = []
        for pid, a in by.items():
            sr = seasonRating(a["ratings"])
            if sr is not None:
                field.append({"person_id": pid, "name": None, "school": a["school"],
                              "school_state": None, "grade": a["grade"], "pool": None,
                              "rating": sr, "n_races": len(a["ratings"])})
        mine = sorted((f for f in field if f["school"] == school),
                      key=lambda f: -f["rating"])
        point = {"date": cut, "n_runners": len(mine),
                 "top7_ids": [f["person_id"] for f in mine[:7]]}
        point.update(teamWeek([f["rating"] for f in mine]))
        teams, _fin = scoreField(topPerSchool(field))
        scored = [t for t in teams if t.get("score") is not None]
        point["n_teams"] = len(scored)
        point["place"], point["score"] = None, None
        for i, t in enumerate(scored):
            if t["team"] == school:
                point["place"], point["score"] = i + 1, t["score"]
                break
        point["meets"] = sorted({r["meet_id"] for r in rows
                                 if r["school"] == school
                                 and (out[-1]["date"] if out else "") < r["day"] <= cut})
        out.append(point)
    return out


def teamSeason(cur, school, state, pool, year=None):
    """Everything the tracker page draws, cached TTL per team."""
    if pool not in POOLS or not state:
        return None

    def compute():
        from predict import _currentSeason
        y = int(year or _currentSeason(cur, "XC"))
        try:
            cur.execute("SAVEPOINT tt")
            division, cols = _division(cur, school, state, pool, y)
            rows = _rows(cur, state, pool, y, division, cols)
            cur.execute("RELEASE SAVEPOINT tt")
        except Exception as exc:                        # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT tt")
            print(f"team tracker {school}: {type(exc).__name__}: {exc}", flush=True)
            return {"error": type(exc).__name__}
        mine = [r for r in rows if r["school"] == school]
        if not mine:
            return {"year": y, "weeks": [], "division": division}
        weeks = weekEnds(min(r["day"] for r in mine), max(r["day"] for r in rows))
        points = replay(rows, school, weeks)
        ids = {pid for p in points for pid in p["top7_ids"]}
        names = _names(cur, ids)
        meets = _meetNames(cur, {m for p in points for m in p["meets"]})
        for p in points:
            p["top7_names"] = [names.get(i, "Unknown") for i in p["top7_ids"]]
            p["meet_names"] = [meets.get(m) or f"Meet {m}" for m in p["meets"]]
        return {"year": y, "weeks": points, "division": division,
                "n_field_schools": len({r["school"] for r in rows if r.get("school")})}
    val, stamp = ttlcache.get(("team_season", school, state, pool, year), compute, ttl=TTL,
                              ttl_of=lambda v: 300.0 if v.get("error") else TTL)
    return dict(val, computed_at=stamp)
