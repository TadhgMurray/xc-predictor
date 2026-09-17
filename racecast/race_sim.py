"""
race_sim.py -- the predicted race run many times, so a prediction can say
how likely rather than only how much.

    from race_sim import simulate, bestLineup
    out = simulate(field, preds, draws=2000)
    out["teams"]["Ipswich"]["p_win"]          # 0.41
    out["h2h"][("Ipswich", "Masconomet")]     # 0.63
    best = bestLineup(field, preds, "Ipswich")

★ WHY (owner, 2026-09-16: "what are the chances this team beats this team in
  a race with the model? And can we have like the team score variance as a
  part of our model predictions"; then "if we doing monte carlo can you do
  the team lineup idea").

  The model already emits a predictive sigma per athlete -- transformer
  .predictInterval returns (seconds, lo, hi, sigma_log), and predict.py
  already carries lo/hi onto every row -- so the distribution is there and
  only the sampling was missing. One loop answers all three questions: draw
  every athlete's time from their own log-normal, score the race under the
  real rules, repeat.

★ ONE SCORER, NOT TWO. The rules live in predict._score and they are subtle
  (five scorers, seven displacers, a cap at what the team ENTERED, unattached
  and incomplete teams lifted out of the scoring order). Reimplementing them
  here would be a second truth that drifts, so scoreDraw is a vectorised
  transcription of that one and tests/test_race_sim.py asserts the two agree
  on random fields. Change _score and that test fails, which is the point.

⚠ A UNIFORM RACE-DAY EFFECT CANNOT CHANGE A SINGLE PLACE, and I said the
  opposite when this was proposed. Scoring is by PLACE; multiplying every
  runner's time by one factor is monotone, so the order -- and therefore
  every score -- is identical. A common effect matters for the TIMES a page
  shows and not at all for who wins. What does change a score is
  correlation WITHIN a team (teammates train together, travel together, and
  have good and bad days together), because that moves a team's five
  scorers as a block: `team_rho` is that, and it widens the score
  distribution without touching the mean.

⚠ AND A PROBABILITY IS ONLY AS GOOD AS THE SIGMA IT CAME FROM. If the
  model's 68% band really covers 50% of outcomes, every number here is
  confidently wrong. scripts/diag_calibration.py measures that, bucketed by
  horizon, and nothing from this module should be published before it
  passes.
"""
import math

import numpy as np

from meet_compile import isTeam
from predict import MAX_PER_TEAM, TEAM_SCORERS

# The default draw count. 2,000 puts the standard error of a win
# probability near 1% (sqrt(0.25/2000)), which is finer than anyone reads a
# percentage off a page.
DRAWS = 2000

# A floor on a per-athlete sigma in log time. A model that returns a
# near-zero band for somebody makes them a certainty, and no runner is one:
# 1.5% is about ten seconds on a 12-minute race, which is a quiet day's
# variation for a trained athlete.
SIGMA_FLOOR = 0.015
SIGMA_CEIL = 0.25          # 25% -- past this the row is not a prediction


def sigmaFromPred(pred, z=1.0):
    """The per-athlete sigma in LOG time, recovered from the band the model
    already published. predictInterval's lo/hi are at +/- z sigma in log
    space, so the half-width of log(hi/lo) is that sigma.

    ! FROM THE BAND, NOT FROM A NEW MODEL CALL. predict.py already puts lo
      and hi on every row, so a page that has drawn a prediction has
      everything the simulation needs and never loads the model twice.
    """
    secs, lo, hi = pred.get("seconds"), pred.get("lo"), pred.get("hi")
    for val in (secs, lo, hi):
        if not val or val <= 0:
            return None
    sig = (math.log(hi) - math.log(lo)) / (2.0 * max(z, 1e-9))
    if not math.isfinite(sig):
        return None
    return min(max(sig, SIGMA_FLOOR), SIGMA_CEIL)


