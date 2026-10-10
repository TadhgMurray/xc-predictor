# Project: xc-predictor / racecast
# File:    ncaa_select.py
# Purpose: who goes to NCAA cross country nationals, given the regionals'
#          finishing orders and the season's head-to-head results -- the
#          selection rules of ncaa_rules.py as functions (owner approved
#          2026-10-10). No database: build_ncaa_projection.py feeds it, and
#          tests/test_ncaa_select.py runs it on toy seasons.
#
# ★ ONE WIN MATRIX, THREE DIVISIONS. Everything the manuals compare is a
#   count of who beat whom, so the season is reduced once to W[i, j] -- how
#   many counted races team i beat team j in -- plus the date of each pair's
#   latest win (D1's tie-break asks for the win "closest to the regional").
#   D1's Kolas points are W summed over the teams already in; D3 reads the
#   same; D2's net wins are W and its sign matrix through common opponents.
#
# ★ THE REGIONALS ARE A RACE IN THE WINDOW. Every manual's window runs to the
#   end of the regionals ("including regionals", D3), so a projected
#   regional adds its own wins: each team beats every team behind it. A
#   third-place team that beat the fourth at regionals holds a win over it
#   the moment the fourth is picked.
#
# ⚠ WHERE THIS IS A MODEL OF A COMMITTEE, said once here and on the page:
#   - D1 push: the manual's condition (3), "Team X is permanently blocking
#     Team Y", is the committee's call. We push only when running the rest
#     of the selection without the push would leave Y out.
#   - D1 criterion 5 and D2 criterion 4 are judgment: the last tie-break is
#     our projected team strength (the order the regionals were drawn from).
#   - D2 criterion 3 (common non-Division II opponents) is not computed:
#     the win matrix covers one division.
#   - D2 second-degree chains ignore the manual's "each link must represent
#     a different competition" rule; first-degree chains honour it where one
#     meet is the pair's only meeting.
#   - D3 has no formula in the manual; see d3Select.
import numpy as np


# ------------------------------------------------------------------ #
#  1. the season, reduced to wins
# ------------------------------------------------------------------ #

def raceWins(race, rules, seven=None):
    """The counted wins in one race: [(winner, loser)].

    race: {"order": [team, ...] by team score, scoring teams only;
           "dnf": [team, ...] that started five or more and did not score;
           "starters": {team: set(person_id)};
           "dual": {(a, b): winner} when the division scores duals}
    seven: {team: set(person_id)} -- each team's projected regional seven,
           for the "A team" rule; None counts every entry as an A team.

    ★ D1: the LOSER must be an "A" team (four or more of its regional seven
      started); the winner may be anybody's B team. D2/D3: both must have
      started five of their seven, or the result "cannot count against or
      help" either team.
    """
    need = rules.get("a_team_min_starters") or 0
    both = rules.get("a_team_applies_to") == "both"

    def isA(team):
        if seven is None or not need:
            return True
        sv = seven.get(team)
        if not sv:
            return False
        return len(sv & race["starters"].get(team, set())) >= need

    out = []
    if rules.get("h2h_scoring") == "dual":
        for (a, b), w in race.get("dual", {}).items():
            if w is None:
                continue
            loser = b if w == a else a
            if isA(loser) and (not both or isA(w)):
                out.append((w, loser))
        return out
    order = race["order"]
    for i, w in enumerate(order):
        wa = isA(w)
        for loser in order[i + 1:]:
            if isA(loser) and (wa or not both):
                out.append((w, loser))
    if rules.get("dnf_team_is_beaten"):
        for loser in race.get("dnf", ()):
            if not isA(loser):
                continue
            for w in order:
                if not both or isA(w):
                    out.append((w, loser))
    return out


def winMatrix(n, wins):
    """W[i, j] = wins of i over j, from [(winner, loser, date)]; and
    {(winner, loser): latest date}."""
    W = np.zeros((n, n), dtype=np.int32)
    last = {}
    for w, lo, day in wins:
        if w == lo:
            continue
        W[w, lo] += 1
        k = (w, lo)
        if day is not None and (k not in last or day > last[k]):
            last[k] = day
    return W, last


