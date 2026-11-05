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
ROUTE_FOUND = 0.8        # a learned route needs this share of its rounds found

# ------------------------------------------------------------------ #
#  the rules
# ------------------------------------------------------------------ #

# NCAA names by division: "Division I" not followed by another I or a V
_D1 = r"(division\s+i(?![iv])|\bd\s*-?\s*i(?![iv])\b|\bdi\b)"
_D2 = r"(division\s+ii(?!i)|\bd\s*-?\s*ii(?!i)\b|\bdii\b)"
_D3 = r"(division\s+iii|\bd\s*-?\s*iii\b|\bdiii\b)"

RULES = (
    {"key": "ncaa_d1", "source": "tfrrs",
     "meet": rf"ncaa.*{_D1}.*champ", "round": rf"ncaa.*{_D1}.*region",
     "not": r"region|pre-?nat|pre-?champ", "window": 21,
     "teams_auto": 2, "teams_at_large": 14, "ind_auto": 4, "ind_auto_within": 25,
     "ind_at_large": 2, "team_size": 7,
     "cite": "2025-26 NCAA DI Cross Country Pre-Championships Manual, sections 2.1 and 2.4: "
             "9 regions; top 2 teams each; 14 at-large; first 4 not on a qualifying team "
             "(top 25 of the region); 2 at-large individuals"},
    {"key": "ncaa_d2", "source": "tfrrs",
     "meet": rf"ncaa.*{_D2}.*champ", "round": rf"ncaa.*{_D2}.*region",
     "not": r"region|pre-?nat|pre-?champ", "window": 21,
     "teams_auto": 3, "teams_at_large": 10, "ind_auto": 2, "ind_auto_top": 5,
     "ind_at_large": 8, "team_size": 7,
     "cite": "2026 NCAA DII Cross Country Championships Selection Procedures: 8 regions; "
             "top 3 teams each; 10 at-large; top 2 not on a qualifying team and anyone "
             "top 5 not on one; 8 at-large individuals by [team qualifiers]/[place]"},
    {"key": "ncaa_d3", "source": "tfrrs",
     "meet": rf"ncaa.*{_D3}.*champ", "round": rf"ncaa.*{_D3}.*region",
     "not": r"region|pre-?nat|pre-?champ", "window": 21,
     "teams_auto": 1, "teams_at_large": 22, "ind_auto": 7, "ind_at_large": 0,
     "team_size": 7,
     "cite": "2025-26 NCAA DIII Cross Country Pre-Championships Manual, section 2.1: "
             "10 regions; 1 team each; 22 at-large; top 7 not on a qualifying team"},
    {"key": "nxn", "source": "anet",
     "meet": r"nike cross nationals|\bnxn\b",
     "round": r"\bnxr\b.*regional|nike cross regional|federation.*\bnxr\b|\bcif\b.*\bstate\b",
     # ★ CALIFORNIA COMES THROUGH ITS STATE MEET (owner, 2026-11-04: "for
     #   california just do CIF state"): every division of it is one round,
     #   run on one course on one day, ranked by time (fieldFromRounds)
     "merged": r"\bcif\b.*\bstate\b",
     # ★ AND NEW YORK IS NOT ITS OWN ROUND ANY MORE (owner, 2026-11-04: "NY
     #   isn't its own thing anymore (starting this year)"): the Federation
     #   meet counts for the 2025 season and before only
     "round_last": ((r"federation", 2025),),
     # a regional's best runners race it whether their school went last
     # year or not (upcoming_preview.regionRunners): an open qualifier
     "region_adds": True,
     # ! A REGIONAL'S MEET ALSO HOLDS ITS COMMUNITY, CITIZENS, KIDS AND
     #   MIDDLE SCHOOL RACES, and the NYS Federation runs "at NXR" (the
     #   server's 2025 list)
     "round_not": r"^(?!.*regional (champ|open)).*(community|citizen|kids|grade school|"
                  r"middle school|\bms\b)",
     # ★ BUT NEW YORK'S TEAMS QUALIFIED THROUGH "NYS Federation Championships at
     #   NXR" in 2025, the day of the merged Northeast & New York regional
     #   (qualifying_check: Bethlehem Central, Shenendehowa, Ward Melville
     #   missed without it)
     "not": r"region|\bnxr\b", "window": 28,
     "teams_auto": 2, "teams_at_large": 8, "ind_auto": 5, "ind_at_large": 5,
     # ★ CALIFORNIA'S AT-LARGE TEAMS COME FROM ITS STATE MEET (qualifying_check
     #   on NXN 2025: Mira Costa, Redondo Union, San Clemente, Coronado, J Serra,
     #   Trabuco Hills missed, all at-large after the CIF state meet), and the
     #   field ran 22 teams a gender: 7 regionals x 2 + 8 at-large
     "team_size": 7,
     "cite": "NXN qualifying (MileSplit / Dyestat summaries; check each year): top 2 "
             "teams per regional; 4 at-large teams per gender; top 5 individuals not on "
             "a qualifying team; about 5 at-large individuals"},
    # ★ BROOKS XC CHAMPIONSHIPS (owner, 2026-11-04: "isn't it only 40?"):
    #   individuals only, the top ten of each of four regionals plus Golden
    #   Tickets to about fifty a gender, raced and scored by region
    {"key": "brooks", "source": "anet",
     "meet": r"\bbrooks\b.*\bnational", "not": r"northeast|midwest|\bsouth\b|\bwest\b|region",
     "round": r"\bbrooks\b.*\b(northeast|midwest|south|west)\b|"
              r"\b(northeast|midwest|south|west)\b.*\bbrooks\b",
     "round_not": r"national|middle school|\bms\b|community|kids|open race", "window": 28,
     "teams_auto": 0, "teams_at_large": 0, "ind_auto": 10, "ind_at_large": 10,
     "ind_at_large_by": "rating", "teams_by_round": r"\b(northeast|midwest|south|west)\b",
     "region_adds": True,
     "team_size": 7,
     "cite": "Brooks XC Championships (events.brooksrunning.com; Wikipedia's Foot Locker / "
             "Brooks entry): four regionals, the top 10 of each qualify; Golden Tickets "
             "to about 50 a gender; the final is scored by region"},
)


