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

import datetime
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
# ★ WHAT MAKES A YEAR A SEASON, for _currentSeason's fallback. A real high
#   school season is hundreds of thousands of results across the corpus; the
#   smallest plausible one is still orders of magnitude above this. The point
#   is only to be far above a typo and far below a season.
SEASON_MIN_RESULTS = 1000

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

        # ! EITHER KEY SPELLING. train.py wrote target_mean/target_std for a
        #   while and this read mean/std; now it writes both. `kind` picks the
        #   inversion below.
        _artifacts = {"mean": float(stats.get("mean", stats.get("target_mean"))),
                      "std": float(stats.get("std", stats.get("target_std"))),
                      "kind": stats.get("kind", "seconds"),
                      # The newest race year the weights were fitted on, for
                      # _clampYear. Absent in a model trained before the year
                      # feature existed, and the clamp then does nothing.
                      "max_year": stats.get("max_year"),
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

    # ★ A COALESCED SQUAD ENTERS SEVEN (issue #86). Two divisions merged under
    #   one name bring fourteen, and all fourteen would take places -- pushing
    #   every other team down and giving the merged squad an advantage no real
    #   team could have.
    if target.get("coalesce") and len(target.get("div_ids") or []) > 1:
        field, preds = _capCoalesced(field, preds)

    teams, finishers = _score(field, preds)

    # ★ THE PAGE RENDERS WHAT A RESULTS PAGE RENDERS, so the row carries what
    #   race.html's row carries: the school's resolved link and label, the
    #   grade spelled for its own level, and the rating on both scales.
    #
    # ! RESOLVED HERE, NOT IN THE BROWSER. schoolHref/schoolLabelIn take the
    #   POOL into account -- Amherst (MA) is a NESCAC college and a middle
    #   school, and a college row's link has to say so. gradeLabel turns "12"
    #   into "Sr" in a high school pool and "SO-2" in a college one. Both are
    #   rules with one correct spelling; a JS twin of either is a second
    #   spelling waiting to drift.
    _decorate(finishers, (target.get("sport") or "XC").upper())
    # ! THE TEAM'S POOL COMES OFF ITS RUNNERS, and off `finishers` rather
    #   than off t["runners"] -- the team's own runner rows carry only what
    #   the scorers table needs (id, name, place, time), while the finish
    #   order carries the pool. The crest for Amherst depends on which
    #   Amherst, so the team row needs one.
    pool_of = {}
    for r in finishers:
        if r.get("school") and r.get("pool"):
            pool_of.setdefault(r["school"], r["pool"])
    for t in teams:
        t.update(_schoolLink(t.get("team"), t.get("state"), None))
        t["pool"] = pool_of.get(t.get("team"))
    _stampCrests(teams, "team", "state")
    _stampCrests(finishers, "school", "school_state")

    return {"available": True,
            "mode": "head_to_head" if head_to_head else "meet",
            "teams": teams,
            "runners": finishers}


# Purpose:   the href and label a MENTION of a school gets, site-wide.
# ! THE SAME TWO FUNCTIONS race.html CALLS. A bare /school/Name sends Oregon
#   (OR) and Oregon (IL) to one page -- see school_identity.schoolHref, which
#   is where that was fixed once.
def _schoolLink(school, state, pool):
    if not school:
        return {"school_href": None, "school_label": None}
    try:
        from school_identity import schoolHref, schoolLabelIn
        return {"school_href": schoolHref(school, state, pool=pool),
                "school_label": schoolLabelIn(school, state)}
    except Exception:                                   # noqa: BLE001
        # Labels not loaded (a script importing predict without the app).
        # A bare name and no link is the old behaviour, not a crash.
        return {"school_href": None, "school_label": school}


# Purpose:   the crest URL for a mention of a school, on rows the BROWSER
#            draws (owner, 2026-09-16: "Can we get the team logos to
#            render?").
#
# ★ THE SERVER HAS TO ANSWER, BECAUSE JS CANNOT ASK. school_logo's whole
#   cache is in this process; a browser can only find out whether a crest
#   exists by fetching it, and a broken <img> per school is worse than no
#   crests at all. school_logo.stampCrests exists for exactly this and says
#   so -- a school without one simply gets no key, so the page renders
#   nothing rather than an error.
#
# ! POOL-AWARE, WHICH stampCrests IS NOT. crestState takes the pool, and the
#   reason is the same one _fieldLevels exists for: Amherst (MA) is a NESCAC
#   college and a regional middle school, and they do not share a crest.
# ! NO QUERY. Everything here is the start-up dict, which is why this can run
#   once per row of a 400-runner championship.
def _stampCrests(rows, school_key, state_key, pool_key="pool", px=64):
    try:
        from school_logo import crestUrl
    except Exception:                                   # noqa: BLE001
        return rows                                     # no cache: no crests
    for r in rows or []:
        school = r.get(school_key)
        if not school:
            continue
        try:
            url = crestUrl(school, r.get(state_key), px, r.get(pool_key))
        except Exception:                               # noqa: BLE001
            url = None
        if url:
            r["crest"] = url
    return rows


# Purpose:   put on each predicted row what a results-page row shows.
# ! THE HS-EQUIVALENT COMES FROM pool_view, the one place that owns it, so the
#   site-wide scale toggle switches these ratings with every other rating on
#   the page (owner, 2026-09-15).
def _decorate(rows, sport):
    from grade_label import gradeLabel
    for r in rows:
        r["grade_label"] = gradeLabel(r.get("grade"), r.get("pool"))
        r.update(_schoolLink(r.get("school"), r.get("school_state"),
                             r.get("pool")))
    rated = [r for r in rows if r.get("rating") is not None]
    if rated:
        try:
            from pool_view import stampBoardRows
            stampBoardRows(rated, rating_keys=("rating",), sport=sport)
        except Exception:                               # noqa: BLE001
            # No conversion available for this pool -- the raw rating shows,
            # which is what the column did before the toggle existed.
            pass


def _fromDivs(from_div, team):
    """The divisions a team's runners came from, when there is more than one.

    None for the ordinary case, so the page shows nothing rather than
    restating the division it is already sitting under.
    """
    got = sorted(from_div.get(team) or ())
    return got if len(got) > 1 else None


def _score(field, preds):
    """Places -> team scores. Standard cross country rules.

    ⚠ DISPLACERS COUNT EVEN THOUGH THEY DO NOT SCORE. Runners 6 and 7 push
      every later finisher's place up, which is the whole tactical point of
      team depth. Dropping them would make a deep team and a top-heavy one
      score identically.

    Returns (teams, finishers) -- the scored teams, and every predicted
    runner in finish order. ONE numbering serves both, because `place` and
    `score_place` are computed here and nowhere else.
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
    # ★ AND "ENOUGH RUNNERS" MEANS ENOUGH ON THE START LINE, NOT ENOUGH IN
    #   THIS LIST (owner, 2026-09-16: "if there's an indiv who qualifies and
    #   runs, and then we add entire roster, that team should not get a place
    #   or displace anybody else"). A school that sent ONE qualifier to a
    #   championship is not a team there. Pressing "add whole squad" to see
    #   what its other six would have run does not enter them -- but it used
    #   to, because the only number here was len(runners), so one qualifier
    #   plus a what-if became a complete team that scored and pushed every
    #   real team's runners down a place.
    #
    # ! SO THE FIELD CARRIES `entered`: how many that school actually had on
    #   the line, stamped by whoever built the roster (see _teamRosters). It
    #   is per team, so every runner of a team carries the same number and
    #   the max is that number -- the added ones carry nothing.
    # ! NO STAMP MEANS NO MEET TO COUNT, which is the manual target: the
    #   named teams ARE the entry list, so the runners present are it.
    counts, entered = {}, {}
    for runner, _pred in order:
        team = runner.get("school")
        if not isTeam(team):
            continue
        counts[team] = counts.get(team, 0) + 1
        n = runner.get("entered")
        if n:
            entered[team] = max(entered.get(team, 0), int(n))

    def onLine(team):
        return entered.get(team, counts.get(team, 0))

    full = {t for t in counts if onLine(t) >= TEAM_SCORERS}

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
    taken = {}
    state = {}
    from_div = {}
    # ★ AND THE FINISH ORDER ITSELF (owner, 2026-09-16: "it should read
    #   exactly like a results page"). A results page is TWO tables -- team
    #   scores, then every finisher -- and the second one needs the whole
    #   field in predicted order, unattached runners included.
    #
    # ! BUILT IN THIS LOOP, NOT IN A SECOND PASS. `place` and `score_place`
    #   are two different numbers computed here once; deriving them again
    #   somewhere else is exactly how the page came to show 82 teams while
    #   the model scored 400.
    finishers = []
    for runner, pred in order:
        place += 1
        team = runner.get("school")
        row = {"person_id": runner["person_id"], "name": runner.get("name"),
               "place": place, "seconds": pred["seconds"],
               "lo": pred.get("lo"), "hi": pred.get("hi"),
               "school": team if isTeam(team) else None,
               "school_state": runner.get("school_state"),
               "grade": runner.get("grade"), "pool": runner.get("pool"),
               "rating": runner.get("rating"), "score_place": None}
        finishers.append(row)
        if not isTeam(team):
            continue
        entry = {"person_id": runner["person_id"], "name": runner.get("name"),
                 "place": place, "seconds": pred["seconds"]}
        # The team's state, taken off its runners -- `team` may already carry
        # a division suffix (#86), so it is not a name the identity table
        # knows any more.
        state.setdefault(team, runner.get("school_state"))
        # Which races this team's runners came out of. More than one means it
        # was coalesced -- the only visible sign the checkbox did anything.
        if runner.get("div_label"):
            from_div.setdefault(team, set()).add(runner["div_label"])
        # An incomplete team's runners keep a finishing place but never take
        # a scoring one -- they are lifted out exactly like the unattached.
        #
        # ⚠ AND A COMPLETE TEAM ONLY TAKES AS MANY SCORING PLACES AS IT
        #   ENTERED, CAPPED AT SEVEN (owner, 2026-09-16: "gotta make it
        #   respect the top 7 who actually ran thing"). A team enters seven;
        #   an eighth runner does not exist to the scorers and cannot
        #   displace. Adding four runners to a school that entered six used
        #   to give it ten displacers -- ten places taken off every team
        #   behind it -- which is an advantage no real team can have.
        if team in full and taken.get(team, 0) < min(onLine(team),
                                                     MAX_PER_TEAM):
            taken[team] = taken.get(team, 0) + 1
            score_place += 1
            entry["score_place"] = score_place
            row["score_place"] = score_place
        by_team.setdefault(team, []).append(entry)

    out = []
    for team, runners in by_team.items():
        # ! THE SCORERS ARE THE ONES WHO TOOK A SCORING PLACE, not the first
        #   five in the list. Past a team's entry cap the rest have no
        #   score_place at all, so reading by position would sum a KeyError.
        scorers = [r for r in runners if r.get("score_place")][:TEAM_SCORERS]
        if len(scorers) < TEAM_SCORERS:
            # An incomplete team cannot score. Shown, not silently dropped --
            # "you are two runners short" is useful information.
            #
            # ! AND THE NUMBER IS THE ONE THAT DECIDED IT. "only 1 entered"
            #   when a person has added six more is the honest note; "only 7
            #   runners" beside seven visible runners reads as a bug.
            # ! WHICHEVER TRUTH IS THE LIMITING ONE. A lone qualifier with
            #   six what-ifs beside them is short on ENTRIES; a school that
            #   entered six and had three taken off the card is short on
            #   RUNNERS. Naming the other number reads as a bug either way.
            n = min(onLine(team), len(runners))
            word = "entered" if onLine(team) < len(runners) else "runners"
            out.append({"team": team, "state": state.get(team),
                        "divs": _fromDivs(from_div, team),
                        "score": None, "runners": runners,
                        "note": f"only {n} {word}"})
            continue
        out.append({
            "team": team,
            "state": state.get(team),
            # Set only when the squad was drawn from more than one division,
            # i.e. when coalesce merged it.
            "divs": _fromDivs(from_div, team),
            "score": sum(r["score_place"] for r in scorers),
            "runners": runners[:TEAM_SCORERS + TEAM_DISPLACERS],
        })

    # Incomplete teams sort last; ties broken by the sixth runner, as in the
    # real rules.
    out.sort(key=lambda t: (t["score"] is None, t["score"] or 0))
    return out, finishers


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

    # ★ THE WEATHER THE RACE WOULD BE RUN IN (owner, 2026-09-06). The
    #   context vector carries the target's weather, so each variant is
    #   one more pass of the network on the same history. The headline
    #   is the venue's NORMAL weather for that time of year at the race
    #   hour when the grid knows it, else the model's no-weather row (the
    #   number the page showed before today; ?weather=none still asks for
    #   it). The forecast rides beside it, 16 days out at most.
    variants = _weatherVariants(cur, spec, target.get("weather") or "both")
    headline = "normal" if "normal" in variants else "none"

    entries = [None] * len(person_ids)
    batch = []                        # (slot, sequence, {variant: context}, venue)
    for slot, pid in enumerate(person_ids):
        hist = by_person.get(pid)
        if not hist:
            entries[slot] = {"seconds": None,
                             "reason": "No rated races in the corpus."}
            continue
        ctxs_by = {}
        venue = None
        seq = None
        for name, wx_row in variants.items():
            target_row = _targetRow(spec, hist[-1], weather=wx_row)
            seq, ctx = _forecastExample(fx, hist, target_row, encoders)
            ctxs_by[name] = ctx
            if venue is None:
                venue = fx.venueIndex(target_row, vocab)
        batch.append((slot, seq, ctxs_by, venue))
        if target.get("mode") in ("rerun", "rerun_exact"):
            orig = [r for r in hist if r.get("meet_id") == spec.get("meet_id")]
            if orig:
                entries[slot] = {"actual": float(orig[-1]["normalized_time"])}

    if batch:
        longest = max(len(s) for _i, s, _c, _v in batch)
        width = fx.SEQUENCE_FEATURES
        seqs = torch.zeros(len(batch), longest, width)
        masks = torch.zeros(len(batch), longest, dtype=torch.bool)
        vens = torch.zeros(len(batch), dtype=torch.long)
        for i, (_slot, s, _c, v) in enumerate(batch):
            seqs[i, :len(s)] = torch.tensor(s, dtype=torch.float32)
            masks[i, :len(s)] = True
            vens[i] = v
        by_variant = {}
        for name in variants:
            ctxs = torch.zeros(len(batch), len(batch[0][2][name]))
            for i, (_slot, _s, c, _v) in enumerate(batch):
                ctxs[i] = torch.tensor(c[name], dtype=torch.float32)
            with torch.no_grad():
                if art.get("kind") == "log_ratio" and hasattr(model,
                                                              "predictInterval"):
                    secs, lo, hi, sig = model.predictInterval(seqs, masks, ctxs,
                                                              vens)
                else:
                    secs = model(seqs, masks, ctxs, vens) * art["std"] + art["mean"]
                    lo = hi = sig = None
            by_variant[name] = (secs, lo, hi, sig)
        secs, lo, hi, sig = by_variant[headline]
        for i, ((slot, s, _c, _v), si) in enumerate(zip(batch, secs)):
            entry = entries[slot] or {}
            entry.update({
                "seconds": round(float(si), 1),
                "n_races": len(s),
                "weather_basis": headline})
            if sig is not None:
                entry.update({
                    "lo": round(float(lo[i]), 1),
                    "hi": round(float(hi[i]), 1),
                    "sigma_pct": round(100.0 * float(sig[i]), 2)})
            if "normal" in variants:
                entry["normal_weather"] = _fc().describe(variants["normal"])
            if "forecast" in variants:
                fsecs = float(by_variant["forecast"][0][i])
                fc_row = variants["forecast"]
                entry["forecast"] = {
                    "seconds": round(fsecs, 1),
                    "delta": round(fsecs - float(si), 1),
                    "conditions": _fc().describe(fc_row),
                    "hour_local": fc_row.get("hour_local"),
                    "fetched_at": fc_row.get("fetched_at"),
                    "source": fc_row.get("source")}
            entries[slot] = entry
    return entries


def _fc():
    import forecast
    return forecast


def _weatherVariants(cur, spec, want):
    """{name: weather row | None} for the passes to run. 'none' is the
    model's no-weather row; 'normal' and 'forecast' come from forecast.py
    and are dropped when they have no answer (no coordinates, no grid
    rows, a date out of the forecast's reach, no network)."""
    want = (want or "both").lower()
    names = {"none": ("none",), "normal": ("normal",), "forecast": ("forecast",),
             "both": ("normal", "forecast"), "all": ("none", "normal", "forecast")
             }.get(want, ("normal", "forecast"))
    out = {}
    fc = _fc()
    hour = fc.raceHour(spec.get("sport"))
    lat, lon, day = spec.get("gps_lat"), spec.get("gps_long"), spec.get("date")
    for name in names:
        if name == "none":
            out["none"] = None
        elif name == "normal":
            row = fc.normalAt(cur, lat, lon, day, hour)
            if row:
                out["normal"] = row
        elif name == "forecast":
            row = fc.forecastAt(lat, lon, day, hour, cur=cur)
            if row:
                out["forecast"] = row
    if not out:
        out["none"] = None
    return out


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
    _clampYear(ctx, fx)
    return seq, ctx


# ⚠ THE YEAR FEATURE EXTRAPOLATES, SO INFERENCE CLAMPS IT. The context
#   vector carries the target race's calendar year (feature_extraction
#   index 22). Every other feature in it is something the model has seen
#   the range of; a year is not, because a race next spring is by
#   definition later than every row the weights were fitted on, and a
#   linear layer on a z-scored year keeps going in whatever direction the
#   trend pointed. Clamping to the last year in the training data asks the
#   model "what would this be worth in the most recent season you know",
#   which is the question actually being asked, instead of letting it
#   invent a trend two years past its evidence.
#
# ! THE CEILING RIDES IN model.pt. train.py records the newest year it
#   trained on; a model saved before that existed has no ceiling and the
#   clamp is then a no-op, which is the old behaviour exactly.
def _clampYear(ctx, fx):
    idx = fx.CONTEXT_YEAR_INDEX
    if idx is None or idx >= len(ctx):
        return
    ceiling = (_artifacts or {}).get("max_year")
    if ceiling and ctx[idx] > ceiling:
        ctx[idx] = float(ceiling)


def _historyRows(cur, person_ids):
    """{person_id: [corpus rows, chronological]} for a whole field in
    two queries -- feature_extraction's own row SQL with an id filter,
    both sports merged by date, exactly the shape training grouped."""
    fx = _fx()
    ids = sorted({int(p) for p in person_ids if p})
    if not ids:
        return {}
    # a database with no weather table serves NULL weather, same as an
    # unfetched meet -- the guard training itself runs under
    cur.execute("SELECT to_regclass('public.weather')")
    row = cur.fetchone()
    has_weather = (row[0] if not isinstance(row, dict)
                   else row.get("to_regclass")) is not None
    # ★ LET THE PLANNER ACTUALLY PLAN THIS (owner, 2026-09-16: the
    #   predictions page 504'd on a championship field).
    #
    #   The corpus row SQL joins FOURTEEN relations -- results, meets, the
    #   distance-override VALUES list, athletes, course_canonical,
    #   course_difficulties, athlete_season_level twice, weather, grade_fix,
    #   pro_athlete_season, college_first_season, upperclass_first_season,
    #   wheelchair_person. join_collapse_limit defaults to EIGHT, and past
    #   it Postgres stops searching join orders and joins them in the order
    #   they are WRITTEN.
    #
    # ⚠ WHICH LEAVES THE OVERRIDE LIST AS AN INNER LOOP. The hand-verified
    #   distances are inlined as 16,221 VALUES rows; written-order joining
    #   makes that the inner side of a nested loop and it is rescanned once
    #   per result row. Measured on the live database: 30,300,741 rows
    #   removed by that one join filter for a 1,868-row sample, and the real
    #   field returns 33,306 rows -- about 540 million comparisons.
    #
    # ! THE SAME JOIN HASHES IN ISOLATION, in 41 ms. Nothing is wrong with
    #   the query; the planner was simply not allowed to look. Raising the
    #   limit costs planning time (18.9 ms measured, and it grows with the
    #   search space) and is paid once per request against tens of seconds.
    #
    # ! SET LOCAL, so it lasts the transaction and never leaks back into the
    #   pooled connection for the next page to inherit.
    cur.execute("SET LOCAL join_collapse_limit = 16")
    cur.execute("SET LOCAL from_collapse_limit = 16")

    out = {}
    for sport, hour in (("XC", fx.XC_DEFAULT_HOUR), ("TF", fx.TF_DEFAULT_HOUR)):
        # ids TWICE: the filter is two indexable branches now, not one
        # COALESCE the planner cannot index through. See personResultsSql.
        cur.execute(fx.personResultsSql(sport, has_weather=has_weather),
                    (hour, fx.MIN_NORMALIZED_TIME, ids, ids))
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

        # ★ A DIFFERENT COURSE, THE SAME MEET (owner, 2026-09-01). Applied
        #   AFTER the meet's own row, so the meet supplies everything --
        #   field, division, date, distance -- and only the venue changes.
        #   An optional distance override comes first, because the difficulty
        #   lookup is per (course, distance) and a course raced at another
        #   length is a different cell.
        if target.get("distance"):
            try:
                spec["distance_meters"] = float(target["distance"])
            except (TypeError, ValueError):
                pass
        _applyCourse(cur, spec, target.get("course"), sport)

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
        _applyCourse(cur, spec, target.get("course"), sport)
    return spec


# Purpose:   swap in a named course, leaving everything else alone.
# Input:     spec -- a target spec already built; course_name -- what to use
#            instead of whatever course the spec currently names.
# Output:    None; spec is updated in place.
#
# ★ THE COURSE IS NOT THE MEET (owner, 2026-09-01). "Run this meet's field at
#   a different venue" is the question the whole tool is for -- what would
#   these teams do at the state course -- and it was reachable only through
#   `manual`, which throws away the meet: its field, its division, its date.
#   The lookup is the one the manual branch already used, lifted out so the
#   meet modes can call it too.
#
# ! ONLY THE COURSE FIELDS MOVE. Nothing here touches the date, the division
#   or the field; a value that comes back NULL leaves the meet's own in place
#   rather than blanking it.
# ⚠ XC ONLY. A track meet's "course" is the track, and difficulty is not
#   modelled per venue there -- an override would be a number with nothing
#   behind it.
def _applyCourse(cur, spec, course_name, sport):
    course = (course_name or "").strip()
    if not course or sport != "XC":
        return
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
    """, {"course": course, "dist": spec.get("distance_meters") or 5000.0})
    row = cur.fetchone()
    if row:
        spec.update({k: v for k, v in dict(row).items() if v is not None})


# every corpus column the vector builders touch; the athlete's own
# facts come from their latest real row, the race's from the spec
_RACE_FIELDS = ("course_name", "distance_meters", "gps_lat", "gps_long",
                "altitude_meters", "canonical_id", "course_difficulty",
                "location_id", "is_indoor", "meet_id", "date")
_WEATHER_FIELDS = ("temp_c", "dew_point_c", "humidity", "apparent_temp_c",
                   "precipitation_mm", "pressure_hpa", "cloud_cover",
                   "wind_speed_km", "wind_dir")


def _targetRow(spec, last_row, weather=None):
    """The hypothetical race as a corpus-shaped row: the athlete as they
    last raced, at the target's venue on the target's date. `weather`,
    a forecast.py row, fills the weather fields; None leaves them as
    the model's no-weather shape."""
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
    if weather:
        # the model's column is wind_speed_km; forecast.py's row says kmh
        for f in _WEATHER_FIELDS:
            row[f] = weather.get(f, weather.get("wind_speed_kmh")
                                 if f == "wind_speed_km" else None)
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

# ★ A SCHOOL THAT BROUGHT FIVE OR FEWER IS NOT A SEVEN-RUNNER TEAM (owner).
#   "thisyear" replaces each attending school with its CURRENT squad and took
#   the top seven of it -- so a school that sent two individuals to the meet
#   came back with a full seven, and the prediction scored a team that is not
#   going to be there. At or below this many at the original running, the
#   school gets AT MOST the number it actually brought.
#
# ! WHY A THRESHOLD AND NOT min(7, n) FOR EVERYONE. A school that brought six
#   is a real team one runner short and will very likely field seven; a school
#   that brought two is almost always unattached individuals sharing a school
#   name. Six and seven keep the rulebook cap; five and under are held to
#   what they actually showed up with.
SMALL_SQUAD_MAX = 5


# Purpose:   how many of a school's current squad may enter the prediction.
# Input:     n_at_meet -- how many that school ran at the original meet.
# Output:    the cap, never above MAX_PER_TEAM.
#
# ! ZERO MEANS "NOT AT THE ORIGINAL MEET" -- a manually named school, where
#   there is no attendance to cap against -- so it keeps the rulebook seven.
def squadCap(n_at_meet):
    if n_at_meet <= 0:
        return MAX_PER_TEAM
    if n_at_meet <= SMALL_SQUAD_MAX:
        return n_at_meet
    return MAX_PER_TEAM


# Purpose:   how many runners each school actually had at the original meet.
# ! Rows with no school are not counted: they are individuals who share the
#   absence of a school, not a squad. Same reasoning as meet_compile.isTeam.
def countsBySchool(originals):
    counts = {}
    for r in originals:
        school = r.get("school")
        if school:
            counts[school] = counts.get(school, 0) + 1
    return counts


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
                                                 "state": _stateOf(school),
                                                 "runners": [], "dropped": []})
            team["runners"].append({"person_id": r["person_id"],
                                    "name": r["name"], "rating": None,
                                    "n_races": None})
        teams = sorted(by_school.values(),
                       key=lambda t: (-len(t["runners"]), t["school"]))
        for t in teams:
            t["runners"].sort(key=lambda x: x["name"])
            # "As it ran" IS the entry list, so what is shown is what entered.
            t["entered"] = len(t["runners"])
        _stampCrests(teams, "school", "state")
        return {"season_year": season_year, "when": when, "teams": teams}

    originals = _exactField(cur, meet_id, div_id, sport)
    at_meet = sorted({r["school"] for r in originals if r.get("school")})
    # ★ THE RACE'S OWN GENDER, from the people who ran it. None means the
    #   field really is mixed -- "All races" at a meet with both -- and then
    #   nothing is filtered, because there is no one right answer to filter to.
    gender = _fieldGender(cur, [r["person_id"] for r in originals], sport)
    # ★ AND THE LEVEL, for the same reason: a school NAME is both a college
    #   and a high school often enough that "everyone at Amherst" is two
    #   different teams. See _fieldLevels.
    levels = _fieldLevels(cur, _lineupIds(originals), sport, season_year)
    squads = _currentSquads(cur, at_meet, sport, season_year, gender=gender,
                            levels=levels)
    current_ids = {e["person_id"] for sq in squads.values() for e in sq}
    # ★ THE CAP IS PER SCHOOL, from what it brought to the original running.
    #   Everyone past the cap goes to `dropped`, not out of the field, so a
    #   school that really is fielding seven this year can be corrected by
    #   hand on the page.
    at_meet_counts = countsBySchool(originals)
    by_school = {}
    for school in at_meet:
        sq = squads.get(school, [])
        cap = squadCap(at_meet_counts.get(school, 0))
        by_school[school] = {"school": school,
                             "state": _stateOf(school),
                             # ! HOW MANY THIS SCHOOL ACTUALLY HAD ON THE
                             #   LINE. The page sends it back so _score can
                             #   tell a team from a lone qualifier with six
                             #   what-ifs added beside them.
                             "entered": at_meet_counts.get(school, 0),
                             "runners": sq[:cap],
                             "dropped": list(sq[cap:])}
    # ★ THE DROPPED NEED THEIR RATING MOST (owner, 2026-09-01). These are the
    #   people a human is deciding whether to add back, and that decision is
    #   "how good were they" -- which was rendered as a blank, because they
    #   have no CURRENT-season row to carry a rating. Their last known one
    #   does exist; it is just in an earlier season.
    absent = [r["person_id"] for r in originals
              if r["person_id"] not in current_ids and r.get("school")]
    last_seen = _lastKnownRatings(cur, absent, sport)

    for r in originals:
        # an original participant with no current-season row: graduated
        # or injured, and the data cannot tell -- listed for the human
        if r["person_id"] in current_ids or not r.get("school"):
            continue
        team = by_school.setdefault(r["school"], {"school": r["school"],
                                                  "state": _stateOf(r["school"]),
                                                  "entered": at_meet_counts.get(
                                                      r["school"], 0),
                                                  "runners": [],
                                                  "dropped": []})
        prev = last_seen.get(r["person_id"], {})
        team["dropped"].append({"person_id": r["person_id"],
                                "name": r["name"],
                                "rating": prev.get("rating"),
                                "pool": prev.get("pool"),
                                "n_races": prev.get("n_races"),
                                # ! THE SEASON IS PART OF THE NUMBER. A 2019
                                #   rating and a 2025 one mean very different
                                #   things about who is standing on the line,
                                #   so the page can say which it is showing.
                                "rating_year": prev.get("year")})
    teams = sorted(by_school.values(),
                   key=lambda t: (-len(t["runners"]), t["school"]))

    # ★ THE DROPPED GET THE HS-EQUIVALENT NUMBER TOO. _squadsForYear stamps
    #   it on everyone with a current-season row, which is `runners`; these
    #   rows come from _lastKnownRatings instead and so were the one place on
    #   the card still showing a raw pool rating beside converted ones. Two
    #   scales in one column is worse than either scale.
    from pool_view import stampBoardRows
    flat = [e for t in teams for e in t["dropped"]]
    if flat:
        stampBoardRows(flat, rating_keys=("rating",), sport=sport)

    # ! AND THE CRESTS, because the browser draws these cards and cannot
    #   find out whether a school has one without fetching it. The team's
    #   pool comes off its own runners: Amherst the college and Amherst the
    #   middle school do not share a crest.
    for t in teams:
        pools = {r.get("pool") for r in t.get("runners") or []}
        t["pool"] = next((p for p in pools if p), None)
    _stampCrests(teams, "school", "state")

    # ! THE PAGE NEEDS IT TOO, so "add from squad" and "add anyone" can offer
    #   the same side of the school this field is made of.
    # ! THE PAGE NEEDS THE LEVEL TOO, so "add from squad" and "add the whole
    #   squad" narrow the same way this field did.
    return {"season_year": season_year, "when": when, "teams": teams,
            "gender": gender, "levels": sorted(levels)}


