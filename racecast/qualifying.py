"""
qualifying.py -- the field of a meet you QUALIFY for: this season's
qualifiers from its rounds, by the meet's published rules.

    import qualifying as Q
    rule = Q.ruleFor(meet_name, source)          # or None: not a qualifying meet we know
    field = Q.fieldFor(cur, rule, meet_date, gender)   # {person_id: school} or None

★ WHY (owner, 2026-11-02: "we have no way currently to make it so that you
  can 'qualify' for a meet, it just takes the same teams/amount as last
  year"; "collegiate very important ... NXR and such too"). A national
  championship's field is this year's regional qualifiers, not last year's
  teams. scripts/qualifier_audit.py measured it on this season's state meets:
  83% of finishers found against the site's 51%.

★ THE RULES ARE THE MEETS' OWN, TYPED IN WITH THEIR SOURCE (RULES). Teams
  and individuals in automatically from each round, then the at-large picks
  a committee makes -- approximated (atLargeTeams, atLargeIndividuals):
    teams        regional finish order is kept within a region (no team ahead
                 of one that beat it at its regional, every manual says so);
                 across regions, the strongest next team by its top five's
                 season ratings (the committees count wins over qualified
                 teams, which the ratings stand in for)
    individuals  NCAA DII's published ratio, [team qualifiers from the
                 region] / [regional place], for every rule with at-large
                 individuals
  High school state, division and conference routes are learned from the
  results instead (scripts/qualifier_audit.py), not typed.

★ AND BEFORE THE ROUNDS HAVE RUN, THE ROUNDS AS WE PREDICT THEM (owner,
  2026-11-02: "This should be what we do ... it's the entire point").
  fieldFromRounds(predict=...) takes a round that has no results yet from
  our own forecast of it (upcoming_preview.predictedRound: the stored or
  live prediction of that race), in predicted finish order, and applies the
  same rule: the likeliest field, weeks ahead. A round that has run is read
  from its results. scripts/qualifying_check.py scored the rules on 2025
  from the rounds' results: 82-93% of finishers found, 28-34 of 32-34 teams.

! NONE WITHOUT ENOUGH ROUNDS: at least MIN_ROUNDS rounds, run or predicted;
  otherwise the meet keeps its last-edition field (last_edition.py).
"""
import collections
import os
import math
import re

MIN_ROUNDS = 3

# ------------------------------------------------------------------ #
#  the rules
# ------------------------------------------------------------------ #

# NCAA names by division: "Division I" not followed by another I or a V
_D1 = r"(division\s+i(?![iv])|\bd\s*-?\s*i(?![iv])\b|\bdi\b)"
_D2 = r"(division\s+ii(?!i)|\bd\s*-?\s*ii(?!i)\b|\bdii\b)"
_D3 = r"(division\s+iii|\bd\s*-?\s*iii\b|\bdiii\b)"