class RegionalLast:
    """The season's {(winner, loser): latest day} with one regional on top:
    a pair that met at a regional last met there, and the winner is whoever
    finished ahead. A view, so a draw does not copy the season's dict."""

    def __init__(self, base, orders, day):
        self.base, self.day = base, day
        self.rank = {}
        for reg, order in orders.items():
            for i, t in enumerate(order):
                self.rank[t] = (reg, i)

    def get(self, key, default=None):
        a, b = key
        ra, rb = self.rank.get(a), self.rank.get(b)
        if ra and rb and ra[0] == rb[0]:
            return self.day if ra[1] < rb[1] else self.base.get(key, default)
        return self.base.get(key, default)

    def __getitem__(self, key):
        got = self.get(key)
        if got is None:
            raise KeyError(key)
        return got


def addRegionals(W, last, orders, day):
    """W and last with each regional's finish added: every team beats each
    team behind it. W is copied; the season's own is untouched."""
    W2 = W.copy()
    for order in orders.values():
        if len(order) < 2:
            continue
        idx = np.asarray(order)
        W2[np.ix_(idx, idx)] += np.triu(np.ones((len(idx), len(idx)), dtype=W.dtype), 1)
    return W2, RegionalLast(last, orders, day)


# ------------------------------------------------------------------ #
#  2. comparisons
# ------------------------------------------------------------------ #

def h2hCompare(a, b, W, last):
    """+1 when a wins the head-to-head with b, -1 when b does, 0 when
    neither (they never met). An even record goes to the team with the win
    "closest to the regional championship date" (D1 criterion 3.b.1)."""
    wa, wb = int(W[a, b]), int(W[b, a])
    if wa > wb:
        return 1
    if wb > wa:
        return -1
    if wa == 0:
        return 0
    da, db = last.get((a, b)), last.get((b, a))
    if da is not None and db is not None and da != db:
        return 1 if da > db else -1
    return 0


def commonCompare(a, b, W):
    """+1 / -1 / 0 on win percentage against common opponents, every
    meeting counted ("including multiple wins and/or losses")."""
    met = (W + W.T) > 0
    common = met[a] & met[b]
    common[a] = common[b] = False
    if not common.any():
        return 0
    wa, la = W[a, common].sum(), W[common, a].sum()
    wb, lb = W[b, common].sum(), W[common, b].sum()
    pa = wa / (wa + la) if wa + la else 0.0
    pb = wb / (wb + lb) if wb + lb else 0.0
    if pa > pb:
        return 1
    if pb > pa:
        return -1
    return 0


def _unbeaten(group, cmp):
    """The members of `group` that lose no pairwise comparison."""
    return [a for a in group
            if not any(cmp(a, b) < 0 for b in group if b != a)]


def breakTie(tied, ctx, steps):
    """One team out of `tied` by the listed steps, and the step that
    decided it. ctx: W, last, place (team -> regional place), gap (team ->
    points behind its region's last automatic qualifier), strength (team ->
    projected rank, low is better)."""
    group = list(tied)
    for step in steps:
        if len(group) == 1:
            break
        if step == "h2h":
            def cmp(a, b):
                return h2hCompare(a, b, ctx["W"], ctx["last"])
        elif step == "common":
            def cmp(a, b):
                return commonCompare(a, b, ctx["W"])
        else:
            cmp = None
        if cmp is not None:
            # ★ "THE TEAM WITHOUT ANY LOSING COMPARISONS"; a cycle leaves
            #   nobody unbeaten, and then the step decides nothing.
            keep = _unbeaten(group, cmp)
            if len(keep) == 1:
                return keep[0], step
            if keep:
                group = keep
            continue
        key = {"place": lambda t: ctx["place"].get(t, 10 ** 6),
               "gap": lambda t: ctx["gap"].get(t, 10 ** 9),
               "strength": lambda t: ctx["strength"].get(t, 10 ** 6)}[step]
        best = min(key(t) for t in group)
        group = [t for t in group if key(t) == best]
        if len(group) == 1:
            return group[0], step
    return min(group, key=lambda t: ctx["strength"].get(t, 10 ** 6)), "strength"