# Purpose:   the gender a race is run in, read off the people who ran it.
# Input:     person_ids -- the original field.
# Output:    "M", "F", or None when the field is genuinely mixed.
#
# ★ POOL CARRIES THE GENDER. Every pool name ends in it -- hs_m, college_f --
#   which is why filtering by pool has always filtered by gender as a side
#   effect everywhere else in the codebase. predict.py had no concept of pool
#   at all, which is how a boys race came to offer girls (owner, 2026-09-01).
#
# ! A SUPERMAJORITY, NOT UNANIMITY. Issue #52 says some athlete_season rows
#   carry the wrong pool, so demanding one pool exactly would hand back None
#   for a real single-gender race and re-open the leak. 90% calls it; a
#   genuine all-races field is far closer to an even split than that.
_GENDER_SUPERMAJORITY = 0.9


# Purpose:   the school LEVELS a race is run at, read off who ran it.
# Output:    {"college"}, {"hs"}, or several for a genuinely mixed meet.
#
# ★ A SCHOOL NAME IS NOT A SCHOOL (owner, 2026-09-16: "when you press add
#   entire roster it adds the entire roster, including ppl not at that
#   school just at a school with same name"). Amherst (MA) is Amherst
#   College AND Amherst Regional; Manchester, Knox, Carthage and Houghton
#   are each a college and a high school. athlete_season keys on the BARE
#   name, so "everyone at Amherst" is both of them -- and at the D3
#   championships that put middle schoolers in a college championship and
#   let them win it.
#
# ! THE LEVEL IS IN THE POOL, which every row already carries: hs_m, ms_f,
#   college_m. The site's school_identity has a richer notion of this (it
#   splits a K-12 into institutions); the pool is the part that is on the
#   row being filtered, and filtering is what this needs.
#
# ⚠ AND IT IS READ OFF THE RACE, NOT ASSUMED. A college championship comes
#   back {"college"} and filters to it; a genuinely mixed meet comes back
#   with several and filters to none of them, which is right -- there is no
#   one answer to narrow to.
def _fieldLevels(cur, person_ids, sport, season_year=None):
    ids = sorted({p for p in person_ids if p is not None})
    if not ids:
        return set()
    # ⚠ ONE ROW PER ATHLETE, THEIR MOST RECENT, AND THAT IS THE WHOLE BUG
    #   (owner, 2026-09-16, on a D3 championship that came back full of
    #   seventh graders: "it's bcs when you expand it it adds them all, and
    #   most are hsers, so it's able to pass the 60%").
    #
    #   This used to count EVERY athlete_season row, over every year. A D3
    #   sophomore has four hs_m rows and one college_m row, so a field of
    #   nothing but college runners came back about 80% `hs` -- and at a 60%
    #   threshold that does not merely fail to filter, it filters to the
    #   WRONG level. Every shared name then resolved to its high school:
    #   Amherst Regional, Knox, Utica, Houghton. The archive outvoted the
    #   start line.
    #
    #   It was invisible at 10% because both levels passed and nothing was
    #   narrowed. Raising the threshold is what made the latent bug bite.
    #
    # ! AND <= THE SEASON BEING PREDICTED, not simply the newest row. Running
    #   last year's meet must read last year's levels, or a prediction of a
    #   2019 race is filtered by who those people are in 2026.
    year_clause = "AND s.year <= %(yr)s" if season_year else ""
    cur.execute(f"""
        SELECT lvl, count(*) AS n
        FROM (
            SELECT DISTINCT ON (s.person_id)
                   split_part(split_part(s.pool, '|', 1), '_', 1) AS lvl
            FROM   athlete_season s
            WHERE  s.person_id = ANY(%(ids)s) AND s.sport = %(sport)s
              AND  s.pool IS NOT NULL
              {year_clause}
            ORDER  BY s.person_id, s.year DESC
        ) x
        WHERE  lvl <> ''
        GROUP  BY lvl
    """, {"ids": ids, "sport": sport, "yr": season_year})
    rows = [(r["lvl"], int(r["n"])) for r in cur.fetchall() if r["lvl"]]
    total = sum(n for _l, n in rows)
    if not total:
        return set()
    # ! A LEVEL WITH A REAL SHARE OF THE FIELD, not every level one stray row
    #   mentions. Issue #52 says some athlete_season rows carry the wrong
    #   pool, so demanding purity would hand back everything and filter
    #   nothing -- the same reason _fieldGender takes a supermajority.
    return {lvl for lvl, n in rows if n / total >= _LEVEL_MIN_SHARE}