RULES = (
    {"key": "ncaa_d1", "source": "tfrrs",
     "meet": rf"ncaa.*{_D1}.*championship", "round": rf"ncaa.*{_D1}.*region",
     "not": r"region|pre-?nat|pre-?champ", "window": 21,
     "teams_auto": 2, "teams_at_large": 14, "ind_auto": 4, "ind_auto_within": 25,
     "ind_at_large": 2, "team_size": 7,
     "cite": "2025-26 NCAA DI Cross Country Pre-Championships Manual, sections 2.1 and 2.4: "
             "9 regions; top 2 teams each; 14 at-large; first 4 not on a qualifying team "
             "(top 25 of the region); 2 at-large individuals"},
    {"key": "ncaa_d2", "source": "tfrrs",
     "meet": rf"ncaa.*{_D2}.*championship", "round": rf"ncaa.*{_D2}.*region",
     "not": r"region|pre-?nat|pre-?champ", "window": 21,
     "teams_auto": 3, "teams_at_large": 10, "ind_auto": 2, "ind_auto_top": 5,
     "ind_at_large": 8, "team_size": 7,
     "cite": "2026 NCAA DII Cross Country Championships Selection Procedures: 8 regions; "
             "top 3 teams each; 10 at-large; top 2 not on a qualifying team and anyone "
             "top 5 not on one; 8 at-large individuals by [team qualifiers]/[place]"},
    {"key": "ncaa_d3", "source": "tfrrs",
     "meet": rf"ncaa.*{_D3}.*championship", "round": rf"ncaa.*{_D3}.*region",
     "not": r"region|pre-?nat|pre-?champ", "window": 21,
     "teams_auto": 1, "teams_at_large": 22, "ind_auto": 7, "ind_at_large": 0,
     "team_size": 7,
     "cite": "2025-26 NCAA DIII Cross Country Pre-Championships Manual, section 2.1: "
             "10 regions; 1 team each; 22 at-large; top 7 not on a qualifying team"},
    {"key": "nxn", "source": "anet",
     "meet": r"nike cross nationals|\bnxn\b", "round": r"\bnxr\b|nike cross regional",
     "not": r"region|\bnxr\b", "window": 28,
     "teams_auto": 2, "teams_at_large": 4, "ind_auto": 5, "ind_at_large": 5,
     "team_size": 7,
     "cite": "NXN qualifying (MileSplit / Dyestat summaries; check each year): top 2 "
             "teams per regional; 4 at-large teams per gender; top 5 individuals not on "
             "a qualifying team; about 5 at-large individuals"},
)


def ruleFor(meet_name, source=None):
    """The rule whose championship this meet is, or None. Pure."""
    nm = (meet_name or "").lower()
    for r in RULES:
        if source and r["source"] != source:
            continue
        if re.search(r["meet"], nm) and not re.search(r["not"], nm):
            return r
    return None


def isRound(rule, meet_name):
    """Is this meet one of the rule's rounds? Pure."""
    return bool(re.search(rule["round"], (meet_name or "").lower()))


# ------------------------------------------------------------------ #
#  the engine (pure)
# ------------------------------------------------------------------ #

def teamOrder(rows):
    """Schools with five or more finishers, best first by the sum of their
    top five places among such schools' runners. rows: [(pid, school,
    seconds, rating)] of one race."""
    by = collections.defaultdict(list)
    for r in rows:
        if r[1]:
            by[r[1]].append(r)
    teams = {s for s, v in by.items() if len(v) >= 5}
    place, pts = 0, collections.defaultdict(list)
    for r in sorted((r for r in rows if r[1] in teams), key=lambda r: r[2]):
        place += 1
        if len(pts[r[1]]) < 5:
            pts[r[1]].append(place)
    return sorted(teams, key=lambda s: (sum(pts[s]), s))


def teamStrength(rows, school):
    """The mean season rating of a school's five best-rated runners in the
    round; -inf with none rated. Higher is stronger."""
    rs = sorted((r[3] for r in rows if r[1] == school and r[3] is not None), reverse=True)[:5]
    return sum(rs) / len(rs) if rs else -math.inf


def atLargeTeams(queues, n, strength):
    """n picks from per-round queues of teams in regional finish order: each
    pick the strongest team at the head of any queue, so no team goes
    ahead of one that beat it at its own round. queues: {round: [school]}."""
    q = {k: list(v) for k, v in queues.items()}
    out = []
    for _ in range(n):
        heads = [(strength(k, v[0]), k) for k, v in q.items() if v]
        if not heads:
            break
        _s, k = max(heads, key=lambda h: (h[0], str(h[1])))
        out.append((k, q[k].pop(0)))
    return out


def atLargeIndividuals(queues, n, n_teams):
    """n picks by NCAA DII's ratio: [team qualifiers from the round] /
    [place in the round], the best head of each round's queue each time.
    queues: {round: [(place, pid)]} in place order; n_teams: {round: int}."""
    q = {k: list(v) for k, v in queues.items()}
    out = []
    for _ in range(n):
        heads = [(n_teams.get(k, 0) / v[0][0], k) for k, v in q.items() if v]
        if not heads:
            break
        _s, k = max(heads, key=lambda h: (h[0], str(h[1])))
        out.append(q[k].pop(0)[1])
    return out


