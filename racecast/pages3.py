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
import os
import threading

import psycopg2.extras
from flask import (Blueprint, abort, jsonify, redirect, render_template,
                   request)

import ttlcache
from database import getConn

bp = Blueprint("pages3", __name__)

_FAIL_TTL = 300.0


# ★ THE NEXT-RACE LINE NEVER HOLDS A WORKER (2026-11-07: /api/next-race/
#   athlete at 60.5 s -- a cold worker predicting a meet's every race inline,
#   killed at gunicorn's 60 s, so the reader got an error and the line stayed
#   hidden anyway, while the worker served nobody else). The compute runs in
#   a background thread; the request waits up to NEXT_RACE_WAIT seconds for
#   it and otherwise answers "not yet" (no-store, so the next view asks
#   again and finds it). next-race.js hides the line for any answer that is
#   not available, exactly as for a failure.
# ! ONE BACKGROUND COMPUTE PER WORKER AT A TIME: a worker has three database
#   connections, and a crawler walking athlete pages must not start a
#   prediction per page.
NEXT_RACE_WAIT = float(os.environ.get("XCP_NEXT_RACE_WAIT", "4"))
_BG = {"busy": False}
_BG_LOCK = threading.Lock()


def _soonOrLater(key, compute, ttl, ttl_of, wait=None):
    """The cached value for `key`; else compute it in the background and
    wait up to `wait` seconds for it. None when it is not ready (or another
    background compute holds this worker's slot)."""
    hit = ttlcache.peek(key)
    if hit is not None:
        return hit
    with _BG_LOCK:
        if _BG["busy"]:
            return None
        _BG["busy"] = True
    box, done = {}, threading.Event()

    def run():
        try:
            box["v"], _ = ttlcache.get(key, compute, ttl=ttl, ttl_of=ttl_of)
        except Exception as exc:                        # noqa: BLE001
            print(f"next race {key}: {type(exc).__name__}: {exc}", flush=True)
            box["v"] = {"available": False}
        finally:
            with _BG_LOCK:
                _BG["busy"] = False
            done.set()
    threading.Thread(target=run, daemon=True, name="next-race").start()
    done.wait(NEXT_RACE_WAIT if wait is None else wait)
    return box.get("v")


def _notYet():
    resp = jsonify({"available": False, "pending": True})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _publicJson(payload):
    """jsonify with the pages' public cache header (app.PAGE_MAX_AGE /
    PAGE_S_MAXAGE): the edge holds what ttlcache computed."""
    from app import PUBLIC_CACHE_CONTROL
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = PUBLIC_CACHE_CONTROL
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
    val = _soonOrLater(("next_race_a", person_id, _today().isoformat()), compute,
                       ttl=U.TTL, ttl_of=lambda v: U.TTL if v.get("available") else _FAIL_TTL)
    return _notYet() if val is None else _publicJson(val)


@bp.route("/api/next-race/school")
def api_next_race_school():
    import upcoming_preview as U
    school = (request.args.get("school") or "").strip()[:200]
    state = (request.args.get("state") or "").strip().upper()[:2] or None
    level = (request.args.get("level") or "").strip().lower()[:16] or None
    if not school:
        return jsonify({"error": "school is required"}), 400

    def compute():
        with getConn() as conn:
            with _cursor(conn) as cur:
                out = U.schoolNextRaces(cur, getConn, school, state, _today(), level=level)
                conn.rollback()
                return out
    val = _soonOrLater(("next_race_s", school, state, level, _today().isoformat()), compute,
                       ttl=U.TTL, ttl_of=lambda v: U.TTL if v.get("available") else _FAIL_TTL)
    return _notYet() if val is None else _publicJson(val)


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
        card += "&src=2"
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


# ------------------------------------------------------------------ #
#  9. units ranked against each other (owner, 2026-11-02)
# ------------------------------------------------------------------ #

UNIT_POOLS = (("college_m", "College men"), ("college_f", "College women"),
              ("hs_m", "HS boys"), ("hs_f", "HS girls"))


@bp.route("/rankings/units")
def units_page():
    """The old standalone board: the Conferences tab of /rankings now
    (owner, 2026-11-02), with the same filters carried over."""
    from urllib.parse import urlencode
    q = {k: v for k, v in request.args.items() if v}
    q["board"] = "units"
    if "kind" in q:
        q["unit_kind"] = q.pop("kind")
    return redirect("/rankings?" + urlencode(q), code=301)


def _first(args, key):
    v = (args.get(key) or "").strip()
    return v.split(",")[0].strip() if v else ""


