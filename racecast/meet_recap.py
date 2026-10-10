# Project: xc-predictor / racecast
# File:    meet_recap.py
# Purpose: "How we did": a stored forecast (meet_forecast.py) against the
#          results, per race, per meet and per week (owner, 2026-10-10,
#          approved).
#
#     raceRecap(forecast, finishers, teams)   one race, scored (pure)
#     compactScore(recap)                     what the table keeps of it
#     summarize(scores)                       races -> one line's numbers
#     meetRecap(cur, meet_id, source)         /meet/recap/xc/<id>
#     weekRecaps(cur, week, state)            /recaps?week=...
#     weekLine(cur, today)                    the About page's sentence
#
# ★ SCORED AGAINST WHAT WAS SAID. Only the stored forecast is ever compared;
#   the recap never re-predicts. A runner the forecast had and the results
#   do not is "did not run", not a miss, and a finisher it did not have is
#   listed as outside the projected field, not hidden.
#
# ★ THE MEDIAN MISS IS meet_track's. The same per-runner percentage error
#   (|predicted - actual| / actual) the predictions page's "on past
#   editions" line uses, so the two numbers mean one thing. A week's median
#   is over every runner of every race, not a median of race medians.
#
# ! A SMALL FIELD IS SHOWN, NOT COUNTED. A race below the backtest's field
#   floor (build_meet_track.MIN_FIELD, read from there) still gets its recap;
#   it is left out of the weekly numbers, where a three-runner "winner
#   picked" would flatter the rate.
import datetime
import statistics

import ttlcache

# The owner's own measure, "got 7 of the top 10".
TOP_N = 10
# How many runners and teams each way the surprises list: the five a
# prediction card lists (cards.renderPredictionCard's top five).
SURPRISES_SHOWN = 5
# The predicted vs actual table shows the preview's own length first
# (pages3.RUNNERS_SHOWN); the rest is behind "every predicted runner".
TABLE_SHOWN = 25

TTL = 3600.0
FAIL_TTL = 300.0


def _minField():
    try:
        from build_meet_track import MIN_FIELD
        return MIN_FIELD
    except Exception:                                   # noqa: BLE001
        # its own fallback, when the script cannot be imported
        return 20


# ------------------------------------------------------------------ #
#  pure: rank agreement
# ------------------------------------------------------------------ #

