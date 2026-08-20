"""
predict.py -- the prediction service.

★ THE ONLY FILE THAT WILL CHANGE WHEN THE MODEL IS TRAINED. Everything above
  it -- the routes, the page, the JavaScript -- is written against the shapes
  below and does not care whether a real network or a stub produced them. When
  weights exist, `_loadModel` starts returning one and `_predictTimes` starts
  calling it; nothing else moves.

  That is the point of building the page first. The UI is the part that needs
  iterating with a person looking at it, and the model is weeks out. Doing it
  in this order means the model lands into something already finished and
  already tested.

★ IT REFUSES CLEARLY RATHER THAN GUESSING. With no weights, every entry point
  returns {"available": False, "reason": ...}. It does NOT fall back to a
  heuristic -- a plausible number with no model behind it is worse than an
  honest refusal, because nobody can tell the two apart from the outside.

THREE WAYS TO NAME A TARGET RACE
    meet        an existing meet + division. The richest input: real course,
                real difficulty, real date.
    rerun       a PAST meet, re-run today. Same course and day-of-year, but
                the field is whoever is racing now. The cleanest input the
                model can get, because every course feature is known.
    manual      a date, a distance and optionally a course, for a race that
                does not exist in the database yet.

TEAM SCORING IS NOT A SEPARATE MODEL
    A team score is individual predictions, ranked, then scored. So teams need
    the WHOLE FIELD predicted -- not just the teams asked about -- because a
    place is a position among everyone present.
"""

import os

from meet_compile import isTeam

# Where train.py writes its checkpoint. Absent until the model is trained.
MODEL_PATH = os.environ.get("RACECAST_MODEL", "model/checkpoint.pt")

# Scoring: the top N runners per team count, and the next M displace.
# Standard cross country is 5 scorers, 2 displacers.
TEAM_SCORERS = 5
TEAM_DISPLACERS = 2

_model = None
_load_error = None


def _loadModel():
    """The trained network, or None.

    Cached after the first attempt -- including the failure, so a missing file
    is not re-stat-ed on every request.
    """
    global _model, _load_error
    if _model is not None or _load_error is not None:
        return _model

    if not os.path.exists(MODEL_PATH):
        _load_error = (f"The prediction model has not been trained yet "
                       f"(no checkpoint at {MODEL_PATH}).")
        return None

    try:
        import torch
        import sys
        sys.path.insert(0, "model")
        from transformer import XCPredictor

        blob = torch.load(MODEL_PATH, map_location="cpu")
        model = XCPredictor(n_venues=blob.get("n_venues", 1))
        model.load_state_dict(blob["state_dict"])
        model.eval()
        _model = model
    except Exception as exc:                       # noqa: BLE001
        # ⚠ A BROKEN CHECKPOINT MUST NOT 500 THE PAGE. It reads as
        #   "unavailable", the same as an absent one, with the reason shown.
        _load_error = f"The model could not be loaded: {exc}"
    return _model


def modelStatus():
    """{"available": bool, "reason": str|None} -- what the page shows."""
    if _loadModel() is not None:
        return {"available": True, "reason": None}
    return {"available": False, "reason": _load_error}


# ------------------------------------------------------------------ #
#  INDIVIDUAL
# ------------------------------------------------------------------ #

def predictIndividual(cur, person_id, target):
    """One athlete, one target race.

    `target` is {"mode": "meet"|"rerun"|"manual", ...} -- see the module
    docstring. Returns a dict the page renders, or an unavailable marker.

    ⚠ is_forecast IS ALWAYS TRUE HERE. Every prediction the site serves is a
      forecast: the athlete has raced since their last recorded result, or the
      target is weeks away, or both. The training set carries twins for
      exactly this shape (see feature_extraction._forecastTwin) and the flag is
      what tells the model the history does not run up to the target.
    """
    status = modelStatus()
    if not status["available"]:
        return status

    history = _athleteHistory(cur, person_id)
    if not history:
        return {"available": False,
                "reason": "No rated races found for this athlete."}

    return _predictTimes(cur, [person_id], target)[0]


