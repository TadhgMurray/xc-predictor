# Project: xc-predictor / racecast
# File:    pages3.py
# Purpose: the routes for the features the owner approved on 2026-10-10:
#
#     /api/next-race/athlete/<id>      next posted meet, predicted time/place
#     /api/next-race/school            the same for a team: its place
#     /meet/preview/xc/<id>            a posted meet, previewed
#     /api/meet-preview/xc/<id>/<div>  one race's projected scores
#     /movers                          the weekly state digest
#     /school/<name>/season            the coach's season tracker
#     /api/runners-like-you/<id>       the chart's next-season fan
#     /athlete/<id>/profile            the recruiting one-pager
#     /meet/recap/xc/<id>              how the stored forecast did (2026-10-10)
#     /recaps?week=&state=             the week's recaps, one line a meet
#     /card/recap/xc/<id>.png          the recap's share card
#
# ★ A BLUEPRINT, AS pages2.py IS. app.py is eleven thousand lines with
#   several people in it at once; this file needs only the connection pool
#   and the template globals, and app.py carries one register line.
#
# ★ THE JSON IS EDGE-CACHEABLE ON PURPOSE. Everything under /api/ goes out
#   no-store by default (app._PRIVATE_PREFIXES) because most of it is per
#   request. These four are a function of the URL and the pipeline, nothing
#   else -- no session, no free text -- so each sets the pages' own public
#   max-age itself, and ttlcache holds the compute under the edge.
import datetime

import psycopg2.extras
from flask import (Blueprint, abort, jsonify, redirect, render_template,
                   request)

import ttlcache
from database import getConn

bp = Blueprint("pages3", __name__)

_FAIL_TTL = 300.0


def _publicJson(payload):
    """jsonify with the pages' public cache header (app.PAGE_MAX_AGE /
    PAGE_S_MAXAGE): the edge holds what ttlcache computed."""
    from app import PAGE_MAX_AGE, PAGE_S_MAXAGE
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = (f"public, max-age={PAGE_MAX_AGE}, "
                                     f"s-maxage={PAGE_S_MAXAGE}")
    return resp


def _cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# ------------------------------------------------------------------ #
#  template helpers: the preview links from /meets and the home page
# ------------------------------------------------------------------ #

@bp.app_context_processor
def _previewHelpers():
    def meetPreviewHref(m):
        """The preview of a weekend.py meet row, or None (track meets have
        no preview: upcoming_preview.py says why)."""
        if not m or m.get("sport") != "XC":
            return None
        from upcoming_preview import previewHref
        return previewHref(m["meet_id"], m.get("source"))

    def comingTop(sport="XC"):
        """The biggest posted meet of the coming week in `sport` (most races
        posted, weekend.py's own size signal), or None."""
        try:
            from weekend import comingUpCached
            meets = [m for d in comingUpCached(getConn) for m in d["meets"]
                     if m["sport"] == sport]
        except Exception:                               # noqa: BLE001
            return None
        if not meets:
            return None
        top = max(meets, key=lambda m: (m["n_races"], -len(m["date"] or "")))
        return dict(top, preview_href=meetPreviewHref(top))
    def recapHref(meet_id, source=None):
        """/meet/recap/xc/<id> when a forecast was stored for this meet and
        its day has come, else None (meet_forecast.recapKeys, cached)."""
        try:
            keys = _recapKeys()
            mid = int(meet_id)
        except Exception:                               # noqa: BLE001
            return None
        from meet_recap import recapHref as href
        src = "tfrrs" if source == "tfrrs" else "anet"
        if (src, mid) in keys:
            return href(mid, src)
        # ! a row with no feed named (the home page's Latest results): anet
        #   first, as upcomingMeet reads it, then tfrrs
        if source is None and ("tfrrs", mid) in keys:
            return href(mid, "tfrrs")
        return None
    return {"meet_preview_href": meetPreviewHref, "coming_top": comingTop,
            "recap_href": recapHref}


# ★ ONE READ PER WORKER PER TTL for every link to a recap on every page: the
#   set of (source, meet_id) with a stored forecast whose day has come
_RECAP_KEYS_TTL = 3600.0