@bp.route("/api/rankings/units")
def api_units():
    """The Conferences tab (unit_board.buildBoard): one row per conference,
    league, section or division, ranked by the average of its best N teams;
    each row carries its member teams for the expanded view."""
    import unit_board as UB
    from rankings import boardYear, nameLateral
    from pool_view import stampBoardRows
    from conversions import stampFiveK
    from school_logo import stampCrests, crestUrl
    from school_identity import levelOf
    args = request.args
    pool = (args.get("pool") or "college_m").strip().lower()
    if pool not in dict(UNIT_POOLS):
        return jsonify({"error": "Conferences and leagues rank college and high school "
                                 "teams: pick a college or high school level."}), 400
    college = pool.startswith("college")
    kinds = [(k, v[0]) for k, v in UB.KINDS.items() if v[1] == college]
    kind = (args.get("unit_kind") or "").strip().lower()
    if kind not in dict(kinds):
        kind = kinds[0][0]
    sport = "TF" if (args.get("sport") or "").strip().upper() == "TF" else "XC"
    st = _first(args, "state").upper() or None
    try:
        n = max(1, min(20, int(args.get("unit_n") or UB.DEFAULT_N)))
    except ValueError:
        n = UB.DEFAULT_N
    within = {c: _first(args, c) for c in UB.WITHIN[college] if c != kind and _first(args, c)}
    if UB.KINDS[kind][2] and not st:
        return jsonify({"rows": [], "kind": kind, "kinds": kinds, "hs_movable": True,
                        "note": f"{UB.KINDS[kind][0]} share names from state to state: "
                                f"pick a state to rank them."})
    with getConn() as conn:
        with _cursor(conn) as cur:
            from app import _seasonYears
            # ! the year filter carries the STORED academic year, as on every
            #   board; with none, the season in progress (a label, converted)
            asked = _first(args, "year")
            if asked.isdigit():
                year, ylab = int(asked), asked
            else:
                ylab = (_seasonYears(cur) or {}).get(sport)
                year = boardYear(sport, ylab) if ylab else None
            if year is None:
                return jsonify({"error": "No current season is known for this sport."}), 400
            grades = [g for g in args.getlist("grade") if g.strip()] if hasattr(args, "getlist") else []
            try:
                if grades:
                    rows = gradeSquads(cur, args, pool, sport, year, kind, st, within)
                    if rows is None:
                        return jsonify({"error": "Too many runners in those grades to race "
                                                 "this board: narrow it with a division "
                                                 "or a state."}), 200
                else:
                    rows = UB.load(cur, kind, pool, sport, year, st, within)
                valid = UB.validKeys(cur, kind, college)
                units = UB.buildBoard(rows, kind, n, valid)
                ids = sorted({u["runner_id"] for u in units if u.get("runner_id")})
                names = {}
                if ids:
                    cur.execute(f"""SELECT s.person_id, a.name FROM unnest(%s::bigint[]) AS s(person_id)
                                    {nameLateral("s")}""", (ids,))
                    names = {r["person_id"]: r["name"] for r in cur.fetchall()}
            except Exception as exc:                     # noqa: BLE001
                conn.rollback()
                return jsonify({"error": f"Conference rankings failed to load "
                                         f"({type(exc).__name__})."}), 500
            conn.rollback()
    UB.sortBoard(units, (args.get("sort") or "avg").strip().lower(),
                 (args.get("dir") or "").strip().lower() or None)
    # ★ FIND ONE ON THE BOARD (owner, 2026-11-04: "you should be able to
    #   search up things"): ranked first, named second, so a conference
    #   found keeps the rank it holds among all of them
    find = (args.get("q") or "").strip().lower()
    if find:
        units = [u for u in units
                 if find in f"{u.get('name') or ''} {u.get('unit') or ''}".lower()]
    level = levelOf(pool)
    for u in units:
        u["runner_name"] = names.get(u.get("runner_id"))
        u["href"] = (UB.regionHref(u["unit"], pool, sport) if kind == "nxr"
                     else UB.unitHref(kind, u["unit"], u["state"] or st, pool, sport))
        u["mark"] = UB.unitMark(kind, u["unit"], u["state"] or st, size=22, shown=u["name"])
        u["best_crest"] = (crestUrl(u["best_team"], u["best_state"], px=64, level=level)
                           if u.get("best_team") else None)
        stampCrests(u["members"], pool=pool)
        stampBoardRows(u["members"], rating_keys=("top5_mean",), pool=pool, sport=sport)
    movable = stampBoardRows(units, rating_keys=("avg", "best_avg", "runner_rating"),
                             pool=pool, sport=sport)
    stampFiveK(units, "avg", pool=pool, sport=sport)
    try:
        off = max(0, int(args.get("offset") or 0))
        lim = max(1, min(200, int(args.get("limit") or 100)))
    except ValueError:
        off, lim = 0, 100
    total = len(units)
    units = units[off:off + lim]
    return jsonify({"rows": units, "kind": kind, "kinds": kinds, "n": n, "total": total,
                    "hs_movable": movable, "season": ylab, "within": within,
                    "grades": grades,
                    "kind_word": UB.KINDS[kind][0][:-1]})


