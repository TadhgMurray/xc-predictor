# Project: xc-predictor / racecast
# File:    build_ncaa_projection.py
# Purpose: project the NCAA cross country championships fields -- regional
#          finishes, automatic qualifiers, the at-large board, the nationals
#          field and each team's odds -- for D1, D2 and D3, men and women,
#          once a week (owner approved 2026-10-10). Pipeline step 11c, after
#          11_teams. /ncaa/<division>/<gender> reads what this stores.
#
#     python racecast/build_ncaa_projection.py                 # all six boards
#     python racecast/build_ncaa_projection.py --division d1 --gender men
#     python racecast/build_ncaa_projection.py --dry-run       # compute, print, store nothing
#
# ★ WHAT IS SIMULATED AND WHAT IS COUNTED.
#   COUNTED: every race inside the selection window that has already been
#   run (tfrrs results, the division's own manual's distance floor and "A
#   team" rule), reduced to who beat whom -- ncaa_select.raceWins.
#   SIMULATED: the regionals (each region's teams, their top seven by season
#   rating, drawn through race_sim's own sampler and scorer), then the
#   selection exactly as ncaa_select applies the manual, then nationals with
#   the field that selection produced. Every draw is one possible November;
#   the odds are how often each thing happened.
#
# ★ WHY THE SEVEN BY SEASON RATING. A regional's lineup is the team's seven
#   best, and the "A team" rule (D1: four of the regional seven started; D2,
#   D3: five) is read against the same seven -- the projected seven is the
#   only one we can know before the regional is entered.
#
# ★ ONE ROW PER BOARD PER WEEK (weeks start Friday, build_rank_snapshot's
#   rule), so the page can show what moved since last week. A run overwrites
#   its own week's row; earlier weeks are kept.
#
# ⚠ WHAT THE NUMBERS CANNOT SEE.
#   - Races not yet run. Early in the window most teams have few counted
#     wins, so the projected at-large order leans on the regional order; it
#     sharpens as the conference meets come in.
#   - A team whose region we cannot place (no official list for D1; a name
#     the D2/D3 lists spell differently) is left out of every regional, and
#     the page lists those teams. ncaa_region_overrides.csv fixes one.
#   - Season ratings, not form: an injury or a redshirt is invisible.
#   - The race-day noise is the measured per-race spread (RACE_SIGMA below);
#     teammates' days are drawn independently (race_sim's team_rho = 0, its
#     default everywhere on the site).
import argparse
import csv
import datetime
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import numpy as np                                      # noqa: E402

import ncaa_rules as NR                                 # noqa: E402
import ncaa_select as S                                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OFFICIAL_CSV = os.path.join(HERE, "ncaa_regions.csv")
OVERRIDES_CSV = os.path.join(HERE, "ncaa_region_overrides.csv")

# ★ THE RACE-DAY SPREAD IS THE MEASURED ONE: one race's deviation from the
#   runner's level, "measured ~3.3%, about 4 rating points" (rankings.py's
#   module docstring; build_team_season.MIN_RACES cites the same number).
#   race_sim takes it as a sigma in log time.
RACE_SIGMA = 0.033

SQUAD = 7          # predict.MAX_PER_TEAM: seven run, five score
SCORERS = 5

DDL = """
CREATE TABLE IF NOT EXISTS ncaa_projection (
    week        date        NOT NULL,   -- the Friday the week starts on
    division    text        NOT NULL,   -- d1 / d2 / d3
    gender      text        NOT NULL,   -- men / women
    year        int         NOT NULL,   -- the XC season (academic year)
    computed_at timestamptz NOT NULL DEFAULT now(),
    payload     jsonb       NOT NULL,
    PRIMARY KEY (week, division, gender, year)
);
"""


# ------------------------------------------------------------------ #
#  1. regions: the manuals' lists, the overrides, the data
# ------------------------------------------------------------------ #

def _readCsv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def loadOfficial(path=OFFICIAL_CSV):
    """{(division slug, 'M'|'F'): {name_norm: [(state, region)]}} from the
    manuals' membership lists. Names keep the state the manual wrote in
    parentheses ('Wheaton College (Massachusetts)')."""
    from build_college_directory import normName, parenState
    slug = {"DI": "d1", "DII": "d2", "DIII": "d3"}
    out = defaultdict(dict)
    for r in _readCsv(path):
        if r.get("eligible", "yes") != "yes":
            continue
        key = (slug[r["division"]], r["gender"])
        nm = r["institution"]
        out[key].setdefault(normName(nm), []).append((parenState(nm), r["region"]))
    return out