def prepare(field, preds, z=1.0):
    """The arrays one simulation needs, or None when nothing can be scored.

    Returns a dict of parallel arrays over the runners that CAN be
    predicted, plus the team bookkeeping _score does: which teams are full,
    and how many scoring places each may take.
    """
    rows = [(f, p) for f, p in zip(field, preds)
            if p.get("seconds") and sigmaFromPred(p, z) is not None]
    if not rows:
        return None
    teams, counts, entered = [], {}, {}
    for f, _p in rows:
        t = f.get("school")
        t = t if isTeam(t) else None
        teams.append(t)
        if t is None:
            continue
        counts[t] = counts.get(t, 0) + 1
        n = f.get("entered")
        if n:
            entered[t] = max(entered.get(t, 0), int(n))

    names = sorted(counts)
    idx = {t: i for i, t in enumerate(names)}
    on_line = np.array([entered.get(t, counts[t]) for t in names], dtype=np.int64)
    return {
        "mu": np.array([math.log(p["seconds"]) for _f, p in rows]),
        "sigma": np.array([sigmaFromPred(p, z) for _f, p in rows]),
        "team": np.array([idx.get(t, -1) for t in teams], dtype=np.int64),
        "names": names,
        # a team that did not put TEAM_SCORERS on the line is not a team here
        "full": on_line >= TEAM_SCORERS,
        # ...and takes only as many scoring places as it entered, capped
        "cap": np.minimum(on_line, MAX_PER_TEAM),
        "person": [f.get("person_id") for f, _p in rows],
        "runner_team": teams,
    }


def scoreDraw(times, team, full, cap, n_teams):
    """(scores, score_place) for one drawn race. `scores` is a float array
    per team with NaN where the team could not score; score_place is per
    runner, 0 where they took none.

    A vectorised transcription of predict._score's pass 2. The rules, in the
    order they bite:
      * finish order is the drawn times, everybody included;
      * only runners of a FULL team take a scoring place;
      * a team takes at most `cap` of them, so its eighth runner neither
        scores nor displaces;
      * a team's score is the sum of its first TEAM_SCORERS scoring places,
        and fewer than that many means no score at all.
    """
    order = np.argsort(times, kind="stable")
    t_ord = team[order]
    # how many of this runner's OWN teammates finished ahead of them: the
    # within-team rank, which is what the cap is counted against
    within = np.zeros(t_ord.shape[0], dtype=np.int64)
    seen = np.zeros(n_teams + 1, dtype=np.int64)          # -1 lands in [-1]
    for i, t in enumerate(t_ord):
        within[i] = seen[t]
        seen[t] += 1
    eligible = (t_ord >= 0) & full[t_ord] & (within < cap[t_ord])
    # the scoring place is the running count of eligible finishers
    place = np.cumsum(eligible)
    score_place = np.where(eligible, place, 0)

    scores = np.full(n_teams, np.nan)
    for t in range(n_teams):
        if not full[t]:
            continue
        got = score_place[(t_ord == t) & eligible]
        if got.shape[0] >= TEAM_SCORERS:
            scores[t] = float(got[:TEAM_SCORERS].sum())
    out = np.zeros_like(score_place)
    out[order] = score_place
    return scores, out


def _draw(rng, mu, sigma, team, n_teams, team_rho, draws):
    """[draws, n] log-normal times. `team_rho` shares that fraction of each
    athlete's variance with their teammates, so a squad has good and bad
    days together -- see the module docstring for why a RACE-wide effect
    would do nothing at all."""
    n = mu.shape[0]
    eps = rng.standard_normal((draws, n))
    if team_rho > 0 and n_teams:
        shared = rng.standard_normal((draws, n_teams + 1))
        mine = shared[:, team]                      # team -1 -> the last col
        mine = np.where(team[None, :] >= 0, mine, 0.0)
        w = math.sqrt(team_rho)
        eps = w * mine + math.sqrt(max(0.0, 1.0 - team_rho)) * eps
    return np.exp(mu[None, :] + sigma[None, :] * eps)