def _ranks(values):
    """Average ranks (1-based), ties sharing the mean of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1
        i = j + 1
    return ranks


def spearman(a, b):
    """Spearman's rank correlation of two equal-length lists, or None for
    fewer than three pairs or no spread."""
    if len(a) != len(b) or len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    if va == 0 or vb == 0:
        return None
    return cov / (va * vb) ** 0.5


def pairsAhead(pred, act):
    """The share of pairs of runners where the one predicted ahead finished
    ahead (tied predictions or finishes are not counted), or None."""
    n, good = 0, 0
    for i in range(len(pred)):
        for j in range(i + 1, len(pred)):
            dp, da = pred[i] - pred[j], act[i] - act[j]
            if dp == 0 or da == 0:
                continue
            n += 1
            good += (dp > 0) == (da > 0)
    return good / n if n else None


# ------------------------------------------------------------------ #
#  pure: one race
# ------------------------------------------------------------------ #

def _sameTeam(a, b):
    """One team: the same school, and the same state when both carry one
    (a team is a school in a state, team_rank._SEP)."""
    if not a or not b or a.get("team") != b.get("team"):
        return False
    sa, sb = a.get("state"), b.get("state")
    return not sa or not sb or sa == sb


def raceRecap(fc, finishers, actual_teams=None):
    """One race's recap.

    fc           the stored race (meet_forecast row: runners in predicted
                 order with seconds, teams with scores)
    finishers    the race's placed finishers IN FINISH ORDER, each
                 {person_id, name, school, time_seconds}
    actual_teams the race's team scores, best first: {team, state, points}

    Returns None when there are no finishers yet."""
    if not finishers:
        return None
    pred = [r for r in (fc.get("runners") or []) if r.get("person_id") is not None]
    pred.sort(key=lambda r: (r.get("place") or 10 ** 6))
    act = {}
    for i, f in enumerate(finishers, 1):
        if f.get("person_id") is not None and f["person_id"] not in act:
            act[f["person_id"]] = (i, float(f["time_seconds"]), f)
    rows, matched = [], []
    for p in pred:
        a = act.get(p["person_id"])
        row = {"person_id": p["person_id"], "name": p.get("name"),
               "school": p.get("school_label") or p.get("school"),
               "school_href": p.get("school_href"),
               "pred_place": p.get("place"), "pred_seconds": p.get("seconds"),
               "act_place": a[0] if a else None, "act_seconds": a[1] if a else None,
               "diff_pct": None}
        if a and p.get("seconds"):
            # + is slower than predicted, - is faster
            row["diff_pct"] = 100.0 * (a[1] - float(p["seconds"])) / float(p["seconds"])
            matched.append(row)
        rows.append(row)
    abs_pct = [100.0 * abs(r["act_seconds"] - float(r["pred_seconds"])) / r["act_seconds"]
               for r in matched if r["act_seconds"]]
    winner = finishers[0]
    pick = pred[0] if pred else None
    n_top = min(TOP_N, len(finishers))
    top_act = {f.get("person_id") for f in finishers[:n_top]}
    top_pred = {p["person_id"] for p in pred[:n_top]}
    hits = len(top_act & top_pred)

    pteams = sorted((t for t in (fc.get("teams") or []) if t.get("score") is not None),
                    key=lambda t: t["score"])
    ateams = [t for t in (actual_teams or []) if t.get("points") is not None]
    team_picked = (_sameTeam(pteams[0], ateams[0]) if pteams and ateams else None)
    team_moves = []
    for i, t in enumerate(pteams, 1):
        j = next((k for k, a in enumerate(ateams, 1) if _sameTeam(t, a)), None)
        if j is not None:
            team_moves.append({"team": t.get("school_label") or t["team"],
                               "href": t.get("school_href"), "pred_place": i,
                               "act_place": j, "pred_score": t["score"],
                               "act_score": ateams[j - 1]["points"], "moved": i - j})

    rho = spearman([r["pred_seconds"] for r in matched], [r["act_seconds"] for r in matched])
    ahead = pairsAhead([r["pred_seconds"] for r in matched], [r["act_seconds"] for r in matched])
    by_diff = sorted(matched, key=lambda r: r["diff_pct"])
    beat = [r for r in by_diff if r["diff_pct"] < 0][:SURPRISES_SHOWN]
    short = [r for r in reversed(by_diff) if r["diff_pct"] > 0][:SURPRISES_SHOWN]
    pids = {p["person_id"] for p in pred}
    outside = [{"person_id": f.get("person_id"), "name": f.get("name"),
                "school": f.get("school"), "place": i, "seconds": float(f["time_seconds"])}
               for i, f in enumerate(finishers[:n_top], 1) if f.get("person_id") not in pids]
    return {
        "n_pred": len(pred), "n_finish": len(finishers), "n_matched": len(matched),
        "n_dnr": sum(1 for r in rows if r["act_place"] is None),
        "median_pct": statistics.median(abs_pct) if abs_pct else None,
        "errs_pct": [round(e, 2) for e in abs_pct],
        "winner": {"person_id": winner.get("person_id"), "name": winner.get("name"),
                   "school": winner.get("school"), "seconds": float(winner["time_seconds"])},
        "pick": ({"person_id": pick["person_id"], "name": pick.get("name"),
                  "school": pick.get("school_label") or pick.get("school"),
                  "seconds": pick.get("seconds")} if pick else None),
        "picked": bool(pick) and pick["person_id"] == winner.get("person_id"),
        "top_n": n_top, "top_hits": hits,
        "team_pick": (pteams[0].get("school_label") or pteams[0]["team"]) if pteams else None,
        "team_winner": (ateams[0].get("label") or ateams[0]["team"]) if ateams else None,
        "team_picked": team_picked,
        "rho": rho, "ahead": ahead,
        "beat": beat, "short": short,
        "teams_up": sorted((t for t in team_moves if t["moved"] > 0),
                           key=lambda t: -t["moved"])[:SURPRISES_SHOWN],
        "teams_down": sorted((t for t in team_moves if t["moved"] < 0),
                             key=lambda t: t["moved"])[:SURPRISES_SHOWN],
        "outside": outside,
        "rows": rows,
    }


_KEEP = ("n_pred", "n_finish", "n_matched", "n_dnr", "median_pct", "errs_pct", "picked",
         "top_n", "top_hits", "team_picked", "rho", "ahead")


def compactScore(rc):
    """What meet_forecast.score keeps of a recap: the numbers the week's
    index and the About line sum, plus the two names a row prints."""
    out = {k: rc.get(k) for k in _KEEP}
    out["winner"] = (rc.get("winner") or {}).get("name")
    out["pick"] = (rc.get("pick") or {}).get("name")
    return out


# ------------------------------------------------------------------ #
#  pure: sums and sentences
# ------------------------------------------------------------------ #

def summarize(scores, min_field=None):
    """Races' scores -> {races, runners, median_pct, picked, picked_pct,
    top_hits, top_n, team_races, team_picked, small} or None. Races with
    fewer finishers than the field floor are counted in `small` only."""
    floor = _minField() if min_field is None else min_field
    use = [s for s in scores if s and s.get("n_matched")]
    small = sum(1 for s in use if (s.get("n_finish") or 0) < floor)
    use = [s for s in use if (s.get("n_finish") or 0) >= floor]
    if not use:
        return None
    errs = [float(e) for s in use for e in (s.get("errs_pct") or [])]
    picked = sum(1 for s in use if s.get("picked"))
    teams = [s for s in use if s.get("team_picked") is not None]
    return {"races": len(use), "runners": len(errs),
            "median_pct": statistics.median(errs) if errs else None,
            "picked": picked, "picked_pct": 100.0 * picked / len(use),
            "top_hits": sum(s.get("top_hits") or 0 for s in use),
            "top_n": sum(s.get("top_n") or 0 for s in use),
            "team_races": len(teams),
            "team_picked": sum(1 for s in teams if s["team_picked"]),
            "small": small}


def pct(v, digits=1):
    return "-" if v is None else f"{v:.{digits}f}%"


def raceWords(rc):
    """The plain sentences under a race: what we got right and wrong."""
    if not rc:
        return []
    out = []
    w, p = rc["winner"], rc.get("pick")
    if rc["picked"]:
        out.append(f"Picked the winner: {w['name']}.")
    elif p:
        out.append(f"Missed the winner: we had {p['name']}, {w['name']} won.")
    out.append(f"Got {rc['top_hits']} of the top {rc['top_n']}.")
    if rc.get("team_picked") is True:
        out.append(f"Picked the team winner: {rc['team_winner']}.")
    elif rc.get("team_picked") is False:
        out.append(f"Missed the team winner: we had {rc['team_pick']}, {rc['team_winner']} won.")
    if rc.get("median_pct") is not None:
        out.append(f"Median miss {pct(rc['median_pct'])} over the {rc['n_matched']} runners "
                   f"we predicted who finished.")
    if rc.get("ahead") is not None:
        out.append(f"Of every two of those runners, the one we had ahead finished ahead "
                   f"{100 * rc['ahead']:.0f}% of the time.")
    return out


def summaryWords(s, label=None):
    """'312 races, median miss 2.1%, winner picked 61%.' style line."""
    if not s:
        return None
    bits = [f"{s['races']} race{'s' if s['races'] != 1 else ''}"]
    if s.get("median_pct") is not None:
        bits.append(f"median miss {pct(s['median_pct'])}")
    bits.append(f"winner picked {s['picked_pct']:.0f}%")
    if s.get("top_n"):
        bits.append(f"{100.0 * s['top_hits'] / s['top_n']:.0f}% of the top {TOP_N} named")
    line = ", ".join(bits) + "."
    return f"{label}: {line}" if label else line[0].upper() + line[1:]


# ------------------------------------------------------------------ #
#  weeks
# ------------------------------------------------------------------ #

def weekStart(day):
    """The Monday on or before `day`: a week of racing is Monday to Sunday,
    so its weekend is whole."""
    return day - datetime.timedelta(days=day.weekday())


def weekLabel(lo):
    hi = lo + datetime.timedelta(days=6)
    a = lo.strftime("%b ") + str(lo.day)
    b = (hi.strftime("%b ") if hi.month != lo.month else "") + str(hi.day)
    return f"{a} to {b}"


def parseWeek(s, default):
    try:
        return weekStart(datetime.date.fromisoformat(str(s)[:10]))
    except (TypeError, ValueError):
        return weekStart(default)


def groupMeets(rows):
    """Stored race rows -> one entry per meet, in date order, each with its
    races' scores summed (field floor applied) and its href."""
    meets = {}
    for r in rows:
        k = (r["source"], int(r["meet_id"]))
        m = meets.setdefault(k, {"source": r["source"], "meet_id": int(r["meet_id"]),
                                 "name": r.get("meet_name"), "date": r.get("meet_date"),
                                 "state": r.get("state"), "venue": r.get("venue"),
                                 "races": 0, "scores": []})
        m["races"] += 1
        if r.get("score"):
            m["scores"].append(r["score"])
    out = []
    for m in meets.values():
        m["summary"] = summarize(m["scores"], min_field=0)
        m["n_scored"] = len(m["scores"])
        m["href"] = recapHref(m["meet_id"], m["source"])
        out.append(m)
    out.sort(key=lambda m: (m["date"] or "", -(m["n_scored"]), m["name"] or ""))
    return out