# ------------------------------------------------------------------ #
#  3. at-large selection
# ------------------------------------------------------------------ #

def _autos(orders, n):
    return [t for order in orders.values() for t in order[:n]]


def _context(orders, scores, W, last, strength, auto_n):
    place, gap = {}, {}
    for order in orders.values():
        last_aq = order[auto_n - 1] if len(order) >= auto_n else None
        for i, t in enumerate(order, start=1):
            place[t] = i
            if last_aq is not None and t in scores and last_aq in scores:
                gap[t] = scores[t] - scores[last_aq]
    return {"W": W, "last": last, "place": place, "gap": gap,
            "strength": strength or {}}


def kolasSelect(orders, scores, W, last, rules, strength=None, push=True,
                steps=("h2h", "common", "place", "gap", "strength"),
                log=False):
    """The D1 at-large selection, one berth at a time.

    orders: {region: [team, ...]} regional finish, scoring teams only
    scores: {team: regional team score}
    W, last: winMatrix with the regionals already added (addRegionals)
    Returns {"auto": [...], "at_large": [...], "pushed": [...],
             "rounds": [...] when log}.

    ★ POINTS ARE RECOUNTED AFTER EVERY PICK. "As each at-large team is
      selected ..., the win totals of all teams under consideration are
      adjusted to reflect any victories over all the teams selected". A
      team pushed in earns its pusher nothing and nobody else anything
      either (criterion 1.e), so it is in the field but not in `credit`.
    """
    n_auto = rules["auto_per_region"]
    slots = rules["at_large_teams"]
    auto = _autos(orders, n_auto)
    field = list(auto)
    credit = np.zeros(W.shape[0], dtype=bool)
    credit[auto] = True
    nxt = {r: n_auto for r in orders}
    pushed_regions = set()
    at_large, pushed, rounds = [], [], []
    ctx = _context(orders, scores, W, last, strength, n_auto)

    def candidates(nx):
        return {r: orders[r][k] for r, k in nx.items() if k < len(orders[r])}

    def points(teams):
        return {t: int(W[t, credit].sum()) for t in teams}

    def pick(cands):
        pts = points(cands.values())
        top = max(pts.values())
        tied = [t for t, p in pts.items() if p == top]
        if len(tied) == 1:
            return tied[0], "points", pts
        t, why = breakTie(tied, ctx, steps)
        return t, why, pts

    def simulateRest(nx, cr, left):
        """The rest of the selection with no further push: who goes in."""
        nx = dict(nx)
        cr = cr.copy()
        got = []
        while left > 0:
            cands = {r: orders[r][k] for r, k in nx.items() if k < len(orders[r])}
            if not cands:
                break
            pts = {t: int(W[t, cr].sum()) for t in cands.values()}
            top = max(pts.values())
            tied = [t for t, p in pts.items() if p == top]
            t = tied[0] if len(tied) == 1 else breakTie(tied, ctx, steps)[0]
            got.append(t)
            cr[t] = True
            reg = next(r for r, c in cands.items() if c == t)
            nx[reg] += 1
            left -= 1
        return got

    while len(at_large) + len(pushed) < slots:
        cands = candidates(nxt)
        if not cands:
            break
        t, why, pts = pick(cands)
        left = slots - len(at_large) - len(pushed)
        did_push = None
        if push and left >= 2:
            # ★ THE PUSH (criterion 1.d): the team right behind a region's
            #   candidate X would be the next pick if X were not in its way.
            for reg, x in cands.items():
                if reg in pushed_regions or x == t:
                    continue
                k = nxt[reg] + 1
                if k >= len(orders[reg]):
                    continue
                y = orders[reg][k]
                alt = dict(cands)
                alt[reg] = y
                yt, _w, _p = pick(alt)
                if yt != y:
                    continue
                # (3) Y could not get in on its own: run the rest without
                #     the push and see
                if y in simulateRest(nxt, credit, left):
                    continue
                did_push = (reg, x, y)
                break
        if did_push:
            reg, x, y = did_push
            if log:
                rounds.append({"pick": y, "pushed": x, "why": "push",
                               "candidates": points(list(cands.values()) + [y])})
            pushed_regions.add(reg)
            pushed.append(x)
            at_large.append(y)
            field += [x, y]
            credit[y] = True
            nxt[reg] += 2
            continue
        at_large.append(t)
        field.append(t)
        credit[t] = True
        reg = next(r for r, c in cands.items() if c == t)
        nxt[reg] += 1
        if log:
            rounds.append({"pick": t, "pushed": None, "why": why, "candidates": pts})
    return {"auto": auto, "at_large": at_large, "pushed": pushed,
            "field": field, "rounds": rounds}