def simulate(field, preds, draws=DRAWS, seed=0, team_rho=0.0, z=1.0):
    """Run the predicted race `draws` times.

    Returns {"teams": {name: {...}}, "h2h": {(a, b): p}, "runners": [...],
             "draws": n} -- or None when nothing could be scored.

    Per team: the mean and sd of its score, the 10th/50th/90th percentiles,
    P(win), P(top 3), and how often it could not field five. Per runner: the
    mean place and its 10-90 band. h2h[(a, b)] is P(a scores better than b),
    counted only over draws where both scored.
    """
    prep = prepare(field, preds, z)
    if prep is None:
        return None
    names = prep["names"]
    n_teams = len(names)
    if not n_teams:
        return None
    rng = np.random.default_rng(seed)
    times = _draw(rng, prep["mu"], prep["sigma"], prep["team"], n_teams,
                  team_rho, draws)

    scores = np.full((draws, n_teams), np.nan)
    places = np.zeros((draws, prep["mu"].shape[0]), dtype=np.int64)
    for d in range(draws):
        s, _sp = scoreDraw(times[d], prep["team"], prep["full"], prep["cap"],
                           n_teams)
        scores[d] = s
        places[d] = np.argsort(np.argsort(times[d], kind="stable"),
                               kind="stable") + 1

    scored = ~np.isnan(scores)
    # ! THE WINNER IS THE LOWEST SCORE AMONG TEAMS THAT SCORED IN THAT DRAW,
    #   and a draw where nobody scored has no winner -- not a winner by
    #   default.
    filled = np.where(scored, scores, np.inf)
    best = filled.min(axis=1)
    any_scored = np.isfinite(best)
    wins = (filled == best[:, None]) & any_scored[:, None]
    # a tie splits the win, as the rules do not (the sixth runner breaks it)
    # -- credited fractionally rather than to whoever sorts first
    n_tied = wins.sum(axis=1)
    win_credit = np.where(n_tied[:, None] > 0, wins / np.maximum(n_tied, 1)[:, None], 0.0)
    rank = np.argsort(np.argsort(filled, axis=1, kind="stable"),
                      axis=1, kind="stable") + 1

    teams = {}
    for i, name in enumerate(names):
        col = scores[:, i]
        ok = ~np.isnan(col)
        teams[name] = {
            "p_win": float(win_credit[:, i].sum() / max(draws, 1)),
            "p_top3": float(((rank[:, i] <= 3) & ok).sum() / max(draws, 1)),
            "score_mean": float(np.nanmean(col)) if ok.any() else None,
            "score_sd": float(np.nanstd(col)) if ok.any() else None,
            "score_p10": float(np.nanpercentile(col, 10)) if ok.any() else None,
            "score_p50": float(np.nanpercentile(col, 50)) if ok.any() else None,
            "score_p90": float(np.nanpercentile(col, 90)) if ok.any() else None,
            "p_incomplete": float((~ok).sum() / max(draws, 1)),
        }

    h2h = {}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                continue
            both = (~np.isnan(scores[:, i])) & (~np.isnan(scores[:, j]))
            if not both.any():
                h2h[(a, b)] = None
                continue
            beat = (scores[both, i] < scores[both, j]).sum()
            tie = (scores[both, i] == scores[both, j]).sum()
            h2h[(a, b)] = float((beat + 0.5 * tie) / both.sum())

    runners = []
    for k, pid in enumerate(prep["person"]):
        col = places[:, k]
        runners.append({"person_id": pid, "school": prep["runner_team"][k],
                        "place_mean": float(col.mean()),
                        "place_p10": float(np.percentile(col, 10)),
                        "place_p90": float(np.percentile(col, 90))})
    return {"teams": teams, "h2h": h2h, "runners": runners, "draws": draws}


# ------------------------------------------------------------------ #
#  THE LINEUP                                                         #
# ------------------------------------------------------------------ #
#
# ★ AND IT IS NOT "THE FASTEST SEVEN" (owner: "given a roster the model
#   decides best lineup for a race"). Five score and two displace, so the
#   sixth and seventh runners' whole job is to push the OTHER teams'
#   scorers back a place -- which makes the best sixth man the one most
#   likely to finish BETWEEN an opponent's fourth and fifth, not the fastest
#   one left. And because the objective is a probability rather than a mean,
#   variance counts: a runner who is sometimes excellent can be worth more
#   than a steady one when you are behind, and worth less when you are
#   ahead. Neither of those is visible in a ranking by expected time, which
#   is why this searches instead of sorting.
#
# ! THE SEARCH IS GREEDY WITH SWAPS, AND EXHAUSTIVE WHEN IT CAN AFFORD TO
#   BE. C(15, 7) is 6,435 -- enumerable; C(30, 7) is 2.03 million -- not.
#   Below ENUMERATE_MAX every lineup is tried, above it the fastest seven
#   are the starting point and single swaps are taken while they help.
ENUMERATE_MAX = 3000
SEARCH_DRAWS = 400          # enough to rank lineups; the winner is re-run