def gradeSquads(cur, args, pool, sport, year, kind, state, within):
    """The Conferences board built from SQUADS OF THE PICKED GRADES (owner,
    2026-11-03: "make it work there", the grade filter): every school's team
    of just those grades, raced from athlete_season by the Teams tab's own
    code (teams.raceReturning), each carrying its units, then narrowed to
    the `within` units and the state as UB.load narrows the stored board.
    None when the field is too big to race."""
    from werkzeug.datastructures import MultiDict
    import teams as T
    import unit_board as UB
    q = MultiDict([("sport", sport), ("pool", pool), ("year", str(year))]
                  + [("grade", g) for g in args.getlist("grade") if g.strip()])
    f, err = T.parseFilters(q)
    if err:
        raise ValueError(err)
    rows, _n = T.raceReturning(cur, f)
    if rows is None:
        return None
    out = []
    for r in rows:
        r = dict(r)
        r.update({k: v for k, v in (r.pop("units", None) or {}).items()})
        if kind == "nxr":
            r["nxr"] = UB.STATE_NXR.get((r.get("state") or "").upper())
        if not r.get(kind):
            continue
        if state and (r.get("state") or "").upper() != state:
            continue
        if any((r.get(c) or "") != v for c, v in (within or {}).items()):
            continue
        out.append(r)
    return out


_WITHIN_LABELS = {"division": "Division", "region": "Region", "section": "Section",
                  "state_div": "State division"}


_UNIT_ATHLETES = """
    SELECT s.person_id, s.school, s.state, s.mean_rating, s.n_races, s.grade,
           a.name
    FROM   athlete_season s
    {names}
    WHERE  s.pool = %(pool)s AND s.sport = %(sport)s AND s.year = %(year)s
      AND  upper(s.{col}) = ANY(%(spell)s) AND s.mean_rating IS NOT NULL
      {state}
    ORDER  BY s.mean_rating DESC
    LIMIT  100
"""

# the meets several of the unit's teams ran this season: its championship,
# its duals and invitationals. ranking_results by (school, sport), indexed.
_UNIT_MEETS = """
    SELECT x.meet_id, x.div_id, min(x.race_date)::text AS day,
           count(DISTINCT x.school) AS n_teams, count(*) AS n_runners,
           COALESCE((SELECT m.meet_name FROM meets m
                     WHERE m.meet_id = x.meet_id AND m.div_id = x.div_id LIMIT 1),
                    (SELECT t.meet_name FROM meets_tfrrs t
                     WHERE t.meet_id = x.meet_id AND t.sport = 'XC' LIMIT 1)) AS meet_name
    FROM   ranking_results x
    WHERE  x.sport = 'XC' AND x.school = ANY(%(schools)s) AND x.pool = %(pool)s
      AND  x.year = %(year)s
    GROUP  BY x.meet_id, x.div_id
    HAVING count(DISTINCT x.school) >= %(min_teams)s
    ORDER  BY min(x.race_date) DESC
    LIMIT  60
"""


