"""upcoming_preview.py -- the posted meets ahead, read for one runner, one
team, or one meet (owner, 2026-10-10, approved):

    athleteNextRace(cur, getConn, person_id)  "Clovis Invite, Sat Oct 18:
                                              predicted ~15:02, about 12th"
    schoolNextRaces(cur, getConn, school, st) the same for a team: its place
    meetPreview(cur, meet_id, source)         /meet/preview/xc/<id>: races,
                                              teams coming, course, weather
    racePrediction(cur, meet_id, div, source) one race's projected scores

★ NOTHING HERE IS A NEW MODEL OR A NEW FIELD. The calendar is weekend.py's
  (the meets the feeds post before they run), the field is last_edition.py's
  (the teams at the meet's previous running, each with this season's squad),
  and the times and places are predict.predictTeam's -- the exact call the
  predictions page makes in "This year" mode. A preview is that page's
  answer, read for one name.

★ CACHED PER MEET, NOT PER VIEWER. The athlete and school pages are edge
  cached and load this client-side from /api/next-race/...; every expensive
  piece is held in ttlcache for TTL by the thing it depends on -- the meet's
  plan (its edition and who ran it) and each race's prediction -- so a
  thousand athletes on one team at one invitational cost one prediction of
  that race per worker per TTL, never one per page view.

! WHO IS GOING IS A GUESS, AND EVERY SENTENCE SAYS WHOSE. No feed posts
  entries. A school "is going" when it ran the meet's last edition; the
  sentence names that edition's date so nobody reads it as an entry list.

! CROSS COUNTRY ONLY. A track meet's field is per event and its races are
  heats; predict.py has no track team score to read a place from. A track
  meet keeps its Predict link and gets no preview.

! NEVER A 500. Each piece degrades to "nothing to say": no posted meet, no
  edition, no model, a timed-out prediction -- the block stays hidden.
"""
import datetime
import re

import ttlcache

TTL = 6 * 3600.0
# ! A FAILED OR EMPTY COMPUTE IS KEPT MINUTES, as breakouts.py keeps one: a
#   model still loading or a busy database costs the next reader a retry,
#   not an afternoon of nothing.
FAIL_TTL = 300.0

# How far back a school's own meets are searched for a name that matches a
# posted meet. A last edition can be two seasons old (a meet skipped for a
# year, last_edition.py's 2020 case); the season before that is a different
# roster entirely.
SEASONS_BACK = 2

# A race whose label says it is not the varsity race. Used only to ORDER the
# candidate races for a runner who has no other evidence of which one they
# run, never to drop one.
_NOT_VARSITY = re.compile(r"\bjv\b|junior varsity|frosh|freshm|novice|open|"
                          r"\bc\s*team|reserve|\bb\s*race|sophomore",
                          re.I)


# ------------------------------------------------------------------ #
#  small pure helpers (tests/test_upcoming_preview.py)
# ------------------------------------------------------------------ #

def dateLabel(iso):
    """'2026-10-18' -> 'Sat Oct 18'; '' for anything else."""
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return d.strftime("%a %b ") + str(d.day)