def loadOverrides(path=OVERRIDES_CSV):
    """{(division, gender or '', school lower, state or ''): region}."""
    out = {}
    for r in _readCsv(path):
        g = (r.get("gender") or "").strip().upper()[:1]
        out[(r["division"].strip().lower(), g, r["school"].strip().lower(),
             (r.get("state") or "").strip().upper())] = r["region"].strip()
    return out


def resolveRegion(div, g, school, state, data_region, official, overrides):
    """(region, how) for one team: the override file first, then the
    manual's list, then the region the data voted (regional attendance,
    scripts/check_school_units.py) when it names a CURRENT region of the
    division. (None, None) when nothing places it."""
    lo = (school or "").strip().lower()
    for k in ((div, g, lo, state or ""), (div, g, lo, ""), (div, "", lo, state or ""),
              (div, "", lo, "")):
        if k in overrides:
            reg = NR.canonicalRegion(div, overrides[k])
            if reg:
                return reg, "override"
    entries = official.get((div, g))
    if entries:
        from build_college_directory import lookup
        got = lookup(entries, school, state)
        reg = NR.canonicalRegion(div, got) if got else None
        if reg:
            return reg, "manual"
    if NR.rules(div).get("data_regions", True):
        reg = NR.canonicalRegion(div, data_region)
        if reg:
            return reg, "data"
    return None, None


# ------------------------------------------------------------------ #
#  2. teams and races (pure: rows in, structures out)
# ------------------------------------------------------------------ #

def buildTeams(athletes, div, g, official, overrides):
    """Teams from athlete rows {person_id, name, school, state, rating,
    n_races, region, conference}. Returns (teams, team_of_person).
    A team is (school, state), as everywhere on the site (team_rank)."""
    from meet_compile import isTeam
    by = defaultdict(list)
    meta = {}
    for a in athletes:
        if a.get("rating") is None or not isTeam(a.get("school")):
            continue
        k = (a["school"], a.get("state"))
        by[k].append(a)
        m = meta.setdefault(k, defaultdict(int))
        m[(a.get("region"), a.get("conference"))] += 1
    teams = []
    for (school, state), rows in sorted(by.items()):
        rows.sort(key=lambda a: (-float(a["rating"]), a["person_id"]))
        (data_region, conf), _n = max(meta[(school, state)].items(), key=lambda kv: kv[1])
        region, how = resolveRegion(div, g, school, state, data_region, official, overrides)
        runners = [{"pid": a["person_id"], "name": a.get("name") or "Unknown",
                    "rating": float(a["rating"]), "n_races": a.get("n_races")}
                   for a in rows]
        top = [r["rating"] for r in runners[:SCORERS]]
        teams.append({"school": school, "state": state, "region": region,
                      "region_src": how, "conference": conf,
                      "runners": runners, "n_rated": len(runners),
                      "top5": round(sum(top) / len(top), 2) if top else None})
    team_of = {}
    for i, t in enumerate(teams):
        for r in t["runners"]:
            team_of[r["pid"]] = i
    return teams, team_of


def _placed(row):
    from meet_compile import xcPlaced
    return xcPlaced(row)


def _started(row):
    import result_status as RS
    return RS.kind(row.get("status"), row.get("time_seconds")) in ("ok", "dnf", "dq")


def dualWinner(pos_a, pos_b):
    """The winner of a dual meet between two teams' finishing positions
    (sorted overall places of their placed runners): 'a', 'b' or None.
    Seven per side score and displace, five score, the sixth breaks a
    tie -- the same rules as meet_compile.scoreRows, on two teams."""
    a, b = pos_a[:SQUAD], pos_b[:SQUAD]
    if len(a) < SCORERS or len(b) < SCORERS:
        return None
    merged = sorted([(p, 0) for p in a] + [(p, 1) for p in b])
    pts = [[], []]
    for place, (_p, side) in enumerate(merged, start=1):
        pts[side].append(place)
    sa, sb = sum(pts[0][:SCORERS]), sum(pts[1][:SCORERS])
    if sa != sb:
        return "a" if sa < sb else "b"
    xa = pts[0][SCORERS] if len(pts[0]) > SCORERS else 10 ** 6
    xb = pts[1][SCORERS] if len(pts[1]) > SCORERS else 10 ** 6
    if xa != xb:
        return "a" if xa < xb else "b"
    return None