def d3Select(orders, scores, W, last, rules, strength=None, log=False):
    """D3: the manual lists criteria and no formula. We read "head-to-head
    competition with teams already in the championships field and other
    potential at-large teams" as wins over the field (Kolas's count, which
    is the same question), ties broken head-to-head among the tied, then
    common opponents, then regional place. No push: D3 has none."""
    return kolasSelect(orders, scores, W, last, rules, strength, push=False,
                       steps=("h2h", "common", "place", "strength"), log=log)


def chainMatrices(W):
    """For D2's net wins: P[i, j] = 1 when i only ever beat j (never lost
    to j). Chains through common opponents are walks on P."""
    P = ((W > 0) & (W.T == 0)).astype(np.int64)
    return P


def d2Net(a, b, W, P, single=None):
    """Net wins of a over b (the manual's "(Direct Wins + Common Competitor
    Wins) - (Direct losses + Common Competitor Losses)"), first- and
    second-degree chains. single[i, j] is the one race id when i and j met
    exactly once, else -1: a first-degree chain whose two links are that
    same race is not counted ("Chains using the same meet in multiple links
    should not be counted")."""
    direct = int(W[a, b]) - int(W[b, a])
    c1a = (P[a] & P[:, b]).astype(bool)
    c1b = (P[b] & P[:, a]).astype(bool)
    if single is not None:
        c1a = c1a & ~((single[a] == single[:, b]) & (single[a] >= 0))
        c1b = c1b & ~((single[b] == single[:, a]) & (single[b] >= 0))
    c1a[[a, b]] = False
    c1b[[a, b]] = False
    c2a = int(P[a] @ P @ P[:, b])
    c2b = int(P[b] @ P @ P[:, a])
    return direct + int(c1a.sum()) - int(c1b.sum()) + c2a - c2b


def d2Select(orders, scores, W, last, rules, strength=None, single=None,
             log=False):
    """D2: one team per round out of the highest remaining team of each
    region (Appendix B).

    1. any team with a losing net record (direct + common competitor
       chains) against another team under consideration is out;
    2. the regional point gap ratio -- the score of the team just ahead
       at its regional over its own -- where the team ahead went in
       at-large (the manual's rule: an automatic qualifier is not a
       comparison);
    4. the committee: "regular season success and strength of schedule",
       read here as wins over the field, then regional place, then our
       projected strength.
    """
    n_auto = rules["auto_per_region"]
    slots = rules["at_large_teams"]
    auto = _autos(orders, n_auto)
    field = list(auto)
    infield = np.zeros(W.shape[0], dtype=bool)
    infield[auto] = True
    nxt = {r: n_auto for r in orders}
    at_large, rounds = [], []
    P = chainMatrices(W)
    ctx = _context(orders, scores, W, last, strength, n_auto)
    via = set()
    while len(at_large) < slots:
        cands = {r: orders[r][k] for r, k in nxt.items() if k < len(orders[r])}
        if not cands:
            break
        teams = list(cands.values())
        net = {(a, b): d2Net(a, b, W, P, single) for a in teams for b in teams if a != b}
        keep = [a for a in teams if not any(net[(a, b)] < 0 for b in teams if b != a)]
        why = "net wins"
        group = keep if keep else teams
        if len(group) > 1:
            ratio = {}
            for r, t in cands.items():
                if t not in group:
                    continue
                k = nxt[r]
                ahead = orders[r][k - 1] if k >= 1 else None
                if ahead in via and ahead in scores and scores.get(t):
                    ratio[t] = scores[ahead] / scores[t]
            if ratio:
                best = max(ratio.values())
                drop = {t for t, v in ratio.items() if v < best}
                group = [t for t in group if t not in drop]
                why = "point gap ratio"
        if len(group) > 1:
            pts = {t: int(W[t, infield].sum()) for t in group}
            top = max(pts.values())
            group = [t for t in group if pts[t] == top]
            why = "committee (wins over field)"
        if len(group) > 1:
            t, why = breakTie(group, ctx, ("place", "strength"))
            why = "committee (" + why + ")"
        else:
            t = group[0]
        if log:
            rounds.append({"pick": t, "pushed": None, "why": why,
                           "candidates": {x: int(W[x, infield].sum()) for x in teams}})
        at_large.append(t)
        via.add(t)
        field.append(t)
        infield[t] = True
        reg = next(r for r, c in cands.items() if c == t)
        nxt[reg] += 1
    return {"auto": auto, "at_large": at_large, "pushed": [], "field": field,
            "rounds": rounds}