def ruleFor(meet_name, source=None):
    """The rule whose championship this meet is, or None. Pure.

    ★ IN EITHER FEED (the nightly, 2026-11-04: the NCAA DII and DIII
      championships fell to learned routes and found too few rounds -- the
      copy kept of each was athletic.net's, and the rules only matched
      tfrrs). A championship is the same meet whichever feed filed it; the
      rule's own "source" is where its ROUNDS are read. `source` is kept
      for the callers and no longer filters."""
    nm = (meet_name or "").lower()
    for r in RULES:
        if re.search(r["meet"], nm) and not re.search(r["not"], nm):
            return r
    return None


def isRound(rule, meet_name, season=None):
    """Is this meet one of the rule's rounds (in `season`, the championship's
    season year, where the rule's rounds changed)? Pure."""
    nm = (meet_name or "").lower()
    if season is not None and any(re.search(pat, nm) and int(season) > last
                                  for pat, last in rule.get("round_last") or ()):
        return False
    return bool(re.search(rule["round"], nm)) and not (
        rule.get("round_not") and re.search(rule["round_not"], nm))


def isMerged(rule, meet_name):
    """A round whose races of a gender are one ranking (CIF State's
    divisions for NXN). Pure."""
    return bool(rule.get("merged") and re.search(rule["merged"], (meet_name or "").lower()))


def pickRace(races):
    """The round's race among a meet's races of one gender: the one its
    label calls the championship, else (in roundResults) the largest. Pure."""
    side = (r"community|citizen|open|kids|\bms\b|middle|\bjv\b|junior varsity|fresh|frosh|"
            r"soph|reserve|novice|youth")
    champ = [r for r in races if re.search(r"champ", r.get("label") or "", re.I)
             and not re.search(side, r.get("label") or "", re.I)]
    if len(champ) == 1:
        return champ
    # no one championship race: never an Open, JV or community race while
    # another is left (owner, 2026-11-04, NXN)
    main = [r for r in races if not re.search(side, r.get("label") or "", re.I)]
    return main or races