def raceFromRows(rows, team_of, name_team, dual=False):
    """One race (meet_id, div_id) as ncaa_select.raceWins reads it.

    rows: dicts {person_id, school, time_seconds, status}, any order.
    team_of: person_id -> team id; name_team: school string -> team id when
    the name is one division team's alone (a teammate with no season rating
    still runs for the team).
    ⚠ EVERY RUNNER IN THE RACE TAKES A PLACE, the other divisions' too: a
      D1 win is read off the race's whole standings (the D1 manual, 2-2).
    """
    from meet_compile import scoreRows
    rows = sorted(rows, key=lambda r: (not _placed(r), r.get("time_seconds") or 1e12))
    scored, starters = [], defaultdict(set)
    key_of = {}
    for r in rows:
        t = team_of.get(r.get("person_id"))
        if t is None:
            t = name_team.get((r.get("school") or "").strip())
        school = f"\x1e{t}" if t is not None else (r.get("school") or "")
        if t is not None:
            key_of[school] = t
            if _started(r):
                starters[t].add(r.get("person_id"))
        scored.append({"school": school, "time_seconds": r.get("time_seconds"),
                       "status": r.get("status")})
    res = scoreRows(scored)
    order = [key_of[t["school"]] for t in res["teams"] if t["school"] in key_of]
    done = set(order)
    dnf = [t for t, s in starters.items() if t not in done and len(s) >= SCORERS]
    race = {"order": order, "dnf": dnf, "starters": dict(starters)}
    if dual:
        pos = defaultdict(list)
        place = 0
        for r in scored:
            if not _placed(r):
                continue
            place += 1
            t = key_of.get(r["school"])
            if t is not None:
                pos[t].append(place)
        full = sorted(t for t, p in pos.items() if len(p) >= SCORERS)
        duals = {}
        for i, a in enumerate(full):
            for b in full[i + 1:]:
                w = dualWinner(pos[a], pos[b])
                duals[(a, b)] = a if w == "a" else b if w == "b" else None
        race["dual"] = duals
    return race


def seasonWins(races, n, rules, seven):
    """(W, last, meets, single) over the counted races.

    races: [{"id": int, "day": iso, "race": raceFromRows(...)}]
    meets[i, j]: races in which i and j both counted; single[i, j]: the race
    id when that is exactly one, else -1 (D2's same-meet chain rule)."""
    wins = []
    meets = np.zeros((n, n), dtype=np.int32)
    single = np.full((n, n), -1, dtype=np.int64)
    for rc in races:
        got = S.raceWins(rc["race"], rules, seven)
        wins += [(w, lo, rc["day"]) for w, lo in got]
        seen = set()
        for w, lo in got:
            pair = (min(w, lo), max(w, lo))
            if pair in seen:
                continue
            seen.add(pair)
            meets[w, lo] += 1
            meets[lo, w] += 1
            single[w, lo] = single[lo, w] = rc["id"] if meets[w, lo] == 1 else -1
    W, last = S.winMatrix(n, wins)
    return W, last, meets, single


# ------------------------------------------------------------------ #
#  3. the simulation
# ------------------------------------------------------------------ #

def _regionArrays(teams, members):
    """The regional field of one region: each team's top seven (a team of
    fewer than five runs them as individuals, as at the real meet)."""
    pid, mu, team_local, gteam = [], [], [], []
    local = {}
    for t in members:
        for r in teams[t]["runners"][:SQUAD]:
            if r["rating"] <= 0:
                continue
            li = local.setdefault(t, len(local))
            pid.append(r["pid"])
            mu.append(-math.log(r["rating"]))
            team_local.append(li)
            gteam.append(t)
    n_local = len(local)
    counts = np.bincount(np.asarray(team_local, dtype=np.int64), minlength=n_local) \
        if team_local else np.zeros(0, dtype=np.int64)
    return {"pid": pid, "mu": np.asarray(mu, dtype=float),
            "team": np.asarray(team_local, dtype=np.int64),
            "gteam": np.asarray(gteam, dtype=np.int64),
            "teams": [t for t, _i in sorted(local.items(), key=lambda kv: kv[1])],
            "full": counts >= SCORERS, "cap": np.minimum(counts, SQUAD)}