def _recapKeys():
    import meet_forecast as MF

    def compute():
        with getConn() as conn:
            with _cursor(conn) as cur:
                out = MF.recapKeys(cur, _today())
                conn.rollback()
                return out
    return ttlcache.get(("recap_keys", _today().isoformat()), compute,
                        ttl=_RECAP_KEYS_TTL)[0]


# ------------------------------------------------------------------ #
#  3. next race, athlete and school
# ------------------------------------------------------------------ #

def _today():
    return datetime.date.today()


@bp.route("/api/next-race/athlete/<int:person_id>")
def api_next_race_athlete(person_id):
    import upcoming_preview as U

    def compute():
        with getConn() as conn:
            with _cursor(conn) as cur:
                out = U.athleteNextRace(cur, getConn, person_id, _today())
                conn.rollback()
                return out
    try:
        val, _ = ttlcache.get(("next_race_a", person_id, _today().isoformat()), compute,
                              ttl=U.TTL, ttl_of=lambda v: U.TTL if v.get("available") else _FAIL_TTL)
    except Exception as exc:                            # noqa: BLE001
        print(f"next race athlete {person_id}: {type(exc).__name__}: {exc}", flush=True)
        val = {"available": False}
    return _publicJson(val)


@bp.route("/api/next-race/school")
def api_next_race_school():
    import upcoming_preview as U
    school = (request.args.get("school") or "").strip()[:200]
    state = (request.args.get("state") or "").strip().upper()[:2] or None
    if not school:
        return jsonify({"error": "school is required"}), 400

    def compute():
        with getConn() as conn:
            with _cursor(conn) as cur:
                out = U.schoolNextRaces(cur, getConn, school, state, _today())
                conn.rollback()
                return out
    try:
        val, _ = ttlcache.get(("next_race_s", school, state, _today().isoformat()), compute,
                              ttl=U.TTL, ttl_of=lambda v: U.TTL if v.get("available") else _FAIL_TTL)
    except Exception as exc:                            # noqa: BLE001
        print(f"next race school {school}: {type(exc).__name__}: {exc}", flush=True)
        val = {"available": False}
    return _publicJson(val)


# ------------------------------------------------------------------ #
#  6. meet preview
# ------------------------------------------------------------------ #

def _src():
    s = (request.args.get("src") or "").strip().lower()
    return "tfrrs" if s == "tfrrs" else None


@bp.route("/meet/preview/xc/<int:meet_id>")
def meet_preview(meet_id):
    import upcoming_preview as U
    src = _src()
    with getConn() as conn:
        with _cursor(conn) as cur:
            pv = U.meetPreview(cur, meet_id, src)
            conn.rollback()
    if pv is None:
        abort(404)
    # ! A MEET THAT HAS RUN IS NOT A PREVIEW: its results page is the answer,
    #   or, when its forecast was stored, the recap of that forecast
    if pv.get("date") and str(pv["date"])[:10] < _today().isoformat():
        if pv.get("frozen"):
            from meet_recap import recapHref
            return redirect(recapHref(meet_id, pv["source"]), code=302)
        return redirect(f"/meet/xc/{meet_id}", code=302)
    card = f"meet_id={meet_id}&sport=XC"
    if pv.get("lead_div") is not None:
        card += f"&div_id={pv['lead_div']}"
    if pv["source"] == "tfrrs":
        card += "&src=tfrrs"
    return render_template("meet_preview.html", pv=pv, card_query=card,
                           src=pv["source"] if pv["source"] == "tfrrs" else None)


