# Project: xc-predictor / racecast
# File:    card_routes.py
# Purpose: the share-card routes, /card/*.png: each page's og:image and what
#          its Share button hands over. The drawing is cards.py; these are
#          the thin routes that serve it. A blueprint, as pages2.py and
#          pages3.py are; app.py carries one register line.
#
#   Moved out of app.py with no change to behaviour (2026-10-24). The two
#   helpers the cards share with the pages (_tf_meet_sources, _loadShare)
#   stay in app.py and are imported where they are used, the way pages3.py
#   imports _serveCard. app.py re-exports _serveCard for pages3's recap card.
#   /img/school/ (the crests) stays in app.py: it is not a card.
import urllib.parse

import psycopg2.extras
from flask import Blueprint, abort, request, url_for

from database import getConn

bp = Blueprint("card_routes", __name__)


@bp.route("/card/athlete/<int:person_id>.png")
def card_athlete(person_id):
    """The athlete's share card (cards.py, 281): the page's og:image, and
    what a Share button hands over. Cached on disk, redrawn every six
    hours. A missing athlete is a 404; a drawing failure is logged and
    falls back to the site-wide image so a preview is never blank."""
    from flask import send_file, redirect as _redirect
    try:
        import cards
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                path = cards.cachedAthleteCard(cur, person_id)
        if path is None:
            abort(404)
        resp = send_file(path, mimetype="image/png", max_age=3600)
        return resp
    except Exception as exc:                          # noqa: BLE001
        if getattr(exc, "code", None) == 404:
            raise
        print(f"card: athlete {person_id} failed ({type(exc).__name__}: {exc})", flush=True)
        return _redirect(url_for("static", filename="og-image.png"))


def _serveCard(kind, build):
    """One card route's body: draw or reuse, serve the PNG; a missing thing
    is a 404, a drawing failure falls back to the site-wide image."""
    from flask import send_file, redirect as _redirect
    try:
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                path = build(cur)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/png", max_age=3600)
    except Exception as exc:                          # noqa: BLE001
        if getattr(exc, "code", None) == 404:
            raise
        print(f"card: {kind} failed ({type(exc).__name__}: {exc})", flush=True)
        return _redirect(url_for("static", filename="og-image.png"))


@bp.route("/card/race/xc/<int:meet_id>/<int:div_id>.png")
def card_race_xc(meet_id, div_id):
    import cards
    return _serveCard(f"race xc {meet_id}/{div_id}",
                      lambda cur: cards.cachedRaceCard(cur, "XC", meet_id, div_id))


@bp.route("/card/race/tf/<int:meet_id>/<int:event_id>/<int:div_id>.png")
def card_race_tf(meet_id, event_id, div_id):
    import cards
    return _serveCard(f"race tf {meet_id}/{event_id}/{div_id}",
                      lambda cur: cards.cachedRaceCard(cur, "TF", meet_id, div_id, event_id))


@bp.route("/card/meet/xc/<int:meet_id>.png")
def card_meet_xc(meet_id):
    import cards
    return _serveCard(f"meet xc {meet_id}", lambda cur: cards.cachedMeetCard(cur, meet_id))


@bp.route("/card/school/<path:school_name>.png")
def card_school(school_name):
    import cards
    state = (request.args.get("state") or "").strip().upper()[:2] or None
    return _serveCard(f"school {school_name}",
                      lambda cur: cards.cachedSchoolCard(cur, school_name, state))


@bp.route("/card/board/<sport>/<pool>.png")
@bp.route("/card/board/<sport>/<pool>/<state>.png")
def card_board(sport, pool, state=None):
    import cards
    year = request.args.get("year", type=int)
    st = (state or "").upper() or None
    return _serveCard(f"board {sport}/{pool}/{st}",
                      lambda cur: cards.cachedBoardCard(cur, sport.lower(), pool.lower(), st, year))


@bp.route("/card/board.png")
def card_board_query():
    """The rankings page's card: the board on screen, whatever its filters,
    from the same query the page sends. A board that draws nothing (the
    JavaScript-only teams and courses boards, an empty filter) previews
    with the site image rather than a 404, since this is an og:image."""
    import cards
    from flask import redirect as _redirect
    args = {k: v for k, v in request.args.items() if v}
    try:
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                path = cards.cachedFilteredBoardCard(cur, args)
        if path:
            from flask import send_file
            return send_file(path, mimetype="image/png", max_age=3600)
    except Exception as exc:                          # noqa: BLE001
        print(f"card: board query failed ({type(exc).__name__}: {exc})", flush=True)
    return _redirect(url_for("static", filename="og-image.png"))


@bp.route("/card/meet/tf/<int:meet_id>.png")
@bp.route("/card/meet/tf/<int:meet_id>/<kind>.png")
def card_meet_tf(meet_id, kind="teams"):
    """The team standings (falling back to the best marks), or at
    /<id>/marks.png the best marks always: two cards, both reachable."""
    import cards
    from app import _tf_meet_sources
    kind = "marks" if kind == "marks" else "teams"

    def build(cur):
        src, _alt, _others = _tf_meet_sources(cur, meet_id, request.args)
        return cards.cachedMeetTfCard(cur, meet_id, src, kind)
    return _serveCard(f"meet tf {meet_id} {kind}", build)


@bp.route("/card/predict.png")
def card_predict():
    """The prediction card takes the same query the page sends to
    /api/predict/team, so the page can share what it just showed -- or
    ?s=<id>, a saved one (api_predict_share)."""
    import cards
    from app import _loadShare
    args = {k: v for k, v in request.args.items() if v}
    if args.get("s"):
        q, _ = _loadShare(args["s"].lower())
        if q is None:
            abort(404)
        args = {k: v[0] for k, v in urllib.parse.parse_qs(q).items() if v and v[0]}
    return _serveCard("predict", lambda cur: cards.cachedPredictionCard(cur, args))


@bp.route("/card/course/<path:course_name>.png")
def card_course(course_name):
    import cards
    return _serveCard(f"course {course_name}", lambda cur: cards.cachedCourseCard(cur, course_name))


@bp.route("/card/venue/tf/<int:location_id>/<indoor>.png")
def card_venue_tf(location_id, indoor):
    import cards
    return _serveCard(f"venue tf {location_id}/{indoor}",
                      lambda cur: cards.cachedVenueCard(cur, location_id, indoor == "in"))