def selectField(rule, rounds):
    """{person_id: school}: the championship's field from its rounds by the
    rule. rounds: {round key: [(pid, school, seconds, rating)]}."""
    size = rule.get("team_size", 7)
    orders = {k: teamOrder(v) for k, v in rounds.items()}
    qualified = {k: list(o[:rule["teams_auto"]]) for k, o in orders.items()}
    queues = {k: o[rule["teams_auto"]:] for k, o in orders.items()}
    for k, school in atLargeTeams(queues, rule.get("teams_at_large", 0),
                                  lambda k, s: teamStrength(rounds[k], s)):
        qualified[k].append(school)
    field = {}
    ind_queues, n_teams = {}, {}
    for k, rows in rounds.items():
        teams = set(qualified[k])
        n_teams[k] = len(teams)
        per = collections.Counter()
        others = []
        for place, r in enumerate(sorted(rows, key=lambda r: r[2]), 1):
            if r[1] in teams:
                if per[r[1]] < size:
                    field[r[0]] = r[1]
                    per[r[1]] += 1
            else:
                others.append((place, r))
        auto, rest = 0, []
        within = rule.get("ind_auto_within")
        for place, r in others:
            takes = (auto < rule.get("ind_auto", 0) and (within is None or place <= within)) \
                or place <= rule.get("ind_auto_top", 0)
            if takes:
                field[r[0]] = r[1]
                auto += 1
            else:
                rest.append((place, r[0]))
        ind_queues[k] = rest
    schools = {r[0]: r[1] for rows in rounds.values() for r in rows}
    for pid in atLargeIndividuals(ind_queues, rule.get("ind_at_large", 0), n_teams):
        field[pid] = schools.get(pid)
    return field


# ------------------------------------------------------------------ #
#  the database half
# ------------------------------------------------------------------ #

def _roundMeets(cur, rule, day, window):
    """[(meet_id, name, date)] of the rule's rounds in the `window` days
    before `day` (ISO), with results."""
    lo = _shift(day, -window)
    if rule["source"] == "tfrrs":
        cur.execute("""SELECT meet_id, meet_name, date FROM meets_tfrrs
                       WHERE sport = 'XC' AND date ~ '^(19|20)[0-9]{2}-'
                         AND date >= %s AND date < %s""", (lo, day))
    else:
        cur.execute("""SELECT DISTINCT meet_id, meet_name, meet_date FROM meets
                       WHERE meet_date ~ '^(19|20)[0-9]{2}-'
                         AND meet_date >= %s AND meet_date < %s""", (lo, day))
    out = []
    for row in cur.fetchall():
        row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        if isRound(rule, row[1]):
            out.append((int(row[0]), row[1], str(row[2])[:10]))
    return out


def _shift(day, days):
    import datetime
    return (datetime.date.fromisoformat(str(day)[:10])
            + datetime.timedelta(days=days)).isoformat()


def roundResults(cur, rule, meet_id, gender, season=None):
    """[(pid, school, seconds, season rating)] of the round meet's race of
    that gender (the largest, when it has several), or []. The rating is
    the season's (or the latest before it): never one made after."""
    import last_edition as LE
    races = [r for r in LE.editionRaces(cur, meet_id, "XC", rule["source"])
             if r.get("gender") == gender]
    best = []
    for race in races:
        cur.execute("""
            SELECT DISTINCT ON (r.person_id) r.person_id, r.school, r.time_seconds,
                   (SELECT x.mean_rating FROM athlete_season x
                    WHERE x.person_id = r.person_id AND x.sport = 'XC'
                      AND (%s::int IS NULL OR x.year <= %s::int)
                    ORDER BY x.year DESC LIMIT 1)
            FROM   results r
            WHERE  r.meet_id = %s AND r.div_id = %s AND r.source = %s
              AND  r.person_id IS NOT NULL AND r.time_seconds > 0
            ORDER  BY r.person_id, r.time_seconds""",
                    (season, season, meet_id, race["div_id"], rule["source"]))
        rows = []
        for row in cur.fetchall():
            row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
            rows.append((int(row[0]), row[1], float(row[2]),
                         float(row[3]) if row[3] is not None else None))
        if len(rows) > len(best):
            best = rows
    return best


