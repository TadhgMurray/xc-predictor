# Project: xc-predictor / racecast
# File:    my_page.py
# Purpose: "My page" (owner, 2026-10-10, approved): one page, under /account,
#          for the athletes and teams an account claims and follows --
#
#              /account/me
#
#          each one's latest rating and how its rank moved, the progress
#          toward the what-it-takes marks, the meets it is likely to run next
#          WITH an automatic prediction for the first of them (owner: "add
#          auto predict"), the season's races, and the follow and alert
#          settings.
#
# ★ ONLY READS WHAT THE SITE ALREADY COMPUTES. The rating and the ranks are
#   athlete_season and season_rank (the athlete page's rank line), the team's
#   are team_season (the team boards); the marks are cuts.athleteLine (the
#   athlete page's "What it takes" line); the likely meets are
#   team_meets.likelyMeets; the prediction is predict.predictIndividual at
#   that meet, the same call /api/predict/individual makes.
#
# ★ THE PREDICTION IS COMPUTED ONCE A DAY, NOT PER VIEW (owner: "cached so a
#   page view never runs the model repeatedly"). next_meet_cache keeps, per
#   athlete and per team, the likely meets and the prediction, stamped with
#   the day; a view reads the row and only a miss computes (and writes it).
#   The nightly alert step (follow_alerts.py --prewarm) fills the rows for
#   everyone followed or claimed, so the first view of the day is a read too.
#   A day, because the data it rests on changes once a night.
#
# ★ RANK MOVEMENT NEEDS A PAST RANK, AND NOTHING KEPT ONE. season_rank and
#   team_season are rebuilt every run; follow_alerts.py writes a snapshot of
#   every followed or claimed subject's rating and ranks each night
#   (follow_snapshot). The movement shown is against the snapshot taken
#   before the subject's latest race: "what that race did", not an arbitrary
#   look-back. With no such snapshot yet (a follow made this week) there is
#   no movement line rather than a made-up one.
#
# ! PRIVATE: under /account, so app._headers sends no-store and robots.txt
#   disallows it. It reads the session; no other page does.
import datetime
import json

from flask import Blueprint, render_template, request

import accounts as AC
import follows as F

bp = Blueprint("my_page", __name__)


# ---- the athlete -----------------------------------------------------------

