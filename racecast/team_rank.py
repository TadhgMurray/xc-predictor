"""
team_rank.py -- rank teams by racing them.

★ THE RANKING IS A MEET THAT NEVER HAPPENED. Every team's top seven athletes
  are put in one hypothetical race, sorted by season rating, and scored with
  the ordinary rules. The board is the finish order of that meet.

WHY NOT AVERAGE THE TOP FIVE RATINGS, WHICH IS ONE QUERY AND NO MODULE
---------------------------------------------------------------------
Because averaging pays for margin and racing does not. Measured on the
example in the self-check below:

    Northgate  one 160 and four 12x   top-5 average 134.8   RACED: 42 pts, 2nd
    Ridgeview  an even 131-135 pack   top-5 average 133.0   RACED: 20 pts, 1st

Northgate's star lifts their average by five points single-handed. In a race
that runner can only ever finish FIRST -- they are worth one point and not a
point more, however far ahead they are -- while the rest of the squad is
buried at 8, 10, 11, 12. Averaging ratings ranks Northgate first; the sport
ranks them second, by 22 points.

★ AND DEPTH ONLY EXISTS IN THE RACED VERSION. Runners six and seven score
  nothing but push every rival scorer back a place, which is the whole
  tactical point of depth. No average of five numbers can see that.

⚠ THE POINTS DEPEND ON WHO ELSE IS IN THE FIELD, which an athlete rating
  never does. A team scores differently in its state's meet than in the
  national one, because the field is different -- exactly as in life. So a
  board is computed per scope and the points are only comparable WITHIN one
  board. The rank is the number to read across boards; the points explain it.

Run `python racecast/team_rank.py` for the self-check.
"""

from meet_compile import scoreRows, isTeam, SCORERS, DISPLACERS

# Seven runners: the five who score and the two who displace. Taking more
# would change nothing -- an eighth runner cannot affect any score.
SQUAD = SCORERS + DISPLACERS

# ⚠ A TEAM IS A SCHOOL IN A STATE, NOT A SCHOOL. Five distinct institutions
#   share the string "La Salle" in this corpus (level_graph measured the
#   team_ids: 1723 / 21012 / 8346 / 599 / 27495). Grouping on the name alone
#   merges them into one fictional super-team that would out-run all five.
#   The state splits most such collisions and costs nothing, because
#   athlete_season already carries it.
#
# ! THE SEPARATOR IS A UNIT SEPARATOR, NOT A HYPHEN OR A SLASH. School names
#   contain both -- "Hollidaysburg Area & Tyrone", "Mt. Si / Snoqualmie" --
#   and a separator that can occur in the data is a key that can collide.
_SEP = "\x1f"


# The unit columns a squad carries from its athletes to the stored board.
# build_team_season writes them into team_season; teams.py filters on them.
UNIT_KEYS = ("division", "region", "conference", "league",
             "state_div", "section_div", "class", "area", "section")


def teamKey(school, state):
    return f"{school or ''}{_SEP}{state or ''}"


def splitKey(key):
    school, _, state = key.partition(_SEP)
    return school, (state or None)


def _raceField(entries):
    """[(rating, key, tie, extra)] -> scoreRows output.

    ★ THE ONE PLACE THE FIELD IS ORDERED AND SCORED. Both callers race the
      same meet -- rankTeams from athlete rows, raceStored from a board that
      was already scored once -- and a second copy of "sort by rating, then
      score" is how the live board and the built one start disagreeing about
      who won.

    ! THE TIE-BREAK IS EXPLICIT, NOT chance. Two entrants on the same rating
      must not swap places between runs, or a team's points move when nothing
      about it changed. Key first, then the caller's stable id.
    """
    entries.sort(key=lambda t: (-t[0], t[1], t[2]))
    rows = [{"school": key, "place": i, "rating": rating, **extra}
            for i, (rating, key, _tie, extra) in enumerate(entries, start=1)]
    return scoreRows(rows)