def fieldFromRounds(cur, rule, day, gender, predict=None):
    """({person_id: school}, rounds predicted) for the championship on
    `day` (ISO) in `gender`, or (None, 0) with fewer than MIN_ROUNDS
    rounds. predict(meet_id, gender) -> rows like roundResults', for a round
    with no results yet; without it only rounds that have run count."""
    if rule is None or gender not in ("M", "F"):
        return None, 0
    rounds, n_pred = {}, 0
    for mid, _name, _d in _roundMeets(cur, rule, day, rule.get("window", 21)):
        rows = roundResults(cur, rule, mid, gender, int(str(day)[:4]))
        if len(rows) < 10 and predict is not None:
            rows = predict(mid, gender) or []
            n_pred += len(rows) >= 10
        if len(rows) >= 10:
            rounds[mid] = rows
    if len(rounds) < MIN_ROUNDS:
        return None, 0
    return selectField(rule, rounds), n_pred


def fieldFor(cur, rule, day, gender):
    """{person_id: school} from the rounds that have run, or None."""
    return fieldFromRounds(cur, rule, day, gender)[0]


# ------------------------------------------------------------------ #
#  the learned routes (high school state / section / district, NAIA)
# ------------------------------------------------------------------ #

# ★ LEARNED, WRITTEN DOWN, CHECKED (owner, 2026-11-02: "state/div/conference
#   can be found by yourself", "you're checking it"). scripts/qualifier_audit.py
#   --write-routes reads last season's results: each championship race, the
#   races in the four weeks before whose top finishers went on to it, and how
#   many teams and individuals each sent. Only routes whose rule rebuilds
#   70%+ of their own field at 60%+ precision are written. A route is found
#   again this season by name (last_edition.editionName) and race label.
ROUTES_PATH = os.environ.get("XCP_QUALIFY_ROUTES") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model", "data",
    "qualifying_routes.json")
_ROUTES = {"mtime": None, "by_meet": {}}


def _loadRoutes():
    import json
    try:
        mtime = os.path.getmtime(ROUTES_PATH)
    except OSError:
        return {}
    if _ROUTES["mtime"] != mtime:
        try:
            with open(ROUTES_PATH) as fh:
                routes = json.load(fh).get("routes") or []
        except Exception:                                # noqa: BLE001
            routes = []
        by = collections.defaultdict(list)
        for r in routes:
            by[(r["source"], r["meet"])].append(r)
        _ROUTES.update(mtime=mtime, by_meet=dict(by))
    return _ROUTES["by_meet"]


def learnedRoute(meet_name, source, race_label, gender):
    """The learned route of this championship race, or None: the same meet
    by edition name, the race by its label, else its gender if the meet has
    one route of that gender."""
    import last_edition as LE
    cands = _loadRoutes().get((source, LE.editionName(meet_name))) or []
    lab = LE.editionName(race_label)
    same = [r for r in cands if lab and LE.editionName(r.get("race")) == lab
            and (not gender or not r.get("gender") or r["gender"] == gender)]
    if len(same) == 1:
        return same[0]
    by_g = [r for r in cands if gender and r.get("gender") == gender]
    return by_g[0] if len(by_g) == 1 else None


