# Project: xc-predictor / racecast
# File:    ncaa_pages.py
# Purpose: the NCAA championships projection pages (owner approved
#          2026-10-10):
#
#     /ncaa                                the six boards
#     /ncaa/<d1|d2|d3>/<men|women>         Regions / At-large / Nationals /
#                                          How selection works
#     /card/ncaa/<division>/<gender>.png   the share card
#
# ★ THE PAGE COMPUTES NOTHING. build_ncaa_projection.py (pipeline step 11c)
#   runs the regionals and the selection and stores one payload per board
#   per week; this reads the newest and the one before it, so every number
#   on the page is the stored one and "since last week" is a subtraction.
#
# ★ A BLUEPRINT, AS pages2.py AND pages3.py ARE: app.py carries one
#   register line.
import psycopg2.extras
from flask import Blueprint, abort, render_template, send_file, redirect, url_for

import ncaa_rules as NR
import ttlcache
from database import getConn

bp = Blueprint("ncaa_pages", __name__)

_TTL = 600.0

# The selection log's reasons, in words a reader can follow.
WHY = {
    "points": "most wins over teams already in",
    "h2h": "tied on wins; won the head-to-head",
    "common": "tied on wins and head-to-head; better against common opponents",
    "place": "tied; finished higher at its regional",
    "gap": "tied; closer to its region's last automatic qualifier",
    "strength": "tied on every listed step; our projected team strength decided "
                "(the committee's own call)",
    "push": "pushed in: the team behind it had the wins to go next",
    "net wins": "no losing record against the other teams under consideration "
                "(head to head and through common opponents)",
    "point gap ratio": "the closest regional point gap to an at-large team ahead",
    "committee (wins over field)": "committee's call -- modeled as most wins over the field",
    "committee (place)": "committee's call -- modeled as regional place",
    "committee (strength)": "committee's call -- modeled as projected team strength",
}


def _load(cur, div, gender):
    """[(week, computed_at, payload)] newest first, at most two; [] when the
    table does not exist yet."""
    cur.execute("SELECT to_regclass('public.ncaa_projection') AS t")
    row = cur.fetchone()
    if not row or row["t"] is None:
        return []
    cur.execute("""SELECT week, computed_at, payload FROM ncaa_projection
                   WHERE division = %s AND gender = %s
                   ORDER BY year DESC, week DESC LIMIT 2""", (div, gender))
    return [(r["week"], r["computed_at"], r["payload"]) for r in cur.fetchall()]


def _teamHref(t):
    from urllib.parse import quote
    href = "/school/" + quote(t["school"] or "", safe="")
    return href + (f"?state={t['state']}" if t.get("state") else "")


def viewModel(payload, prev=None):
    """Everything the template draws, from a stored payload (and last
    week's, for the movement). Pure -- the mock and the tests call it."""
    if not payload:
        return None
    teams = [dict(t, id=i, href=_teamHref(t)) for i, t in enumerate(payload["teams"])]
    prev_by = {}
    if prev:
        for t in prev.get("teams", []):
            prev_by[(t["school"], t.get("state"))] = t
    for t in teams:
        p = prev_by.get((t["school"], t.get("state")))
        t["d_qual"] = (round(t["p_qual"] - p["p_qual"], 1)
                       if p and p.get("p_qual") is not None and t.get("p_qual") is not None else None)
        t["prev_proj"] = p.get("proj") if p else None
    sel = payload["selection"]
    bubble = set()
    for rd in sel["rounds"]:
        bubble |= {c for c, _pts in rd["candidates"]}
    infield = set(sel["auto"]) | set(sel["at_large"]) | set(sel["pushed"])
    for t in teams:
        t["bubble"] = t["id"] in bubble and t["id"] not in infield

    rounds = []
    for k, rd in enumerate(sel["rounds"], start=1):
        rounds.append({
            "n": k, "pick": teams[rd["pick"]],
            "pushed": teams[rd["pushed"]] if rd.get("pushed") is not None else None,
            "why": WHY.get(rd["why"], rd["why"]),
            "candidates": [(teams[c], pts, c == rd["pick"] or c == rd.get("pushed"))
                           for c, pts in rd["candidates"]],
        })
    ind_by_region = {}
    for ind in payload.get("individuals", []):
        ind_by_region.setdefault(ind["region"], []).append(
            dict(ind, team_row=teams[ind["team"]]))
    regions = []
    for reg in payload["regions"]:
        rows = []
        for k, t in enumerate(reg["order"], start=1):
            rows.append(dict(teams[t], proj_place=k))
        odds = [dict(r, team_row=teams[r["team"]]) for r in reg["individuals"]]
        regions.append({"name": reg["name"], "rows": rows,
                        "individuals": ind_by_region.get(reg["name"], []),
                        "odds": odds})
    wins = []
    for t, rows in payload.get("wins", {}).items():
        tm = teams[int(t)]
        if not (tm["bubble"] or tm["proj"] in ("at-large", "pushed")):
            continue
        detail = [(teams[o], n_season, n_reg) for o, n_season, n_reg in rows]
        wins.append({"team": tm, "season": sum(d[1] for d in detail),
                     "regional": sum(d[2] for d in detail), "detail": detail})
    wins.sort(key=lambda w: (-(w["season"] + w["regional"]), w["team"]["strength_rank"]))
    field = sorted((t for t in teams if t["id"] in infield),
                   key=lambda t: (-(t["p_win"] or 0), t["nat_place"] or 999))
    out_rows = sorted((t for t in teams if t["id"] not in infield and (t["p_qual"] or 0) > 0),
                      key=lambda t: -t["p_qual"])
    moved_in = [t for t in teams if t["id"] in infield and t["prev_proj"] in ("out", None) and prev]
    moved_out = [t for t in teams if t["id"] not in infield
                 and t["prev_proj"] in ("auto", "at-large", "pushed")]
    unplaced = [teams[t] for t in payload.get("unplaced", [])]
    return {"p": payload, "teams": teams, "rounds": rounds, "regions": regions,
            "wins": wins, "field": field, "next_in": out_rows,
            "moved_in": moved_in, "moved_out": moved_out, "unplaced": unplaced,
            "auto": [teams[t] for t in sel["auto"]],
            "at_large": [teams[t] for t in sel["at_large"]],
            "pushed": [teams[t] for t in sel["pushed"]],
            "individuals": payload.get("individuals", [])}