def rankTeams(athletes):
    """[{school, state, rating, person_id, name}] -> ranked teams.

    One scope's worth of athlete-seasons in, one scored meet out. Pure: no
    database, no globals, so the whole ranking rule is testable without a
    corpus.

    ! THE SCORING IS scoreRows, NOT A SECOND COPY OF THE RULES. Teams too
      short to score are lifted out and the places renumber around them, and
      ties break on the sixth runner -- all of it already written once.

    ⚠ BUT THE isTeam TEST IS DONE HERE, ON THE REAL SCHOOL NAME, AND NOT LEFT
      TO scoreRows. It used to be: this passed the team KEY as `school` on the
      theory that the key carries the school name as its first field, so the
      test would still see one. That is true for the SUBSTRING rules and
      false for every ANCHORED one -- and _NOT_A_TEAM is mostly anchored.
      "Unattached" matched (\bunattached\b needs no anchor) while "N/A",
      "Individual", "none", "0", "---" and "USA" all sailed through, because
      the string being tested was "N/A\x1fNY" and $ never matched.

      Half a rule working is worse than none: it looked handled, and the
      cases it missed are exactly the ones nobody would notice -- a team
      called "N/A" quietly holding a rank on a national board.
    """
    squads = {}
    for a in athletes:
        rating = a.get("rating")
        if rating is None or not isTeam(a.get("school")):
            continue
        squads.setdefault(teamKey(a.get("school"), a.get("state")), []).append(a)

    field = []
    for key, members in squads.items():
        members.sort(key=lambda m: -float(m["rating"]))
        for m in members[:SQUAD]:
            field.append((float(m["rating"]), key, str(m.get("person_id")),
                          {"person_id": m.get("person_id"),
                           "name": m.get("name"),
                           # ★ THE RUNNER'S OWN POOL, so the HS-equivalent
                           #   view can convert each roster row by ITS
                           #   factor. Without it a pool=all board would
                           #   scale every scorer by the board's pool, which
                           #   is not a pool any of them race in.
                           "pool": m.get("pool"),
                           "sport": m.get("sport")}))

    scored = _raceField(field)

    out = []
    for t in scored["teams"]:
        school, state = splitKey(t["school"])
        members = squads[t["school"]]
        top5 = [float(m["rating"]) for m in members[:SCORERS]]
        out.append({
            "school": school,
            "state": state,
            # ★ THE SEASON, CARRIED LIKE THE UNITS BELOW. Every athlete of
            #   one squad shares it, so the first member speaks for the
            #   team. Absent from this dict until 2026-09-15, which made
            #   teams.sortAndPage's tie-break raise KeyError on every raced
            #   board -- the returning squad and the event window both.
            "year": members[0].get("year"),
            "rank": t["place"],
            "points": t["points"],
            "n_athletes": len(members),
            # Shown BESIDE the rank, not instead of it: when a team's average
            # disagrees with where it placed, that gap is the story -- a high
            # average and a poor rank is a top-heavy squad.
            "top5_mean": round(sum(top5) / len(top5), 2) if top5 else None,
            "fifth_rating": round(top5[-1], 2) if len(top5) == SCORERS else None,
            "best_rating": round(top5[0], 2) if top5 else None,
            # ★ THE SEVEN RATINGS THAT ENTERED THE MEET, kept so the board can
            #   be RE-raced later against a different field -- several seasons
            #   at once, say. Seven and not more because an eighth runner
            #   cannot affect any score; see SQUAD.
            "ratings": [round(float(m["rating"]), 2) for m in members[:SQUAD]],
            # ★ THE SQUAD'S UNITS, CARRIED THROUGH (2026-09-08). Every
            #   athlete of one team shares them -- they are stamped per
            #   (school, state) upstream and the key IS (school, state) --
            #   so the first member speaks for the squad. Passed along
            #   rather than looked up again: a second lookup would be a
            #   second answer, and this is the value the athlete boards
            #   already filter on.
            "units": {u: members[0].get(u) for u in UNIT_KEYS},
            # ! THE ROSTER ROWS, AND THEY CARRY THEIR POOL. app.py stamps
            #   hs_rating onto each of these; the HS-equivalent toggle
            #   showed raw pool ratings here until 2026-09-15 because only
            #   the TEAM-level numbers were ever stamped.
            "scorers": [{"person_id": r.get("person_id"), "name": r.get("name"),
                         "place": r["score_place"],
                         "pool": r.get("pool"), "sport": r.get("sport"),
                         "rating": round(float(r["rating"]), 2)}
                        for r in t["runners"][:SQUAD]],
        })
    return out