def latestSeason(cur, person_id):
    """The athlete's latest rated season (the athlete page's header rule:
    a rated season first, the newest, the one with the most races in a
    tie), with the name. None when there is none."""
    from rankings import nameLateral
    cur.execute(f"""
        SELECT s.person_id, s.sport, s.pool, s.year, s.mean_rating, s.n_races,
               s.school, s.state, s.grade, s.last_race, a.name
        FROM   athlete_season s
        {nameLateral('s')}
        WHERE  s.person_id = %s AND s.mean_rating IS NOT NULL
        ORDER  BY s.year DESC, s.last_race DESC NULLS LAST, s.n_races DESC NULLS LAST
        LIMIT  1
    """, (person_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def seasonRanks(cur, season):
    """{nation, nation_total, state_rank} from season_rank, or None."""
    try:
        cur.execute("SELECT to_regclass('public.season_rank') AS t")
        if not (AC._one(cur) or {}).get("t"):
            return None
        cur.execute("""SELECT nation, nation_total, state_rank FROM season_rank
                       WHERE person_id = %s AND pool = %s AND sport = %s AND year = %s""",
                    (season["person_id"], season["pool"], season["sport"], season["year"]))
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
        return None


def snapshotBefore(cur, subject, sport, pool, year, day):
    """The newest snapshot taken on or before `day` (a nightly snapshot on
    race day was taken before the race ran), or None."""
    if not day:
        return None
    cur.execute("""SELECT taken_on, rating, nation, state_rank FROM follow_snapshot
                   WHERE subject = %s AND sport = %s AND pool = %s AND year = %s AND taken_on <= %s
                   ORDER BY taken_on DESC LIMIT 1""", (subject, sport, pool, year, day))
    row = cur.fetchone()
    return dict(row) if row else None


def movement(now, then):
    """{rating, nation, state} deltas between a snapshot and the current
    numbers. A rank's delta is positive when the rank number went DOWN
    (moved up the board). None where either side is missing."""
    if not then:
        return None

    def d_rank(a, b):
        return (int(b) - int(a)) if a is not None and b is not None else None
    out = {"since": then.get("taken_on"),
           "rating": (round(float(now["rating"]) - float(then["rating"]), 1)
                      if now.get("rating") is not None and then.get("rating") is not None else None),
           "nation": d_rank(now.get("nation"), then.get("nation")),
           "state": d_rank(now.get("state_rank"), then.get("state_rank"))}
    if all(out[k] in (None, 0) for k in ("rating", "nation", "state")):
        out["unchanged"] = True
    return out


def seasonRaces(cur, person_id, sport, year):
    """The season's rated races, newest first, with meet names and links
    (breakouts._fillMeets, the breakouts page's own lookup)."""
    import breakouts as B
    cur.execute("""SELECT result_id, race_date, speed_rating, time_seconds, distance,
                          meet_id, div_id, event_id
                   FROM   ranking_results
                   WHERE  person_id = %s AND sport = %s AND year = %s AND speed_rating IS NOT NULL
                   ORDER  BY race_date DESC, result_id DESC""", (person_id, sport, year))
    rows = [dict(r) for r in cur.fetchall()]
    return B._fillMeets(cur, sport, rows)


def _genderOf(pool):
    g = (pool or "").rsplit("_", 1)[-1]
    return {"m": "M", "f": "F"}.get(g)


# ---- the next meet, and the prediction there --------------------------------

def _today():
    return datetime.date.today()


def dayLabel(iso, weekday=False):
    """'Oct 17' (or 'Sat, Oct 17') from a date or an ISO string; '' else."""
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return (d.strftime("%a, ") if weekday else "") + d.strftime("%b ") + str(d.day)


def cachedNext(cur, subject, today):
    cur.execute("SELECT payload FROM next_meet_cache WHERE subject = %s AND computed_on = %s",
                (subject, today))
    row = cur.fetchone()
    if not row:
        return None
    p = row["payload"]
    return json.loads(p) if isinstance(p, str) else p


def storeNext(cur, subject, today, payload):
    cur.execute("""INSERT INTO next_meet_cache (subject, computed_on, payload) VALUES (%s, %s, %s)
                   ON CONFLICT (subject) DO UPDATE SET computed_on = EXCLUDED.computed_on,
                                                       payload = EXCLUDED.payload""",
                (subject, today, json.dumps(payload, default=str)))


def likelyBoth(cur, school, state, today, level=None):
    """The team's likely meets in both sports, date order (a cross country
    team in October has track meets posted for March too)."""
    from team_meets import likelyMeets
    out = []
    for sport in ("XC", "TF"):
        try:
            out += likelyMeets(cur, school, state, sport, today, level)
        except Exception as exc:                        # noqa: BLE001
            cur.connection.rollback()
            print(f"[my_page] likely meets {sport} failed ({type(exc).__name__}: {exc})", flush=True)
    out.sort(key=lambda m: (m["date"], m["name"] or ""))
    return out


def pickRace(races, gender, ran_div=None, mapped=None):
    """Which posted race of the meet an athlete runs: one of their gender,
    and of those the one that maps (last_edition.mapRace) to the race they
    ran at the last edition, when they ran it; else the first, the host's
    own order (anet numbers divisions as they are set up, varsity first).
    mapped(race) -> the edition div_ids that race maps to."""
    mine = [r for r in races if not gender or not r.get("gender") or r["gender"] == gender]
    if not mine:
        return None
    if ran_div is not None and mapped is not None:
        for r in mine:
            if ran_div in (mapped(r) or ()):
                return r
    return mine[0]


def _predictAt(cur, person_ids, meet, race):
    """predict.predictIndividual's answer for each athlete at this race, or
    an unavailable marker. XC only: a track meet's divisions carry no event,
    and a prediction needs one."""
    from predict import modelStatus, _servedTimes
    if meet["sport"] != "XC":
        return {pid: {"available": False,
                      "reason": "Track predictions need the event: open the meet's prediction."}
                for pid in person_ids}
    status = modelStatus()
    if not status.get("available"):
        return {pid: dict(status) for pid in person_ids}
    target = {"mode": "meet", "meet_id": int(meet["meet_id"]), "sport": "XC",
              "source": meet["source"], "weather": "none",
              "div_id": str(race["div_id"]) if race else None}
    entries = _servedTimes(cur, list(person_ids), target)
    out = {}
    for pid, e in zip(person_ids, entries):
        if e.get("seconds") is None:
            out[pid] = {"available": False, "reason": e.get("reason") or "No rated races found."}
        else:
            keep = {k: e.get(k) for k in ("seconds", "lo", "hi", "basis", "sigma_pct", "rating")}
            out[pid] = {"available": True, **keep}
    return out


def _editionDiv(cur, person_id, meet, edition):
    table = "results" if meet["sport"] == "XC" else "results_tf"
    cur.execute(f"SELECT div_id FROM {table} WHERE meet_id = %s AND source = %s AND person_id = %s LIMIT 1",
                (int(edition["meet_id"]), meet["source"], person_id))
    row = cur.fetchone()
    return row["div_id"] if row else None


def _raceFor(cur, person_id, gender, meet):
    from last_edition import editionRaces, mapRace, upcomingRaces
    races, _meta = upcomingRaces(cur, meet["meet_id"], meet["sport"], meet["source"])
    ed = meet.get("edition") or {}
    ran = _editionDiv(cur, person_id, meet, ed) if ed.get("meet_id") else None
    mapped = None
    if ran is not None:
        edition = editionRaces(cur, ed["meet_id"], meet["sport"], meet["source"])
        mapped = lambda r: mapRace(r, races, edition)[0]   # noqa: E731
    return pickRace(races, gender, ran, mapped)


def computeAthleteNext(cur, person_id, season, today):
    """{meets, next, race, prediction} for an athlete: their team's likely
    meets from today and the prediction at the first of them."""
    meets = likelyBoth(cur, season["school"], season["state"], today) if season.get("school") else []
    out = {"meets": meets[:], "next": None, "race": None, "prediction": None}
    if not meets:
        return out
    m = meets[0]
    out["next"] = m
    try:
        race = _raceFor(cur, person_id, _genderOf(season.get("pool")), m)
        out["race"] = race
        out["prediction"] = _predictAt(cur, [person_id], m, race)[person_id]
    except Exception as exc:                            # noqa: BLE001
        cur.connection.rollback()
        print(f"[my_page] prediction for {person_id} failed ({type(exc).__name__}: {exc})", flush=True)
        out["prediction"] = {"available": False, "reason": "The prediction failed; it is logged."}
    return out


def athleteNext(cur, person_id, season, today=None, write=True):
    """computeAthleteNext behind next_meet_cache: one compute a day."""
    today = today or _today()
    subject = f"athlete:{int(person_id)}"
    hit = cachedNext(cur, subject, today)
    if hit is not None:
        return hit
    val = computeAthleteNext(cur, person_id, season, today)
    if write:
        storeNext(cur, subject, today, val)
    return json.loads(json.dumps(val, default=str))


def athleteCard(cur, person_id, today=None, claimed=False, follow_id=None):
    """Everything the page shows for one athlete, or None (no rated season)."""
    import cuts
    today = today or _today()
    season = latestSeason(cur, person_id)
    if season is None:
        return None
    ranks = seasonRanks(cur, season) or {}
    races = seasonRaces(cur, person_id, season["sport"], season["year"])
    last = races[0]["race_date"] if races else None
    now = {"rating": season["mean_rating"], "nation": ranks.get("nation"),
           "state_rank": ranks.get("state_rank")}
    then = snapshotBefore(cur, f"athlete:{int(person_id)}", season["sport"], season["pool"],
                          season["year"], last)
    wit = cuts.athleteLine(cur, person_id) if (season["pool"] or "").startswith("hs_") else None
    nxt = athleteNext(cur, person_id, season, today)
    from school_identity import schoolLabelIn
    return {"kind": "athlete", "person_id": int(person_id), "name": season.get("name") or "Athlete",
            "claimed": claimed, "follow_id": follow_id, "season": season, "ranks": ranks,
            "school_label": schoolLabelIn(season["school"], season.get("state")) if season.get("school") else "",
            "move": movement(now, then), "wit": wit, "races": races, "next": nxt,
            "season_label": season["year"] + 1 if season["sport"] == "TF" else season["year"]}


# ---- the team --------------------------------------------------------------

def _levelPools(level):
    return {"hs": ("hs_m", "hs_f"), "college": ("college_m", "college_f"),
            "ms": ("ms_m", "ms_f")}.get(level or "")


def teamBoards(cur, school, state, level=None):
    """The team's place on its newest season's boards, per pool: [{pool,
    sport, year, nation, state_rank, points, top5_mean, n_athletes}]."""
    cur.execute("""SELECT pool, sport, year, scope, rank, points, top5_mean, n_athletes
                   FROM   team_season
                   WHERE  span = 'season' AND school = %s
                     AND  (%s::text IS NULL OR state = %s)
                     AND  year = (SELECT max(year) FROM team_season
                                  WHERE span = 'season' AND school = %s
                                    AND (%s::text IS NULL OR state = %s))""",
                (school, state, state, school, state, state))
    pools = _levelPools(level)
    by = {}
    for r in cur.fetchall():
        r = dict(r)
        if pools and r["pool"] not in pools:
            continue
        k = (r["sport"], r["pool"])
        row = by.setdefault(k, {"sport": r["sport"], "pool": r["pool"], "year": r["year"],
                                "nation": None, "state_rank": None, "points": None,
                                "top5_mean": r.get("top5_mean"), "n_athletes": r.get("n_athletes")})
        if r["scope"] == "usa":
            row["nation"] = r["rank"]
        else:
            row["state_rank"] = r["rank"]
            row["points"] = r["points"]
    return sorted(by.values(), key=lambda r: (r["sport"] != "XC", r["pool"]))


def _poolWords(pool):
    level, _, g = (pool or "").partition("_")
    word = {"m": "Men" if level == "college" else "Boys",
            "f": "Women" if level == "college" else "Girls"}.get(g, pool)
    return word


def teamSquad(cur, school, state, pool, sport, year):
    """The team's scoring squad this season: its top team_rank.SQUAD by
    season rating -- the runners the team boards race."""
    from rankings import nameLateral
    from team_rank import SQUAD
    cur.execute(f"""SELECT s.person_id, s.mean_rating, a.name
                    FROM   athlete_season s
                    {nameLateral('s')}
                    WHERE  s.school = %s AND (%s::text IS NULL OR s.state = %s)
                      AND  s.pool = %s AND s.sport = %s AND s.year = %s
                      AND  s.mean_rating IS NOT NULL
                    ORDER  BY s.mean_rating DESC LIMIT %s""",
                (school, state, state, pool, sport, year, SQUAD))
    return [dict(r) for r in cur.fetchall()]


def computeTeamNext(cur, school, state, level, boards, today):
    """{meets, next, squads: [{pool, label, race, runners: [... prediction]}]}"""
    meets = likelyBoth(cur, school, state, today, level)
    out = {"meets": meets, "next": meets[0] if meets else None, "squads": []}
    m = out["next"]
    if not m:
        return out
    from last_edition import upcomingRaces
    try:
        races, _meta = upcomingRaces(cur, m["meet_id"], m["sport"], m["source"])
        for b in boards:
            if b["sport"] != m["sport"]:
                continue
            squad = teamSquad(cur, school, state, b["pool"], b["sport"], b["year"])
            if not squad:
                continue
            race = pickRace(races, _genderOf(b["pool"]))
            preds = _predictAt(cur, [r["person_id"] for r in squad], m, race)
            runners = [dict(r, prediction=preds.get(r["person_id"])) for r in squad]
            runners.sort(key=lambda r: ((r["prediction"] or {}).get("seconds") is None,
                                        (r["prediction"] or {}).get("seconds") or 0))
            out["squads"].append({"pool": b["pool"], "label": _poolWords(b["pool"]),
                                  "race": race, "runners": runners})
    except Exception as exc:                            # noqa: BLE001
        cur.connection.rollback()
        print(f"[my_page] team prediction failed ({type(exc).__name__}: {exc})", flush=True)
    return out


def teamNext(cur, school, state, level, boards, today=None, write=True):
    today = today or _today()
    subject = F.subjectKey({"kind": "team", "school": school, "state": state, "level": level})
    hit = cachedNext(cur, subject, today)
    if hit is not None:
        return hit
    val = computeTeamNext(cur, school, state, level, boards, today)
    if write:
        storeNext(cur, subject, today, val)
    return json.loads(json.dumps(val, default=str))


def teamCard(cur, school, state, level=None, today=None, claimed=False, follow_id=None):
    from school import schoolMeets
    from school_identity import primaryState, schoolHref, schoolLabelIn
    from team_meets import calHref
    today = today or _today()
    state = state or primaryState(school)
    boards = teamBoards(cur, school, state, level)
    for b in boards:
        b["label"] = _poolWords(b["pool"])
        subj = f"team:{school}|{state or ''}|{b['pool']}"
        cur.execute("""SELECT max(race_date) AS d FROM ranking_results
                       WHERE school = %s AND pool = %s AND sport = %s AND year = %s""",
                    (school, b["pool"], b["sport"], b["year"]))
        last = (AC._one(cur) or {}).get("d")
        b["move"] = movement({"rating": None, "nation": b["nation"], "state_rank": b["state_rank"]},
                             snapshotBefore(cur, subj, b["sport"], b["pool"], b["year"], last))
    sport = boards[0]["sport"] if boards else "XC"
    year = boards[0]["year"] if boards else None
    recent = schoolMeets(cur, school, sport, year=year, state=state, primary=primaryState(school)) if year else []
    return {"kind": "team", "school": school, "state": state, "level": level,
            "label": schoolLabelIn(school, state) if state else school,
            "href": schoolHref(school, state), "cal_href": calHref(school, state, level),
            "claimed": claimed, "follow_id": follow_id, "boards": boards,
            "recent": recent, "recent_sport": sport,
            "next": teamNext(cur, school, state, level, boards, today)}


# ---- the page --------------------------------------------------------------

def subjectsFor(claims, follows):
    """[(kind, key, claimed, follow_id)] -- claims first, then follows, each
    subject once (a claimed athlete who is also followed is one card)."""
    out, seen = [], {}
    for c in claims:
        if c["kind"] in ("athlete", "coach_self") and c.get("person_id"):
            k = ("athlete", int(c["person_id"]))
        elif c["kind"] == "coach_team" and c.get("school"):
            k = ("team", (c["school"], c.get("state"), c.get("level")))
        else:
            continue
        if k not in seen:
            seen[k] = len(out)
            out.append([k[0], k[1], True, None])
    for f in follows:
        k = (("athlete", int(f["person_id"])) if f["kind"] == "athlete"
             else ("team", (f["school"], f.get("state"), f.get("level"))))
        if k in seen:
            out[seen[k]][3] = f["id"]
            continue
        seen[k] = len(out)
        out.append([k[0], k[1], False, f["id"]])
    return [tuple(x) for x in out]


def _card(cur, kind, key, claimed, follow_id, today):
    try:
        if kind == "athlete":
            return athleteCard(cur, key, today, claimed, follow_id)
        school, state, level = key
        return teamCard(cur, school, state, level, today, claimed, follow_id)
    except Exception as exc:                            # noqa: BLE001
        cur.connection.rollback()
        print(f"[my_page] card {kind} failed ({type(exc).__name__}: {exc})", flush=True)
        return None


@bp.route("/account/me")
def my_page():
    sess, go = AC._requireSession()
    if go:
        return go
    a = sess["account"]
    today = _today()
    cards, follows, cadence, ready = [], [], F.DEFAULT_CADENCE, True
    with AC._db() as (conn, cur):
        claims = AC.claimsFor(cur, a["id"])
        ready = F.tablesReady(cur)
        if ready:
            follows = F.followsFor(cur, a["id"])
            cadence = F.cadenceFor(cur, a["id"])
        for kind, key, claimed, fid in subjectsFor(claims, follows):
            if not ready and not claimed:
                continue
            c = _card(cur, kind, key, claimed, fid, today) if ready else None
            if c is None:
                c = {"kind": kind, "missing": True, "claimed": claimed, "follow_id": fid,
                     "person_id": key if kind == "athlete" else None,
                     "school": key[0] if kind == "team" else None,
                     "state": key[1] if kind == "team" else None}
            cards.append(c)
        conn.commit()
    from recruiting import fmtTime
    resp = render_template("my_page.html", account=a, csrf=sess["csrf"], cards=cards,
                           follows=follows, cadence=cadence, cadences=F.CADENCES,
                           cadence_words=F.CADENCE_WORDS, ready=ready,
                           mail_on=AC.mailEnabled() and bool(F.alertSecret()),
                           fmt_time=fmtTime, today=today.isoformat(), day=dayLabel,
                           notice=request.args.get("notice", "")[:200],
                           error=request.args.get("error", "")[:200])
    from flask import make_response
    return F.setHint(make_response(resp))