@bp.route("/unit/<kind>/<path:name>")
def unit_page(kind, name):
    """One conference, league, section or division, laid out like a school
    page (owner, 2026-11-02: "the actual conference page kind of sucks too.
    Maybe should be more like the school page"): its mark and facts, the
    sport and season pickers, then Standings, Best runners and Meets."""
    import unit_board as UB
    from rankings import boardYear, nameLateral
    from pool_view import stampBoardRows
    from conversions import stampFiveK
    from school_logo import stampCrests
    from school_identity import levelOf
    if kind not in UB.KINDS:
        abort(404)
    college = UB.KINDS[kind][1]
    # a high school "conference" (school_unit carries them: Delaware's New
    # Castle County) is a high school unit -- its chips link with pool=hs_*
    if college and (request.args.get("pool") or "").strip().lower().startswith("hs"):
        college = False
    states, names = _stateNames()
    st = (request.args.get("state") or "").strip().upper() or None
    if st and st not in names:
        st = None
    if not college and not st:
        abort(404)                # a high school unit is a name inside a state
    pools = [p for p in UNIT_POOLS if p[0].startswith("college") == college]
    pool = (request.args.get("pool") or pools[0][0]).strip().lower()
    if pool not in dict(pools):
        pool = pools[0][0]
    sport = "TF" if (request.args.get("sport") or "").strip().lower() in ("tf", "track") else "XC"
    key = UB.normKey(name)
    teams, athletes, meets, years, place, n_units, label, year = [], [], [], [], None, 0, None, None
    parent = {}
    with getConn() as conn:
        with _cursor(conn) as cur:
            from app import _seasonYears
            asked = (request.args.get("year") or "").strip()
            if asked.isdigit():
                year = int(asked)
            else:
                lab = (_seasonYears(cur) or {}).get(sport)
                year = boardYear(sport, lab) if lab else None
            if year is not None:
                try:
                    every_rows = UB.load(cur, kind, pool, sport, year, st if not college else None)
                    teams = UB.dropOddDivision(
                        [t for t in every_rows if UB.normKey(t.get(kind)) == key], kind)
                    spell = sorted({(t.get(kind) or "").strip().upper() for t in teams} | {name.upper()})
                    teams.sort(key=lambda t: (t["top5_mean"] is None, -(t["top5_mean"] or 0)))
                    for c in UB.WITHIN[college]:
                        if c != kind and teams and teams[0].get(c):
                            parent[c] = teams[0][c]
                            break
                    valid = UB.validKeys(cur, kind, college)
                    board = UB.buildBoard([t for t in every_rows
                                           if all(t.get(c) == v for c, v in parent.items())],
                                          kind, UB.DEFAULT_N, valid)
                    UB.sortBoard(board, "avg")
                    n_units = len(board)
                    place = next((u for u in board if u["key"] == key), None)
                    cur.execute(_UNIT_ATHLETES.format(
                        col=kind, names=nameLateral("s"),
                        state="AND s.state = %(state)s" if not college else ""),
                        {"pool": pool, "sport": sport, "year": year, "spell": spell, "state": st})
                    athletes = [dict(r) for r in cur.fetchall()]
                    cur.execute(f"""SELECT DISTINCT year FROM team_season
                                    WHERE span = 'season' AND scope = 'usa' AND pool = %s
                                      AND sport = %s AND upper({kind}) = ANY(%s)
                                    ORDER BY year DESC LIMIT 12""", (pool, sport, spell))
                    years = [r["year"] for r in cur.fetchall()]
                    ids = sorted({t["best_person_id"] for t in teams if t.get("best_person_id")})
                    if ids:
                        cur.execute(f"""SELECT s.person_id, a.name FROM unnest(%s::bigint[]) AS s(person_id)
                                        {nameLateral("s")}""", (ids,))
                        nm = {r["person_id"]: r["name"] for r in cur.fetchall()}
                        for t in teams:
                            t["best_name"] = nm.get(t.get("best_person_id"))
                    if sport == "XC" and teams:
                        try:
                            cur.execute("SAVEPOINT unit_meets")
                            cur.execute("SET LOCAL statement_timeout = '4s'")
                            cur.execute(_UNIT_MEETS, {"schools": sorted({t["school"] for t in teams}),
                                                      "pool": pool, "year": year,
                                                      "min_teams": min(3, len(teams))})
                            meets = [dict(r) for r in cur.fetchall()]
                            cur.execute("RELEASE SAVEPOINT unit_meets")
                        except Exception:                # noqa: BLE001 -- the tab is optional
                            cur.execute("ROLLBACK TO SAVEPOINT unit_meets")
                            meets = []
                except Exception:                        # noqa: BLE001
                    conn.rollback()
            conn.rollback()
    if not teams and not athletes:
        abort(404)
    stampBoardRows(teams, rating_keys=("top5_mean", "best_rating"), pool=pool, sport=sport)
    stampFiveK(teams, "top5_mean", pool=pool, sport=sport)
    stampCrests(teams, pool=pool)
    stampBoardRows(athletes, rating_keys=("mean_rating",), pool=pool, sport=sport)
    stampFiveK(athletes, "mean_rating", pool=pool, sport=sport)
    if place:
        stampBoardRows([place], rating_keys=("avg",), pool=pool, sport=sport)
    stored = teams[0][kind] if teams else name
    shown = UB.prettyName(UB.label(kind, stored, st))
    board_href = (f"/rankings?board=units&unit_kind={kind}&pool={pool}&sport={sport}"
                  + (f"&state={st}" if st else "")
                  + "".join(f"&{c}={v}" for c, v in parent.items()))
    return render_template(
        "unit.html", kind=kind, kind_label=UB.KINDS[kind][0][:-1], unit=shown, stored=stored,
        state=st, state_name=names.get(st or "", ""), pool=pool, pools=pools, sport=sport,
        year=year, years=years, teams=teams, athletes=athletes, meets=meets, place=place,
        n_units=n_units, parent=parent, college=college,
        mark=UB.unitMark(kind, stored, st, size=44, shown=shown), level=levelOf(pool),
        board_href=board_href,
        n_runners=sum(int(t.get("n_athletes") or 0) for t in teams),
        top25=sum(1 for t in teams if t.get("rank") and int(t["rank"]) <= UB.TOP_TEAMS),
        self_href=lambda p=pool, s=sport, y=None: UB.unitHref(kind, stored, st, p, s)
        + (f"&year={y}" if y else ""))
