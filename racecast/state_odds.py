# Project: xc-predictor / racecast
# File:    state_odds.py
# Purpose: State odds (owner, 2026-10-10, approved: "state-qualifying odds for
#          every athlete and team"). For each state, division and gender of
#          high school cross country: each athlete's chance to qualify for the
#          state meet and to finish top 25 / top 8 / first there, and each
#          team's chance to qualify as a team, finish top 3 and win.
#
#              /state-odds/<st>?division=<slug>&gender=boys|girls
#
#          Built nightly by build_state_odds.py into three tables; every page
#          (this one, the athlete line, the school page, the projection) only
#          READS them. Nothing here simulates on a page view.
#
# ★ NO NEW MODEL, NO NEW SCORER, NO NEW FIELD (2026-10-10).
#     * the FIELD is projections._fieldUncached -- every school's best seven
#       by season rating, the field /projections publishes;
#     * the CENTRE of each athlete's day is that season rating, the order
#       /projections publishes;
#     * the SPREAD is predict._formRating's sigma -- the log-rating band
#       predict._ratingTimes puts on every rating-served prediction, read
#       back through race_sim.sigmaFromPred (floor and ceiling included), so
#       a page's "41% to win" comes out of the same band its "15:42 (15:20 to
#       16:05)" does;
#     * every race -- a section final and the state meet alike -- is scored
#       by race_sim._scoreDraws, the vectorised predict._score.
#
# ★ QUALIFYING IS SIMULATED ONLY WHERE THE REPO KNOWS HOW IT WORKED. No state's
#   rule book is typed in anywhere in this repo (cuts.py: "the marks are read
#   off who was actually there, not off a rule book"), and none is typed in
#   here. What the repo DOES have is cuts.py's record of last season's rounds
#   before state: each section / regional final, and which of its finishers
#   went on to run the state meet -- split by cuts.advancerSplit into teams
#   (a school that took TEAM_SCORERS or more) and individuals. Where that
#   record covers EVERY team in the projected field (roundsFor), the season
#   is simulated round by round: each round sends as many teams and
#   individuals as it sent last season, then the state meet is run with
#   whoever got through. Anywhere else the page says so and shows only the
#   state meet, run with the whole projected field, labelled "if they make
#   state" -- never a qualifying number made up to fill the gap.
#
# ⚠ WHAT IS NOT MODELLED, AND THE PAGES SAY SO: entries (who a coach runs),
#   injuries, a changed qualifying rule, division moves, and any correlation
#   between an athlete's section race and their state race (each race is an
#   independent draw from the same form band -- the same floor race_sim
#   keeps with team_rho=0: the narrowest the spread can honestly be).
import hashlib
import json
import math

import numpy as np

import race_sim
import ttlcache

# ------------------------------------------------------------------ #
#  how many simulations
# ------------------------------------------------------------------ #

# ★ THE PAGES PRINT WHOLE PERCENTS, so a probability is shown to DISPLAY_STEP.
DISPLAY_STEP = 0.01
# ★ DRAWS IS SOLVED FROM THE PRECISION SHOWN, NOT PICKED (2026-10-10). A
#   Monte Carlo probability over N independent seasons has standard error
#   sqrt(p(1-p)/N), largest at p = 1/2 where it is sqrt(1/(4N)). Holding that
#   worst case to half the printed step -- so the simulation's own noise is
#   smaller than the rounding the page already does -- gives
#       N = 1 / (4 * (DISPLAY_STEP / 2)^2) = 10,000.
#   At the numbers a reader leans on (near 0 or near 1) the error is far
#   smaller: at p = 0.05 it is 0.22 points. Every odds number published is
#   UNCONDITIONAL (a share of all N seasons), so N is the sample behind each
#   one; no published number is a share of a smaller subset.
DRAWS = int(math.ceil(1.0 / (4.0 * (DISPLAY_STEP / 2.0) ** 2)))

# ★ THE PLACE LINES ARE cuts.PLACE_LINES -- "Top 25" and "Top 8", the lines
#   /what-it-takes already publishes and already says are not each state's
#   rule -- plus first place. The team line is top 3 (the owner's ask).
TEAM_TOP = 3


def placeLines():
    """[(key, n, label)] for the individual lines, cuts' own."""
    import cuts
    return list(cuts.PLACE_LINES)