# ------------------------------------------------------------------ #
#  the engine (pure)
# ------------------------------------------------------------------ #

# ★ UNATTACHED IS NOT A TEAM (scripts/qualifying_check.py, NXN 2025: an
#   NXR's "Unattached IL", "UNA-CO", "UNA-UT" each had five runners and were
#   sent on as teams). meet_compile.isTeam, and the UNA-<state> spelling it
#   does not know.
_UNATTACHED = re.compile(r"^\s*una\b|^\s*una-|unattach|independent|\bunaff", re.I)


def isTeam(school):
    from meet_compile import isTeam as compiledTeam
    return compiledTeam(school) and not _UNATTACHED.search(school or "")


def teamOrder(rows):
    """Schools with five or more finishers, best first by the sum of their
    top five places among such schools' runners. rows: [(pid, school,
    seconds, rating)] of one race."""
    by = collections.defaultdict(list)
    for r in rows:
        if r[1]:
            by[r[1]].append(r)
    teams = {s for s, v in by.items() if len(v) >= 5 and isTeam(s)}
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


def atLargeTeams(queues, n, strength, taken=()):
    """n picks from per-round queues of teams in regional finish order: each
    pick the strongest team at the head of any queue, so no team goes
    ahead of one that beat it at its own round. queues: {round: [school]};
    a school already in (`taken`, or picked from another queue) is passed."""
    q = {k: list(v) for k, v in queues.items()}
    out, seen = [], set(taken)
    for _ in range(n):
        for v in q.values():
            while v and v[0] in seen:
                v.pop(0)
        heads = [(strength(k, v[0]), k) for k, v in q.items() if v]
        if not heads:
            break
        _s, k = max(heads, key=lambda h: (h[0], str(h[1])))
        out.append((k, q[k].pop(0)))
        seen.add(out[-1][1])
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


def selectField(rule, rounds, pools=None, labels=None):
    """{person_id: school}: the championship's field from its rounds by the
    rule. rounds: {round key: [(pid, school, seconds, rating)]}; pools: other
    races at-large teams may also come from (rule["at_large_from"]), same
    shape -- no automatic places and no individuals from them."""
    size = rule.get("team_size", 7)
    rounds, pools = _oneSpelling(rounds, pools or {})
    orders = {k: teamOrder(v) for k, v in rounds.items()}
    qualified = {k: list(o[:rule["teams_auto"]]) for k, o in orders.items()}
    queues = {k: o[rule["teams_auto"]:] for k, o in orders.items()}
    queues.update({("pool", k): teamOrder(v) for k, v in pools.items()})
    rows_of = dict(rounds)
    rows_of.update({("pool", k): v for k, v in pools.items()})
    auto = {s for v in qualified.values() for s in v}
    field = {}
    for k, school in atLargeTeams(queues, rule.get("teams_at_large", 0),
                                  lambda k, s: teamStrength(rows_of[k], s), taken=auto):
        if k in qualified:
            qualified[k].append(school)
        else:
            for r in sorted((r for r in rows_of[k] if r[1] == school), key=lambda r: r[2])[:size]:
                field[r[0]] = r[1]
    schools = {r[0]: r[1] for rows in rounds.values() for r in rows}
    tickets_first = rule.get("ind_at_large_by") == "rating"
    if tickets_first:
        # ★ GOLDEN TICKETS FIRST (owner, 2026-11-04: "take the ten fastest as
        #   indivs first, then do the people in regionals"): the best-rated
        #   runners of every round are in before any regional place is
        #   counted, and a regional's top ten are the ten best of the rest
        best = {}
        for rows in rounds.values():
            for r in rows:
                if r[0] not in field and r[3] is not None:
                    best[r[0]] = max(best.get(r[0], r[3]), r[3])
        for pid in sorted(best, key=lambda p: -best[p])[:rule.get("ind_at_large", 0)]:
            field[pid] = schools.get(pid)
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
            if r[0] in field:           # a Golden Ticket already: not a regional place
                continue
            takes = (auto < rule.get("ind_auto", 0) and (within is None or place <= within)) \
                or place <= rule.get("ind_auto_top", 0)
            if takes:
                field[r[0]] = r[1]
                auto += 1
            else:
                rest.append((place, r[0]))
        ind_queues[k] = rest
    if not tickets_first:
        for pid in atLargeIndividuals(ind_queues, rule.get("ind_at_large", 0), n_teams):
            field[pid] = schools.get(pid)
    field = _capTeams(field, rounds, pools, size)
    if labels and rule.get("teams_by_round"):
        # ★ RACED BY REGION (Brooks): each runner under the region they came
        #   through, a Golden Ticket under the region of their round
        came = {}
        for k, rows in rounds.items():
            for r in rows:
                came.setdefault(r[0], k)
        field = {p: labels.get(came.get(p), s) for p, s in field.items()}
    return field