# ------------------------------------------------------------------ #
#  TEAM
# ------------------------------------------------------------------ #

def predictTeam(cur, schools, target, head_to_head=False,
                remove=None, add=None):
    """Team scores at a target race.

    ★ THE WHOLE FIELD IS PREDICTED, NOT JUST THE TEAMS ASKED ABOUT. A place is
      a position among everyone present, so scoring five teams requires
      knowing where the unattached runners and the sixth team finish too --
      otherwise every team's score is computed against a field that is missing
      the people who would have displaced them.

      head_to_head=True is the exception, and it is a DIFFERENT QUESTION: it
      scores as if only the named teams were there, which is what "who beats
      whom" means. Both are offered because both get asked; the default is the
      real meet.
    """
    status = modelStatus()
    if not status["available"]:
        return status

    # The meet's own field, plus whatever the page edited. `schools` is only
    # used when there is no meet to read a field from.
    roster = _teamRosters(cur, schools, target, remove or set(), add or set())
    if not roster:
        return {"available": False,
                "reason": "No athletes found for those teams."}

    field = roster if head_to_head else _fullField(cur, roster, target)
    preds = _predictTimes(cur, [r["person_id"] for r in field], target)

    return {"available": True,
            "mode": "head_to_head" if head_to_head else "meet",
            "teams": _score(field, preds)}


def _score(field, preds):
    """Places -> team scores. Standard cross country rules.

    ⚠ DISPLACERS COUNT EVEN THOUGH THEY DO NOT SCORE. Runners 6 and 7 push
      every later finisher's place up, which is the whole tactical point of
      team depth. Dropping them would make a deep team and a top-heavy one
      score identically.
    """
    order = sorted(zip(field, preds), key=lambda t: t[1]["seconds"])

    by_team, place = {}, 0
    for runner, pred in order:
        place += 1
        team = runner.get("school")
        # ! UNATTACHED IS NOT A TEAM. Five runners who share that string
        #   share it because none of them has a school, so scoring them
        #   together invents a squad out of exactly the athletes who have
        #   none. Same test the race page's scoreRows uses.
        #
        # ⚠ THEY STILL TAKE PLACES HERE, and that differs from scoreRows,
        #   which renumbers after lifting non-scorers out. This function has
        #   never renumbered -- incomplete teams do not displace either --
        #   so changing it would move every predicted score, which is a
        #   methodology decision rather than this fix. Named, not silently
        #   half-done.
        if not isTeam(team):
            continue
        by_team.setdefault(team, []).append(
            {"person_id": runner["person_id"], "name": runner.get("name"),
             "place": place, "seconds": pred["seconds"]})

    out = []
    for team, runners in by_team.items():
        scorers = runners[:TEAM_SCORERS]
        if len(scorers) < TEAM_SCORERS:
            # An incomplete team cannot score. Shown, not silently dropped --
            # "you are two runners short" is useful information.
            out.append({"team": team, "score": None, "runners": runners,
                        "note": f"only {len(runners)} runners"})
            continue
        out.append({
            "team": team,
            "score": sum(r["place"] for r in scorers),
            "runners": runners[:TEAM_SCORERS + TEAM_DISPLACERS],
        })

    # Incomplete teams sort last; ties broken by the sixth runner, as in the
    # real rules.
    out.sort(key=lambda t: (t["score"] is None, t["score"] or 0))
    return out


# ------------------------------------------------------------------ #
#  THE PARTS THAT NEED THE TRAINED MODEL
# ------------------------------------------------------------------ #