# Modes a division can be simulated in.
MODE_QUAL = "qualifying"        # last season's rounds, then the state meet
MODE_STATE = "state_only"       # the state meet alone: "if they make state"

GENDERS = {"boys": ("hs_m", "Boys"), "girls": ("hs_f", "Girls")}


# ------------------------------------------------------------------ #
#  per-athlete spread
# ------------------------------------------------------------------ #

def sigmaOf(rows):
    """The log-rating sigma of one athlete's form, predict._formRating's,
    or None when there is nothing to read it from.

    rows: predict._ratingRows' rows for this athlete, newest first."""
    from predict import _formRating
    form = _formRating(rows, "XC") if rows else None
    return form[1] if form else None


def fallbackSigma():
    """⚠ AN ATHLETE WITH A SEASON RATING BUT NO ROWS predict CAN READ (a
    person-id merge mid-flight) takes the WIDEST band the predictor itself
    allows a fresh form (predict._RATING_SIGMA's ceiling) -- the least
    certain the model ever is about a current runner, not a number made up
    here."""
    from predict import _RATING_SIGMA
    return float(_RATING_SIGMA[1])


# ------------------------------------------------------------------ #
#  the rounds before state, from cuts' record
# ------------------------------------------------------------------ #

def _slug(v):
    import projections as P
    return P.divisionSlug(v) if v else None


def _cellFor(cells, units, division_slug):
    """The round cell one school's runners go through, or None.

    ★ THE SAME MATCH cuts.lineFor MAKES for the athlete line: a section cell
      by the school's section and section division ("all" when the school
      has none on record); a regional cell by the school's region and its
      state division. A section is tried first -- cuts.meetLevel files a
      meet that names both as a section."""
    sec = (units.get("section") or "").upper()
    if sec:
        sdiv = _slug(str(units["section_div"]).upper()) \
            if units.get("section_div") else "all"
        for c in cells:
            if c["kind"] == "section" and (c["unit"] or "").upper() == sec \
                    and c["slug"] == sdiv:
                return c
    reg = (units.get("region") or "").upper()
    if reg:
        rdiv = division_slug or "all"
        for c in cells:
            if c["kind"] == "region" and (c["unit"] or "").upper() == reg \
                    and c["slug"] in (rdiv, "all"):
                return c
    return None


def usableCells(data, gender, division_slug):
    """cuts' round cells that feed this state division and can be simulated:
    a named unit, a latest season whose SAMPLE is not flagged (cuts._hardFlags
    -- the same bar the athlete line holds a section to), and the team /
    individual split on record."""
    import cuts
    out = []
    for c in cuts.sectionsFeeding(data, gender, division_slug):
        s = c.get("latest")
        if not c.get("unit") or not s or cuts._hardFlags(s):
            continue
        if s.get("teams_advanced") is None or s.get("individuals_advanced") is None:
            continue
        out.append(c)
    return out


def roundsFor(field, data, gender, division_slug, school_units):
    """(rounds, why) for one division.

    rounds: [{key, label, kind, year, teams, individuals, members}] when the
    qualifying round can be simulated for this field, else None -- and
    `why` says in words which it is.

    ★ EVERY TEAM IN THE FIELD MUST HAVE ITS ROUND ON RECORD. A round
      simulated for some schools and not others would send a state meet of
      only the covered schools' qualifiers, and every podium number in it
      would be inflated by the schools left out. So one school with no
      route on record sends the whole division to the state-only odds --
      the honest answer is "we don't know how they get there", not a
      partial one.
    ! A RUNNER WITH NO TEAM has no section on record by construction; such
      runners are left out of a simulated qualifying season (path "none")
      rather than holding the whole division back, and the page counts them.
    """
    from meet_compile import isTeam
    if not data or not data.get("genders"):
        return None, "No section or regional finals are on record for this state."
    cells = usableCells(data, gender, division_slug)
    if not cells:
        return None, ("No round before state is on record for this division "
                      "with enough rated runners to read how many advanced.")
    by_key = {}
    missing = set()
    for f in field:
        school = f.get("school")
        if not school or not isTeam(school):
            continue
        cell = _cellFor(cells, school_units.get(school) or {}, division_slug)
        if cell is None:
            missing.add(school)
            continue
        k = (cell["kind"], cell["unit"], cell["slug"])
        rd = by_key.setdefault(k, {
            "key": "|".join(str(x) for x in k), "label": cell["label"],
            "kind": cell["kind"], "year": cell["latest"]["year"],
            "teams": int(cell["latest"]["teams_advanced"]),
            "individuals": int(cell["latest"]["individuals_advanced"]),
            "members": []})
        rd["members"].append(f["person_id"])
    if missing:
        n = len(missing)
        return None, (f"{n} school{'s' if n != 1 else ''} in this field "
                      f"{'have' if n != 1 else 'has'} no section or regional "
                      "route to state on record.")
    if not by_key:
        return None, "No school in this field has a route to state on record."
    rounds = sorted(by_key.values(), key=lambda r: r["key"])
    return rounds, None


