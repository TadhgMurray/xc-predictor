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

# Where train.py writes its artifacts. model.pt is a bare state_dict;
# target_stats/encoders/venue_vocab ride beside it in model/data.
# Anchored on THIS file, not the working directory -- the app launches
# from wherever the owner's shell happens to be.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.environ.get(
    "RACECAST_MODEL", os.path.join(_ROOT, "model", "data", "model.pt"))
MODEL_DATA = os.path.dirname(MODEL_PATH) or os.path.join(_ROOT, "model",
                                                         "data")

# Scoring: the top N runners per team count, and the next M displace.
# Standard cross country is 5 scorers, 2 displacers.
TEAM_SCORERS = 5
TEAM_DISPLACERS = 2

_model = None
_artifacts = None
_load_error = None


def _fx():
    """feature_extraction, imported lazily -- it parses corrections.py at
    import, which is seconds the pages that never predict should not pay.
    Absolute paths: its own relative inserts assume the repo root."""
    import sys
    for sub in ("model", "scripts", "engine"):
        p = os.path.join(_ROOT, sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import feature_extraction
    return feature_extraction


def _loadModel():
    """The trained network plus everything inference needs, or None.

    Cached after the first attempt -- including the failure, so a missing file
    is not re-stat-ed on every request.

    ★ FOUR ARTIFACTS OR NOTHING. The weights alone cannot predict: the
      target stats un-z-score the output, the encoders make grade/school
      match training's integers, and the venue vocabulary maps a course
      to the embedding row it trained into. A partial set predicts
      garbage that looks like a number, so a missing piece is a refusal.

    ★ n_venues FROM THE CHECKPOINT ITSELF (the saved embedding's row
      count) -- any other number either crashes the load or silently
      reindexes every venue.
    """
    global _model, _artifacts, _load_error
    if _model is not None or _load_error is not None:
        return _model

    stats_path = os.path.join(MODEL_DATA, "target_stats.pkl")
    enc_path = os.path.join(MODEL_DATA, "encoders.pkl")
    vocab_path = os.path.join(MODEL_DATA, "venue_vocab.pkl")
    for path, what in ((MODEL_PATH, "checkpoint"),
                       (stats_path, "target stats"),
                       (enc_path, "encoders"),
                       (vocab_path, "venue vocabulary")):
        if not os.path.exists(path):
            _load_error = (f"The prediction model has not been trained yet "
                           f"(no {what} at {path}).")
            return None

    try:
        import pickle
        import torch
        import sys
        p = os.path.join(_ROOT, "model")
        if p not in sys.path:
            sys.path.insert(0, p)
        from transformer import XCPredictor

        blob = torch.load(MODEL_PATH, map_location="cpu")
        # train.py saves a bare state_dict; an older wrapper dict still loads
        state = blob["state_dict"] if isinstance(blob, dict) and \
            "state_dict" in blob else blob
        n_venues = state["venue_embedding.weight"].shape[0]
        model = XCPredictor(n_venues=n_venues)
        model.load_state_dict(state)
        model.eval()

        with open(stats_path, "rb") as f:
            stats = pickle.load(f)
        with open(enc_path, "rb") as f:
            encoders = pickle.load(f)
        with open(vocab_path, "rb") as f:
            vocab = pickle.load(f)["vocab"]

        _artifacts = {"mean": float(stats["mean"]), "std": float(stats["std"]),
                      "encoders": encoders, "vocab": vocab}
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

    out = _predictTimes(cur, [person_id], target)[0]
    if out.get("seconds") is None:
        return {"available": False,
                "reason": out.get("reason",
                                  "No rated races found for this athlete.")}
    return {"available": True, **out}


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
    # An athlete the corpus cannot predict (no rated rows) is left out of
    # the predicted race rather than handed an invented time.
    order = sorted(((f, p) for f, p in zip(field, preds)
                    if p.get("seconds") is not None),
                   key=lambda t: t[1]["seconds"])

    # ★ PASS 1 -- WHO CAN ACTUALLY SCORE. Only runners from a complete team
    #   occupy places in the scoring order, so the set has to be known before
    #   the first place is handed out.
    #
    # ! UNATTACHED IS NOT A TEAM. Five runners who share that string share it
    #   because none of them has a school, so scoring them together invents a
    #   squad out of exactly the athletes who have none. Same isTeam the race
    #   page's scoreRows uses.
    counts = {}
    for runner, _pred in order:
        team = runner.get("school")
        if isTeam(team):
            counts[team] = counts.get(team, 0) + 1
    full = {t for t, n in counts.items() if n >= TEAM_SCORERS}

    # ★ PASS 2 -- TWO DIFFERENT PLACES, AND THEY ARE NOT THE SAME NUMBER.
    #
    #     place        where the runner is predicted to FINISH, counting
    #                  everybody in the race.
    #     score_place  where they stand once unattached runners and
    #                  incomplete teams are lifted out, which is what the
    #                  points are summed from.
    #
    #   This is the rule scoreRows already applied on the race page, and it
    #   is the real one: an unattached runner finishing second takes nothing
    #   away from the teams behind them. Predictions used to sum raw
    #   finishing places, so every predicted score was inflated by whoever
    #   happened to be running unattached that day.
    by_team, place, score_place = {}, 0, 0
    for runner, pred in order:
        place += 1
        team = runner.get("school")
        if not isTeam(team):
            continue
        entry = {"person_id": runner["person_id"], "name": runner.get("name"),
                 "place": place, "seconds": pred["seconds"]}
        # An incomplete team's runners keep a finishing place but never take
        # a scoring one -- they are lifted out exactly like the unattached.
        if team in full:
            score_place += 1
            entry["score_place"] = score_place
        by_team.setdefault(team, []).append(entry)

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
            "score": sum(r["score_place"] for r in scorers),
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
    """[{seconds, ...}] -- one per person_id, in order.

    ★ THE SWAP POINT, NOW SWAPPED. Builds the same feature vectors
      feature_extraction.py writes -- the corpus row SQL filtered to
      these athletes, the same base-vector/context builders, the venue
      vocabulary index, is_forecast=1 -- runs the network, un-z-scores.
      `seconds` is a NORMALIZED time (the flat-5K-equivalent the model
      trains on), which is also what makes fields on different courses
      comparable; the page formats it as a time.

    An athlete the corpus has no rated rows for gets {"seconds": None,
    "reason": ...} -- no fallback number, the module's standing rule.
    """
    import torch

    fx = _fx()
    _loadModel()
    model, art = _model, _artifacts
    encoders, vocab = art["encoders"], art["vocab"]

    by_person = _historyRows(cur, person_ids)
    spec = _targetSpec(cur, target)

    entries = [None] * len(person_ids)
    batch = []                        # (slot, sequence, context, venue)
    for slot, pid in enumerate(person_ids):
        hist = by_person.get(pid)
        if not hist:
            entries[slot] = {"seconds": None,
                             "reason": "No rated races in the corpus."}
            continue
        target_row = _targetRow(spec, hist[-1])
        seq, ctx = _forecastExample(fx, hist, target_row, encoders)
        batch.append((slot, seq, ctx, fx.venueIndex(target_row, vocab)))
        # rerun: the athlete's ACTUAL normalized time at the original
        # running, for the "vs what happened" readout
        if target.get("mode") in ("rerun", "rerun_exact"):
            orig = [r for r in hist if r.get("meet_id") == spec.get("meet_id")]
            if orig:
                entries[slot] = {"actual": float(orig[-1]["normalized_time"])}

    if batch:
        longest = max(len(s) for _i, s, _c, _v in batch)
        width = fx.SEQUENCE_FEATURES
        seqs = torch.zeros(len(batch), longest, width)
        masks = torch.zeros(len(batch), longest, dtype=torch.bool)
        ctxs = torch.zeros(len(batch), len(batch[0][2]))
        vens = torch.zeros(len(batch), dtype=torch.long)
        for i, (_slot, s, c, v) in enumerate(batch):
            seqs[i, :len(s)] = torch.tensor(s, dtype=torch.float32)
            masks[i, :len(s)] = True
            ctxs[i] = torch.tensor(c, dtype=torch.float32)
            vens[i] = v
        with torch.no_grad():
            z = model(seqs, masks, ctxs, vens)
        for (slot, s, _c, _v), zi in zip(batch, z):
            entry = entries[slot] or {}
            entry.update({
                "seconds": round(float(zi) * art["std"] + art["mean"], 1),
                "n_races": len(s)})
            entries[slot] = entry
    return entries


def _forecastExample(fx, hist, target_row, encoders):
    """(sequence, context) for one athlete's hypothetical next race --
    the exact vectors buildAthleteExamples would emit for this target,
    built directly so a 60-race athlete costs one example, not sixty.
    is_forecast is always True: see predictIndividual's header."""
    base = fx._baseVectors(hist, encoders)
    tdate = fx._parseDate(target_row["date"])
    seq = []
    for j, r in enumerate(hist):
        v = base[j].copy()
        v[2] = float((tdate - fx._parseDate(r["date"])).days)
        seq.append(v)
    seq = seq[-fx.MAX_SEQ_LEN:]
    prior = hist[-fx.MAX_SEQ_LEN:]
    ctx = fx._buildContextVector(target_row, seq, prior, encoders,
                                 is_forecast=True)
    return seq, ctx


def _historyRows(cur, person_ids):
    """{person_id: [corpus rows, chronological]} for a whole field in
    two queries -- feature_extraction's own row SQL with an id filter,
    both sports merged by date, exactly the shape training grouped."""
    fx = _fx()
    ids = sorted({int(p) for p in person_ids if p})
    if not ids:
        return {}
    out = {}
    for sport, hour in (("XC", fx.XC_DEFAULT_HOUR), ("TF", fx.TF_DEFAULT_HOUR)):
        cur.execute(fx.personResultsSql(sport),
                    (hour, fx.MIN_NORMALIZED_TIME, ids))
        for r in cur.fetchall():
            row = dict(r)
            pid = row.get("person_id") or row.get("athlete_id")
            out.setdefault(pid, []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r["date"])
    return out


def _targetSpec(cur, target):
    """The target race's own features, resolved once for the whole
    field: date, distance, venue identity, difficulty, geography.
    Weather stays None -- the future's weather is not known, and the
    training rows carried missing weather often enough that zeros are a
    shape the model has seen.
    """
    mode = target.get("mode")
    sport = (target.get("sport") or "XC").upper()
    spec = {"sport": sport, "is_xc": sport == "XC",
            "meet_id": target.get("meet_id")}

    if mode in ("meet", "rerun", "rerun_exact"):
        meet_id = int(target["meet_id"])
        div = target.get("div_id")
        div = int(div) if div and str(div).isdigit() else None
        if sport == "XC":
            cur.execute("""
                SELECT m.course_name, m.distance AS distance_meters,
                       m.gps_lat, m.gps_long, m.altitude_meters,
                       cc.canonical_id,
                       COALESCE(cd.difficulty, 0.0) AS course_difficulty,
                       (SELECT min(r.date) FROM results r
                        WHERE r.meet_id = m.meet_id) AS date
                FROM meets m
                LEFT JOIN course_canonical cc
                       ON cc.course_name = m.course_name
                      AND round(cc.gps_lat::numeric, 5)
                          = round(m.gps_lat::numeric, 5)
                      AND round(cc.gps_long::numeric, 5)
                          = round(m.gps_long::numeric, 5)
                LEFT JOIN course_difficulties cd
                       ON cd.canonical_id = cc.canonical_id
                      AND cd.distance_m =
                          (round(m.distance / 100.0) * 100)::int
                WHERE m.meet_id = %(meet)s
                  AND (%(div)s::bigint IS NULL OR m.div_id = %(div)s)
                LIMIT 1
            """, {"meet": meet_id, "div": div})
        else:
            cur.execute("""
                SELECT NULL AS course_name, m.distance_meters,
                       NULL AS gps_lat, NULL AS gps_long,
                       NULL AS altitude_meters,
                       NULL AS canonical_id, 0.0 AS course_difficulty,
                       m.location_id, m.is_indoor,
                       (SELECT min(r.date) FROM results_tf r
                        WHERE r.meet_id = m.meet_id) AS date
                FROM meets_tf m
                WHERE m.meet_id = %(meet)s
                  AND (%(div)s::bigint IS NULL OR m.div_id = %(div)s)
                  AND m.distance_meters IS NOT NULL
                LIMIT 1
            """, {"meet": meet_id, "div": div})
        row = cur.fetchone()
        if row:
            spec.update(dict(row))
        # rerun_exact keeps the original date: "as it ran" is the
        # honest backtest. rerun takes the page's editable date (same
        # month and day this year by default); a bare year still shifts.
        if mode == "rerun" and spec.get("date"):
            if target.get("date"):
                spec["date"] = target["date"]
            else:
                year = target.get("year")
                if year and str(year).isdigit():
                    spec["date"] = f"{int(year)}{str(spec['date'])[4:]}"
    else:                                          # manual
        spec["date"] = target["date"]
        d = target.get("distance")
        spec["distance_meters"] = (float(d) if d else
                                   5000.0 if sport == "XC" else 1600.0)
        course = (target.get("course") or "").strip()
        if course and sport == "XC":
            cur.execute("""
                SELECT m.course_name, m.gps_lat, m.gps_long,
                       m.altitude_meters, cc.canonical_id,
                       COALESCE(cd.difficulty, 0.0) AS course_difficulty
                FROM meets m
                LEFT JOIN course_canonical cc
                       ON cc.course_name = m.course_name
                      AND round(cc.gps_lat::numeric, 5)
                          = round(m.gps_lat::numeric, 5)
                      AND round(cc.gps_long::numeric, 5)
                          = round(m.gps_long::numeric, 5)
                LEFT JOIN course_difficulties cd
                       ON cd.canonical_id = cc.canonical_id
                      AND cd.distance_m =
                          (round(%(dist)s / 100.0) * 100)::int
                WHERE m.course_name = %(course)s
                ORDER BY (cc.canonical_id IS NULL), (cd.difficulty IS NULL)
                LIMIT 1
            """, {"course": course, "dist": spec["distance_meters"]})
            row = cur.fetchone()
            if row:
                spec.update({k: v for k, v in dict(row).items()
                             if v is not None})
    return spec


# every corpus column the vector builders touch; the athlete's own
# facts come from their latest real row, the race's from the spec
_RACE_FIELDS = ("course_name", "distance_meters", "gps_lat", "gps_long",
                "altitude_meters", "canonical_id", "course_difficulty",
                "location_id", "is_indoor", "meet_id", "date")
_WEATHER_FIELDS = ("temp_c", "dew_point_c", "humidity", "apparent_temp_c",
                   "precipitation_mm", "pressure_hpa", "cloud_cover",
                   "wind_speed_km", "wind_dir")


def _targetRow(spec, last_row):
    """The hypothetical race as a corpus-shaped row: the athlete as they
    last raced, at the target's venue on the target's date."""
    row = dict(last_row)
    row["is_xc"] = spec["is_xc"]
    row["is_indoor"] = None if spec["is_xc"] else bool(spec.get("is_indoor"))
    for f in _RACE_FIELDS:
        row[f] = spec.get(f)
    # the context vector floats these two unconditionally; the corpus
    # COALESCEs them, so an unknown course means 0.0 there too
    row["course_difficulty"] = float(spec.get("course_difficulty") or 0.0)
    row["distance_meters"] = float(spec.get("distance_meters") or
                                   (5000.0 if spec["is_xc"] else 1600.0))
    for f in _WEATHER_FIELDS:
        row[f] = None
    row["place"] = None            # leakage guard: unknown by definition
    row["normalized_time"] = 0.0   # dummy; nothing reads a target's target
    row["time_seconds"] = None
    if spec["is_xc"]:
        row["location_id"] = None
    else:
        row["canonical_id"] = None
    return row


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


def meetField(cur, meet_id, div_id, sport, season_year=None,
              when="thisyear"):
    """The field for a re-run, grouped by school -- and WHEN decides who.

    ★ asran: EXACTLY the people who raced it. No season gate, nobody
      dropped -- "as it actually ran" means that field, graduated
      seniors included, because they really were there.

    ★ thisyear: each attending school's CURRENT squad, top seven as the
      predicted lineup, the rest listed for hand-editing -- including
      this year's freshmen, who were not at the original running. The
      original participants with no current-season row show under
      dropped so an injured athlete can be added back by hand.

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

    if when == "asran":
        by_school = {}
        for r in _exactField(cur, meet_id, div_id, sport):
            school = r["school"] or "Unattached"
            team = by_school.setdefault(school, {"school": school,
                                                 "runners": [], "dropped": []})
            team["runners"].append({"person_id": r["person_id"],
                                    "name": r["name"], "rating": None,
                                    "n_races": None})
        teams = sorted(by_school.values(),
                       key=lambda t: (-len(t["runners"]), t["school"]))
        for t in teams:
            t["runners"].sort(key=lambda x: x["name"])
        return {"season_year": season_year, "when": when, "teams": teams}

    originals = _exactField(cur, meet_id, div_id, sport)
    at_meet = sorted({r["school"] for r in originals if r.get("school")})
    squads = _currentSquads(cur, at_meet, sport, season_year)
    current_ids = {e["person_id"] for sq in squads.values() for e in sq}
    by_school = {}
    for school in at_meet:
        sq = squads.get(school, [])
        by_school[school] = {"school": school,
                             "runners": sq[:MAX_PER_TEAM],
                             "dropped": list(sq[MAX_PER_TEAM:])}
    for r in originals:
        # an original participant with no current-season row: graduated
        # or injured, and the data cannot tell -- listed for the human
        if r["person_id"] in current_ids or not r.get("school"):
            continue
        team = by_school.setdefault(r["school"], {"school": r["school"],
                                                  "runners": [],
                                                  "dropped": []})
        team["dropped"].append({"person_id": r["person_id"],
                                "name": r["name"], "rating": None,
                                "n_races": None})
    teams = sorted(by_school.values(),
                   key=lambda t: (-len(t["runners"]), t["school"]))
    return {"season_year": season_year, "when": when, "teams": teams}


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
    """The field for the target, after the page's edits.

    ★ THE MODE DECIDES WHO RACES. rerun_exact is the original field,
      every person who actually ran, exactly -- that is what "as it ran"
      means, and it is also the honest backtest. rerun ("run it this
      year") is each attending school's CURRENT squad, top seven --
      whoever is racing now, including the freshman who was not at last
      year's running. A manual target with named schools takes their
      current top sevens the same way.
    """
    mode = target.get("mode")
    sport = (target.get("sport") or "XC").upper()
    div = target.get("div_id")
    div = int(div) if div and str(div).isdigit() else None

    entries = []
    if target.get("meet_id"):
        originals = _exactField(cur, int(target["meet_id"]), div, sport)
        if mode == "rerun_exact":
            entries = originals
        else:
            at_meet = sorted({r["school"] for r in originals
                              if isTeam(r.get("school"))})
            squads = _currentSquads(cur, at_meet, sport,
                                    _currentSeason(cur, sport))
            entries = [e for sch in sorted(squads)
                       for e in squads[sch][:MAX_PER_TEAM]]
    elif schools:
        squads = _currentSquads(cur, schools, sport,
                                _currentSeason(cur, sport))
        entries = [e for sch in sorted(squads)
                   for e in squads[sch][:MAX_PER_TEAM]]

    rm = {int(x) for x in remove if str(x).isdigit()}
    entries = [e for e in entries if e["person_id"] not in rm]
    have = {e["person_id"] for e in entries}
    add_ids = {int(x) for x in add if str(x).isdigit()} - have
    if add_ids:
        entries.extend(_athleteEntries(cur, add_ids, sport,
                                       _currentSeason(cur, sport)))
    return entries


def _fullField(cur, roster, target):
    """Everyone expected at the target race, not just the named teams.

    For a meet-based target the roster IS already the whole field -- the
    page sends edits against the meet's own entrants, never a subset --
    and for a manual target nothing beyond the named teams is knowable.
    The head_to_head split upstream is what changes: it scores the
    remaining teams as if nobody else raced."""
    return roster


_NAME_LATERAL = """
        LEFT JOIN LATERAL (
            SELECT NULLIF(TRIM(x.first_name), '') AS first_name,
                   NULLIF(TRIM(x.last_name),  '') AS last_name
            FROM   athletes x
            WHERE  x.athlete_id = {pid}
            ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL) DESC
            LIMIT  1
        ) a ON TRUE
"""


def _exactField(cur, meet_id, div_id, sport):
    """Everyone who actually ran a meet: person, name, school."""
    table = "results" if sport == "XC" else "results_tf"
    div_clause = "AND r.div_id = %(div)s" if div_id else ""
    cur.execute(f"""
        SELECT DISTINCT ON (r.person_id)
               r.person_id, r.school,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '') AS name
        FROM   {table} r
        {_NAME_LATERAL.format(pid="r.person_id")}
        WHERE  r.meet_id = %(meet)s {div_clause}
          AND  r.person_id IS NOT NULL
        ORDER  BY r.person_id
    """, {"meet": meet_id, "div": div_id})
    return [{"person_id": r["person_id"], "school": r["school"],
             "name": (r["name"] or "").strip() or "Unknown"}
            for r in cur.fetchall()]


def _currentSquads(cur, schools, sport, season_year):
    """{school: [runners best-first]} for this season, one bulk query."""
    schools = [s for s in schools if s]
    if not schools or season_year is None:
        return {}
    cur.execute(f"""
        SELECT s.school, s.person_id,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '') AS name,
               s.mean_rating, s.n_races
        FROM   athlete_season s
        {_NAME_LATERAL.format(pid="s.person_id")}
        WHERE  s.school = ANY(%(schools)s)
          AND  s.year   = %(yr)s
          AND  s.sport  = %(sport)s
        ORDER  BY s.school, s.mean_rating DESC NULLS LAST
    """, {"schools": schools, "yr": season_year, "sport": sport})
    out = {}
    for r in cur.fetchall():
        out.setdefault(r["school"], []).append({
            "person_id": r["person_id"], "school": r["school"],
            "name": (r["name"] or "").strip() or "Unknown",
            "rating": (round(float(r["mean_rating"]), 1)
                       if r["mean_rating"] is not None else None),
            "n_races": r["n_races"]})
    return out


def _athleteEntries(cur, person_ids, sport, season_year):
    """name + school for hand-added athletes: this season's row first,
    the athletes table for anyone without one."""
    ids = sorted(person_ids)
    if not ids:
        return []
    out, seen = [], set()
    if season_year is not None:
        cur.execute(f"""
            SELECT s.person_id, s.school,
                   COALESCE(a.first_name, '') || ' '
                       || COALESCE(a.last_name, '') AS name
            FROM   athlete_season s
            {_NAME_LATERAL.format(pid="s.person_id")}
            WHERE  s.person_id = ANY(%(ids)s)
              AND  s.year = %(yr)s AND s.sport = %(sport)s
        """, {"ids": ids, "yr": season_year, "sport": sport})
        for r in cur.fetchall():
            if r["person_id"] in seen:
                continue
            seen.add(r["person_id"])
            out.append({"person_id": r["person_id"], "school": r["school"],
                        "name": (r["name"] or "").strip() or "Unknown"})
    rest = [i for i in ids if i not in seen]
    if rest:
        cur.execute("""
            SELECT DISTINCT ON (athlete_id) athlete_id AS person_id,
                   school,
                   NULLIF(TRIM(concat_ws(' ', first_name, last_name)), '')
                       AS name
            FROM   athletes
            WHERE  athlete_id = ANY(%(ids)s)
            ORDER  BY athlete_id,
                      (COALESCE(TRIM(first_name), '') <> '') DESC
        """, {"ids": rest})
        for r in cur.fetchall():
            out.append({"person_id": r["person_id"], "school": r["school"],
                        "name": r["name"] or "Unknown"})
    return out