# Purpose:   the ids whose level decides the field's, i.e. the LINEUP.
# ★ THE TOP SEVEN PER SCHOOL, NOT EVERY NAME ON THE ENTRY LIST (owner,
#   2026-09-16: "the 60% shoild be for current top 7 me thinks, and it
#   applied to the expanded rosters"). A meet where one school enters forty
#   in an open race and twenty schools enter seven would otherwise be voted
#   on by that one school. A team enters seven; seven is what it gets to say.
# ! ORDER IS THE ORDER GIVEN, which for _exactField is finishing order -- so
#   the seven that count are the seven that scored, not an arbitrary seven.
def _lineupIds(rows, per_team=None):
    per_team = MAX_PER_TEAM if per_team is None else per_team
    seen, out = {}, []
    for r in rows:
        school = r.get("school")
        if school:
            if seen.get(school, 0) >= per_team:
                continue
            seen[school] = seen.get(school, 0) + 1
        if r.get("person_id") is not None:
            out.append(r["person_id"])
    return out


# A level has to be this much of the field to count as one of its levels.
#
# ⚠ 10% WAS FAR TOO LENIENT (owner, 2026-09-16: "make it more than 10%, maybe
#   more like 50-70%"). It is the share at which a level is kept, so at 0.10 a
#   college championship with 12% mis-pooled rows comes back {"college","ms"}
#   -- both levels pass, and a filter that keeps both filters nothing. The
#   middle schoolers the threshold exists to remove are exactly the rows most
#   likely to sit in that 10%.
#
# ★ AT 0.60 ONLY ONE LEVEL CAN PASS, so the answer is "the level this race is
#   clearly at, or nothing". A genuinely mixed meet -- an open race half high
#   school and half college -- clears the bar for neither and is not filtered,
#   which is the right answer: there is no single level to narrow to.
_LEVEL_MIN_SHARE = 0.60