def raceStored(rows):
    """Re-race an already-built board against ITSELF.

    ★ THIS IS WHAT MAKES "ONE FIRST PLACE" TRUE RATHER THAN DRAWN. team_season
      holds one scored meet per season, so a board showing three years holds
      three rank-1 rows -- each true about its own year, and useless as an
      ordering. The fix is not to renumber the rows 1, 2, 3, which would
      invent a championship nobody ran. It is to RUN one: take every team the
      filter selected, enter the same top seven again, and score the field
      they actually make together. Then there is one winner because a meet
      was held, not because a loop counted.

    ⚠ IT IS ONLY MEANINGFUL BECAUSE RATINGS ARE ERA-ADJUSTED. 100 is the pool
      mean in 1998 and in 2025, so a 2003 squad and a 2025 squad can be put
      in the same field and the comparison means something. Racing raw times
      across twenty years would not be.

    ⚠ EVERY ROW IS ITS OWN TEAM, INCLUDING TWO ROWS WITH THE SAME NAME.
      Newbury Park 2024 and Newbury Park 2025 are two entries in this meet,
      not one squad of fourteen -- so the key is the ROW, not (school,
      state). Keying on the name would let a school that appears in thirty
      seasons field its thirty best runners as one team and win by a mile.

    ! AND THE isTeam TEST IS ON THE SCHOOL NAME, NOT ON THE KEY, for the
      reason rankTeams spells out: _NOT_A_TEAM is mostly anchored patterns,
      and a key with the state appended never matches one. Rows in
      team_season were already filtered at build time, so this is a second
      line of defence -- but a board is not the place to rely on somebody
      else having done it.

    Returns rows in raced order, each carrying:
        rank, points              this meet's result -- what the board shows
        board_rank, board_points  what the row scored in the board it came
                                  from, kept because "won its year" (or
                                  "third all-time") is worth not losing

    ! DELIBERATELY NOT CALLED season_rank. The rows can come from a season
      board or from the all-time one, and only the caller knows which -- a
      name that asserts the wrong one is how a tooltip ends up confidently
      labelling an all-time rank as a season finish.

    Returns None if any row has no stored ratings, which means the table
    predates the column and the caller must fall back to the stored board.
    """
    entries, by_key = [], {}
    for i, row in enumerate(rows):
        ratings = row.get("ratings")
        if not ratings:
            return None
        if not isTeam(row.get("school")):
            continue
        key = f"{row.get('school') or ''}{_SEP}{i}"
        by_key[key] = row
        # Sorted rather than trusted: the column is written sorted, but a
        # board that silently mis-scores because a builder changed is worse
        # than one line of defence here.
        for j, rating in enumerate(sorted((float(r) for r in ratings),
                                          reverse=True)[:SQUAD]):
            entries.append((rating, key, f"{i:08d}-{j}", {}))

    scored = _raceField(entries)

    out = []
    for t in scored["teams"]:
        row = by_key[t["school"]]
        out.append({**row,
                    "rank": t["place"],
                    "points": t["points"],
                    "board_rank": row.get("rank"),
                    "board_points": row.get("points")})
    out.sort(key=lambda r: r["rank"])
    return out


# ------------------------------------------------------------------ #
#  SELF-CHECK -- `python racecast/team_rank.py`
# ------------------------------------------------------------------ #

