# Project: xc-predictor / racecast
# File:    shortlist.py
# Purpose: The saved college shortlist (owner, 2026-10-10, approved): from a
#          college's recruiting page, save it (two to four programs, stored
#          on the account), and see them side by side --
#
#              /account/shortlist[?gender=f&sport=TF]
#
#          what they recruit (recruiting.schoolRecruits, the college page's
#          own summary), how their recruits develop, and how strong their
#          conference is.
#
# ★ EVERY NUMBER IS ONE THE RECRUITING PAGES ALREADY PRINT, OR READ OFF THE
#   SAME TABLES. Recruit medians and quartiles: recruiting.schoolRecruits.
#   Conference strength: recruiting.schoolTable, the table the recruiting
#   page sorts, grouped by its own conference column. Development: the
#   college_recruit rows (build_recruiting) joined to the same athletes'
#   later seasons, and to the model's projection of them out of high school
#   (build_recruit_projection's recruit_projection).
#
# ★ DEVELOPMENT IS TWO NUMBERS, AND THEY ANSWER DIFFERENT QUESTIONS.
#     "after arriving"  the recruits' rating from their first college season
#                       to their latest one AT THAT SCHOOL -- measured, on the
#                       college pool's own scale, as a share of the race
#                       (recruiting.py: a rating gain d from r is a time
#                       improvement of d / (r + d), so equal points are not
#                       equal improvement and the share is what compares).
#     "the model expected" the median projected one-year gain of the same
#                       recruits from their last high-school season
#                       (recruit_projection.proj_gain_pct): how much room the
#                       program's recruits had, by the model's reading, before
#                       they arrived.
#   A program whose first number beats its second develops its runners past
#   what their high-school trajectory said; the page says which is which and
#   never subtracts one scale from the other.
#
# ! TWO TO FOUR (owner). One program is the recruiting page itself; a fifth
#   column does not fit beside four on a laptop and is a list, not a
#   comparison. Saving a fifth says so instead of dropping the oldest.
#
# ! EVERY POST IS CSRF + SAME ORIGIN (accounts.csrfOk), and the page that
#   reads the list is under /account (private, no-store, never at the edge).
import statistics
import urllib.parse

from flask import Blueprint, jsonify, redirect, render_template, request

import accounts as AC
import follows as F

bp = Blueprint("shortlist", __name__)

MIN_PROGRAMS = 2
MAX_PROGRAMS = 4


# ---- pure ----------------------------------------------------------------

def parseProgram(values):
    """A save/remove request -> ((school, state), error). The state is the
    recruiting page's ?state= (two letters) or empty."""
    school = (values.get("school") or "").strip()[:200]
    if not school:
        return None, "Which program?"
    state = (values.get("state") or "").strip().upper()
    if state and (len(state) != 2 or not state.isalpha()):
        return None, "The state must be two letters."
    return (school, state), None


def timeShare(gain, rating):
    """A rating gain as a share of the race (percent): d / (r + d) of the
    time. recruiting.py derives it; None when it cannot be."""
    if gain is None or rating is None or float(rating) + float(gain) <= 0:
        return None
    return round(100.0 * float(gain) / (float(rating) + float(gain)), 1)


def developmentOf(pairs):
    """pairs: [(first_rating, latest_rating)] of recruits with a later
    season. {n, median_gain, median_pct} or None when nobody has one."""
    gains = [(float(b) - float(a), float(a)) for a, b in pairs
             if a is not None and b is not None]
    if not gains:
        return None
    pcts = [timeShare(g, r) for g, r in gains]
    return {"n": len(gains),
            "median_gain": round(statistics.median(g for g, _ in gains), 1),
            "median_pct": round(statistics.median(p for p in pcts if p is not None), 1)}


def conferenceOf(rows, school, state, key="conference"):
    """The program's conference (or division) from recruiting.schoolTable
    rows: {name, n, median, rank, of} -- the median of the members' recruit
    medians, and where this program's median ranks among them. None when
    the program has no conference on record."""
    me = next((r for r in rows if r["school"] == school
               and (r.get("state") or "") == (state or r.get("state") or "")), None)
    if me is None or not me.get(key):
        return None
    members = [r for r in rows if r.get(key) == me[key] and r.get("median") is not None]
    if not members:
        return None
    ordered = sorted(members, key=lambda r: -r["median"])
    rank = next((i + 1 for i, r in enumerate(ordered) if r is me), None)
    return {"name": me[key], "n": len(members),
            "median": round(statistics.median(r["median"] for r in members), 1),
            "rank": rank, "of": len(members)}