def _fieldGender(cur, person_ids, sport):
    ids = sorted({p for p in person_ids if p is not None})
    if not ids:
        return None
    cur.execute("""
        SELECT upper(right(s.pool, 1)) AS g, count(*) AS n
        FROM   athlete_season s
        WHERE  s.person_id = ANY(%(ids)s)
          AND  s.sport = %(sport)s
          AND  s.pool IS NOT NULL
        GROUP  BY 1
        ORDER  BY n DESC
    """, {"ids": ids, "sport": sport})
    rows = [r for r in cur.fetchall() if r["g"] in ("M", "F")]
    if not rows:
        return None
    total = sum(r["n"] for r in rows)
    if rows[0]["n"] / float(total) >= _GENDER_SUPERMAJORITY:
        return rows[0]["g"]
    return None            # genuinely mixed: an all-races field


# Purpose:   the most recent rating on record for each person, any season.
# Input:     person_ids -- ids with no row in the season being predicted.
# Output:    {person_id: {"rating", "n_races", "year"}}
#
# ! DISTINCT ON, NEWEST FIRST. One row per person -- the latest season they
#   have a rating for -- rather than every season they ever raced.
def _lastKnownRatings(cur, person_ids, sport):
    ids = sorted({p for p in person_ids if p is not None})
    if not ids:
        return {}
    # ! POOL COMES BACK TOO, and it is not decoration: it is the only thing
    #   that says what the rating MEANS, so it is what the HS-equivalent
    #   conversion needs. A dropped runner's last season may be in a
    #   different pool from the one they would race in now (an eighth-grader
    #   moving up), and the factor is the old pool's -- that IS the rating
    #   being shown.
    cur.execute("""
        SELECT DISTINCT ON (s.person_id)
               s.person_id, s.mean_rating, s.n_races, s.year, s.pool
        FROM   athlete_season s
        WHERE  s.person_id = ANY(%(ids)s)
          AND  s.sport = %(sport)s
          AND  s.mean_rating IS NOT NULL
        ORDER  BY s.person_id, s.year DESC
    """, {"ids": ids, "sport": sport})
    return {r["person_id"]: {
                "rating": round(float(r["mean_rating"]), 1),
                "n_races": r["n_races"], "year": r["year"],
                "pool": r["pool"]}
            for r in cur.fetchall()}