def _selfCheck():
    def squad(school, ratings, state="AR"):
        return [{"school": school, "state": state, "rating": r,
                 "person_id": f"{school}-{i}", "name": f"{school} {i}"}
                for i, r in enumerate(ratings, start=1)]

    bad = 0

    def check(label, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}"
              + ("" if ok else f"  (want {want!r})"))

    # ---- the case the module exists for --------------------------------
    print("a pack beats a superstar, which an average cannot see")
    teams = rankTeams(squad("Ridgeview", [135, 134, 133, 132, 131, 130, 129])
                      + squad("Northgate", [160, 130, 129, 128, 127, 126, 125]))
    by = {t["school"]: t for t in teams}
    check("Ridgeview raced 1st", by["Ridgeview"]["rank"], 1)
    check("Ridgeview points", by["Ridgeview"]["points"], 20)
    check("Northgate raced 2nd", by["Northgate"]["rank"], 2)
    check("but Northgate's average is higher",
          by["Northgate"]["top5_mean"] > by["Ridgeview"]["top5_mean"], True)

    # ---- depth displaces, which is the other thing an average misses ----
    print("\ndepth pushes a rival's scorers back")
    shallow = rankTeams(squad("Deep", [140, 139, 138, 137, 136, 135, 134])
                        + squad("Rival", [141, 133, 132, 131, 130]))
    deep = {t["school"]: t["points"] for t in shallow}
    thin = rankTeams(squad("Deep", [140, 139, 138, 137, 136])
                     + squad("Rival", [141, 133, 132, 131, 130]))
    thin_pts = {t["school"]: t["points"] for t in thin}
    check("Rival scores worse against the deeper squad",
          deep["Rival"] > thin_pts["Rival"], True)

    # ---- the rules scoreRows already enforces --------------------------
    print("\ninherited rules still hold")
    mixed = rankTeams(squad("Real", [140, 139, 138, 137, 136])
                      + squad("Unattached", [150, 149, 148, 147, 146])
                      + squad("Short", [145, 144, 143]))
    names = {t["school"] for t in mixed}
    check("no Unattached team", "Unattached" in names, False)
    check("no team too short to score", "Short" in names, False)
    check("the real team scores 1-5 despite finishing behind them",
          next(t["points"] for t in mixed if t["school"] == "Real"), 15)

    # ---- a national team is not a school team ---------------------------
    print("\nnational teams are excluded; schools named after countries are not")
    from meet_compile import isTeam
    excluded = ["USA", "U.S.A.", "usa", "United States", "Team USA",
                "Team Canada", "Team Great Britain", "Kenya National Team"]
    # ⚠ EVERY ONE OF THESE IS A REAL US SCHOOL. American towns are named
    #   after countries, so a bare country name cannot be the test -- see the
    #   comment beside _NOT_A_TEAM. pro_flag's header cites Denmark High
    #   School as the string that broke a simpler rule than this one.
    kept = ["Denmark High School", "Peru Central", "Cuba-Rushford",
            "Poland Seminary", "Norway-Paris", "Lebanon", "Mexico High School",
            "China Spring", "Canada", "Jamaica High School", "Teaneck",
            "India Hook Elementary"]
    check("no national team survives",
          [n for n in excluded if isTeam(n)], [])
    check("no school named after a country is lost",
          [n for n in kept if not isTeam(n)], [])

    # And it survives the scoring path, not just the predicate.
    intl = rankTeams(squad("Fayetteville", [140, 139, 138, 137, 136], state="AR")
                     + squad("USA", [165, 164, 163, 162, 161], state="AR")
                     + squad("Denmark High School", [130, 129, 128, 127, 126],
                             state="SC"))
    names = {t["school"] for t in intl}
    check("USA does not score", "USA" in names, False)
    check("Denmark High School does", "Denmark High School" in names, True)
    check("and the real school wins despite finishing behind USA",
          next(t["points"] for t in intl if t["school"] == "Fayetteville"), 15)

    # ---- same name, two states ------------------------------------------
    print("\nsame name in two states is two teams")
    twins = rankTeams(squad("La Salle", [140, 139, 138, 137, 136], state="IL")
                      + squad("La Salle", [130, 129, 128, 127, 126], state="PA"))
    check("two rows", len(twins), 2)
    check("distinct states", sorted(t["state"] for t in twins), ["IL", "PA"])

    # ---- re-racing an already-built board ------------------------------
    print("\nracing several seasons produces exactly one winner")

    def season(year, squads, state="CA"):
        """One season's team_season rows, scored the way the builder does --
        every team in that year racing each other, and nobody else."""
        athletes = []
        for school, ratings in squads:
            athletes += squad(school, ratings, state=state)
        return [{**t, "year": year, "sport": "XC", "pool": "hs_m",
                 "scope": "usa"} for t in rankTeams(athletes)]

    # Three seasons of a two-team board, each scored on its own -- so the
    # board holds three rank-1 rows, which is the whole problem.
    board = (
        season(2025, [("Newbury Park", [152, 151, 150, 149, 148, 147, 146]),
                      ("Great Oak",    [150, 148, 146, 145, 144, 143, 142])])
        + season(2024, [("Newbury Park", [148, 147, 146, 145, 144, 143, 142]),
                        ("Great Oak",    [149, 147, 145, 144, 143, 142, 141])])
        + season(2023, [("Newbury Park", [140, 139, 138, 137, 136, 135, 134]),
                        ("Great Oak",    [141, 139, 137, 136, 135, 134, 133])]))
    check("the stored board really does hold three first places",
          sum(1 for r in board if r["rank"] == 1), 3)

    raced = raceStored(board)
    check("one first place after racing",
          sum(1 for r in raced if r["rank"] == 1), 1)
    check("six teams, six distinct places",
          sorted(r["rank"] for r in raced), [1, 2, 3, 4, 5, 6])
    check("the strongest season wins",
          (raced[0]["school"], raced[0]["year"]), ("Newbury Park", 2025))
    check("the season each row won is not lost",
          sum(1 for r in raced if r["board_rank"] == 1), 3)

    # ⚠ WITHIN ONE SEASON THE HEAD-TO-HEAD IS UNCHANGED HERE -- derived from
    #   the fixture, not asserted from the ratings, because the pack wins two
    #   of these three years and eyeballing the top runner gets it backwards.
    #
    #   It is NOT a law: adding teams to a field can reverse a close result,
    #   since a rival's sixth and seventh runners displace your scorers. That
    #   is the sport, not a bug -- and it is exactly why the board re-races
    #   rather than renumbering a sort, which could never show it at all.
    raced_at = {(r["school"], r["year"]): r["rank"] for r in raced}
    stored_at = {(r["school"], r["year"]): r["rank"] for r in board}
    def agrees(year):
        rows = [k for k in stored_at if k[1] == year]
        a, b = sorted(rows, key=lambda k: stored_at[k])
        return raced_at[a] < raced_at[b]
    check("each season's own 1-2 survives the cross-year race",
          [agrees(y) for y in (2023, 2024, 2025)], [True, True, True])

    # ---- the same school in two seasons is two teams --------------------
    print("\nthe same school twice is two entries, not one deep squad")
    twice = raceStored(
        season(2025, [("Ridge", [140, 139, 138, 137, 136, 135, 134])])
        + season(2024, [("Ridge", [141, 140, 139, 138, 137, 136, 135])]))
    check("two rows out", len(twice), 2)
    check("distinct years", sorted(r["year"] for r in twice), [2024, 2025])
    # Fourteen runners keyed as one squad would score 1..5 = 15 and leave the
    # other row unscoreable; two teams of seven score 1-3-5-7-9 and 2-4-6-8-10.
    check("scored as two teams of seven",
          sorted(r["points"] for r in twice), [25, 30])

    # ---- and it refuses rather than guessing ---------------------------
    print("\nno ratings stored means no race, not a wrong one")
    check("returns None", raceStored([{"school": "X", "rank": 1}]), None)

    print("\nall cases pass" if not bad else f"\n{bad} FAILURES")
    return bad


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(_selfCheck())