def _drawRace(rng, arr, draws):
    import race_sim as R
    n_t = len(arr["teams"])
    sigma = np.full(arr["mu"].shape[0], RACE_SIGMA)
    times = R._draw(rng, arr["mu"], sigma, arr["team"], n_t, 0.0, draws)
    scores, _places = R._scoreDraws(times, arr["team"], arr["full"], arr["cap"], n_t)
    return times, scores


def _teamOrder(score_row, jitter):
    """Scoring teams by score, low first; a tied score goes by `jitter`.
    ⚠ THE RULE BOOK BREAKS A TIE ON THE SIXTH RUNNER; race_sim's scorer
      does not return the sixth's place, so a tie here is a coin flip."""
    ok = ~np.isnan(score_row)
    idx = np.nonzero(ok)[0]
    return idx[np.lexsort((jitter[idx], score_row[idx]))]


def simulate(teams, region_of, W, last, meets, single, rules, draws, seed=0,
             strength=None):
    """Run `draws` Novembers. Returns the per-team and per-runner tallies."""
    rng = np.random.default_rng(seed)
    T = len(teams)
    regions = [r for r in rules["regions"] if any(region_of.get(t) == r for t in range(T))]
    arrays, times_by, scores_by = {}, {}, {}
    for reg in regions:
        arr = _regionArrays(teams, [t for t in range(T) if region_of.get(t) == reg])
        if not arr["teams"]:
            continue
        arrays[reg] = arr
        times_by[reg], scores_by[reg] = _drawRace(rng, arr, draws)
    day = rules["regional_date"].isoformat()
    add_reg = rules.get("window_includes_regionals")
    meth = rules["method"]

    tally = {k: np.zeros(T) for k in ("auto", "at_large", "pushed", "qual",
                                      "reg_place", "reg_n", "reg_score",
                                      "nat_win", "nat_top3", "nat_place", "nat_n")}
    run_ind = defaultdict(float)
    run_place = defaultdict(float)
    run_n = defaultdict(int)
    nat_runner = {}
    for t in range(T):
        for r in teams[t]["runners"][:SQUAD]:
            if r["rating"] > 0:
                nat_runner[r["pid"]] = (t, -math.log(r["rating"]))

    for d in range(draws):
        orders, scores = {}, {}
        finish, team_of_runner, time_of = {}, {}, {}
        for reg, arr in arrays.items():
            srow = scores_by[reg][d]
            jit = rng.random(srow.shape[0])
            loc = _teamOrder(srow, jit)
            order = [arr["teams"][i] for i in loc]
            orders[reg] = order
            for pl, i in enumerate(loc, start=1):
                t = arr["teams"][i]
                scores[t] = float(srow[i])
                tally["reg_place"][t] += pl
                tally["reg_n"][t] += 1
                tally["reg_score"][t] += scores[t]
            trow = times_by[reg][d]
            fo = np.argsort(trow, kind="stable")
            finish[reg] = [arr["pid"][i] for i in fo]
            for pl, i in enumerate(fo, start=1):
                p = arr["pid"][i]
                run_place[p] += pl
                run_n[p] += 1
                time_of[p] = float(trow[i])
                team_of_runner[p] = int(arr["gteam"][i])
        if add_reg:
            W2, last2 = S.addRegionals(W, last, orders, day)
        else:
            W2, last2 = W, last
        single2 = None
        if meth == "d2":
            single2 = _singleWithRegionals(meets, single, orders)
        sel = S.selectTeams(meth, orders, scores, W2, last2, rules, strength, single2)
        field = sel["field"]
        for t in sel["auto"]:
            tally["auto"][t] += 1
        for t in sel["at_large"]:
            tally["at_large"][t] += 1
        for t in sel["pushed"]:
            tally["pushed"][t] += 1
        qualified = set(field)
        for t in qualified:
            tally["qual"][t] += 1
        n_team_qual = {reg: sum(1 for t in order if t in qualified)
                       for reg, order in orders.items()}
        inds = S.selectIndividuals(finish, team_of_runner, qualified, rules,
                                   n_team_qual, time_of)
        for p, _reg, _k in inds:
            run_ind[p] += 1
        _nationals(rng, teams, field, [p for p, _r, _k in inds], nat_runner, tally)
    return {"tally": tally, "run_ind": run_ind, "run_place": run_place,
            "run_n": run_n, "regions": list(arrays), "arrays": arrays}