# ★ THE SITE'S OWN MERGES FIRST (owner, 2026-11-04: "There are other things
#   like this in the codebase, can you make sure that they use those
#   results"). scripts/merge_school_names.py writes school_name_alias:
#   two spellings one team's athletes wear both of, proved by the people
#   (La Jolla (CA) / La Jolla-CA), the boards already file rows under it
#   (build_ranking_results.canonicalSchool). A round's rows come from the
#   results table in each feed's own spelling; the alias puts them under
#   the boards' one, and nameKey catches what is left.
_ALIAS = {"at": 0.0, "map": {}}
ALIAS_TTL = 3600


def loadAliases(cur):
    """school_name_alias as {variant: canonical}, chains followed; read at
    most once an hour per process. {} when the table is missing."""
    import time
    from school_name import resolveChains
    if _ALIAS["at"] and time.time() - _ALIAS["at"] < ALIAS_TTL:
        return _ALIAS["map"]
    got = {}
    try:
        cur.execute("SAVEPOINT q_alias")
        cur.execute("SELECT to_regclass('public.school_name_alias') IS NOT NULL AS ok")
        row = cur.fetchone()
        if row and (row["ok"] if isinstance(row, dict) else row[0]):
            cur.execute("SELECT variant, canonical FROM school_name_alias")
            for r in cur.fetchall():
                v, c = (r["variant"], r["canonical"]) if isinstance(r, dict) else (r[0], r[1])
                if v and c and v != c:
                    got[v] = c
        cur.execute("RELEASE SAVEPOINT q_alias")
    except Exception:                                   # noqa: BLE001
        try:
            cur.execute("ROLLBACK TO SAVEPOINT q_alias")
        except Exception:                               # noqa: BLE001
            pass
    _ALIAS["map"] = {k: v for k, v in resolveChains(got).items() if k != v}
    _ALIAS["at"] = time.time()
    return _ALIAS["map"]


def canonicalSchool(school):
    """The boards' spelling of a school (school_name_alias, once loaded)."""
    return _ALIAS["map"].get(school, school) if school else school


def oneSpellingField(field):
    """A finished field {pid: school} with each school under one spelling:
    the site's alias, then names that differ only in decoration. Pure but
    for the loaded alias."""
    from school_name import nameKey
    field = {p: canonicalSchool(s) for p, s in (field or {}).items()}
    counts = collections.defaultdict(collections.Counter)
    for s in field.values():
        if s:
            counts[nameKey(s) or s][s] += 1
    spell = {}
    for c in counts.values():
        if len(c) > 1:
            best = min(c.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0]
            spell.update({n: best for n in c})
    return {p: spell.get(s, s) for p, s in field.items()}