@bp.route("/api/meet-preview/xc/<int:meet_id>/<int:div_id>")
def api_meet_preview_race(meet_id, div_id):
    import upcoming_preview as U
    src = _src()
    try:
        with getConn() as conn:
            with _cursor(conn) as cur:
                pred = U.racePrediction(cur, meet_id, div_id, src)
                conn.rollback()
    except Exception as exc:                            # noqa: BLE001
        print(f"meet preview {meet_id}/{div_id}: {type(exc).__name__}: {exc}", flush=True)
        pred = {"available": False, "reason": "The prediction could not be made."}
    if not pred.get("available"):
        return _publicJson({"available": False, "reason": pred.get("reason")})
    teams = [dict(t, place=i + 1) for i, t in
             enumerate(t for t in pred["teams"] if t.get("score") is not None)]
    # ★ THE 5K COLUMN (owner, 2026-10-10): each projected runner's rating as
    #   the track 5K it is worth, and the header for the field's pools
    from conversions import fiveKLabel
    shown = pred["runners"][:RUNNERS_SHOWN]
    fks = [U.fiveKOf(r.get("rating"), r.get("pool")) for r in shown]
    out = {"available": True, "n_field": pred["n_field"],
           "teams": [{"team": t["team"], "label": t.get("school_label") or t["team"],
                      "href": t.get("school_href"), "state": t.get("state"),
                      "score": t["score"], "place": t["place"],
                      "scorers": [s.get("score_place") for s in t["scorers"][:5]]}
                     for t in teams[:TEAMS_SHOWN]],
           "n_teams": len(teams),
           "runners": [{"person_id": r["person_id"], "name": r.get("name"),
                        "school": r.get("school_label") or r.get("school"),
                        "href": r.get("school_href"), "grade": r.get("grade_label"),
                        "place": r.get("place"), "time": U.clock(r.get("seconds")),
                        "five_k": fk}
                       for r, fk in zip(shown, fks)],
           "fk_label": fiveKLabel([r.get("pool") for r, fk in zip(shown, fks) if fk]),
           "frozen": (pred.get("frozen") or {}).get("made_on"),
           "predict_href": U.predictHref(meet_id, div_id, src)}
    return _publicJson(out)


# What the preview lists of a predicted race: the scoring teams a results
# page's sidebar shows above the fold, and the individuals a meet card's
# "top" list shows. The full field is a click away on /predictions.
TEAMS_SHOWN = 15
RUNNERS_SHOWN = 25


# ------------------------------------------------------------------ #
#  7. movers
# ------------------------------------------------------------------ #

def _stateNames():
    from landing import STATE_NAMES, US_STATES
    return [(c, STATE_NAMES[c]) for c in US_STATES if c in STATE_NAMES], STATE_NAMES


@bp.route("/movers")
def movers_page():
    import breakouts as B
    import movers as M
    states, names = _stateNames()
    st = (request.args.get("state") or "").strip().upper() or None
    if st and st not in names:
        st = None
    pool = (request.args.get("pool") or "hs_m").strip().lower()
    if pool not in M.LEVEL_OF:
        pool = "hs_m"
    with getConn() as conn:
        with _cursor(conn) as cur:
            sport_arg = (request.args.get("sport") or "").strip().lower()
            sport = B.SPORTS.get(sport_arg) or B.defaultSport(B.latestDates(cur))
            d = M.digest(cur, sport, pool, st) if st else None
            conn.rollback()
    level = M.LEVEL_OF[pool]
    words = B.LEVELS[level][1]
    lede = M.sentences(d, names[st], d["window"]) if d else []
    return render_template("movers.html", d=d, state=st,
                           state_name=names.get(st or "", ""), states=states,
                           pool=pool, pools=[(p, B.LEVELS[l][1]) for p, l in M.LEVEL_OF.items()],
                           level=level, words=words, sport=sport, lede=lede)


# ------------------------------------------------------------------ #
#  8. team season tracker
# ------------------------------------------------------------------ #

@bp.route("/school/<path:school_name>/season")
def team_season_page(school_name):
    import team_tracker as T
    from panels import isTeamName
    import school_identity
    if not isTeamName(school_name):
        abort(404)
    state = (request.args.get("state") or "").strip().upper()[:2] or None
    pool = (request.args.get("pool") or "hs_m").strip().lower()
    if pool not in T.POOLS:
        pool = "hs_m"
    if not state:
        state = school_identity.primaryState(school_name)
    with getConn() as conn:
        with _cursor(conn) as cur:
            data = T.teamSeason(cur, school_name, state, pool) if state else None
            conn.rollback()
    if data is None:
        abort(404)
    return render_template("team_season.html", school=school_name, state=state,
                           pool=pool, pools=T.POOLS, data=data)


# ------------------------------------------------------------------ #
#  9. runners like you, on the chart
# ------------------------------------------------------------------ #

