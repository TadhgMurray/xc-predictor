# Project: xc-predictor / racecast
# File:    pages2.py
# Purpose: the routes for two pages the owner approved on 2026-10-04 --
#          "Who wins state?" (/projections, see projections.py) and
#          "Breakouts this week" (/breakouts, see breakouts.py).
#
# ★ A BLUEPRINT, NOT MORE app.py. app.py is nine thousand lines and three
#   people edit it at once; these pages need nothing from it but the
#   connection pool and the template globals, both of which a blueprint gets
#   for free. app.py carries one register line.
#
# ! PUBLIC AND CACHEABLE. Neither path is in app._PRIVATE_PREFIXES, so both
#   go out with the site's public s-maxage header like every board -- the
#   edge absorbs the views and ttlcache absorbs the edge's misses.
import time

import psycopg2.extras
from flask import Blueprint, abort, redirect, render_template, request

import breakouts as B
import projections as P
from database import getConn

bp = Blueprint("pages2", __name__)


@bp.app_template_filter("proj_time")
def _projTime(seconds):
    return P.fmtTime(seconds)


@bp.app_template_filter("ago_words")
def _agoWords(stamp):
    """'12 minutes ago' for a ttlcache stamp."""
    if not stamp:
        return ""
    mins = int(max(0, time.time() - stamp) // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hrs = mins // 60
    return f"{hrs} hour{'s' if hrs != 1 else ''} ago"


def _stateNames():
    from landing import STATE_NAMES, US_STATES
    return [(c, STATE_NAMES[c]) for c in US_STATES if c in STATE_NAMES], \
        STATE_NAMES


# ------------------------------------------------------------------ #
#  /projections
# ------------------------------------------------------------------ #

def _gender():
    g = (request.args.get("g") or "boys").strip().lower()
    return g if g in P.GENDERS else "boys"


@bp.route("/projections")
def projections_index():
    states, _names = _stateNames()
    return render_template("projections.html", mode="index",
                           states=states, gender=_gender())


@bp.route("/projections/<state>")
def projections_state(state):
    states, names = _stateNames()
    st = (state or "").upper()
    if st not in names:
        abort(404)
    if state != st.lower():
        return redirect(f"/projections/{st.lower()}", code=301)
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            divisions = P.divisionsFor(cur, st)
    return render_template("projections.html", mode="state", state=st,
                           state_name=names[st], states=states,
                           divisions=divisions, gender=_gender())


@bp.route("/projections/<state>/<division>")
def projections_page(state, division):
    states, names = _stateNames()
    st = (state or "").upper()
    if st not in names:
        abort(404)
    want = P.divisionSlug(division)
    if state != st.lower() or division != want:
        q = ("?" + request.query_string.decode("utf-8", "replace")
             if request.query_string else "")
        return redirect(f"/projections/{st.lower()}/{want}{q}", code=301)
    gender = _gender()
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            divisions = P.divisionsFor(cur, st)
            if want == "all":
                div = None
            else:
                div = next((d for d in divisions if d["slug"] == want), None)
                if div is None:
                    # ! A 404, NOT AN EMPTY PAGE: an invented slug is not a
                    #   division, and a crawler should not index one.
                    abort(404)
            proj = P.project(cur, st, div, gender)
    label = div["label"] if div else f"{st} all divisions"
    return render_template("projections.html", mode="projection",
                           state=st, state_name=names[st], states=states,
                           divisions=divisions, division=div,
                           division_slug=want, division_label=label,
                           gender=gender, gender_words=P.GENDERS[gender][1],
                           proj=proj, thin_races=P.THIN_RACES,
                           per_school=P.PER_SCHOOL)


# ------------------------------------------------------------------ #
#  /breakouts
# ------------------------------------------------------------------ #

@bp.route("/breakouts")
def breakouts_page():
    states, names = _stateNames()
    level = (request.args.get("level") or "hs-boys").strip().lower()
    if level not in B.LEVELS:
        level = "hs-boys"
    st = (request.args.get("state") or "").strip().upper() or None
    if st and st not in names:
        st = None
    try:
        days = int(request.args.get("days") or B.WINDOW_DAYS)
    except ValueError:
        days = B.WINDOW_DAYS
    if days not in B.WINDOW_CHOICES:
        days = B.WINDOW_DAYS
    units = B.unitArgs(request.args)
    q = (request.args.get("q") or "").strip()[:B.SEARCH_MAX] or None
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            latest = B.latestDates(cur)
            sport_arg = (request.args.get("sport") or "").strip().lower()
            sport = B.SPORTS.get(sport_arg) or B.defaultSport(latest)
            # ★ THE PRECOMPUTED TABLE FIRST (build_breakouts.py): every
            #   filter is a read. The live compute is the fallback for a
            #   server that has not built it yet -- it knows no units or
            #   search, and says so.
            data = B.fromTable(cur, sport, level, days, st, units, q)
            live = data is None
            if live:
                data = B.compute(cur, sport, level, min(days, 14))
                breakouts = B.pickRows(data["breakouts"], "jump", st)
                prs = B.pickRows(data["prs"], "gain", st)
            else:
                breakouts, prs = data["breakouts"], data["prs"]
    return render_template(
        "breakouts.html", data=data, level=level,
        level_words=B.LEVELS[level][1], levels=B.LEVELS,
        sport=sport, state=st, state_name=names.get(st or "", ""),
        states=states, days=days, window_choices=B.WINDOW_CHOICES,
        units=units, q=q or "", live=live,
        breakouts=breakouts, prs=prs,
        min_prior=B.MIN_PRIOR, min_jump=B.MIN_JUMP, check_jump=B.CHECK_JUMP,
        check_gain=B.CHECK_GAIN)