# ------------------------------------------------------------------ #
#  the simulation (pure: no database)
# ------------------------------------------------------------------ #

def _topTeams(scores, k):
    """[rows, n_teams] bool: the k best (lowest) scores per draw. A team that
    could not score (NaN) is never among them.
    ⚠ A TIE AT THE CUT goes to the team listed first (a stable sort), where
      the real rules look at the sixth runner -- the same simplification
      race_sim.simulate makes for the win, at integer scores' rare ties."""
    filled = np.where(np.isnan(scores), np.inf, scores)
    rank = np.argsort(np.argsort(filled, axis=1, kind="stable"),
                      axis=1, kind="stable")
    return (rank < k) & ~np.isnan(scores)


def simulate(field, rounds=None, draws=DRAWS, seed=0, lines=None):
    """Run the season `draws` times.

    field:  [{person_id, school, rating, sigma}] -- rating the season rating
            (higher is faster), sigma its log-rating sigma.
    rounds: None for the state meet alone, or roundsFor's rounds.

    Returns {"mode", "draws", "athletes": {pid: {...}}, "teams": {school:
    {...}}, "no_path": [pid]}. Per athlete p_qualify (None with no rounds),
    one p_<key> per place line, p_win, place_mean (over the seasons they
    reached state). Per team p_qualify (None with no rounds), p_top3, p_win,
    score_mean (over the seasons it scored at state).
    """
    lines = lines if lines is not None else placeLines()
    rated = [f for f in field if f.get("rating") and float(f["rating"]) > 0]
    no_path = []
    round_of = None
    if rounds is not None:
        member = {}
        for i, rd in enumerate(rounds):
            for pid in rd["members"]:
                member.setdefault(pid, i)
        no_path = [f["person_id"] for f in rated if f["person_id"] not in member]
        rated = [f for f in rated if f["person_id"] in member]
        round_of = np.array([member[f["person_id"]] for f in rated],
                            dtype=np.int64)
    mode = MODE_QUAL if rounds is not None else MODE_STATE
    out = {"mode": mode, "draws": draws, "athletes": {}, "teams": {},
           "no_path": no_path}
    if not rated:
        return out
    # ★ ONLY THE ORDER IS SCORED, so a rating's reciprocal stands in for its
    #   time: log(1/rating) moves exactly as -log(rating), and the log-rating
    #   sigma is then the log-time sigma -- the same algebra predict.
    #   _ratingTimes uses for its band (rating * exp(+-sigma) -> lo / hi).
    #   race_sim's own header: a uniform scale on every time changes no place.
    preds = [{"seconds": 1.0 / float(f["rating"]),
              "sigma_pct": 100.0 * float(f["sigma"])} for f in rated]
    prep = race_sim.prepare(rated, preds)
    if prep is None or len(prep["person"]) != len(rated):
        raise ValueError("race_sim.prepare dropped a rated runner")
    mu, sigma, team = prep["mu"], prep["sigma"], prep["team"]
    names, full, cap = prep["names"], prep["full"], prep["cap"]
    n, n_teams = mu.shape[0], len(names)

    specs = []
    if rounds is not None:
        for i, rd in enumerate(rounds):
            idx = np.where(round_of == i)[0]
            g_teams = np.unique(team[idx][team[idx] >= 0])
            local = {int(t): j for j, t in enumerate(g_teams)}
            l_team = np.array([local.get(int(t), -1) for t in team[idx]],
                              dtype=np.int64)
            specs.append((rd, idx, g_teams, l_team, full[g_teams], cap[g_teams]))

    n_qual = np.zeros(n)
    n_line = {key: np.zeros(n) for key, _n, _l in lines}
    n_first = np.zeros(n)
    place_sum = np.zeros(n)
    t_qual = np.zeros(n_teams)
    t_top = np.zeros(n_teams)
    t_win = np.zeros(n_teams)
    t_score = np.zeros(n_teams)
    t_scored = np.zeros(n_teams)

    rng = np.random.default_rng(seed)
    step = max(1, race_sim._CHUNK_CELLS // max(n, 1))
    has_team = team >= 0
    team_c = np.where(has_team, team, 0)
    for lo in range(0, draws, step):
        rows = min(step, draws - lo)
        if rounds is not None:
            # -- the rounds before state: each its own race --------------
            t_sec = race_sim._draw(rng, mu, sigma, team, n_teams, 0.0, rows)
            qual = np.zeros((rows, n), dtype=bool)
            team_q = np.zeros((rows, n_teams), dtype=bool)
            for rd, idx, g_teams, l_team, full_l, cap_l in specs:
                sc, pl = race_sim._scoreDraws(t_sec[:, idx], l_team, full_l,
                                              cap_l, len(g_teams))
                tq = (_topTeams(sc, rd["teams"]) if len(g_teams)
                      else np.zeros((rows, 0), dtype=bool))
                if len(g_teams):
                    team_q[:, g_teams] = tq
                    on_q = tq[:, np.where(l_team >= 0, l_team, 0)] \
                        & (l_team >= 0)[None, :]
                else:
                    on_q = np.zeros((rows, idx.shape[0]), dtype=bool)
                # ★ THEN THE INDIVIDUALS: the first `individuals` finishers
                #   not already going with a qualifying team.
                order = np.argsort(pl, axis=1, kind="stable")
                cand = ~np.take_along_axis(on_q, order, axis=1)
                take = cand & (np.cumsum(cand, axis=1) <= rd["individuals"])
                ind_q = np.zeros_like(take)
                np.put_along_axis(ind_q, order, take, axis=1)
                qual[:, idx] = on_q | ind_q
            # a qualifier from a school whose TEAM did not qualify races
            # unattached at state: a place, no team points
            team_state = (np.where(has_team[None, :] & team_q[:, team_c],
                                   team[None, :], -1) if n_teams
                          else np.full((rows, n), -1, dtype=np.int64))
            t_qual += team_q.sum(axis=0)
        else:
            qual = np.ones((rows, n), dtype=bool)
            team_state = team
        # -- the state meet ------------------------------------------------
        times = race_sim._draw(rng, mu, sigma, team, n_teams, 0.0, rows)
        # ! A NON-QUALIFIER IS NOT IN THE RACE: drawn last and unattached,
        #   so they take no scoring place and push nobody back.
        times = np.where(qual, times, np.inf)
        sc, pl = race_sim._scoreDraws(times, team_state, full, cap, n_teams)
        n_qual += qual.sum(axis=0)
        for key, k, _label in lines:
            n_line[key] += (qual & (pl <= k)).sum(axis=0)
        n_first += (qual & (pl == 1)).sum(axis=0)
        place_sum += np.where(qual, pl, 0).sum(axis=0)
        if n_teams:
            ok = ~np.isnan(sc)
            filled = np.where(ok, sc, np.inf)
            best = filled.min(axis=1)
            anyone = np.isfinite(best)
            wins = (filled == best[:, None]) & anyone[:, None]
            # a tie splits the win, as race_sim.simulate credits it
            n_tied = np.maximum(wins.sum(axis=1), 1)
            t_win += (wins / n_tied[:, None]).sum(axis=0)
            rank = np.argsort(np.argsort(filled, axis=1, kind="stable"),
                              axis=1, kind="stable") + 1
            t_top += ((rank <= TEAM_TOP) & ok).sum(axis=0)
            t_score += np.where(ok, sc, 0.0).sum(axis=0)
            t_scored += ok.sum(axis=0)

    q = rounds is not None
    for k, pid in enumerate(prep["person"]):
        a = {"p_qualify": float(n_qual[k] / draws) if q else None,
             "p_win": float(n_first[k] / draws),
             "place_mean": (float(place_sum[k] / n_qual[k])
                            if n_qual[k] else None)}
        for key, _n, _l in lines:
            a["p_" + key] = float(n_line[key][k] / draws)
        out["athletes"][pid] = a
    for j, name in enumerate(names):
        out["teams"][name] = {
            "p_qualify": float(t_qual[j] / draws) if q else None,
            "p_top3": float(t_top[j] / draws),
            "p_win": float(t_win[j] / draws),
            "score_mean": (float(t_score[j] / t_scored[j])
                           if t_scored[j] else None),
            "n_runners": int((team == j).sum())}
    return out


# ------------------------------------------------------------------ #
#  fingerprint: what makes a rebuild necessary
# ------------------------------------------------------------------ #

# ★ BUMPED BY HAND WHEN THE SIMULATION ITSELF CHANGES, so the nightly build
#   recomputes every division instead of trusting yesterday's fingerprints.
MODEL_VERSION = "2026-10-10.1"


def fingerprint(field, rounds, draws=DRAWS):
    """A stable hash of everything the odds depend on. The same inputs give
    the same hash, the same seed and therefore the same numbers -- which is
    what makes the nightly build idempotent and lets it skip a division
    whose field has not moved."""
    rows = sorted((int(f["person_id"]), f.get("school") or "",
                   round(float(f["rating"]), 2), round(float(f["sigma"]), 4))
                  for f in field if f.get("rating"))
    rd = None if rounds is None else [
        (r["key"], r["year"], r["teams"], r["individuals"],
         sorted(int(p) for p in r["members"])) for r in rounds]
    blob = json.dumps({"v": MODEL_VERSION, "d": draws, "f": rows, "r": rd},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def seedOf(fp):
    return int(fp[:16], 16) % (2 ** 32)


# ------------------------------------------------------------------ #
#  words
# ------------------------------------------------------------------ #

def fmtPct(p):
    """'94%', '<1%', '>99%', '' for None. ★ NEVER '0%' OR '100%': ten
    thousand seasons without a thing happening is not a proof it cannot."""
    if p is None:
        return ""
    p = float(p)
    if p < DISPLAY_STEP / 2.0:
        return "<1%"
    if p > 1.0 - DISPLAY_STEP / 2.0:
        return ">99%"
    return f"{int(round(100.0 * p))}%"


def roundsWords(rounds):
    """'Central Section D2: 2 teams + 5 individuals (2025)' per round."""
    out = []
    for r in rounds or []:
        t, i = r.get("teams", 0), r.get("individuals", 0)
        out.append(f"{r['label']}: {t} team{'s' if t != 1 else ''} + "
                   f"{i} individual{'s' if i != 1 else ''} ({r['year']})")
    return out


# ------------------------------------------------------------------ #
#  reading the tables (pages)
# ------------------------------------------------------------------ #

_TTL = 30 * 60.0


def _rows(cur):
    out = []
    for r in cur.fetchall():
        out.append(dict(r) if not isinstance(r, (tuple, list)) else r)
    return out


def _hasTables(cur):
    cur.execute("SELECT to_regclass('public.state_odds_meta') AS t")
    row = cur.fetchone()
    t = row["t"] if isinstance(row, dict) else (row[0] if row else None)
    return t is not None


def _latestYear(cur):
    cur.execute("SELECT max(year) AS y FROM state_odds_meta")
    row = cur.fetchone()
    y = row["y"] if isinstance(row, dict) else (row[0] if row else None)
    return int(y) if y is not None else None


def stateMeta(cur, state):
    """[meta rows] for one state's latest season, division order."""
    import projections as P
    if not _hasTables(cur):
        return []
    y = _latestYear(cur)
    if y is None:
        return []
    cur.execute("""
        SELECT year, state, division, gender, division_label, mode, rounds,
               why, draws, n_field, n_no_path, built_at, as_of
        FROM   state_odds_meta WHERE state = %s AND year = %s
    """, (state, y))
    rows = _rows(cur)
    for r in rows:
        if isinstance(r.get("rounds"), str):
            r["rounds"] = json.loads(r["rounds"])
    rows.sort(key=lambda r: (P.divisionSortKey(r["division"]), r["gender"]))
    return rows


def divisionOdds(cur, state, division, gender, year):
    cur.execute("""
        SELECT person_id, name, school, school_state, grade, rating, sigma,
               p_qualify, p_top, p_podium, p_win, place_mean, path
        FROM   state_odds_athlete
        WHERE  year = %s AND state = %s AND division = %s AND gender = %s
        ORDER  BY p_podium DESC NULLS LAST, p_top DESC NULLS LAST,
                  p_qualify DESC NULLS LAST, rating DESC
    """, (year, state, division, gender))
    athletes = _rows(cur)
    cur.execute("""
        SELECT school, school_state, n_runners, p_qualify, p_top3, p_win,
               score_mean, path
        FROM   state_odds_team
        WHERE  year = %s AND state = %s AND division = %s AND gender = %s
        ORDER  BY p_win DESC, p_top3 DESC, p_qualify DESC NULLS LAST,
                  score_mean NULLS LAST
    """, (year, state, division, gender))
    return athletes, _rows(cur)


def foldBelowStep(rows, keys):
    """(shown, n_folded): rows that would print '<1%' on EVERY odds column
    are folded into a count -- the cut is the display step itself, not a
    number picked for the page."""
    shown, folded = [], 0
    for r in rows:
        if any(r.get(k) is not None and float(r[k]) >= DISPLAY_STEP / 2.0
               for k in keys):
            shown.append(r)
        else:
            folded += 1
    return shown, folded


def _connCall(fn, default):
    try:
        import psycopg2.extras
        from database import getConn
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                return fn(cur)
    except Exception:                                   # noqa: BLE001
        return default


def athleteOdds(person_id):
    """The athlete line's row, or None: the newest season's odds for this
    athlete, with the division's mode and label. Cached briefly; never
    raises (a missing table is no line)."""
    def compute():
        def q(cur):
            if not _hasTables(cur):
                return None
            cur.execute("""
                SELECT a.state, a.division, a.gender, a.p_qualify, a.p_top,
                       a.p_podium, a.p_win, a.path, m.mode, m.division_label,
                       m.year
                FROM   state_odds_athlete a
                JOIN   state_odds_meta m USING (year, state, division, gender)
                WHERE  a.person_id = %s
                ORDER  BY a.year DESC LIMIT 1
            """, (int(person_id),))
            row = cur.fetchone()
            return dict(row) if row else None
        return _connCall(q, None)
    try:
        val, _ = ttlcache.get(("odds-ath", int(person_id)), compute, ttl=_TTL)
    except (TypeError, ValueError):
        return None
    return athleteLine(val) if val else None


def athleteLine(row):
    """The words of the athlete line from one odds row (pure)."""
    import cuts
    if not row or row.get("path") == "none":
        return None
    podium_n = dict((k, n) for k, n, _l in cuts.PLACE_LINES).get("podium")
    st = (row.get("state") or "").lower()
    href = (f"/state-odds/{st}?division={row['division']}"
            f"&gender={row['gender']}")
    out = {"href": href, "label": row.get("division_label"),
           "podium_n": podium_n, "mode": row.get("mode"),
           "qualify": fmtPct(row.get("p_qualify"))
           if row.get("mode") == MODE_QUAL else None,
           "podium": fmtPct(row.get("p_podium"))}
    return out


def schoolOdds(school, state):
    """[team odds rows with mode/label] for one school's newest season, one
    per gender (and division). Never raises."""
    def compute():
        def q(cur):
            if not _hasTables(cur):
                return []
            cur.execute("""
                SELECT t.state, t.division, t.gender, t.p_qualify, t.p_top3,
                       t.p_win, t.path, m.mode, m.division_label, m.year
                FROM   state_odds_team t
                JOIN   state_odds_meta m USING (year, state, division, gender)
                WHERE  t.school = %s AND (%s::text IS NULL OR t.state = %s)
                  AND  t.year = (SELECT max(year) FROM state_odds_meta)
                ORDER  BY t.gender, t.division
            """, (school, state, state))
            return [dict(r) for r in cur.fetchall()]
        return _connCall(q, [])
    val, _ = ttlcache.get(("odds-school", school, state), compute, ttl=_TTL)
    out = []
    for r in val or []:
        st = (r.get("state") or "").lower()
        out.append(dict(r, href=(f"/state-odds/{st}?division={r['division']}"
                                 f"&gender={r['gender']}"),
                        words=GENDERS.get(r["gender"], (None, r["gender"]))[1]))
    return out


def projectionOdds(state, division, gender):
    """{school: team odds} and the mode for /projections' team table, or
    None when the odds are not built for that division."""
    def compute():
        def q(cur):
            if not _hasTables(cur):
                return None
            y = _latestYear(cur)
            if y is None:
                return None
            cur.execute("""SELECT mode FROM state_odds_meta WHERE year = %s
                           AND state = %s AND division = %s AND gender = %s""",
                        (y, state, division, gender))
            m = cur.fetchone()
            if not m:
                return None
            cur.execute("""SELECT school, p_qualify, p_top3, p_win
                           FROM state_odds_team WHERE year = %s AND state = %s
                           AND division = %s AND gender = %s""",
                        (y, state, division, gender))
            return {"mode": dict(m)["mode"],
                    "teams": {r["school"]: dict(r) for r in cur.fetchall()}}
        return _connCall(q, None)
    val, _ = ttlcache.get(("odds-proj", state, division, gender), compute,
                          ttl=_TTL)
    return val


# ------------------------------------------------------------------ #
#  the page
# ------------------------------------------------------------------ #

from flask import Blueprint, abort, redirect, render_template, request  # noqa: E402

bp = Blueprint("state_odds", __name__)


@bp.app_template_filter("odds_pct")
def _oddsPct(p):
    return fmtPct(p)


@bp.app_template_global("state_odds_athlete")
def _tplAthlete(person_id):
    return athleteOdds(person_id) if person_id else None


@bp.app_template_global("state_odds_school")
def _tplSchool(school, state=None):
    return schoolOdds(school, state) if school else []


@bp.app_template_global("state_odds_projection")
def _tplProjection(state, division, gender):
    return projectionOdds(state, division, gender) if state else None


def _stateNames():
    from landing import STATE_NAMES, US_STATES
    return [(c, STATE_NAMES[c]) for c in US_STATES if c in STATE_NAMES], \
        STATE_NAMES


def pageContext(state, metas, division, gender, athletes, teams, names):
    """Everything the template needs, from rows already read (pure, so the
    screenshots' mocks and the tests drive the same code the route does)."""
    import cuts
    divs = []
    for m in metas:
        if m["division"] not in [d["slug"] for d in divs]:
            divs.append({"slug": m["division"], "label": m["division_label"]})
    meta = next((m for m in metas if m["division"] == division
                 and m["gender"] == gender), None)
    keys = ["p_qualify", "p_top", "p_podium", "p_win"]
    shown, folded = foldBelowStep(athletes, keys)
    lines = {k: lab for k, _n, lab in cuts.PLACE_LINES}
    return {"state": state, "state_name": names.get(state, state),
            "divisions": divs, "division": division, "gender": gender,
            "gender_words": GENDERS[gender][1], "meta": meta,
            "athletes": shown, "n_folded": folded,
            "n_no_path": sum(1 for a in athletes if a.get("path") == "none"),
            "teams": [t for t in teams if t.get("n_runners") is None
                      or t["n_runners"] > 0],
            "top_label": lines.get("top"), "podium_label": lines.get("podium"),
            "team_top": TEAM_TOP, "draws": (meta or {}).get("draws") or DRAWS,
            "display_step": DISPLAY_STEP,
            "rounds_words": roundsWords((meta or {}).get("rounds")),
            "MODE_QUAL": MODE_QUAL}


@bp.route("/state-odds")
def odds_index():
    st = (request.args.get("state") or "").strip().upper()
    if st:
        return redirect(f"/state-odds/{st.lower()}", code=302)
    return redirect("/projections", code=302)


@bp.route("/state-odds/<state>")
def odds_page(state):
    _states, names = _stateNames()
    st = (state or "").upper()
    if st not in names:
        abort(404)
    if state != st.lower():
        q = ("?" + request.query_string.decode("utf-8", "replace")
             if request.query_string else "")
        return redirect(f"/state-odds/{st.lower()}{q}", code=301)
    gender = (request.args.get("gender") or request.args.get("g")
              or "boys").strip().lower()
    if gender not in GENDERS:
        gender = "boys"
    want = _slug(request.args.get("division") or "") or None
    import psycopg2.extras
    from database import getConn
    athletes, teams = [], []
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            metas = stateMeta(cur, st)
            if metas:
                have = [m["division"] for m in metas if m["gender"] == gender] \
                    or [m["division"] for m in metas]
                if want is None or want not in have:
                    if want is not None and want not in [m["division"] for m in metas]:
                        abort(404)
                    want = want if want in have else have[0]
                athletes, teams = divisionOdds(cur, st, want, gender,
                                               metas[0]["year"])
    ctx = pageContext(st, metas, want, gender, athletes, teams, names)
    return render_template("state_odds.html", **ctx)


def asOf(dates):
    """The newest race date among the field's rows ('YYYY-MM-DD'), or None."""
    ds = [d for d in dates if d]
    if not ds:
        return None
    return max(str(d) for d in ds)