def schoolSquad(cur, school, sport, season_year=None, limit=40,
                gender=None, levels=None):
    """Everyone racing for a school now, best first.

    ★ TWO JOBS, ONE ANSWER. Adding a team that was not at the meet needs its
      seven; adding one more runner to a team already there needs the rest of
      the squad. Both are "who runs for this school now", so both read this
      and the page slices it differently.

    ⚠ AND IT IS THE SAME ANSWER meetField GIVES, WHICH IT WAS NOT (owner,
      2026-09-01: "No one from Woodbridge College has raced this season").
      This used to be its own query with a hard `year = current`, while the
      field itself goes through _currentSquads -- which carries last season's
      roster forward when the new one is empty, ages out the 12s/SRs, and
      drops transfers who are already racing elsewhere (#82, #83).

      So in August and September the two disagreed completely: the meet's own
      teams came back full, and adding ANY team reported it as empty. The
      school had not stopped existing; the current season simply had not
      started. Reported against a real school with a real squad, and it was
      never about that school -- it was every add, all preseason.

    ! SO IT DELEGATES NOW. One rule for who is on a squad, in one place. A
      second implementation of "who runs here now" is a second answer, and
      this is what the second answer cost.
    """
    if season_year is None:
        season_year = _currentSeason(cur, sport)

    squads = _currentSquads(cur, [school], sport, season_year, gender=gender,
                            levels=levels)
    runners = squads.get(school, [])

    # ★ SAY WHICH EMPTY THIS IS (owner, 2026-09-01: "No one from Carondelet
    #   has raced this season"). Carondelet is an all-girls school and the
    #   race was a boys race, so the gendered lookup is CORRECT to find
    #   nobody -- and "has not raced this season" is a false explanation of a
    #   true result. The school is racing; it is just not racing here.
    #
    # ! ONE EXTRA QUERY, ONLY ON THE EMPTY PATH, so the common case pays
    #   nothing. If the school has a squad on the other side, the page can
    #   say so instead of implying the data is missing.
    other = 0
    if not runners and gender:
        other = len(_currentSquads(cur, [school], sport, season_year,
                                   levels=levels).get(school, []))

    # ! THE ADDED TEAM'S CARD GETS A CREST TOO, or a school added by hand is
    #   the one card on the page without one. The squad's own pool picks
    #   which school of the name it is.
    crest = _stampCrests([{"school": school, "state": _stateOf(school),
                           "pool": next((r.get("pool") for r in runners
                                         if r.get("pool")), None)}],
                         "school", "state")[0].get("crest")

    return {"school": school, "season_year": season_year,
            "gender": gender, "crest": crest,
            # How many the school HAS, on the side this race is not. 0 means
            # the school really has nobody racing, either side.
            "other_gender": other,
            # `school` rides on each entry from _currentSquads; the page keys
            # off the top-level one, so it is dropped rather than sent twice.
            "runners": [{k: v for k, v in r.items() if k != "school"}
                        for r in runners[:limit]]}


# ⚠ CACHED, AND THAT IS NOT AN OPTIMISATION -- IT IS THE FIX FOR A
#   REGRESSION I SHIPPED (2026-09-15). The fallback below groups over the
#   whole of `results` to find the newest year that looks like a season.
#   That is the right ANSWER and a ruinous thing to do per request: with
#   season_year_XC missing from homepage_meta every squad call took that
#   path, and one school's squad took 33 SECONDS. The page did not look
#   broken, it looked dead -- "the squads buttons either don't work or take
#   so long they don't work" (owner).
#
# ! THE TTL IS SHORT BECAUSE THE ANSWER MOVES ONCE A SEASON. An hour means
#   a pipeline run that writes homepage_meta is picked up without a
#   restart, and the scan happens at most once an hour per worker.
_SEASON_CACHE = {}
_SEASON_TTL = 3600.0

# How many seasons back the fallback will probe before giving up and
# answering with the academic year we are in. Six covers a database mid
# rebuild; beyond that the answer is not "an older season", it is "the
# boards are empty", and the ceiling is the honest reply.
_SEASON_LOOKBACK = 6


def _currentSeason(cur, sport):
    """The season the boards are showing, so the field agrees with them."""
    import time as _t
    hit = _SEASON_CACHE.get(sport)
    if hit and _t.time() - hit[0] <= _SEASON_TTL:
        return hit[1]
    year = _currentSeasonUncached(cur, sport)
    _SEASON_CACHE[sport] = (_t.time(), year)
    return year


def _currentSeasonUncached(cur, sport):
    cur.execute("SELECT value FROM homepage_meta WHERE key = %s",
                (f"season_year_{sport}",))
    row = cur.fetchone()
    if row and row["value"]:
        # ⚠ THE META HOLDS THE LABEL, AND THIS WANTS THE STORED YEAR. A track
        #   season is stored under the year it OPENS in (Dec 2025 - Jul 2026
        #   is 2025) and named year + 1 everywhere a person reads it;
        #   panels.py publishes the label on purpose, for the home page. Every
        #   caller of this function compares the result against
        #   athlete_season.year, which is STORED -- so reading the label back
        #   raw asked track for a season one year ahead of the one that
        #   exists, and found nobody in it.
        #
        # ! XC's label and stored year are the same, which is why this never
        #   showed: the sport it breaks is the one nobody has predicted yet.
        from school import storedYear
        return storedYear(sport, row["value"])

    # ⚠ THE FALLBACK USED TO BE max(substring(date,1,4)) AND ONE BAD ROW
    #   TOOK THE PAGE DOWN (owner, 2026-09-15: "0 athletes for all teams").
    #   homepage_meta held season_year_TF but no season_year_XC, so XC came
    #   down this path -- and a single corrupt date in `results` with the
    #   year 2223 won the max(). Every squad query then asked for season
    #   2223, found nobody, and the carry-forward asked 2222 and found
    #   nobody either: 396 teams, 0 runners, with no error anywhere.
    #
    # ★ SO: THE NEWEST YEAR THAT LOOKS LIKE A SEASON, not the newest string
    #   in the column. A real season has tens of thousands of results; a
    #   typo has one. And nothing ahead of the academic year we are actually
    #   in can be a current season, whatever the data says.
    # ★ AND THE FALLBACK PROBES AN INDEX INSTEAD OF SCANNING THE CORPUS
    #   (owner, 2026-09-16: 6,199 ms inside a 14 s prediction). Grouping
    #   `results` by year to find the newest real season is the right ANSWER
    #   and 54M rows of work; caching it only moved the cost to one request
    #   an hour per worker, where it sat at six seconds.
    #
    # ! ASKED OF athlete_season, NEWEST YEAR FIRST, THROUGH as_board_mean_idx
    #   (pool, sport, year, ...) -- so each probe is an index lookup, and the
    #   answer is found at the first year that has one. A handful of probes
    #   instead of a full aggregate.
    #
    # ⚠ AND STILL BOUNDED BY A FLOOR, because that is what this fallback is
    #   FOR: one corrupt row dated 2223 once won a max() and every squad
    #   query then asked for season 2223 and found nobody -- 396 teams, 0
    #   runners, no error anywhere. The LIMIT inside the subquery caps the
    #   work at SEASON_MIN_RESULTS index rows per probe, so the guard costs
    #   a bounded amount rather than an aggregate.
    from season_year import academicYear
    ceiling = academicYear(datetime.date.today())
    pools = [f"{lvl}_{g}" for lvl in ("hs", "college", "ms") for g in ("m", "f")]
    for year in range(ceiling, ceiling - _SEASON_LOOKBACK, -1):
        cur.execute("""
            SELECT count(*) AS n FROM (
                SELECT 1 FROM athlete_season
                WHERE  pool = ANY(%(pools)s)
                  AND  sport = %(sport)s
                  AND  year  = %(year)s
                LIMIT  %(floor)s
            ) x
        """, {"pools": pools, "sport": sport, "year": year,
              "floor": SEASON_MIN_RESULTS})
        row = cur.fetchone()
        if row and int(row["n"]) >= SEASON_MIN_RESULTS:
            return year
    return ceiling