def simpleQualifiers(rows, teams, individuals, size=7):
    """A round's top `teams` teams (at most `size` each) and its top
    `individuals` off those teams. rows: [(pid, school, seconds, rating)].
    Pure."""
    order = teamOrder(rows)
    keep = set(order[:teams])
    out, per, ind = {}, collections.Counter(), []
    for r in sorted(rows, key=lambda r: r[2]):
        if r[1] in keep:
            if per[r[1]] < size:
                out[r[0]] = r[1]
                per[r[1]] += 1
        else:
            ind.append(r)
    out.update({r[0]: r[1] for r in ind[:individuals]})
    return out


def _thisRound(cur, rnd, day, source):
    """(meet_id, div_id, ran) of this season's edition of a learned round:
    the meet of that edition name dated within two weeks of a year after
    the round's date and before `day`, its race by label (else gender)."""
    import datetime
    import last_edition as LE
    near = datetime.date.fromisoformat(rnd["date"]) + datetime.timedelta(days=364)
    lo, hi = (near - datetime.timedelta(days=14)).isoformat(), \
        min(day, (near + datetime.timedelta(days=14)).isoformat())
    if source == "tfrrs":
        cur.execute("""SELECT meet_id, meet_name, date FROM meets_tfrrs
                       WHERE sport = 'XC' AND date ~ '^(19|20)[0-9]{2}-'
                         AND date >= %s AND date < %s""", (lo, hi))
    else:
        cur.execute("""SELECT DISTINCT meet_id, meet_name, meet_date FROM meets
                       WHERE meet_date ~ '^(19|20)[0-9]{2}-'
                         AND meet_date >= %s AND meet_date < %s""", (lo, hi))
    for row in cur.fetchall():
        row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        if LE.editionName(row[1]) != rnd["meet"]:
            continue
        races = LE.editionRaces(cur, int(row[0]), "XC", source)
        ran = bool(races)
        if not ran:
            import upcoming_preview as U
            races = [{"div_id": r["div_id"], "label": r["label"], "gender": r.get("gender"),
                      "distance": r.get("distance")}
                     for r in (U.meetPlan(cur, int(row[0]), source) or {}).get("races") or []]
        lab = LE.editionName(rnd.get("race"))
        pick = [r for r in races if lab and LE.editionName(r.get("label")) == lab
                and (not rnd.get("gender") or not r.get("gender") or r["gender"] == rnd["gender"])]
        if len(pick) != 1:
            pick = [r for r in races if rnd.get("gender") and r.get("gender") == rnd["gender"]]
        if len(pick) == 1:
            return int(row[0]), int(pick[0]["div_id"]), ran
    return None


def learnedField(cur, route, day, predict_div=None):
    """({person_id: school}, rounds predicted) for a learned route's race on
    `day`, or (None, 0) when fewer than half of its rounds are found.
    predict_div(meet_id, div_id) -> rows, for a round with no results yet."""
    field, found, n_pred = {}, 0, 0
    for rnd in route.get("rounds") or []:
        at = _thisRound(cur, rnd, day, route["source"])
        if at is None:
            continue
        mid, div, ran = at
        rows = []
        if ran:
            cur.execute("""
                SELECT DISTINCT ON (r.person_id) r.person_id, r.school, r.time_seconds
                FROM   results r
                WHERE  r.meet_id = %s AND r.div_id = %s AND r.source = %s
                  AND  r.person_id IS NOT NULL AND r.time_seconds > 0
                ORDER  BY r.person_id, r.time_seconds""", (mid, div, route["source"]))
            for row in cur.fetchall():
                row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
                rows.append((int(row[0]), row[1], float(row[2]), None))
        elif predict_div is not None:
            rows = predict_div(mid, div) or []
            n_pred += len(rows) >= 5
        if len(rows) < 5:
            continue
        found += 1
        field.update(simpleQualifiers(rows, int(rnd.get("teams") or 0),
                                      int(rnd.get("individuals") or 0)))
    if found * 2 < len(route.get("rounds") or []) or not field:
        return None, 0
    return field, n_pred