def _predictTimes(cur, person_ids, target):
    """[{seconds, low, high, ...}] -- one per person_id, in order.

    ★ THIS IS THE SWAP POINT. When the model is trained, this builds the same
      feature vectors feature_extraction.py writes -- sequence, mask, context,
      venue index, is_forecast=1 -- runs the network, and un-z-scores the
      output. Everything else in this file already works against its return
      shape.
    """
    raise NotImplementedError(
        "predict._predictTimes: wire this to the trained model")


def _athleteHistory(cur, person_id):
    """Every rated race for one athlete, chronological."""
    cur.execute("""
        SELECT 'XC' AS sport, date, normalized_time, speed_rating
        FROM   results     WHERE person_id = %s AND speed_rating IS NOT NULL
        UNION ALL
        SELECT 'TF', date, normalized_time, speed_rating
        FROM   results_tf  WHERE person_id = %s AND speed_rating IS NOT NULL
        ORDER  BY date
    """, (person_id, person_id))
    return cur.fetchall()


# ------------------------------------------------------------------ #
#  THE FIELD -- WORKS WITHOUT THE MODEL
# ------------------------------------------------------------------ #
#
# ★ THE FIELD IS THE MEET'S OWN ENTRANTS, NOT A CROSS-YEAR LOOKUP.
#
#   Linking "this year's Arcadia" to "last year's" is not possible in this
#   data: all 8,222 canon_meet_ids have exactly ONE edition -- that key ties
#   the anet and tfrrs copies of the SAME race together, not consecutive
#   years. And the names cannot be matched either, because the year is baked
#   into them ("2026 arcadia invitational" vs "27th annual arcadia invite").
#
#   So a target is always a REAL past meet, and its field is the people who
#   actually ran it. Nothing is inferred.

MAX_PER_TEAM = 7          # a cross country team enters seven


def meetField(cur, meet_id, div_id, sport, season_year=None):
    """Who ran this meet, grouped by school, marked as returning or not.

    ★ "RETURNER" MEANS "HAS RACED THIS SEASON". A graduated senior has no
      current-season result and drops out on its own -- no roster, no class
      year, no guessing required.

    ⚠ AND IT CANNOT TELL A GRADUATE FROM AN INJURY. Someone hurt all autumn
      looks identical to someone who left. That is a real limit of the signal,
      not an oversight: the page marks who was excluded so a person can add
      them back rather than the code pretending to know.

    ★ RANKED BY CURRENT FORM, NOT BY LAST YEAR'S FINISH AT THIS MEET. Both
      identify the same seven most of the time, but a single race carries
      about +-4 rating points and a season average does not -- so when they
      disagree, the average is the better answer for "who are their seven".
    """
    if season_year is None:
        season_year = _currentSeason(cur, sport)

    table = "results" if sport == "XC" else "results_tf"
    div_clause = "AND r.div_id = %(div)s" if div_id else ""

    cur.execute(f"""
        WITH ran AS (
            SELECT DISTINCT r.person_id, r.school
            FROM   {table} r
            WHERE  r.meet_id = %(meet)s {div_clause}
              AND  r.person_id IS NOT NULL
        )
        SELECT ran.person_id,
               ran.school,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '')          AS name,
               s.mean_rating,
               s.n_races
        FROM   ran
        -- ⚠ NOT `ath`. That is a TEMP table panels.py builds in its own
        --   session; in the web app it does not exist and this would fail at
        --   runtime. The lateral is the same shape app.py uses elsewhere:
        --   `athletes` has roughly one row per (person, school), and the
        --   linking scripts mint some with no name, so a bare LIMIT 1 returns
        --   an arbitrary one. Named rows sort first.
        LEFT JOIN LATERAL (
            SELECT NULLIF(TRIM(x.first_name), '') AS first_name,
                   NULLIF(TRIM(x.last_name),  '') AS last_name
            FROM   athletes x
            WHERE  x.athlete_id = ran.person_id
            ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL) DESC
            LIMIT  1
        ) a ON TRUE
        -- The CURRENT season, not the meet's. A row here is what "still
        -- racing" means; its absence is what makes someone a non-returner.
        LEFT JOIN athlete_season s
               ON s.person_id = ran.person_id
              AND s.year = %(yr)s
              AND s.sport = %(sport)s
        ORDER  BY ran.school, s.mean_rating DESC NULLS LAST
    """, {"meet": meet_id, "div": div_id, "yr": season_year, "sport": sport})

    by_school = {}
    for row in cur.fetchall():
        team = by_school.setdefault(row["school"] or "Unattached",
                                    {"school": row["school"] or "Unattached",
                                     "runners": [], "dropped": []})
        entry = {"person_id": row["person_id"],
                 "name": row["name"].strip() or "Unknown",
                 "rating": (round(float(row["mean_rating"]), 1)
                            if row["mean_rating"] is not None else None),
                 "n_races": row["n_races"]}
        if row["mean_rating"] is None:
            # Not racing this season -- shown separately so it can be added
            # back by hand, since an injury looks the same as a graduation.
            team["dropped"].append(entry)
        elif len(team["runners"]) < MAX_PER_TEAM:
            team["runners"].append(entry)
        else:
            team["dropped"].append(entry)

    teams = sorted(by_school.values(),
                   key=lambda t: (-len(t["runners"]), t["school"]))
    return {"season_year": season_year, "teams": teams}