# Purpose:   a school's HOME state, for display beside its name.
# Output:    'NC', or None for a school the identity table does not place.
# Detail:
#   ★ THE STATE IS A LABEL, NEVER A KEY (issue #95). Every data path -- the
#     squad endpoint, teamIsIn, _score's grouping, ranking_results.school --
#     matches on the BARE name, so the state can only be attached on the way
#     out. This is the same split that broke add/remove when the search
#     index's "DeWitt (MI)" was used as an identity (#87), and it is written
#     as a SEPARATE FIELD here rather than folded into `school` for exactly
#     that reason.
#
#   ! SAME MAP THE REST OF THE SITE RENDERS THROUGH. school_identity.loadLabels
#     runs at app import and primaryState is a dict lookup, so this costs
#     nothing per row and cannot disagree with the school_label filter. A
#     second source of "where is this school" would be a second answer.
#
#   ⚠ A SCHOOL SPLIT ACROSS STATES HAS NO SINGLE ANSWER, which is why
#     search_index emits one ROW PER STATE for those. primaryState returns the
#     largest cluster; a genuinely split name shows its main state, and that
#     is better than showing none.
def _stateOf(school):
    if not school:
        return None
    try:
        from school_identity import primaryState
        return primaryState(school)
    except Exception:                                   # noqa: BLE001
        # Labels not loaded (a script importing predict without the app).
        # A missing state renders as a bare name, which is the old behaviour.
        return None


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

    # ★ THE LINEUP THE PAGE IS SHOWING WINS, WHEN IT SENDS ONE (owner,
    #   2026-09-16: "you can literally see the correct list there. Why not
    #   just take those athletes?").
    #
    # ⚠ EVERYTHING BELOW IS A SECOND DERIVATION OF THE FIELD, and the page
    #   has already made the first one -- it fetched it, rendered it, let a
    #   person edit it, and they pressed Predict on what they could see. When
    #   the two disagree the model scores a race nobody asked for: at the D3
    #   championships the page showed 82 teams and this scored 400+, with
    #   middle schoolers in a college championship.
    #
    # ! SO THE SCHOOL COMES FROM THE PAGE TOO, not from a fresh lookup. The
    #   card says which team a runner is on; resolving it again could put
    #   them on a different one and re-open the same divergence one level
    #   down. Only the NAME is fetched, because the page does not send it and
    #   the scorers list needs it.
    #
    # ! AND THE EDITS ARE ALREADY IN IT. add/remove describe changes to a
    #   field the page derived; when the page sends the field itself they are
    #   redundant, and applying them again would remove someone twice.
    explicit = target.get("field")
    if explicit:
        # ! AND HOW MANY EACH SCHOOL ENTERED, WHICH IS NOT len(ids). The page
        #   shows a lone qualifier's whole squad when a person asks for it;
        #   the ids are then seven and the entry is still one. _score needs
        #   the entry count to know that team cannot score or displace.
        by_id, entered = {}, {}
        for row in explicit:
            school, ids = row[0], row[1]
            n = row[2] if len(row) > 2 else None
            if n is not None:
                entered[school] = int(n)
            for pid in ids:
                by_id.setdefault(int(pid), school)
        # ! THE SCHOOL STILL COMES FROM THE PAGE; only the columns the page
        #   does not send are looked up. Resolving the school again is the
        #   divergence this whole branch exists to prevent.
        known = {e["person_id"]: e
                 for e in _athleteEntries(cur, list(by_id), sport,
                                          _currentSeason(cur, sport))}
        entries = []
        for pid, school in by_id.items():
            k = known.get(pid) or {}
            entries.append({"person_id": pid, "school": school,
                            "name": k.get("name") or "Unknown",
                            "grade": k.get("grade"), "pool": k.get("pool"),
                            "rating": k.get("rating"),
                            "entered": entered.get(school),
                            "school_state": _stateOf(school)})
        return entries

    # ★ SEVERAL DIVISIONS AS ONE RACE (issue #86). Each division's roster is
    #   built exactly as a single division's is, then they are put in one
    #   field. Nothing about the per-division build changes -- this is a
    #   union, not a different way of choosing runners.
    div_ids = [d for d in (target.get("div_ids") or []) if d]
    if target.get("meet_id") and len(div_ids) > 1:
        entries = _combinedRoster(cur, target, div_ids, sport, mode)
    elif target.get("meet_id"):
        originals = _exactField(cur, int(target["meet_id"]), div, sport)
        if mode == "rerun_exact":
            # The exact field IS the entry list, so counting it is counting
            # who entered -- no stamp needed, and _score falls back to it.
            entries = originals
        else:
            at_meet = sorted({r["school"] for r in originals
                              if isTeam(r.get("school"))})
            # ★ SAME GENDER AS meetField DERIVES, or the page shows one
            #   lineup and the model scores another.
            ids = [r["person_id"] for r in originals]
            year = _currentSeason(cur, sport)
            squads = _currentSquads(cur, at_meet, sport, year,
                                    gender=_fieldGender(cur, ids, sport),
                                    levels=_fieldLevels(
                                        cur, _lineupIds(originals), sport,
                                        year))
            # ★ SAME PER-SCHOOL CAP AS meetField. These two must agree or the
            #   page shows one lineup and the model scores another.
            at_meet_counts = countsBySchool(originals)
            entries = []
            for sch in sorted(squads):
                for e in squads[sch][:squadCap(at_meet_counts.get(sch, 0))]:
                    # ! WHAT THE SCHOOL ENTERED, carried on every runner so
                    #   _score can tell a team from a lone qualifier. The cap
                    #   above already keeps the lineup honest; `add` is what
                    #   can push it past what the school brought.
                    e["entered"] = at_meet_counts.get(sch, 0)
                    entries.append(e)
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
    # ! STAMPED HERE BECAUSE HERE IS BEFORE THE SUFFIX. _combinedRoster calls
    #   this function once per division and THEN rewrites `school` to
    #   "Broughton (Varsity)" for a school in two of them. Reading the state
    #   after that would be reading it off a name that no longer exists.
    for e in entries:
        e["school_state"] = _stateOf(e.get("school"))
    return entries