@bp.route("/api/runners-like-you/<int:person_id>")
def api_runners_like_you(person_id):
    from comps import nextSeasonFan, runnersLikeYou
    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        sport = "XC"

    def compute():
        from predict import _currentSeason
        with getConn() as conn:
            with _cursor(conn) as cur:
                fan = nextSeasonFan(runnersLikeYou(cur, person_id, sport))
                # ★ ONLY A NEXT SEASON THAT HAS NOT HAPPENED. The comps are
                #   read off the athlete's newest HS season; when that is an
                #   old one, the real next season is already on the chart.
                if fan and fan["year"] - 1 != int(_currentSeason(cur, sport)):
                    fan = None
                conn.rollback()
                return {"available": bool(fan), "fan": fan}
    try:
        val, _ = ttlcache.get(("rly_fan", person_id, sport), compute, ttl=6 * 3600.0,
                              ttl_of=lambda v: 6 * 3600.0 if v.get("available") else _FAIL_TTL)
    except Exception as exc:                            # noqa: BLE001
        print(f"runners like you {person_id}: {type(exc).__name__}: {exc}", flush=True)
        val = {"available": False}
    return _publicJson(val)


# ------------------------------------------------------------------ #
#  11. the recruiting one-pager
# ------------------------------------------------------------------ #

@bp.route("/athlete/<int:person_id>/profile")
def athlete_profile(person_id):
    import profile_page as PP

    def compute():
        with getConn() as conn:
            with _cursor(conn) as cur:
                out = PP.profileData(cur, person_id)
                conn.rollback()
                return out
    p, _ = ttlcache.get(("profile", person_id), compute, ttl=6 * 3600.0,
                        ttl_of=lambda v: 6 * 3600.0 if v else _FAIL_TTL)
    if p is None:
        abort(404)
    return render_template("athlete_profile.html", p=p)


# ------------------------------------------------------------------ #
#  12. how we did: the recap of a stored forecast (2026-10-10)
# ------------------------------------------------------------------ #

@bp.route("/meet/recap/xc/<int:meet_id>")
def meet_recap(meet_id):
    import meet_recap as R
    src = _src()

    def compute():
        with getConn() as conn:
            with _cursor(conn) as cur:
                out = R.meetRecap(cur, meet_id, src)
                conn.rollback()
                return out or {"none": True}
    rc, _ = ttlcache.get(("recap", src, meet_id, _today().isoformat()), compute,
                         ttl=R.TTL, ttl_of=lambda v: _FAIL_TTL if v.get("none") else R.TTL)
    if rc.get("none"):
        abort(404)
    # ! NOT RUN YET: the forecast is the preview's to show
    if rc.get("date") and rc["date"] > _today().isoformat():
        from upcoming_preview import previewHref
        return redirect(previewHref(meet_id, rc["source"]), code=302)
    return render_template("meet_recap.html", rc=rc,
                           src=rc["source"] if rc["source"] == "tfrrs" else None,
                           shown=R.TABLE_SHOWN, top_n=R.TOP_N, min_field=R._minField())


@bp.route("/recaps")
def recaps_page():
    import meet_forecast as MF
    import meet_recap as R
    states, names = _stateNames()
    st = (request.args.get("state") or "").strip().upper() or None
    if st and st not in names:
        st = None
    with getConn() as conn:
        with _cursor(conn) as cur:
            # ★ NO WEEK ASKED: the newest week with a scored race, so the
            #   page opens on results rather than on an empty week
            default = MF.latestScoredDate(cur) or _today()
            week = R.parseWeek(request.args.get("week"), default)
            data = R.weekRecaps(cur, week, st)
            conn.rollback()
    one = datetime.timedelta(days=7)
    nxt = week + one
    return render_template("recaps.html", data=data, state=st,
                           state_name=names.get(st or "", ""), states=states,
                           prev_week=(week - one).isoformat(),
                           next_week=nxt.isoformat() if nxt <= _today() else None,
                           min_field=R._minField(), top_n=R.TOP_N)


@bp.route("/card/recap/xc/<int:meet_id>.png")
def card_recap(meet_id):
    import cards
    from app import _serveCard
    src = _src()
    return _serveCard(f"recap xc {meet_id}",
                      lambda cur: cards.cachedRecapCard(cur, meet_id, src))