def recapHref(meet_id, source):
    href = f"/meet/recap/xc/{int(meet_id)}"
    return href + "?src=tfrrs" if source == "tfrrs" else href


def weekRecaps(cur, week, state=None):
    """{week, label, meets, summary} for /recaps."""
    import meet_forecast as MF
    hi = week + datetime.timedelta(days=6)
    rows = MF.weekRows(cur, week, hi, state)
    meets = groupMeets(rows)
    return {"week": week, "label": weekLabel(week), "meets": meets,
            "summary": summarize([r["score"] for r in rows if r.get("score")]),
            "n_forecast": len(rows)}


def weekLine(cur, today=None):
    """{"text", "href", "week"} for the newest week with scored races, or
    None. Cached per worker for TTL."""
    today = today or datetime.date.today()

    def compute():
        import meet_forecast as MF
        last = MF.latestScoredDate(cur)
        if last is None:
            return {"none": True}
        wk = weekStart(last)
        got = weekRecaps(cur, wk)
        if not got["summary"]:
            return {"none": True}
        so_far = wk + datetime.timedelta(days=6) >= today
        label = f"Week of {weekLabel(wk)}" + (" (so far)" if so_far else "")
        return {"text": summaryWords(got["summary"], label), "week": wk.isoformat(),
                "href": f"/recaps?week={wk.isoformat()}", "summary": got["summary"]}
    try:
        val, _ = ttlcache.get(("recap_week_line", today.isoformat()), compute, ttl=TTL,
                              ttl_of=lambda v: FAIL_TTL if v.get("none") else TTL)
    except Exception as exc:                            # noqa: BLE001
        print(f"recap week line: {type(exc).__name__}: {exc}", flush=True)
        return None
    return None if val.get("none") else val