def _evaluate(field, preds, keep, draws, seed, team_rho, z, team):
    """(p_win, score_mean) for a lineup: the simulation with only `keep` of
    the team's runners in the field. `keep` is a set of indices into field."""
    sub_f, sub_p = [], []
    for i, (f, p) in enumerate(zip(field, preds)):
        if f.get("school") == team and i not in keep:
            continue
        sub_f.append(f)
        sub_p.append(p)
    out = simulate(sub_f, sub_p, draws=draws, seed=seed, team_rho=team_rho, z=z)
    if not out or team not in out["teams"]:
        return -1.0, None
    got = out["teams"][team]
    return got["p_win"], got["score_mean"]


def bestLineup(field, preds, team, k=MAX_PER_TEAM, draws=SEARCH_DRAWS,
               seed=0, team_rho=0.0, z=1.0, objective="p_win"):
    """The `k` runners of `team` that maximise P(win) (or minimise the mean
    score with objective="score").

    Returns {"lineup": [person_id...], "p_win": p, "score_mean": s,
             "considered": n, "method": "enumerate"|"greedy",
             "alternatives": [...]} or None.

    ⚠ THE REST OF THE FIELD IS HELD FIXED, which is the honest framing of the
      question a coach asks -- "who should I run against these people" -- and
      not a claim about what the other coaches would then do.
    """
    import itertools

    own = [i for i, f in enumerate(field) if f.get("school") == team
           and preds[i].get("seconds")]
    if len(own) <= k:
        keep = set(own)
        p, s = _evaluate(field, preds, keep, draws, seed, team_rho, z, team)
        return {"lineup": [field[i].get("person_id") for i in own],
                "p_win": p, "score_mean": s, "considered": 1,
                "method": "all", "alternatives": []}

    better = (lambda a, b: a[0] > b[0]) if objective == "p_win" else \
             (lambda a, b: (a[1] is not None)
                           and (b[1] is None or a[1] < b[1]))
    tried = {}

    def look(keep):
        key = tuple(sorted(keep))
        if key not in tried:
            tried[key] = _evaluate(field, preds, set(keep), draws, seed,
                                   team_rho, z, team)
        return tried[key]

    n_comb = math.comb(len(own), k)
    if n_comb <= ENUMERATE_MAX:
        method = "enumerate"
        for combo in itertools.combinations(own, k):
            look(combo)
    else:
        method = "greedy"
        # the fastest k by predicted time, then single swaps while they help
        order = sorted(own, key=lambda i: preds[i]["seconds"])
        cur = list(order[:k])
        look(cur)
        moved = True
        while moved:
            moved = False
            outside = [i for i in own if i not in cur]
            for a in list(cur):
                for b in outside:
                    cand = [x for x in cur if x != a] + [b]
                    if better(look(cand), look(cur)):
                        cur = cand
                        moved = True
                        break
                if moved:
                    break

    ranked = sorted(tried.items(),
                    key=lambda kv: (-kv[1][0] if objective == "p_win"
                                    else (kv[1][1] if kv[1][1] is not None
                                          else float("inf"))))
    best_key, (p, s) = ranked[0]
    return {
        "lineup": [field[i].get("person_id") for i in best_key],
        "p_win": p, "score_mean": s,
        "considered": len(tried), "method": method,
        "alternatives": [
            {"lineup": [field[i].get("person_id") for i in key],
             "p_win": v[0], "score_mean": v[1]}
            for key, v in ranked[1:6]],
    }