def _oneSpelling(rounds, pools):
    """The rounds' rows with each school under one spelling (owner,
    2026-11-04: NCAA DI listed "Washington St." and "Washington State (WA)",
    a runner each -- one feed's spelling a round that ran, the other's a
    round predicted). Spellings that differ only in decoration
    (school_name.sameKey: "St." and "State") take the commonest. Pure."""
    from school_name import nameKey
    if _ALIAS["map"]:
        rounds = {k: [(r[0], canonicalSchool(r[1])) + tuple(r[2:]) for r in v]
                  for k, v in rounds.items()}
        pools = {k: [(r[0], canonicalSchool(r[1])) + tuple(r[2:]) for r in v]
                 for k, v in pools.items()}
    counts = collections.defaultdict(collections.Counter)
    for rows in list(rounds.values()) + list(pools.values()):
        for r in rows:
            if r[1]:
                counts[nameKey(r[1]) or r[1]][r[1]] += 1
    spell = {}
    for _k, c in counts.items():
        if len(c) > 1:
            best = min(c.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0]
            spell.update({name: best for name in c})

    def fix(rows):
        return [(r[0], spell.get(r[1], r[1])) + tuple(r[2:]) for r in rows]
    if not spell:
        return rounds, pools
    return ({k: fix(v) for k, v in rounds.items()}, {k: fix(v) for k, v in pools.items()})


def _capTeams(field, rounds, pools, size):
    """At most `size` runners a school (owner, 2026-11-04: Southlake Carroll
    listed with 14 at NXN): a school through two rounds is one team, its
    best `size` by season rating, else by time. Pure."""
    best = {}
    for rows in list(rounds.values()) + list((pools or {}).values()):
        for pid, school, sec, rating in rows:
            key = (-(rating or 0), sec)
            if pid in field and (pid not in best or key < best[pid]):
                best[pid] = key
    by = collections.defaultdict(list)
    for pid, school in field.items():
        by[school].append(pid)
    out = dict(field)
    for school, pids in by.items():
        if school and isTeam(school) and len(pids) > size:
            for pid in sorted(pids, key=lambda p: best.get(p, (0, 0)))[size:]:
                out.pop(pid, None)
    return out


# ------------------------------------------------------------------ #
#  the database half
# ------------------------------------------------------------------ #

def _roundMeets(cur, rule, day, window, season=None):
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
    from season_year import seasonYearFromIso
    # the championship's season decides which rounds count (round_last),
    # also when last season's rounds are read for it (pastRounds)
    season = season if season is not None else seasonYearFromIso("XC", str(day)[:10])
    out = []
    for row in cur.fetchall():
        row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        if isRound(rule, row[1], season):
            out.append((int(row[0]), row[1], str(row[2])[:10]))
    return out


def _seasonOf(day):
    from season_year import seasonYearFromIso
    return seasonYearFromIso("XC", str(day)[:10])


def _shift(day, days):
    import datetime
    return (datetime.date.fromisoformat(str(day)[:10])
            + datetime.timedelta(days=days)).isoformat()


def roundResults(cur, rule, meet_id, gender, season=None, every=False):
    """[(pid, school, seconds, season rating)] of the round meet's race of
    that gender (the largest, when it has several), or []; every=True: a
    list of every race of that gender's rows. The rating is the season's
    (or the latest before it): never one made after."""
    import last_edition as LE
    races = [r for r in _gendered(cur, rule, meet_id,
                                  LE.editionRaces(cur, meet_id, "XC", rule["source"]))
             if r.get("gender") == gender]
    if not every:
        races = pickRace(races)
    best, all_rows = [], []
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
        all_rows.append(rows)
        if len(rows) > len(best):
            best = rows
    return all_rows if every else best