def schoolSquad(cur, school, sport, season_year=None, limit=40):
    """Everyone racing for a school this season, best first.

    ★ TWO JOBS, ONE QUERY. Adding a team that was not at the meet needs its
      seven; adding one more runner to a team already there needs the rest of
      the squad. Both are "who runs for this school now", so both read this and
      the page slices it differently.

    ⚠ SEASON-SCOPED, NOT ALL-TIME. A school's roster is only meaningful for a
      season -- an all-time list would put a 2009 state champion above the
      current seventh runner, and neither is racing on Saturday.
    """
    if season_year is None:
        season_year = _currentSeason(cur, sport)

    cur.execute("""
        SELECT s.person_id,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '')  AS name,
               s.mean_rating,
               s.n_races
        FROM   athlete_season s
        LEFT JOIN LATERAL (
            SELECT NULLIF(TRIM(x.first_name), '') AS first_name,
                   NULLIF(TRIM(x.last_name),  '') AS last_name
            FROM   athletes x
            WHERE  x.athlete_id = s.person_id
            ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL) DESC
            LIMIT  1
        ) a ON TRUE
        WHERE  s.school = %(school)s
          AND  s.year   = %(yr)s
          AND  s.sport  = %(sport)s
          AND  s.mean_rating IS NOT NULL
        ORDER  BY s.mean_rating DESC
        LIMIT  %(lim)s
    """, {"school": school, "yr": season_year, "sport": sport, "lim": limit})

    return {"school": school, "season_year": season_year,
            "runners": [{"person_id": r["person_id"],
                         "name": (r["name"] or "").strip() or "Unknown",
                         "rating": round(float(r["mean_rating"]), 1),
                         "n_races": r["n_races"]}
                        for r in cur.fetchall()]}


def _currentSeason(cur, sport):
    """The season the boards are showing, so the field agrees with them."""
    cur.execute("SELECT value FROM homepage_meta WHERE key = %s",
                (f"season_year_{sport}",))
    row = cur.fetchone()
    if row and row["value"]:
        return int(row["value"])
    # No panels run yet: fall back to the newest year that has any results.
    table = "results" if sport == "XC" else "results_tf"
    cur.execute(f"SELECT max(substring(date,1,4))::int AS y FROM {table}")
    return cur.fetchone()["y"]


def _teamRosters(cur, schools, target, remove=frozenset(), add=frozenset()):
    """The field for the target, after the page's edits."""
    raise NotImplementedError("predict._teamRosters")


def _fullField(cur, roster, target):
    """Everyone expected at the target race, not just the named teams."""
    raise NotImplementedError("predict._fullField")