# Purpose:   one field out of several divisions, for a combined race.
# Input:     div_ids -- the divisions to merge; target carries `coalesce`.
# Output:    entries, with `school` rewritten when a school is in more than one.
#
# ★ A SCHOOL IN TWO DIVISIONS IS TWO TEAMS BY DEFAULT (owner, 2026-09-01).
#   _score groups on `school`, so "Cabell Midland (Varsity)" and "Cabell
#   Midland (JV)" score as two squads with no runner pooled between them --
#   which is what actually happened on the day.
#
# ★ AND COALESCE IS AN OPTION, because the owner's rule is to offer the choice.
#   Coalescing keeps the bare name, so the two entries become one squad.
# ⚠ WHICH IS NOT FREE, AND THE CAP IS WHY. A school entered twice brings
#   fourteen runners; scored as one team all fourteen take places, pushing
#   every other team's runners down and handing the merged squad an advantage
#   no real team could have. predictTeam trims a coalesced squad to seven --
#   see _capCoalesced -- so it enters what a team is allowed to enter.
# ! ONLY SCHOOLS ACTUALLY IN TWO DIVISIONS ARE SUFFIXED. Labelling every team
#   would put "(Varsity)" on schools that only ran once, which is noise.
def _combinedRoster(cur, target, div_ids, sport, mode):
    base = dict(target)
    per_div, labels = [], {}
    for d in div_ids:
        one = dict(base)
        one["div_id"] = d
        one.pop("div_ids", None)
        rows = _teamRosters(cur, None, one)
        labels[d] = _divisionLabel(cur, target.get("meet_id"), d, sport) or str(d)
        # ★ WHERE EACH RUNNER CAME FROM, ALWAYS (owner, 2026-09-01: "need to
        #   check coalesce actually works... which div does it end up showing
        #   in?"). Coalescing is invisible in the result -- the squad simply
        #   appears once under its bare name -- so there is no way to tell a
        #   coalesced team from a team that only ever ran one division, and
        #   therefore no way to tell whether the checkbox did anything.
        #   Carrying the division per entry lets _score say.
        # ★ AND THE RACE'S GENDER, WHICH COALESCE HAS TO RESPECT. See below.
        # ! ONLY WHEN COALESCING. It is a query per division, and nothing
        #   else here reads it -- the un-coalesced path suffixes by division,
        #   which needs no gender at all.
        if target.get("coalesce"):
            g = _fieldGender(cur, [e["person_id"] for e in rows], sport)
            for e in rows:
                e["div_gender"] = g
        for e in rows:
            e["div_label"] = labels[d]
        per_div.append((d, rows))

    if not target.get("coalesce"):
        seen = {}
        for d, rows in per_div:
            for e in rows:
                sch = e.get("school")
                if sch:
                    seen.setdefault(sch, set()).add(d)
        for d, rows in per_div:
            for e in rows:
                sch = e.get("school")
                if sch and len(seen.get(sch, ())) > 1:
                    e["school"] = f"{sch} ({labels[d]})"
    else:
        # ★ COALESCE MERGES WITHIN A GENDER, NEVER ACROSS ONE (owner,
        #   2026-09-01: "when a team is coalesced how does it choose girls vs
        #   boys?"). It did not choose. _score groups on the school name and
        #   _capCoalesced then keeps the FASTEST SEVEN of whatever landed
        #   under it -- so a school entered in a boys race and a girls race
        #   was merged into one fourteen-runner squad and trimmed to the
        #   seven fastest, which is the boys. The girls team did not lose;
        #   it silently stopped existing.
        #
        # ⚠ AND MIXED-GENDER RACES ARE ALLOWED, which is exactly why this
        #   matters. The owner's ruling is that you may race a boys division
        #   against a girls one -- so the two teams must both be ON the
        #   start line, as two teams. Coalesce is for a school that brought
        #   FOURTEEN RUNNERS TO ONE COMPETITION; a boys team and a girls team
        #   are not that, they are two teams sharing a name.
        #
        # ! SO THE KEY IS (school, gender). All one gender and the name stays
        #   bare, which is the ordinary case and the behaviour that shipped.
        #   Two genders and each side keeps its own name, exactly as the
        #   un-coalesced path suffixes by division.
        genders = {}
        for _d, rows in per_div:
            for e in rows:
                sch = e.get("school")
                if sch:
                    genders.setdefault(sch, set()).add(e.get("div_gender"))
        for _d, rows in per_div:
            for e in rows:
                sch = e.get("school")
                if sch and len(genders.get(sch, ())) > 1:
                    g = e.get("div_gender")
                    side = {"M": "Boys", "F": "Girls"}.get(g) or "Mixed"
                    e["school"] = f"{sch} ({side})"

    out, seen_ids = [], set()
    for _d, rows in per_div:
        for e in rows:
            # ! ONE ROW PER PERSON. An athlete entered in two divisions of the
            #   same meet would otherwise run twice in one race.
            if e["person_id"] in seen_ids:
                continue
            seen_ids.add(e["person_id"])
            out.append(e)
    return out


# The division's own name, for the "(Varsity)" suffix. Falls back to the id.
def _divisionLabel(cur, meet_id, div_id, sport):
    if not meet_id or not div_id:
        return None
    table, col = ("meets", "division") if sport == "XC" else ("meets_tf",
                                                              "division")
    cur.execute(f"SELECT {col} AS d FROM {table} "
                f"WHERE meet_id = %(m)s AND div_id = %(v)s LIMIT 1",
                {"m": int(meet_id), "v": int(div_id)})
    row = cur.fetchone()
    return ((row or {}).get("d") or "").strip() or None


# Purpose:   a coalesced squad enters what a team is allowed to enter.
# Input:     field/preds as predictTeam holds them.
# Output:    the field, trimmed to MAX_PER_TEAM per school by predicted time.
#
# ⚠ BY PREDICTED TIME, NOT BY RATING. The rating is what we had before the
#   model ran; the prediction is the model's own answer for THIS race, and
#   scoring should use the seven it thinks are fastest here.
# ! AN UNPREDICTABLE RUNNER IS NOT TRIMMED, because they are not in the
#   scoring order at all -- _score already drops them.
def _capCoalesced(field, preds):
    paired = list(zip(field, preds))
    timed = [(f, p) for f, p in paired if p.get("seconds") is not None]
    timed.sort(key=lambda t: t[1]["seconds"])
    kept, per_school = set(), {}
    for f, _p in timed:
        sch = f.get("school")
        if not isTeam(sch):
            kept.add(id(f))
            continue
        per_school[sch] = per_school.get(sch, 0) + 1
        if per_school[sch] <= MAX_PER_TEAM:
            kept.add(id(f))
    out_f, out_p = [], []
    for f, p in paired:
        if p.get("seconds") is None or id(f) in kept:
            out_f.append(f)
            out_p.append(p)
    return out_f, out_p


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


# The class that graduates out of a level at season's end: a 12 leaves
# high school, an SR leaves college. Everyone else carries forward.
#
# ★ COMPARED NORMALISED (issue #82). This was a hand-listed set of case
#   variants -- ("12", "SR", "sr", "Sr") -- matched with exact equality, so
#   "12th", "Senior", "SENIOR" and anything with stray whitespace all slipped
#   through and graduated seniors carried into this year's lineup. The query
#   now upper-cases and trims before comparing, so only the SPELLINGS need
#   listing, not their capitalisations.
# ⚠ THIS LIST WAS AN EXACT MATCH AND IT LET SENIORS THROUGH (owner,
#   2026-09-15: "predicting a future race does not remove seniors from last
#   season"). UPPER(BTRIM(grade)) caught "12", "12TH", "SR" and "SENIOR" and
#   nothing else -- so "Sr." kept its period and stayed, and every COLLEGE
#   senior stayed, because the feeds write those as "SR-4" or "16" and
#   neither is in this list. A squad carried forward still had its
#   graduating class in it, which is the one thing the carry-forward exists
#   to remove.
#
# ★ rankings.gradeKeySql ALREADY KNOWS. It is the one place that decides a
#   senior is a senior whatever the feed called them: it strips non-digits,
#   reads the fr/so/jr/sr/se prefixes, and maps a college 13-16 onto its
#   class word. A high school senior keys to '12' and a college senior to
#   'sr', so those two keys are the whole terminal set.
#
# ⚠ AND THE OLD SPELLING LIST IS GONE, NOT KEPT BESIDE IT. It survived the
#   switch to keys as an unused query parameter, which is the shape a second
#   answer to "who is a senior" comes back in: the next reader to reach for
#   it would have got the list that MISSED "Sr." and "SR-4" -- the exact bug
#   the keys were introduced to fix. One definition, and it is the key.
#
# ! AND THE DEFINITION IS roster.py's NOW, because the school page needs the
#   same one. Re-exported here so this module's callers and its tests do not
#   have to know where the rule lives.
from roster import TERMINAL_KEYS as _TERMINAL_KEYS


def _currentSquads(cur, schools, sport, season_year, gender=None,
                   levels=None):
    """{school: [runners best-first]} for this season.

    ★ A NEW SEASON STARTS EMPTY, SO LAST YEAR'S ROSTER CARRIES FORWARD
      MINUS THE GRADUATING CLASS. In August the current season has no
      rows yet and a naive query predicts a meet with nobody in it.
      A school with no current-season row takes its previous season's
      squad with the 12s/SRs aged out -- they are the one group the
      data KNOWS is gone; everyone else is presumed back until real
      results say otherwise. Ratings shown are last season's.

    ★ AND MINUS ANYONE ALREADY RACING ELSEWHERE (issue #83). A transfer is
      not a terminal grade, so aging-out left them on the old school's
      squad while they raced for the new one. They are told apart from a
      graduate by the one thing that differs: a transfer HAS a
      current-season row, at another school, and a graduate has none."""
    schools = [s for s in schools if s]
    if not schools or season_year is None:
        return {}
    # ★ THE "CURRENT" SEASON IS ROUTINELY A FINISHED ONE, and that is why the
    #   first version of this fix did nothing (owner, 2026-08-31).
    #   _currentSeason returns the season the BOARDS are showing, so that the
    #   field agrees with them -- and in August that is still LAST season,
    #   because the new one has almost no results yet. Every school therefore
    #   HAS a row for it, `missing` comes back empty, the carry-forward never
    #   runs, and the aging-out that lives on that path never executes. The
    #   squad handed back is last year's roster: its seniors, at its schools.
    #
    #   So the test is not "did we fall back", it is "is the season we are
    #   reading from OVER". academicYear knows: the season is the academic
    #   year, August to July, named for the year it opens in.
    # ! ONE YEAR OF AGING ONLY. A squad two or more seasons stale would need
    #   its 11s aged out as well, and that is a data problem worth seeing
    #   rather than silently papering over.
    from season_year import academicYear
    now = academicYear(datetime.date.today())
    stale = season_year < now

    squads = _squadsForYear(cur, schools, sport, season_year,
                            exclude_terminal=stale,
                            active_year=now if stale else None,
                            gender=gender, levels=levels)

    # ★ THE CARRY-FORWARD IS PER SCHOOL AND PER RACE NOW, NOT ALL-OR-NOTHING
    #   (owner, 2026-09-15: "keep everybody else on the roster until they
    #   either don't run the first 3 races or until they race for another
    #   team... 2026 roster for a team after their first race should include
    #   ppl from last year who got sick during the first race").
    #
    # ⚠ THE TEST USED TO BE "HAS THIS SCHOOL ANY ROW AT ALL", and that is
    #   what was wrong with it. One athlete of a forty-person programme runs
    #   a September opener, the school stops being `missing`, and the other
    #   thirty-nine drop out of the field, the squad picker and the school
    #   page in the same instant -- because a roster was being read off who
    #   had happened to race, three weeks into a season.
    #
    # ! ONLY WHERE THE SEASON WE ARE READING IS THE LIVE ONE. A stale season
    #   is a finished one: it has its full roster already, and carrying a
    #   second season on top of it would age nobody out correctly (see the
    #   one-year note above).
    from roster import carryingSchools
    carrying = ([] if stale
                else sorted(carryingSchools(cur, schools, sport, season_year)))
    if carrying:
        prev = _squadsForYear(cur, carrying, sport, season_year - 1,
                              exclude_terminal=True,
                              active_year=season_year, gender=gender,
                              levels=levels)
        for sch, rows in prev.items():
            # ! MERGED, NOT REPLACED. The school may already have runners
            #   this season; those rows are the better ones -- this season's
            #   rating, this season's grade -- so the carried set fills in
            #   around them and never over them.
            have = {r["person_id"] for r in squads.get(sch, [])}
            add = [r for r in rows if r["person_id"] not in have]
            for r in add:
                r["carried"] = True   # last season's roster, aged forward
            if add:
                # ⚠ SORTED EXPLICITLY FIRST. _bestFirst only REORDERS a squad
                #   that spans pools -- with one pool it hands the list back
                #   untouched, which is right when the list came out of an
                #   ORDER BY and wrong here, where two ordered lists have
                #   just been concatenated. Without this the returners sit in
                #   a block below everyone racing, whatever they ran.
                merged = sorted(squads.get(sch, []) + add,
                                key=lambda r: -(r.get("rating") or 0))
                squads[sch] = _bestFirst(merged, sport)
    return squads