# ------------------------------------------------------------------ #
#  the results side
# ------------------------------------------------------------------ #

def actualByDiv(cur, meet_id, source):
    """{div_id: [finishers in finish order]} -- placed finishers only (a DQ
    or a DNF keeps no place, the race page's rule)."""
    from app import _athlete_lateral, _hasResultsStatus, _name_sql, _xcPlaced
    cur.execute(f"""
        SELECT r.div_id, r.person_id, r.time_seconds, r.school, r.team_id,
               {"r.status" if _hasResultsStatus(cur) else "NULL::text"} AS status,
               {_name_sql('r')} AS name
        FROM   results r
        {_athlete_lateral('r')}
        WHERE  r.meet_id = %(m)s AND r.source = %(src)s
          AND  r.time_seconds > 0 AND r.time_seconds < 90000
        ORDER  BY r.div_id, r.time_seconds
    """, {"m": int(meet_id), "src": source})
    out = {}
    for row in cur.fetchall():
        if _xcPlaced(row):
            out.setdefault(int(row["div_id"]), []).append(dict(row))
    return out


def actualTeams(cur, finishers, meet_state, source):
    """The race's team scores from its finishers, best first: our own
    scoring (meet_compile.scoreRows, five score and two displace), as the
    meet page's winner column scores a race with no published scores."""
    from meet_compile import scoreRows, splitCollisionTeams, unsplitTeams
    ranked = [{**r, "place": i} for i, r in enumerate(finishers, 1)]
    splitCollisionTeams(cur, ranked, meet_state=meet_state, source=source)
    teams = unsplitTeams(scoreRows(ranked)).get("teams") or []
    return [{"team": t["school"], "state": t.get("state"), "points": t["points"]}
            for t in teams]