def _singleWithRegionals(meets, single, orders):
    """D2's single-meeting matrix with the regional as one more meeting."""
    s2 = single.copy()
    for order in orders.values():
        if len(order) < 2:
            continue
        idx = np.asarray(order)
        blk = np.ix_(idx, idx)
        # never met before: the regional is their one meeting (id -2, which
        # no season race has); met once: now twice, so no single meeting
        s2[blk] = np.where(meets[blk] == 0, -2, -1)
    return s2


def _nationals(rng, teams, field, individuals, nat_runner, tally):
    """One nationals: the selected teams' sevens and the individual
    qualifiers (who take places and score for nobody), one draw."""
    import race_sim as R
    mu, team = [], []
    local = {t: i for i, t in enumerate(field)}
    for t in field:
        for r in teams[t]["runners"][:SQUAD]:
            if r["rating"] > 0:
                mu.append(-math.log(r["rating"]))
                team.append(local[t])
    for p in individuals:
        got = nat_runner.get(p)
        if got:
            mu.append(got[1])
            team.append(-1)
    if not field or not mu:
        return
    mu = np.asarray(mu)
    team = np.asarray(team, dtype=np.int64)
    n_t = len(field)
    counts = np.bincount(team[team >= 0], minlength=n_t)
    times = R._draw(rng, mu, np.full(mu.shape[0], RACE_SIGMA), team, n_t, 0.0, 1)
    scores, _pl = R._scoreDraws(times, team, counts >= SCORERS,
                                np.minimum(counts, SQUAD), n_t)
    row = scores[0]
    loc = _teamOrder(row, rng.random(n_t))
    for pl, i in enumerate(loc, start=1):
        t = field[i]
        tally["nat_place"][t] += pl
        tally["nat_n"][t] += 1
        if pl == 1:
            tally["nat_win"][t] += 1
        if pl <= 3:
            tally["nat_top3"][t] += 1


# ------------------------------------------------------------------ #
#  4. one board
# ------------------------------------------------------------------ #