def fitOf(rating, summary):
    """Where a high-school rating sits among a program's recruits (owner,
    2026-10-10: "that's the point" of the list) -- {key, words}:
      top    at or above their top quarter (p75)
      in     inside the range they recruit (their slowest recruit up)
      below  under their slowest recruit
    The recruiting page's own quartiles; None when either side is missing."""
    if rating is None or not summary or summary.get("p75") is None or summary.get("min") is None:
        return None
    r = float(rating)
    if r >= float(summary["p75"]):
        return {"key": "top", "words": "top quarter of their recruits"}
    if summary.get("p25") is not None and r >= float(summary["p25"]):
        return {"key": "in", "words": "in range: the middle of their recruits"}
    if r >= float(summary["min"]):
        return {"key": "in", "words": "in range, toward the bottom"}
    return {"key": "below", "words": "below range"}


def youFrom(cur, claims, sport):
    """The signed-in athlete as a recruiting subject: their claimed athlete
    page's latest high-school season (recruiting._athleteSubject, the
    recruiting page's own subject) -- {name, rating, gender} or None."""
    import recruiting as R
    for c in claims:
        if c["kind"] in ("athlete", "coach_self") and c.get("person_id"):
            subj = R._athleteSubject(cur, int(c["person_id"]))
            rating = R.subjectRating(subj, sport) if subj else None
            if rating is not None:
                return {"name": subj.get("name"), "rating": float(rating), "gender": subj.get("gender"),
                        "person_id": int(c["person_id"])}
    return None


# ---- database ------------------------------------------------------------

def listFor(cur, account_id):
    cur.execute("""SELECT school, state, added_at FROM account_shortlist
                   WHERE account_id = %s ORDER BY added_at, school""", (account_id,))
    return [dict(r) for r in cur.fetchall()]


def _development(cur, school, state, gender, sport):
    """(after_arriving, model_expected) for one program's recruits."""
    params = {"school": school, "gender": gender, "sport": sport, "state": state or None}
    cur.execute("""
        SELECT r.person_id, r.first_year, r.first_rating, r.hs_year,
               (SELECT s.mean_rating FROM athlete_season s
                WHERE  s.person_id = r.person_id AND s.sport = r.sport
                  AND  s.school = r.school AND s.pool LIKE 'college%%'
                  AND  s.year > r.first_year AND s.mean_rating IS NOT NULL
                ORDER  BY s.year DESC LIMIT 1) AS latest
        FROM   college_recruit r
        WHERE  r.school = %(school)s AND r.gender = %(gender)s AND r.sport = %(sport)s
          AND  (%(state)s::text IS NULL OR r.state = %(state)s)
    """, params)
    rows = [dict(x) for x in cur.fetchall()]
    after = developmentOf([(r["first_rating"], r["latest"]) for r in rows if r.get("latest") is not None])
    expected = None
    hs = [(r["person_id"], r["hs_year"]) for r in rows if r.get("hs_year") is not None]
    if hs and AC._one(_regclass(cur, "recruit_projection")).get("t"):
        cur.execute("""
            SELECT p.proj_gain_pct FROM recruit_projection p
            JOIN   unnest(%s::bigint[], %s::int[]) AS h(person_id, year)
                   ON h.person_id = p.person_id AND h.year = p.year
            WHERE  p.sport = %s AND p.proj_gain_pct IS NOT NULL
        """, ([p for p, _ in hs], [int(y) for _, y in hs], sport))
        vals = [float(x["proj_gain_pct"]) for x in cur.fetchall()]
        if vals:
            expected = {"n": len(vals), "median_pct": round(statistics.median(vals), 1)}
    return after, expected


def _regclass(cur, name):
    cur.execute("SELECT to_regclass(%s) AS t", (f"public.{name}",))
    return cur


def comparison(cur, programs, gender, sport):
    """One column per saved program: the recruiting summary, development,
    conference and division strength. A program with no recruits of this
    gender and sport is still a column, saying so."""
    import recruiting as R
    table = R.schoolTable(cur, gender, sport)
    cols = []
    for p in programs:
        school, state = p["school"], p.get("state") or ""
        summary, _recruits = R.schoolRecruits(cur, school, state or None, gender, sport)
        col = {"school": school, "state": state, "summary": summary,
               "href": f"/recruiting/school/{urllib.parse.quote(school, safe='/')}"
                       + (f"?state={state}&gender={gender}" if state else f"?gender={gender}")}
        if summary is not None:
            col["after"], col["expected"] = _development(cur, school, summary.get("state") or state,
                                                         gender, sport)
            col["conference"] = conferenceOf(table, school, summary.get("state"))
            col["division"] = conferenceOf(table, school, summary.get("state"), key="division")
        cols.append(col)
    return cols


# ---- routes --------------------------------------------------------------