def scoreMeet(cur, source, meet_id, stored):
    """{div_id: (recap, compact score)} for the stored races with results."""
    actual = actualByDiv(cur, meet_id, source)
    out = {}
    for fc in stored:
        fin = actual.get(int(fc["div_id"]))
        if not fin:
            continue
        try:
            cur.execute("SAVEPOINT rc_teams")
            teams = actualTeams(cur, fin, fc.get("state"), source)
            cur.execute("RELEASE SAVEPOINT rc_teams")
        except Exception as exc:                        # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT rc_teams")
            print(f"recap teams {meet_id}/{fc['div_id']}: {type(exc).__name__}: {exc}",
                  flush=True)
            teams = []
        rc = raceRecap(fc, fin, teams)
        if rc:
            out[int(fc["div_id"])] = (rc, compactScore(rc))
    return out


def meetRecap(cur, meet_id, source=None):
    """Everything /meet/recap/xc/<id> renders, or None when no forecast was
    stored for the meet."""
    import meet_forecast as MF
    stored = MF.loadMeet(cur, meet_id, source)
    if not stored:
        return None
    src = stored[0]["source"]
    scored = scoreMeet(cur, src, meet_id, stored)
    races = []
    for fc in stored:
        rc, sc = scored.get(int(fc["div_id"]), (None, None))
        races.append({"div_id": int(fc["div_id"]), "label": fc.get("race_label"),
                      "gender": fc.get("gender"), "distance": fc.get("distance"),
                      "made_on": fc.get("made_on"), "model_basis": fc.get("model_basis"),
                      "model_version": fc.get("model_version"),
                      "recap": rc, "score": sc, "words": raceWords(rc),
                      "small": bool(rc) and rc["n_finish"] < _minField()})
    # ★ THE BIGGEST SCORED RACE FIRST, as the preview leads with the biggest
    races.sort(key=lambda r: (r["recap"] is None, -(r["recap"] or {}).get("n_finish", 0),
                              r["div_id"]))
    first = stored[0]
    made = sorted({r["made_on"] for r in races if r["made_on"]})
    day = datetime.date.fromisoformat(first["meet_date"]) if first.get("meet_date") else None
    return {"meet_id": int(meet_id), "source": src, "name": first.get("meet_name"),
            "week": weekStart(day).isoformat() if day else None,
            "date": first.get("meet_date"), "venue": first.get("venue"),
            "state": first.get("state"), "races": races,
            "n_scored": sum(1 for r in races if r["recap"]),
            "summary": summarize([r["score"] for r in races if r["score"]], min_field=0),
            "made_on": made[-1] if made else None,
            "edition": ({"meet_id": first.get("edition_meet_id"),
                         "date": first.get("edition_date")}
                        if first.get("edition_meet_id") else None)}