def project(div, gender, teams, team_of, races, draws, seed=0, asof=None):
    """Everything the page shows for one (division, gender), as a dict
    ready for json. `races` are rows already filtered to the window and
    the distance floor: [{"id", "day", "name", "rows": [...]}]."""
    rules = NR.rules(div)
    g = NR.GENDERS[gender][0]
    T = len(teams)
    region_of = {i: t["region"] for i, t in enumerate(teams) if t["region"]}
    seven = {i: {r["pid"] for r in t["runners"][:SQUAD]} for i, t in enumerate(teams)}
    # a name that is one team's alone carries that team's unrated runners
    name_count = defaultdict(list)
    for i, t in enumerate(teams):
        name_count[t["school"]].append(i)
    name_team = {k: v[0] for k, v in name_count.items() if len(v) == 1}
    dual = rules.get("h2h_scoring") == "dual"
    built = [{"id": rc["id"], "day": rc["day"], "name": rc.get("name"),
              "race": raceFromRows(rc["rows"], team_of, name_team, dual)}
             for rc in races]
    W, last, meets, single = seasonWins(built, T, rules, seven)
    strength_order = sorted(range(T), key=lambda t: -(teams[t]["top5"] or 0))
    strength = {t: k for k, t in enumerate(strength_order)}

    sim = simulate(teams, region_of, W, last, meets, single, rules, draws, seed, strength)
    tl = sim["tally"]

    # ---- the most likely November: regionals in expected order
    exp_orders, exp_scores = {}, {}
    for reg in sim["regions"]:
        members = [t for t in sim["arrays"][reg]["teams"] if tl["reg_n"][t] > 0]
        members.sort(key=lambda t: (tl["reg_place"][t] / tl["reg_n"][t], strength[t]))
        exp_orders[reg] = members
        for t in members:
            exp_scores[t] = tl["reg_score"][t] / tl["reg_n"][t]
    if rules.get("window_includes_regionals"):
        W2, last2 = S.addRegionals(W, last, exp_orders, rules["regional_date"].isoformat())
    else:
        W2, last2 = W, last
    single2 = _singleWithRegionals(meets, single, exp_orders) if rules["method"] == "d2" else None
    det = S.selectTeams(rules["method"], exp_orders, exp_scores, W2, last2, rules,
                        strength, single2, log=True)
    field = set(det["field"])
    credit = np.zeros(T, dtype=bool)
    credit[[t for t in det["field"] if t not in det["pushed"]]] = True

    status = {}
    for t in det["auto"]:
        status[t] = "auto"
    for t in det["at_large"]:
        status[t] = "at-large"
    for t in det["pushed"]:
        status[t] = "pushed"

    # individuals in the expected order
    exp_finish, team_of_runner = {}, {}
    for reg, arr in sim["arrays"].items():
        idx = sorted(range(len(arr["pid"])),
                     key=lambda i: sim["run_place"][arr["pid"][i]] / max(sim["run_n"][arr["pid"][i]], 1))
        exp_finish[reg] = [arr["pid"][i] for i in idx]
        for i in idx:
            team_of_runner[arr["pid"][i]] = int(arr["gteam"][i])
    n_team_qual = {reg: sum(1 for t in o if t in field) for reg, o in exp_orders.items()}
    det_inds = S.selectIndividuals(exp_finish, team_of_runner, field, rules, n_team_qual)

    runner_meta = {}
    for i, t in enumerate(teams):
        for r in t["runners"][:SQUAD]:
            runner_meta[r["pid"]] = (i, r)

    def pct(x):
        return round(100.0 * x / draws, 1)

    out_teams = []
    for t in range(T):
        tm = teams[t]
        n = tl["reg_n"][t]
        out_teams.append({
            "school": tm["school"], "state": tm["state"], "region": tm["region"],
            "region_src": tm["region_src"], "conference": tm["conference"],
            "top5": tm["top5"], "n_rated": tm["n_rated"],
            "strength_rank": strength[t] + 1,
            "reg_place": round(tl["reg_place"][t] / n, 2) if n else None,
            "reg_score": round(tl["reg_score"][t] / n, 1) if n else None,
            "p_auto": pct(tl["auto"][t]), "p_at_large": pct(tl["at_large"][t] + tl["pushed"][t]),
            "p_qual": pct(tl["qual"][t]),
            "p_win": pct(tl["nat_win"][t]), "p_top3": pct(tl["nat_top3"][t]),
            "nat_place": round(tl["nat_place"][t] / tl["nat_n"][t], 1) if tl["nat_n"][t] else None,
            "proj": status.get(t, "out" if t in region_of else None),
            "wins_now": int(W[t, credit].sum()),
            "wins_season": int(W[t].sum()),
        })

    def winsDetail(t):
        """This team's counted wins over the projected field: [(team,
        races won, at the regional too)]."""
        rows = []
        for o in np.nonzero(W2[t] > 0)[0]:
            o = int(o)
            if not credit[o]:
                continue
            reg_win = int(W2[t, o] - W[t, o])
            rows.append([o, int(W[t, o]), reg_win])
        rows.sort(key=lambda x: (-(x[1] + x[2]), strength[x[0]]))
        return rows

    bubble = set()
    for rd in det["rounds"]:
        bubble |= set(rd["candidates"])
    regions_out = []
    for reg in rules["regions"]:
        order = exp_orders.get(reg, [])
        arr = sim["arrays"].get(reg)
        runners = []
        if arr:
            for p in exp_finish[reg]:
                k = sim["run_ind"].get(p, 0)
                if k <= 0:
                    continue
                t, r = runner_meta[p]
                runners.append({"pid": p, "name": r["name"], "team": t,
                                "rating": r["rating"], "p_ind": pct(k),
                                "place": round(sim["run_place"][p] / max(sim["run_n"][p], 1), 1)})
        runners.sort(key=lambda x: (-x["p_ind"], x["place"]))
        regions_out.append({"name": reg, "order": order, "individuals": runners,
                            "n_teams": len(order)})
    unplaced = sorted((t for t in range(T) if t not in region_of and teams[t]["n_rated"] >= SCORERS),
                      key=lambda t: strength[t])
    lo, hi = NR.window(div)
    return {
        "division": div, "gender": gender, "year": rules["season"],
        "asof": asof, "draws": draws, "race_sigma": RACE_SIGMA,
        "window": [lo.isoformat(), hi.isoformat()],
        "min_distance": NR.minDistance(div, g),
        "rules": {k: rules.get(k) for k in (
            "source", "confidence", "teams_total", "auto_per_region", "at_large_teams",
            "method", "ind_method", "ind_auto_per_region", "ind_at_large", "ind_total",
            "ind_auto_top", "ind_auto_top_all", "a_team_min_starters", "a_team_applies_to",
            "h2h_scoring", "dates_confidence", "nationals_site")},
        "regional_date": rules["regional_date"].isoformat(),
        "nationals_date": rules["nationals_date"].isoformat(),
        "counts": {"teams": T, "placed": len(region_of),
                   "unplaced": len(unplaced), "races": len(built),
                   "wins": int(W.sum()),
                   "region_src": {s: sum(1 for t in teams if t["region_src"] == s)
                                  for s in ("manual", "override", "data")}},
        "teams": out_teams,
        "regions": regions_out,
        "selection": {"auto": det["auto"], "at_large": det["at_large"],
                      "pushed": det["pushed"],
                      "rounds": [{"pick": rd["pick"], "pushed": rd["pushed"], "why": rd["why"],
                                  "candidates": sorted(([int(k), int(v)] for k, v in rd["candidates"].items()),
                                                       key=lambda kv: (-kv[1], strength[kv[0]]))}
                                 for rd in det["rounds"]]},
        "wins": {str(t): winsDetail(t) for t in sorted(bubble | field)},
        "individuals": [{"pid": p, "name": runner_meta[p][1]["name"], "team": runner_meta[p][0],
                         "region": reg, "kind": kind} for p, reg, kind in det_inds],
        "unplaced": unplaced[:200],
    }