def selectTeams(division_method, orders, scores, W, last, rules, strength=None,
                single=None, log=False):
    if division_method == "kolas":
        return kolasSelect(orders, scores, W, last, rules, strength, log=log)
    if division_method == "d2":
        return d2Select(orders, scores, W, last, rules, strength, single, log=log)
    return d3Select(orders, scores, W, last, rules, strength, log=log)


# ------------------------------------------------------------------ #
#  4. individuals
# ------------------------------------------------------------------ #

def selectIndividuals(finish, team_of, qualified, rules, n_team_qual=None,
                      time_of=None):
    """Individual qualifiers.

    finish: {region: [runner, ...]} in finishing order (every runner)
    team_of: runner -> team id (None for a runner with no scoring team)
    qualified: set of teams going as teams
    n_team_qual: {region: number of teams it sends} (D2's ratio)
    time_of: runner -> drawn time (D1's tie-break)
    Returns [(runner, region, "auto" | "at-large")].
    """
    meth = rules["ind_method"]
    out = []
    pools = {}
    for reg, runners in finish.items():
        pool = []
        for place, r in enumerate(runners, start=1):
            if team_of.get(r) in qualified:
                continue
            pool.append((place, r))
        pools[reg] = pool
    nxt = {}
    if meth == "d1":
        top = rules["ind_auto_top"]
        fourth = {}
        for reg, pool in pools.items():
            got = [(p, r) for p, r in pool if p <= top][:rules["ind_auto_per_region"]]
            out += [(r, reg, "auto") for _p, r in got]
            nxt[reg] = len(got)
            if got:
                fourth[reg] = got[-1][1]
        left = max(rules["ind_at_large"], rules["ind_total"] - len(out))
        while left > 0:
            cands = []
            for reg, pool in pools.items():
                k = nxt[reg]
                if k < len(pool) and pool[k][0] <= top:
                    p, r = pool[k]
                    gap = 0.0
                    if time_of is not None and reg in fourth:
                        gap = time_of.get(r, 0.0) - time_of.get(fourth[reg], 0.0)
                    cands.append((p, gap, reg, r))
            if not cands:
                break
            p, _g, reg, r = min(cands)
            out.append((r, reg, "at-large"))
            nxt[reg] += 1
            left -= 1
        return out
    if meth == "d2":
        for reg, pool in pools.items():
            k = 0
            for i, (p, r) in enumerate(pool):
                if i < rules["ind_auto_per_region"] or p <= rules["ind_auto_top_all"]:
                    out.append((r, reg, "auto"))
                    k = i + 1
            nxt[reg] = k
        left = rules["ind_at_large"]
        while left > 0:
            cands = []
            for reg, pool in pools.items():
                k = nxt[reg]
                if k < len(pool):
                    p, r = pool[k]
                    n = (n_team_qual or {}).get(reg, 0)
                    cands.append((-(n / p), p, reg, r))
            if not cands:
                break
            _s, _p, reg, r = min(cands)
            out.append((r, reg, "at-large"))
            nxt[reg] += 1
            left -= 1
        return out
    for reg, pool in pools.items():
        out += [(r, reg, "auto") for _p, r in pool[:rules["ind_auto_per_region"]]]
    return out