def _squadsForYear(cur, schools, sport, year, exclude_terminal=False,
                   active_year=None, gender=None, levels=None):
    """One season's squads per school.

    exclude_terminal -- drop the graduating class (issue #82).
    active_year      -- drop anyone who already has a row in THIS season at
                        any school, i.e. a transfer (issue #83).
    gender           -- "M"/"F": only that side of the school. A school has a
                        boys team and a girls team and they are not one squad.
    levels           -- {"college"}, {"hs"}: only that level of the school.
                        A NAME is not a school -- Amherst (MA) is a college
                        and a regional high school, and athlete_season keys
                        on the bare name. See _fieldLevels.
    """
    # ! BOTH CLAUSES COME FROM roster.py. They are the carry-forward RULE,
    #   and the school page applies the same one -- two spellings of "who
    #   graduated" is two rosters for one team.
    import roster
    grade_clause = roster.graduatedClause("s") if exclude_terminal else ""
    move_clause = (roster.transferredClause("s")
                   if active_year is not None else "")
    # ★ A SCHOOL IS TWO TEAMS. Without this the boys squad and the girls squad
    #   come back as one list and the top seven of it is a mixed team.
    gender_clause = ("AND upper(right(s.pool, 1)) = %(gender)s"
                     if gender in ("M", "F") else "")
    # ★ AND A NAME IS NOT A SCHOOL. Same split the pool already carries.
    level_clause = ("AND split_part(split_part(s.pool, '|', 1), '_', 1) "
                    "= ANY(%(levels)s)" if levels else "")
    cur.execute(f"""
        SELECT s.school, s.person_id, s.grade, s.pool,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '') AS name,
               s.mean_rating, s.n_races
        FROM   athlete_season s
        {_NAME_LATERAL.format(pid="s.person_id")}
        WHERE  s.school = ANY(%(schools)s)
          AND  s.year   = %(yr)s
          AND  s.sport  = %(sport)s
          {grade_clause}
          {move_clause}
          {gender_clause}
          {level_clause}
        ORDER  BY s.school, s.mean_rating DESC NULLS LAST
    """, {"schools": schools, "yr": year, "sport": sport,
          "term_keys": list(_TERMINAL_KEYS),
          "active_yr": active_year,
          "gender": gender,
          "levels": sorted(levels) if levels else None})
    out = {}
    for r in cur.fetchall():
        entry = {
            "person_id": r["person_id"], "school": r["school"],
            # Carried for the cross-pool sort below, not for display.
            "pool": r.get("pool"),
            # ! AND FOR DISPLAY: a results page has a Grade column, and the
            #   prediction's results table is the same table (owner,
            #   2026-09-16). gradeLabel spells it for the row's own level.
            "grade": r.get("grade"),
            "name": (r["name"] or "").strip() or "Unknown",
            "rating": (round(float(r["mean_rating"]), 1)
                       if r["mean_rating"] is not None else None),
            "n_races": r["n_races"]}
        # ! AND SAY SO WHEN WE CANNOT TELL. A blank or NULL grade is not a
        #   senior and not a returner -- it is unknown, and the aging-out
        #   cannot rule on it either way. Flagged rather than guessed, so the
        #   page can mark them instead of the code pretending to know.
        if exclude_terminal and not (r.get("grade") or "").strip():
            entry["grade_unknown"] = True
        out.setdefault(r["school"], []).append(entry)

    # ★ THE HS-EQUIVALENT NUMBER, STAMPED BESIDE THE POOL ONE (owner,
    #   2026-09-15: "the speed ratings for the ppl on the teams is not
    #   hs-equivalent when it should be if the scale is hs-equivalent").
    #   _bestFirst already converts through repFactor to SORT a mixed-pool
    #   squad correctly -- it has to, since a rating is pool-relative -- but
    #   the number shown was always the raw one. Every entry carries its own
    #   pool, so each converts by its own factor.
    #
    # ⚠ BESIDE, NOT INSTEAD. `rating` stays the pool rating because the
    #   prediction maths and the data-rating attributes the page reads back
    #   are built on it. This is a display value only.
    from pool_view import stampBoardRows
    flat = [e for rows in out.values() for e in rows]
    if flat:
        stampBoardRows(flat, rating_keys=("rating",), sport=sport)
    return {sch: _bestFirst(rows, sport) for sch, rows in out.items()}


# Purpose:   order one school's squad so the top seven are the top seven.
# Detail:
#   ★ A RATING IS POOL-RELATIVE, SO SORTING ON IT ACROSS POOLS IS WRONG
#     (owner, 2026-09-01: "don't have issues with like a ms-hs choosing all
#     msers"). 100 is the MEAN OF YOUR OWN POOL -- an ms_m 120 and an hs_m
#     120 are nothing like the same runner. A K-12 school whose middle and
#     high school athletes share one name would put its best eighth-graders
#     above its varsity, and squadCap would then take seven of them.
#
#   ! CONVERTED TO ONE SCALE BEFORE COMPARING, using pool_view.repFactor --
#     the same conversion the HS-equivalent view on the athlete page uses, so
#     the page and this cannot disagree about what a rating is worth.
#
#   ⚠ ONLY WHEN A SCHOOL ACTUALLY SPANS POOLS, which is rare. One pool and
#     the order is already right and is left exactly as the database returned
#     it -- no factor fetched, no behaviour changed, nothing to go wrong in
#     the overwhelmingly common case.
#
#   ⚠ AND A MISSING FACTOR MEANS LEAVE IT ALONE. hsFactor returns None when
#     it cannot compute one, and its own docstring says callers must never
#     read that as 1.0. Converting some of a list and not the rest would be
#     worse than converting none of it.
#
#   ! THE DISPLAYED RATING IS UNTOUCHED. This changes the ORDER only; the
#     number beside a runner is still their own pool's, which is what the
#     rest of the site shows. Displaying HS-equivalents by default is #50.
def _bestFirst(rows, sport):
    pools = {r.get("pool") for r in rows}
    if len(pools) < 2:
        return rows
    from pool_view import repFactor
    factors = {}
    for pl in pools:
        f = repFactor(pl, sport) if pl else None
        if not f:
            return rows                 # cannot compare honestly -- do not
        factors[pl] = float(f)
    return sorted(
        rows,
        key=lambda r: -((r["rating"] or 0) * factors[r["pool"]]))


def _athleteEntries(cur, person_ids, sport, season_year):
    """name + school for hand-added athletes: this season's row first,
    the athletes table for anyone without one.

    ★ AND grade/rating/pool, BECAUSE THE RESULTS TABLE HAS THOSE COLUMNS
      (owner, 2026-09-16: "it should read exactly like a results page"). They
      are on the athlete_season row this already reads, so they cost nothing;
      the athletes-table fallback has none and the columns render blank,
      which is what a results page does for a runner it knows nothing about.
    """
    ids = sorted(person_ids)
    if not ids:
        return []
    out, seen = [], set()
    if season_year is not None:
        cur.execute(f"""
            SELECT s.person_id, s.school, s.grade, s.pool,
                   s.mean_rating AS rating,
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
                        "grade": r["grade"], "pool": r["pool"],
                        # ⚠ ROUNDED, LIKE EVERY OTHER RATING ON THE SITE
                        #   (owner, 2026-09-16: "the ratings are off even for
                        #   real athletes"). mean_rating is a REAL, so the
                        #   raw value prints as 110.451996 and 115.118004 --
                        #   float32 noise rendered as five decimal places of
                        #   false precision. _squadsForYear has always
                        #   rounded here; this path was added later and did
                        #   not, so the same athlete read differently
                        #   depending on which query found them.
                        "rating": (round(float(r["rating"]), 1)
                                   if r["rating"] is not None else None),
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