# ------------------------------------------------------------------ #
#  5. the database
# ------------------------------------------------------------------ #

def loadAthletes(cur, unit, pool, year):
    from rankings import nameLateral
    cur.execute(f"""
        SELECT s.person_id, btrim(s.school) AS school, upper(btrim(s.state)) AS state,
               s.mean_rating AS rating, s.n_races, s.region, s.conference,
               COALESCE(a.name, 'Unknown') AS name
        FROM   athlete_season s
        {nameLateral("s")}
        WHERE  s.sport = 'XC' AND s.year = %(year)s AND s.pool = %(pool)s
          AND  s.division = %(unit)s AND s.mean_rating IS NOT NULL
    """, {"year": year, "pool": pool, "unit": unit})
    return [dict(r) for r in cur.fetchall()]


def loadRaces(cur, unit, pool, year, lo, hi, min_distance):
    """Every tfrrs race in [lo, hi) with one of the division's runners in
    it, at or over the distance floor -- all of its rows, every school."""
    from meet_compile import _statusSql
    cur.execute(f"""
        WITH ours AS (
            SELECT DISTINCT person_id FROM athlete_season
            WHERE  sport = 'XC' AND year = %(year)s AND pool = %(pool)s
              AND  division = %(unit)s AND person_id IS NOT NULL
        ), races AS (
            SELECT DISTINCT r.meet_id, r.div_id
            FROM   results r JOIN ours o ON o.person_id = r.person_id
            WHERE  r.source = 'tfrrs' AND r.date >= %(lo)s AND r.date < %(hi)s
        )
        SELECT r.meet_id, r.div_id, min(r.date::text) OVER (PARTITION BY r.meet_id, r.div_id) AS day,
               r.person_id, btrim(r.school) AS school, r.time_seconds, {_statusSql(cur)},
               COALESCE(dov.distance::real,
                        (mt.division_distances -> r.div_id::text ->> 'distance')::real) AS distance,
               mt.meet_name
        FROM   results r
        JOIN   races x ON x.meet_id = r.meet_id AND x.div_id = r.div_id
        LEFT JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND mt.sport = 'XC'
        LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
        WHERE  r.source = 'tfrrs'
    """, {"year": year, "pool": pool, "unit": unit, "lo": lo.isoformat(),
          "hi": hi.isoformat()})
    by = defaultdict(list)
    info = {}
    for r in cur.fetchall():
        k = (r["meet_id"], r["div_id"])
        by[k].append({"person_id": r["person_id"], "school": r["school"],
                      "time_seconds": r["time_seconds"], "status": r["status"]})
        info[k] = (str(r["day"])[:10], r["distance"], r["meet_name"])
    out, short = [], 0
    for i, (k, rows) in enumerate(sorted(by.items())):
        day, dist, name = info[k]
        # ! A RACE WITH NO DISTANCE IS KEPT, not dropped: tfrrs leaves the
        #   per-division distance blank on some meets, and dropping them
        #   would drop real wins. The floor drops only what is known short.
        if dist is not None and float(dist) < min_distance:
            short += 1
            continue
        out.append({"id": i, "day": day, "name": name, "rows": rows})
    return out, short