def fieldFromRounds(cur, rule, day, gender, predict=None, predict_past=None, info=None):
    """({person_id: school}, rounds predicted) for the championship on
    `day` (ISO) in `gender`, or (None, 0) with fewer than MIN_ROUNDS
    rounds. predict(meet_id, gender) -> rows like roundResults', for a round
    with no results yet; without it only rounds that have run count.
    predict_past(meet_id, div_id, on) -> rows: last season's round run
    again on `on` (ISO), for a round whose edition this season is not
    posted (pastRounds). `info`, a dict, gets "rounds": how many counted."""
    import last_edition as LE
    if rule is None or gender not in ("M", "F"):
        return None, 0
    if cur is not None:
        loadAliases(cur)
    rounds, n_pred, got = {}, 0, set()
    seen = sorted(_roundMeets(cur, rule, day, rule.get("window", 21)),
                  key=lambda m: _sideMeet(m[1]))
    for mid, name, _d in seen:
        # ! ONE ROUND PER EDITION: a region filed under two meet ids is one
        #   region (the nightly, 2026-11-04: DI read 11 and 13 rounds of 9)
        if roundKey(name) in got:
            continue
        merged = isMerged(rule, name)
        rows = (_oneRanking(roundResults(cur, rule, mid, gender, int(str(day)[:4]), every=True))
                if merged else roundResults(cur, rule, mid, gender, int(str(day)[:4])))
        if len(rows) < 10 and predict is not None:
            rows = (predict(mid, gender, True) if merged else predict(mid, gender)) or []
            n_pred += len(rows) >= 10
        if len(rows) >= 10:
            rounds[mid] = rows
            got.add(roundKey(name))
    past, past_ok = [], 0
    if predict_past is not None:
        past = pastRounds(cur, rule, day, gender, got)
        acc = collections.OrderedDict()
        for mid, div, on in past:          # a merged round: one entry per race
            acc.setdefault(mid, []).append(predict_past(mid, div, on) or [])
        for mid, lists in acc.items():
            rows = _oneRanking(lists) if len(lists) > 1 else lists[0]
            if len(rows) >= 10:
                rounds[mid] = rows
                n_pred += 1
                past_ok += 1
    if info is not None:
        # what was looked at, for the nightly to say why a field is missing
        info.update(rounds=len(rounds), this_season=len(seen), counted=len(rounds) - past_ok,
                    last_season=len({p[0] for p in past}), last_season_ok=past_ok)
    if len(rounds) < MIN_ROUNDS:
        return None, 0
    labels = None
    if rule.get("teams_by_round"):
        names = {mid: name for mid, name, _d in seen}
        names.update({mid: name for mid, name, _d in
                      _roundMeets(cur, rule, _shift(day, -364), rule.get("window", 21),
                                  season=_seasonOf(day))})
        labels = {}
        for mid, name in names.items():
            m = re.search(rule["teams_by_round"], (name or "").lower())
            if m:
                labels[mid] = f"{m.group(1).title()} region"
    pools = {}
    if rule.get("at_large_from"):
        extra = dict(rule, round=rule["at_large_from"], round_not=None)
        for mid, _name, _d in _roundMeets(cur, extra, day, rule.get("window", 21)):
            for i, rows in enumerate(roundResults(cur, rule, mid, gender,
                                                  int(str(day)[:4]), every=True)):
                if len(rows) >= 10:
                    pools[(mid, i)] = rows
    return selectField(rule, rounds, pools, labels), n_pred


# ★ LAST YEAR'S ROUNDS, RUN THIS YEAR (owner's nightly, 2026-11-04: the
#   NCAA championships "no race with enough rounds" -- this season's
#   regionals are not posted until the week they run, so there was nothing
#   to predict). A round whose edition this season is not among the rounds
#   found is last season's meet, its schools' squads now, on its date a year
#   on (364 days, the same weekday): the predictions page's "this year" of
#   the same meet. The nightly only (upcoming_preview.predictedPast).
def _oneRanking(lists):
    """Several races' rows as one ranking by time, one row a runner (their
    fastest). Pure."""
    best = {}
    for rows in lists or []:
        for r in rows or []:
            if r[0] not in best or r[2] < best[r[0]][2]:
                best[r[0]] = r
    return sorted(best.values(), key=lambda r: r[2])