def _boards():
    return [(d, NR.LABEL[d], g, NR.GENDERS[g][2]) for d in NR.DIVISIONS for g in NR.GENDERS]


@bp.route("/ncaa")
def ncaa_index():
    return render_template("ncaa.html", d=None, boards=_boards(), division=None,
                           gender=None, rules=NR.RULES, labels=NR.LABEL)


def _fetch(division, gender):
    def compute():
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                got = _load(cur, division, gender)
                conn.rollback()
        return got
    val, _ = ttlcache.get(("ncaa", division, gender), compute, ttl=_TTL)
    return val


@bp.route("/ncaa/<division>/<gender>")
def ncaa_board(division, gender):
    division, gender = division.lower(), gender.lower()
    if division not in NR.RULES or gender not in NR.GENDERS:
        abort(404)
    got = _fetch(division, gender)
    week = computed = None
    vm = None
    if got:
        week, computed, payload = got[0]
        prev = got[1][2] if len(got) > 1 else None
        vm = viewModel(payload, prev)
        # ago_words (pages2) reads an epoch, as the movers digest stores it
        vm["week"] = week
        vm["computed_at"] = computed.timestamp() if hasattr(computed, "timestamp") else computed
        vm["prev_week"] = got[1][0] if len(got) > 1 else None
    return render_template("ncaa.html", d=vm, boards=_boards(), division=division,
                           gender=gender, rules=NR.RULES, labels=NR.LABEL,
                           rule=NR.RULES[division], why=WHY,
                           gender_word=NR.GENDERS[gender][2])


# ------------------------------------------------------------------ #
#  the share card
# ------------------------------------------------------------------ #

def cardData(payload, division, gender):
    """The card: the projected nationals field's ten likeliest winners."""
    vm = viewModel(payload)
    if not vm or not vm["field"]:
        return None
    rows = []
    for t in vm["field"][:10]:
        rows.append({"name": t["school"],
                     "school": ("auto · " if t["proj"] == "auto" else "at-large · ") + (t["region"] or ""),
                     "grade": "",
                     "rating": f"{t['p_win']:.0f}%" if t["p_win"] >= 1 else "<1%"})
    return {"title": f"NCAA {NR.LABEL[division]} {NR.GENDERS[gender][2]} XC · projected nationals",
            "sub": f"Week of {payload.get('asof') or ''} · unofficial projection · chance to win",
            "rows": rows, "value_label": "WIN"}


@bp.route("/card/ncaa/<division>/<gender>.png")
def ncaa_card(division, gender):
    import cards
    division, gender = division.lower(), gender.lower()
    if division not in NR.RULES or gender not in NR.GENDERS:
        abort(404)
    try:
        got = _fetch(division, gender)
        if not got:
            abort(404)
        week, _c, payload = got[0]

        def build():
            d = cardData(payload, division, gender)
            return cards.renderBoardCard(d) if d else None
        path = cards._cached(f"ncaa-{division}-{gender}-{week}.png", build)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/png", max_age=3600)
    except Exception as exc:                          # noqa: BLE001
        if getattr(exc, "code", None) == 404:
            raise
        print(f"card: ncaa {division}/{gender} failed ({type(exc).__name__}: {exc})", flush=True)
        return redirect(url_for("static", filename="og-image.png"))