def clock(seconds):
    """A predicted time to the whole second -- a prediction carries no
    tenths it could defend (projections.fmtTime, the same rule)."""
    if seconds is None:
        return None
    s = int(round(float(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def ordinal(n):
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def placeAmong(seconds, field_seconds):
    """Where a time lands in a predicted field: 1 + how many are faster.
    For a runner the field did not carry (their school's squad cap left
    them out), so the place is read against the same predicted times."""
    if seconds is None:
        return None
    return 1 + sum(1 for s in field_seconds if s is not None and s < seconds)


def raceOrder(race, person_id, in_field):
    """The sort key that picks a runner's race among a meet's candidates:
    the race they ran at the last edition first, then a race whose
    predicted field holds them, then the varsity-looking label, then the
    race where their school brought the most runners last time."""
    return (person_id in race.get("persons", ()),
            in_field,
            not _NOT_VARSITY.search(race.get("label") or ""),
            race.get("school_n", 0))


def previewHref(meet_id, source):
    """/meet/preview/xc/<id>, with the feed when it is tfrrs -- the anet and
    tfrrs ids collide, as weekend.predictHref says."""
    href = f"/meet/preview/xc/{int(meet_id)}"
    return href + "?src=tfrrs" if source == "tfrrs" else href


def predictHref(meet_id, div_id, source):
    href = f"/predictions?meet_id={int(meet_id)}&sport=XC"
    if div_id is not None:
        href += f"&div_id={int(div_id)}"
    return href + ("&src=tfrrs" if source == "tfrrs" else "")


def fiveKOf(rating, pool):
    """★ THE 5K COLUMN (owner, 2026-10-10): the track 5K a rating is worth,
    {"time", "dist"} (conversions.fiveK, 3200 m in middle school), or None.
    The rating is the one the prediction used, on its own pool's scale."""
    if rating is None or not pool:
        return None
    try:
        from conversions import fiveK
        return fiveK(rating, pool, "XC")
    except Exception:                                   # noqa: BLE001
        return None


def _snap(distance):
    try:
        return int(round(float(distance) / 100.0) * 100) if distance else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ #
#  the meet's plan: its edition, its races, who ran each
# ------------------------------------------------------------------ #

def meetPlan(cur, meet_id, source=None):
    """The posted meet with, per race, the schools that ran the race it maps
    to at the last edition. Cached TTL per meet; None for no posted meet."""
    def compute():
        try:
            cur.execute("SAVEPOINT up_plan")
            out = _meetPlanUncached(cur, int(meet_id), source)
            cur.execute("RELEASE SAVEPOINT up_plan")
            return out
        except Exception as exc:                        # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT up_plan")
            print(f"upcoming plan {meet_id}: {type(exc).__name__}: {exc}", flush=True)
            return None
    val, _ = ttlcache.get(("up_plan", source, int(meet_id)), compute, ttl=TTL,
                          ttl_of=lambda v: TTL if v else FAIL_TTL)
    return val


def _meetPlanUncached(cur, meet_id, source):
    from last_edition import (upcomingMeet, findLastEdition, editionRaces,
                              mapRace, raceGender)
    up = upcomingMeet(cur, meet_id, "XC", source)
    if up is None:
        return None
    src = up["source"]
    ed = findLastEdition(cur, meet_id, "XC", up)
    edition, by_div, persons = [], {}, {}
    if ed is not None:
        edition = editionRaces(cur, ed["meet_id"], "XC", src)
        cur.execute("""
            SELECT r.div_id, r.school, r.person_id, r.team_id
            FROM   results r
            WHERE  r.meet_id = %(m)s AND r.source = %(src)s
              AND  r.person_id IS NOT NULL
        """, {"m": int(ed["meet_id"]), "src": src})
        rows = [dict(r) for r in cur.fetchall()]
        # ★ A TEAM IS A SCHOOL IN A STATE (team_rank._SEP): "Jesuit" ran
        #   in Oregon and in California. The edition's own runners say which
        #   namesake they were, by predict._teamStates -- the rule the
        #   predictions page labels the same teams with.
        from predict import _teamStates
        states = _teamStates(cur, [r for r in rows if r.get("school")],
                             up.get("state") or ed.get("state"))
        for r in rows:
            persons.setdefault(r["div_id"], set()).add(int(r["person_id"]))
            if not r.get("school"):
                continue
            key = (r["school"], states.get(r["school"]))
            d = by_div.setdefault(r["div_id"], {})
            d[key] = d.get(key, 0) + 1
    genders = [raceGender(r, up["races"], edition) if edition else r.get("gender")
               for r in up["races"]]
    races = []
    for r, g in zip(up["races"], genders):
        ids, how = (mapRace(r, up["races"], edition) if edition
                    else (None, "whole"))
        divs = ids if ids is not None else list(by_div)
        schools, who = {}, set()
        for d in divs:
            for key, n in by_div.get(d, {}).items():
                schools[key] = schools.get(key, 0) + n
            who |= persons.get(d, set())
        races.append({"div_id": r["div_id"], "label": r["label"],
                      "distance": r.get("distance"), "gender": g,
                      "matched": how, "schools": schools, "persons": who})
    return {"meet_id": meet_id, "source": src, "name": up.get("name"),
            "date": up.get("date"), "venue": up.get("venue"),
            "state": up.get("state"),
            "edition": ({"meet_id": ed["meet_id"],
                         "date": str(ed.get("date") or "")[:10],
                         "name": ed.get("name")} if ed else None),
            "races": races}


# ------------------------------------------------------------------ #
#  one race's prediction -- predict.predictTeam, cached
# ------------------------------------------------------------------ #

def _target(meet_id, div_id, source):
    """The request the predictions page sends for this race in "This year"
    mode, through app._target so it is validated by the same rules."""
    from app import _target as appTarget
    args = {"mode": "meet", "meet_id": str(int(meet_id)), "sport": "XC",
            "div_id": str(int(div_id)) if div_id is not None else ""}
    if source == "tfrrs":
        args["src"] = "tfrrs"
    t, err = appTarget(args)
    if err:
        raise ValueError(err)
    return t


def livePrediction(cur, meet_id, div_id, source=None):
    """predict.predictTeam for one race, now, uncached: {"available",
    "teams", "runners", "n_field"} or {"available": False, "reason"}. The
    forecast step (build_meet_forecasts.py) stores this; pages read it
    through racePrediction."""
    from app import _withSource
    from predict import predictTeam
    try:
        cur.execute("SAVEPOINT up_pred")
        target = _withSource(cur, _target(meet_id, div_id, source))
        out = predictTeam(cur, [], target)
        cur.execute("RELEASE SAVEPOINT up_pred")
    except NotImplementedError:
        cur.execute("ROLLBACK TO SAVEPOINT up_pred")
        return {"available": False, "reason": "The prediction model is not loaded."}
    except Exception as exc:                            # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT up_pred")
        print(f"upcoming prediction {meet_id}/{div_id}: {type(exc).__name__}: {exc}",
              flush=True)
        return {"available": False, "reason": "The prediction could not be made."}
    if not out.get("available"):
        return {"available": False, "reason": out.get("reason")}
    return _trim(out, target)


def racePrediction(cur, meet_id, div_id, source=None):
    """{"available", "teams": [...], "runners": [...], "n_field"} for one
    race, cached TTL per race. A failure is {"available": False, reason}
    and is kept FAIL_TTL.

    ★ THE STORED FORECAST FIRST (meet_forecast.py, 2026-10-10). Once the
      pipeline has frozen a race, every page that reads it -- the preview,
      the next-race lines -- shows the stored numbers, with "frozen" saying
      when they were made, so what a reader saw is what the recap scores."""
    def compute():
        import meet_forecast as MF
        if div_id is not None:
            stored = MF.loadRace(cur, meet_id, div_id, source)
            if stored:
                return stored
        return livePrediction(cur, meet_id, div_id, source)
    val, _ = ttlcache.get(("up_pred", source, int(meet_id), div_id), compute, ttl=TTL,
                          ttl_of=lambda v: TTL if v.get("available") else FAIL_TTL)
    return val


# what the preview pages read of a prediction; the full payload (every
# runner's sim, crest, link) stays the predictions page's
_RUNNER_KEYS = ("person_id", "name", "school", "school_state", "school_label",
                "school_href", "grade", "grade_label", "pool", "rating",
                "place", "score_place", "seconds", "lo", "hi")
_TEAM_KEYS = ("team", "state", "school_label", "school_href", "score", "note", "pool")


def _trim(out, target):
    runners = [{k: r.get(k) for k in _RUNNER_KEYS} for r in out.get("runners") or []]
    runners.sort(key=lambda r: (r.get("place") or 10 ** 6))
    teams = []
    for t in out.get("teams") or []:
        row = {k: t.get(k) for k in _TEAM_KEYS}
        row["scorers"] = [{"person_id": r.get("person_id"), "name": r.get("name"),
                           "place": r.get("place"), "score_place": r.get("score_place"),
                           "seconds": r.get("seconds")}
                          for r in (t.get("runners") or [])]
        teams.append(row)
    return {"available": True, "teams": teams, "runners": runners,
            "n_field": len(runners), "target_source": target.get("source")}


def teamPlace(pred, school, state=None):
    """(place, score, n_scoring) of one team in a prediction, or Nones.
    The place counts scoring teams only, as a results page numbers them."""
    scored = [t for t in pred.get("teams") or [] if t.get("score") is not None]
    for i, t in enumerate(scored):
        if t.get("team") == school and (state is None or t.get("state") in (None, state)):
            return i + 1, t["score"], len(scored)
    return None, None, len(scored)


# ------------------------------------------------------------------ #
#  conditions: the course and the weather at the meet
# ------------------------------------------------------------------ #

def conditions(cur, meet_id, div_id, source, venue, distance):
    """{"difficulty", "difficulty_pct", "forecast_text", "normal_text"}
    for a race, cached TTL per meet. Every part is optional."""
    def compute():
        out = {"difficulty": None, "difficulty_pct": None,
               "forecast_text": None, "normal_text": None, "has_forecast": False}
        if venue:
            try:
                from app import get_course_cell_difficulties
                import difficulty_view
                cells = get_course_cell_difficulties(cur, venue)
                snap = _snap(distance)
                # ! THIS RACE'S DISTANCE OR NOTHING: another distance's cell
                #   is a different course as far as the engine is concerned
                d = cells.get(snap) if snap else None
                out["difficulty"] = d
                out["difficulty_pct"] = difficulty_view.diffPct(d, "XC") if d is not None else None
            except Exception as exc:                    # noqa: BLE001
                cur.connection.rollback()
                print(f"upcoming difficulty {venue}: {type(exc).__name__}: {exc}", flush=True)
        try:
            import forecast as fc
            from app import _withSource
            from predict import _targetSpec
            cur.execute("SAVEPOINT up_wx")
            spec = _targetSpec(cur, _withSource(cur, _target(meet_id, div_id, source)))
            cur.execute("RELEASE SAVEPOINT up_wx")
            hour = fc.raceHour("XC")
            lat, lon, day = spec.get("gps_lat"), spec.get("gps_long"), spec.get("date")
            if lat is not None and day:
                fx = fc.forecastAt(lat, lon, day, hour, cur=cur)
                out["forecast_text"] = fc.describe(fx)
                out["has_forecast"] = bool(fx)
                if not fx:
                    out["normal_text"] = fc.describe(fc.normalAt(cur, lat, lon, day, hour))
        except Exception as exc:                        # noqa: BLE001
            try:
                cur.execute("ROLLBACK TO SAVEPOINT up_wx")
            except Exception:                           # noqa: BLE001
                cur.connection.rollback()
            print(f"upcoming weather {meet_id}: {type(exc).__name__}: {exc}", flush=True)
        return out
    val, _ = ttlcache.get(("up_cond", source, int(meet_id), div_id), compute, ttl=TTL)
    return val


# ------------------------------------------------------------------ #
#  which posted meets a school is going to
# ------------------------------------------------------------------ #

def _postedXC(getConn, today):
    from weekend import comingUpCached
    return [m for d in comingUpCached(getConn, today) for m in d["meets"]
            if m["sport"] == "XC"]


def _schoolMeetNames(cur, school, state, year):
    """{editionName: True} of the meets the school raced in the last
    SEASONS_BACK seasons -- the prefilter that keeps this to a handful of
    meet plans per school instead of every meet on the calendar."""
    from last_edition import editionName
    cur.execute("""
        WITH g AS (
            SELECT DISTINCT rr.meet_id FROM ranking_results rr
            WHERE  rr.school = %(s)s AND rr.sport = 'XC'
              AND  rr.year >= %(y0)s
              AND  (%(st)s::text IS NULL OR rr.state = %(st)s)
        )
        SELECT COALESCE((SELECT min(m.meet_name) FROM meets m WHERE m.meet_id = g.meet_id),
                        (SELECT min(mt.meet_name) FROM meets_tfrrs mt
                          WHERE mt.meet_id = g.meet_id AND mt.sport = 'XC')) AS meet_name
        FROM g
    """, {"s": school, "st": state, "y0": int(year) - SEASONS_BACK})
    return {editionName(r["meet_name"]) for r in cur.fetchall() if r.get("meet_name")}


def schoolMatches(cur, getConn, school, state, today=None):
    """[(posted meet, plan, [races the school ran last edition])] soonest
    first, for one (school, state)."""
    from last_edition import editionName
    from predict import _currentSeason
    today = today or datetime.date.today()
    posted = _postedXC(getConn, today)
    if not posted:
        return []
    year = _currentSeason(cur, "XC")
    names = _schoolMeetNames(cur, school, state, year)
    out = []
    for m in sorted(posted, key=lambda m: (m["date"], -m["n_races"])):
        if editionName(m["name"]) not in names:
            continue
        plan = meetPlan(cur, m["meet_id"], m["source"])
        if not plan or not plan.get("edition"):
            continue
        races = []
        for r in plan["races"]:
            n = sum(c for (s, st), c in r["schools"].items()
                    if s == school and (state is None or st in (None, state)))
            if n:
                races.append(dict(r, school_n=n))
        if races:
            out.append((m, plan, races))
    return out


# ------------------------------------------------------------------ #
#  the two page blocks
# ------------------------------------------------------------------ #

def _meetBits(m, plan, race, cond):
    return {"meet_id": m["meet_id"], "source": m["source"], "name": m["name"],
            "date": m["date"], "date_label": dateLabel(m["date"]),
            "venue": m.get("venue"), "race": race["label"],
            "div_id": race["div_id"], "gender": race.get("gender"),
            "distance": race.get("distance"),
            "edition_date": plan["edition"]["date"],
            "preview_href": previewHref(m["meet_id"], m["source"]),
            "predict_href": predictHref(m["meet_id"], race["div_id"], m["source"]),
            "difficulty_pct": cond.get("difficulty_pct"),
            "weather": cond.get("forecast_text") or cond.get("normal_text"),
            "weather_kind": ("forecast" if cond.get("has_forecast")
                             else "normal" if cond.get("normal_text") else None)}


def _latestXcSeason(cur, person_id, year):
    cur.execute("""
        SELECT school, state, pool, mean_rating
        FROM   athlete_season
        WHERE  person_id = %s AND sport = 'XC' AND year = %s
        ORDER  BY n_races DESC NULLS LAST LIMIT 1
    """, (person_id, year))
    r = cur.fetchone()
    return dict(r) if r else None


def athleteNextRace(cur, getConn, person_id, today=None):
    """{"available", "races": [...]} -- the athlete's predicted time and
    place at each posted meet their team ran last time, soonest first."""
    from predict import _currentSeason, predictIndividual
    year = _currentSeason(cur, "XC")
    season = _latestXcSeason(cur, person_id, year)
    if not season or not season.get("school"):
        return {"available": False, "reason": "No cross country season this year."}
    gender = {"m": "M", "f": "F"}.get((season.get("pool") or "")[-1:])
    found = []
    for m, plan, races in schoolMatches(cur, getConn, season["school"],
                                        season.get("state"), today):
        races = [r for r in races if r.get("gender") in (None, gender)]
        if not races:
            continue
        preds = {r["div_id"]: racePrediction(cur, m["meet_id"], r["div_id"], m["source"])
                 for r in races}

        def inField(r):
            p = preds[r["div_id"]]
            return bool(p.get("available")) and any(
                x["person_id"] == person_id for x in p["runners"])
        race = max(races, key=lambda r: raceOrder(r, person_id, inField(r)))
        pred = preds[race["div_id"]]
        if not pred.get("available"):
            continue
        me = next((x for x in pred["runners"] if x["person_id"] == person_id), None)
        if me is not None:
            seconds, lo, hi, place = me.get("seconds"), me.get("lo"), me.get("hi"), me.get("place")
            rating, rpool = me.get("rating"), me.get("pool")
        else:
            # ★ NOT IN THE PREDICTED SEVEN: their own prediction, placed
            #   against the same field's predicted times
            def one():
                try:
                    cur.execute("SAVEPOINT up_ind")
                    got = predictIndividual(cur, person_id,
                                            _target(m["meet_id"], race["div_id"], m["source"]))
                    cur.execute("RELEASE SAVEPOINT up_ind")
                    return got
                except Exception as exc:                # noqa: BLE001
                    cur.execute("ROLLBACK TO SAVEPOINT up_ind")
                    print(f"upcoming individual {person_id}: {type(exc).__name__}: {exc}",
                          flush=True)
                    return {"available": False}
            got, _ = ttlcache.get(("up_ind", person_id, m["source"], m["meet_id"],
                                   race["div_id"]), one, ttl=TTL,
                                  ttl_of=lambda v: TTL if v.get("available") else FAIL_TTL)
            if not got.get("available"):
                continue
            seconds, lo, hi = got.get("seconds"), got.get("lo"), got.get("hi")
            place = placeAmong(seconds, [x.get("seconds") for x in pred["runners"]])
            rating, rpool = None, None
        if rating is None:
            rating, rpool = season.get("mean_rating"), season.get("pool")
        cond = conditions(cur, m["meet_id"], race["div_id"], m["source"],
                          m.get("venue"), race.get("distance"))
        row = _meetBits(m, plan, race, cond)
        row.update({"seconds": seconds, "time": clock(seconds),
                    "lo_time": clock(lo), "hi_time": clock(hi),
                    "place": place, "place_word": ordinal(place) if place else None,
                    "n_field": pred["n_field"], "in_field": me is not None,
                    "five_k": fiveKOf(rating, rpool)})
        found.append(row)
    return {"available": bool(found), "school": season["school"],
            "state": season.get("state"), "races": found}


def schoolNextRaces(cur, getConn, school, state, today=None):
    """{"available", "races": [...]} -- per posted meet the team ran last
    time, its projected team place in each gender's race."""
    found = []
    for m, plan, races in schoolMatches(cur, getConn, school, state, today):
        # one race per gender: the one the school brought most runners to
        best = {}
        for r in races:
            g = r.get("gender")
            if g not in best or (not _NOT_VARSITY.search(r["label"] or ""),
                                 r["school_n"]) > (not _NOT_VARSITY.search(best[g]["label"] or ""),
                                                   best[g]["school_n"]):
                best[g] = r
        for g, race in sorted(best.items(), key=lambda kv: str(kv[0])):
            pred = racePrediction(cur, m["meet_id"], race["div_id"], m["source"])
            if not pred.get("available"):
                continue
            place, score, n_teams = teamPlace(pred, school, state)
            cond = conditions(cur, m["meet_id"], race["div_id"], m["source"],
                              m.get("venue"), race.get("distance"))
            row = _meetBits(m, plan, race, cond)
            mine = [x for x in pred["runners"] if x.get("school") == school]
            row.update({"team_place": place, "team_place_word": ordinal(place) if place else None,
                        "score": score, "n_teams": n_teams,
                        "top": [{"person_id": x["person_id"], "name": x["name"],
                                 "time": clock(x.get("seconds")), "place": x.get("place"),
                                 "five_k": fiveKOf(x.get("rating"), x.get("pool"))}
                                for x in mine[:1]],
                        "n_field": pred["n_field"]})
            found.append(row)
    return {"available": bool(found), "races": found}


# ------------------------------------------------------------------ #
#  /meet/preview/xc/<id>
# ------------------------------------------------------------------ #

RECORDS_SHOWN = 3     # per gender: the course record and the two behind it


def meetPreview(cur, meet_id, source=None):
    """Everything the preview page renders but the predictions (which load
    per race from racePrediction): the posted meet, its races with the
    number of teams coming, the course's difficulty and records, and the
    weather. None when nothing is posted under the id."""
    plan = meetPlan(cur, meet_id, source)
    if plan is None:
        return None
    races = []
    for r in plan["races"]:
        races.append({"div_id": r["div_id"], "label": r["label"],
                      "distance": r.get("distance"), "gender": r.get("gender"),
                      "matched": r["matched"], "n_teams": len(r["schools"]),
                      "predict_href": predictHref(meet_id, r["div_id"], plan["source"])})
    # ★ THE BIGGEST RACE FIRST: the most teams coming is what a reader opened
    #   the page for, and what the share card shows
    order = sorted(races, key=lambda r: (-r["n_teams"], r["div_id"]))
    lead = order[0] if order else None
    dist = (lead or {}).get("distance")
    cond = (conditions(cur, meet_id, lead["div_id"], plan["source"], plan.get("venue"), dist)
            if lead else {})
    records = courseRecords(cur, plan.get("venue"), dist)
    # ★ THE FROZEN FORECAST, when the pipeline has stored one (meet_forecast)
    import meet_forecast as MF
    stored = MF.loadMeet(cur, meet_id, plan["source"])
    made = sorted({r["made_on"] for r in stored if r.get("made_on")})
    return {"frozen": ({"made_on": made[-1], "n_races": len(stored)} if made else None),
            "meet_id": meet_id, "source": plan["source"], "name": plan["name"],
            "date": plan["date"], "date_label": dateLabel(plan["date"]),
            "venue": plan.get("venue"), "state": plan.get("state"),
            "edition": plan.get("edition"), "races": races,
            "lead_div": lead["div_id"] if lead else None,
            "conditions": cond, "records": records}


def courseRecords(cur, venue, distance):
    """{"M": [...], "F": [...], "dist"} from the PRECOMPUTED course board
    (app.loadCourseBoard, pipeline 12b), or None. Never the live compute:
    that is the 39-second Mt. SAC query, and a preview is not worth it."""
    if not venue:
        return None
    snap = _snap(distance)
    try:
        from app import loadCourseBoard
        ctx = loadCourseBoard(cur, venue, snap)
    except Exception as exc:                            # noqa: BLE001
        cur.connection.rollback()
        print(f"upcoming records {venue}: {type(exc).__name__}: {exc}", flush=True)
        return None
    if not ctx or not ctx.get("sel_dist"):
        return None
    recs = ctx.get("records") or {}
    return {"dist": ctx["sel_dist"],
            "M": (recs.get("M") or [])[:RECORDS_SHOWN],
            "F": (recs.get("F") or [])[:RECORDS_SHOWN],
            "difficulty": ctx.get("sel_difficulty")}