def pastRounds(cur, rule, day, gender, have=()):
    """[(meet_id, div_id, on)] of last season's rounds of `rule` in
    `gender` whose edition name is not in `have`: the race pickRace takes
    (the one with the most runners when it leaves several), and `on`, its
    date a year on."""
    import last_edition as LE
    out, have = [], set(have)
    from season_year import seasonYearFromIso
    for mid, name, d in sorted(_roundMeets(cur, rule, _shift(day, -364), rule.get("window", 21),
                                           season=seasonYearFromIso("XC", str(day)[:10])),
                               key=lambda m: _sideMeet(m[1])):
        if roundKey(name) in have:
            continue
        races = [r for r in _gendered(cur, rule, mid,
                                      LE.editionRaces(cur, mid, "XC", rule["source"]))
                 if r.get("gender") == gender]
        if isMerged(rule, name):
            out.extend((mid, int(r["div_id"]), _shift(d, 364)) for r in races)
            have.add(roundKey(name))
            continue
        races = pickRace(races)
        if not races:
            continue
        if len(races) > 1:
            cur.execute("""SELECT div_id, count(*) AS n FROM results
                           WHERE meet_id = %s AND source = %s GROUP BY div_id""",
                        (mid, rule["source"]))
            n = {}
            for row in cur.fetchall():
                row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
                n[int(row[0])] = int(row[1])
            races = [max(races, key=lambda r: n.get(int(r["div_id"]), 0))]
        out.append((mid, int(races[0]["div_id"]), _shift(d, 364)))
        have.add(roundKey(name))
    return out


# ★ ONE REGION, HOWEVER ITS MEET IS NAMED THAT YEAR (owner's NXN check,
#   2026-11-04: "NXR South Regional Championships" this season and "... &
#   Community Race" last season were two regions, and the Northeast was read
#   from its "Regional Open" meet). A round is its edition name less the
#   words that only say which meet of the weekend it is.
_ROUND_NOISE = {"championship", "championships", "champs", "champ", "regional", "regionals",
                "region", "community", "race", "races", "open", "and", "the", "meet", "of"}


def roundKey(name):
    """Which round a meet is, as a set of words. Pure."""
    import last_edition as LE
    return frozenset(w for w in re.findall(r"[a-z0-9]+", (LE.editionName(name) or "").lower())
                     if w not in _ROUND_NOISE)


def _sideMeet(name):
    """0 for a round's championship meet, 1 for its open / community meet:
    the championship is read first. Pure."""
    nm = (name or "").lower()
    return int(bool(re.search(r"\bopen\b|community|citizen", nm)))


def _gendered(cur, rule, meet_id, races):
    """A meet's races, each with a gender: its runners' (editionRaces), else
    its label's, else the season pools of the people who ran it
    (predict._fieldGender). The owner's Brooks check, 2026-11-04: three of
    the four 2025 regionals named no gender anywhere but in who ran them,
    and only one counted."""
    out = []
    for r in races or []:
        g = r.get("gender") or labelGender(r.get("label"))
        if g is None and cur is not None:
            try:
                cur.execute("SAVEPOINT q_gender")
                cur.execute("""SELECT DISTINCT person_id FROM results
                               WHERE meet_id = %s AND div_id = %s AND source = %s
                                 AND person_id IS NOT NULL LIMIT 300""",
                            (int(meet_id), int(r["div_id"]), rule["source"]))
                ids = [int(x["person_id"] if isinstance(x, dict) else x[0])
                       for x in cur.fetchall()]
                import predict as P
                g = P._fieldGender(cur, ids, "XC") if ids else None
                cur.execute("RELEASE SAVEPOINT q_gender")
            except Exception:                           # noqa: BLE001
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT q_gender")
                except Exception:                       # noqa: BLE001
                    pass
                g = None
        out.append(dict(r, gender=g))
    return out


def labelGender(label):
    """"M" / "F" from a race label that says so, else None. Pure."""
    lab = (label or "").lower()
    women = re.search(r"\b(women|womens|women's|girls?|female|ladies)\b", lab)
    men = re.search(r"\b(men|mens|men's|boys?|male)\b", lab)
    if women and not men:
        return "F"
    if men and not women:
        return "M"
    return None


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
        div = _roundDiv(races, rnd)
        if div is not None:
            return int(row[0]), div, ran
    return None