@bp.route("/api/shortlist/state")
def api_shortlist_state():
    """What the Save button draws: {signed_in, saved, count, max, csrf}."""
    sess = AC.currentSession()
    if not sess:
        return jsonify({"signed_in": False})
    prog, err = parseProgram(request.args)
    if err:
        return jsonify({"signed_in": True, "error": err}), 400
    try:
        with AC._db() as (conn, cur):
            if not F.tablesReady(cur):
                return jsonify({"signed_in": True, "ready": False})
            have = listFor(cur, sess["account"]["id"])
            conn.commit()
    except Exception as exc:                            # noqa: BLE001
        print(f"[shortlist] state failed ({type(exc).__name__}: {exc})", flush=True)
        return jsonify({"signed_in": True, "ready": False})
    saved = any(h["school"] == prog[0] and h["state"] == prog[1] for h in have)
    return jsonify({"signed_in": True, "ready": True, "saved": saved, "count": len(have),
                    "max": MAX_PROGRAMS, "csrf": sess["csrf"]})


@bp.route("/api/shortlist", methods=["POST"])
def api_shortlist():
    """Save (action=save) or remove (action=remove) a program. JSON for the
    button; a form post from the comparison page redirects back to it."""
    sess = AC.currentSession()
    wants_json = request.headers.get("X-CSRF") is not None

    def answer(payload, status=200, notice=None):
        if wants_json:
            return jsonify(payload), status
        q = {"error": payload["error"]} if payload.get("error") else ({"notice": notice} if notice else {})
        return redirect("/account/shortlist" + ("?" + urllib.parse.urlencode(q) if q else ""))
    if not sess:
        return answer({"error": "Sign in to save programs."}, 401)
    if not AC.csrfOk(sess):
        return answer({"error": "That request did not come from this site."}, 400)
    prog, err = parseProgram(request.form)
    if err:
        return answer({"error": err}, 400)
    action = (request.form.get("action") or "save").strip()
    if action not in ("save", "remove"):
        return answer({"error": "Unknown action."}, 400)
    aid = sess["account"]["id"]
    with AC._db() as (conn, cur):
        if not F.tablesReady(cur):
            return answer({"error": "The shortlist is not set up on this server yet."}, 503)
        have = listFor(cur, aid)
        there = any(h["school"] == prog[0] and h["state"] == prog[1] for h in have)
        if action == "save" and not there:
            if len(have) >= MAX_PROGRAMS:
                conn.rollback()
                return answer({"error": f"Your shortlist holds {MAX_PROGRAMS} programs. "
                                        "Remove one to add another.", "count": len(have)}, 409)
            cur.execute("""INSERT INTO account_shortlist (account_id, school, state)
                           VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""", (aid, prog[0], prog[1]))
        elif action == "remove":
            cur.execute("DELETE FROM account_shortlist WHERE account_id = %s AND school = %s AND state = %s",
                        (aid, prog[0], prog[1]))
        AC.logEvent(cur, f"shortlist_{action}", aid, detail={"school": prog[0], "state": prog[1]})
        count = len(listFor(cur, aid))
        conn.commit()
    return answer({"ok": True, "saved": action == "save", "count": count}, 200,
                  notice="Saved." if action == "save" else "Removed.")


@bp.route("/account/shortlist")
def shortlist_page():
    import recruiting as R
    sess, go = AC._requireSession()
    if go:
        return go
    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in R.SPORTS:
        sport = "XC"
    cols, ready, built, you = [], True, True, None
    with AC._db() as (conn, cur):
        ready = F.tablesReady(cur)
        programs = listFor(cur, sess["account"]["id"]) if ready else []
        built = R._tableExists(cur, "college_recruit")
        # ★ WHERE YOU'D FIT: the claimed athlete's own rating, and their
        #   gender picks the recruits unless the reader switched it
        try:
            you = youFrom(cur, AC.claimsFor(cur, sess["account"]["id"]), sport) if built else None
        except Exception as exc:                        # noqa: BLE001
            conn.rollback()
            print(f"[shortlist] subject failed ({type(exc).__name__}: {exc})", flush=True)
        gender = (request.args.get("gender") or (you or {}).get("gender") or "m").strip().lower()
        if gender not in R.GENDERS:
            gender = "m"
        if programs and built:
            cols = comparison(cur, programs, gender, sport)
            for c in cols:
                # ! only against recruits of the athlete's own gender
                c["fit"] = (fitOf(you["rating"], c.get("summary"))
                            if you and you.get("gender") == gender else None)
        elif programs:
            # ! THE SAVED NAMES STILL SHOW (and can be removed) before the
            #   recruiting table is built; the numbers say they are coming
            cols = [{"school": p["school"], "state": p.get("state") or "", "summary": None,
                     "href": f"/recruiting/school/{urllib.parse.quote(p['school'], safe='/')}"}
                    for p in programs]
        conn.commit()
    return render_template("shortlist.html", cols=cols, gender=gender, sport=sport,
                           ready=ready, built=built, csrf=sess["csrf"], you=you,
                           n=len(cols), min_n=MIN_PROGRAMS, max_n=MAX_PROGRAMS,
                           fmt_time=R.fmtTime,
                           notice=request.args.get("notice", "")[:200],
                           error=request.args.get("error", "")[:200])