def liveYear(cur):
    from predict import _currentSeasonUncached
    return int(_currentSeasonUncached(cur, "XC"))


def store(conn, week, payload):
    import psycopg2.extras
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("""DELETE FROM ncaa_projection
                       WHERE week = %s AND division = %s AND gender = %s AND year = %s""",
                    (week, payload["division"], payload["gender"], payload["year"]))
        cur.execute("""INSERT INTO ncaa_projection (week, division, gender, year, payload)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (week, payload["division"], payload["gender"], payload["year"],
                     psycopg2.extras.Json(payload)))
    conn.commit()


def main():
    import race_sim
    ap = argparse.ArgumentParser()
    ap.add_argument("--division", choices=NR.DIVISIONS)
    ap.add_argument("--gender", choices=tuple(NR.GENDERS))
    ap.add_argument("--draws", type=int, default=race_sim.DRAWS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json-out", help="also write each board's payload here (a directory)")
    args = ap.parse_args()

    import psycopg2.extras
    from build_rank_snapshot import weekKey
    from database import getConn

    today = datetime.date.today()
    week = weekKey(today)
    official, overrides = loadOfficial(), loadOverrides()
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            year = liveYear(cur)
            print(f"  NCAA projection: XC season {year}, week of {week}, {args.draws} draws")
            for div in ([args.division] if args.division else NR.DIVISIONS):
                rules = NR.rules(div)
                if rules["season"] != year:
                    # ⚠ THE RULES ARE ONE SEASON'S. A new season needs its
                    #   manual read and ncaa_rules updated; until then the
                    #   dates and counts would be last year's.
                    print(f"  {div}: ncaa_rules is for {rules['season']}, the live season is "
                          f"{year} -- skipped until the rules are updated")
                    continue
                lo, hi = NR.window(div)
                hi_eff = min(hi, today + datetime.timedelta(days=1))
                for gender in ([args.gender] if args.gender else tuple(NR.GENDERS)):
                    g, pool, _w = NR.GENDERS[gender]
                    t0 = time.time()
                    ath = loadAthletes(cur, NR.UNIT[div], pool, year)
                    teams, team_of = buildTeams(ath, div, g, official, overrides)
                    races, short = loadRaces(cur, NR.UNIT[div], pool, year, lo, hi_eff,
                                             NR.minDistance(div, g))
                    conn.rollback()
                    payload = project(div, gender, teams, team_of, races, args.draws,
                                      args.seed, asof=today.isoformat())
                    payload["counts"]["races_short"] = short
                    c = payload["counts"]
                    print(f"  {div} {gender}: {c['teams']} teams, {c['placed']} placed "
                          f"({c['region_src']}), {c['unplaced']} unplaced with 5+ rated; "
                          f"{c['races']} races ({short} under the distance floor), "
                          f"{c['wins']} counted wins; {time.time() - t0:.1f}s")
                    if args.json_out:
                        os.makedirs(args.json_out, exist_ok=True)
                        with open(os.path.join(args.json_out, f"{div}_{gender}.json"), "w") as fh:
                            json.dump(payload, fh)
                    if not args.dry_run:
                        store(conn, week, payload)
    print("  done")


if __name__ == "__main__":
    main()