def _roundDiv(races, rnd):
    """The div_id of a learned round's race among a meet's races: by label
    (else gender), or None when that leaves not exactly one. Pure."""
    import last_edition as LE
    lab = LE.editionName(rnd.get("race"))
    pick = [r for r in races if lab and LE.editionName(r.get("label")) == lab
            and (not rnd.get("gender") or not r.get("gender") or r["gender"] == rnd["gender"])]
    if len(pick) != 1:
        pick = [r for r in races if rnd.get("gender") and r.get("gender") == rnd["gender"]]
    return int(pick[0]["div_id"]) if len(pick) == 1 else None


def _lastRound(cur, rnd, source):
    """(meet_id, div_id) of the learned round itself -- last season's meet
    the route was learned from: its ids when the routes file has them, else
    the meet of that edition name on that date (a day either side)."""
    import last_edition as LE
    if rnd.get("meet_id") and rnd.get("div_id"):
        return int(rnd["meet_id"]), int(rnd["div_id"])
    lo, hi = _shift(rnd["date"], -1), _shift(rnd["date"], 1)
    if source == "tfrrs":
        cur.execute("""SELECT meet_id, meet_name FROM meets_tfrrs
                       WHERE sport = 'XC' AND date ~ '^(19|20)[0-9]{2}-'
                         AND date >= %s AND date <= %s""", (lo, hi))
    else:
        cur.execute("""SELECT DISTINCT meet_id, meet_name FROM meets
                       WHERE meet_date ~ '^(19|20)[0-9]{2}-'
                         AND meet_date >= %s AND meet_date <= %s""", (lo, hi))
    for row in cur.fetchall():
        row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        if LE.editionName(row[1]) != rnd["meet"]:
            continue
        div = _roundDiv(LE.editionRaces(cur, int(row[0]), "XC", source), rnd)
        if div is not None:
            return int(row[0]), div
    return None


def learnedField(cur, route, day, predict_div=None, predict_past=None, info=None):
    """({person_id: school}, rounds predicted) for a learned route's race on
    `day`, or (None, 0) when fewer than ROUTE_FOUND of its rounds are found.
    predict_div(meet_id, div_id) -> rows, for a round with no results yet;
    predict_past(meet_id, div_id, on) -> rows, last season's round run again
    on `on`, for one this season has not posted (fieldFromRounds)."""
    if cur is not None:
        loadAliases(cur)
    field, found, n_pred = {}, 0, 0
    tally = collections.Counter()
    for rnd in route.get("rounds") or []:
        at = _thisRound(cur, rnd, day, route["source"])
        tally["this_season"] += at is not None
        rows = []
        if at is not None:
            mid, div, ran = at
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
        # ★ NOT POSTED, OR POSTED AND NOT YET PREDICTABLE (the nightly,
        #   2026-11-04: rounds "found this season" that counted nothing):
        #   last season's round, run again a year on
        if len(rows) < 5 and predict_past is not None:
            last = _lastRound(cur, rnd, route["source"])
            tally["last_season"] += last is not None
            if last is not None:
                rows = predict_past(last[0], last[1], _shift(rnd["date"], 364)) or []
                if len(rows) >= 5:
                    n_pred += 1
                    tally["last_season_ok"] += 1
        if len(rows) < 5:
            continue
        found += 1
        field.update(simpleQualifiers(rows, int(rnd.get("teams") or 0),
                                      int(rnd.get("individuals") or 0)))
    # ! MOST OF THE ROUNDS OR NONE (qualifying_status, 2026-11-03: Oregon 6A
    #   listed without Jesuit, Central Catholic and Sunset -- a district not
    #   found is a district's teams missing, so a route that finds under
    #   ROUTE_FOUND of its rounds keeps the last-edition field instead)
    if info is not None:
        info.update(tally, rounds=found, of=len(route.get("rounds") or []))
    if found < ROUTE_FOUND * len(route.get("rounds") or []) or not field:
        return None, 0
    return oneSpellingField(field), n_pred
