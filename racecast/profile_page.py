"""profile_page.py -- the recruiting one-pager: /athlete/<id>/profile (owner,
2026-10-10, approved). One printable sheet a high-schooler can hand or
email to a college coach: bests, current rating and where it ranks, the
progression, and the ten strongest programmes the rating fits.

    profileData(cur, person_id) -> dict | None

★ EVERY NUMBER IS ONE THE LIVE PAGES ALREADY PUBLISH, read through their own
  functions: the season and graduation year from recruiting.recruitProfile,
  the bests from recruiting.personalBests (real marks, never conversions --
  the 2026-09-15 rule), the ranks from app.buildRankLine (the athlete
  page's rank line), the fit from recruiting.schoolTable + tierFor (the
  /recruiting page). A sheet that disagreed with the site would be the one
  a coach checks.

★ THE TOP TEN FIT, DEFINED. Of the colleges whose recent recruits put this
  rating in "recruit range" or better (tierFor: at or above the school's
  25th-percentile recruit), the ten with the fastest median recruit. That is
  the strongest list the athlete is a realistic recruit for -- not the ten
  where they would be the best (which is the weakest ten), and not reaches.

! PRINT IS THE PDF. There is no PDF library in requirements-site.txt, so
  "Save as PDF" is the browser's print dialog and print CSS does the rest.
"""
import datetime

FIT_N = 10
FIT_TIERS = ("top", "solid", "recruit")


def topFit(rows, rating, n=FIT_N):
    """The fastest-median schools where `rating` is in FIT_TIERS -- pure."""
    from recruiting import tierFor
    if rating is None:
        return []
    out = []
    for r in rows:
        t = tierFor(rating, r, r.get("division"))
        if t and t["key"] in FIT_TIERS:
            out.append(dict(r, tier=t))
    out.sort(key=lambda r: (-(r.get("median") or 0), r.get("school") or ""))
    return out[:n]


def _chartPoints(cur, person_id):
    """Every rated race, in athlete_chart_data's point shape
    ({d, v, y, sp, meet}), oldest first -- the progression chart's line.
    From ranking_results, one indexed read, rather than the athlete page's
    whole race walk."""
    cur.execute("""
        SELECT DISTINCT ON (rr.race_date, round(rr.time_seconds::numeric, 1))
               rr.race_date::text AS d, rr.speed_rating AS v, rr.sport, rr.year
        FROM   ranking_results rr
        WHERE  rr.person_id = %s AND rr.speed_rating IS NOT NULL
        ORDER  BY rr.race_date, round(rr.time_seconds::numeric, 1)
    """, (person_id,))
    pts = []
    for r in cur.fetchall():
        y = int(r["year"]) + (1 if r["sport"] == "TF" else 0)
        pts.append({"d": r["d"], "v": round(float(r["v"]), 1), "y": y, "sp": r["sport"]})
    return pts


def profileData(cur, person_id):
    import recruiting as R
    from app import buildRankLine
    from season_floor import percentileWords, poolWords
    prof = R.recruitProfile(cur, person_id)
    if prof is None:
        return None
    rated = [s for s in prof["seasons"] if s.get("mean_rating") is not None]
    latest = max(rated, key=lambda s: (s["year"], s.get("last_race") or "")) if rated else None
    ranks, percentile = [], None
    if latest:
        try:
            ranks = buildRankLine(cur, person_id, latest) or []
        except Exception as exc:                        # noqa: BLE001
            cur.connection.rollback()
            print(f"profile ranks {person_id}: {type(exc).__name__}: {exc}", flush=True)
        nation = next((e for e in ranks if e.get("label") == "Nation"), None)
        percentile = percentileWords(nation["rank"], nation.get("total")) if nation else None
    gender = (prof.get("pool") or "hs_m")[-1:]
    fits = {}
    for p in prof.get("placements") or []:
        try:
            rows = R.schoolTable(cur, gender, p["sport"])
        except Exception as exc:                        # noqa: BLE001
            cur.connection.rollback()
            print(f"profile fit {person_id}: {type(exc).__name__}: {exc}", flush=True)
            rows = []
        fits[p["sport"]] = topFit(rows, p["rating"])
    return {
        "person_id": person_id, "name": prof["name"], "school": prof.get("school"),
        "state": prof.get("state"), "school_label": prof.get("school_label"),
        "pool": prof.get("pool"), "pool_words": poolWords(prof.get("pool")),
        "grad_year": prof.get("grad_year"), "seasons": prof["seasons"],
        "latest": latest, "percentile": percentile,
        "ranks": [e for e in ranks if not e.get("wip")],
        "bests": R.personalBests(cur, person_id),
        "fits": fits, "tiers": R.TIERS,
        "points": _chartPoints(cur, person_id),
        "printed": datetime.date.today().isoformat(),
    }
