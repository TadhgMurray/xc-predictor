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

from meet_compile import scoreRows, SCORERS, DISPLACERS

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


def teamKey(school, state):
    return f"{school or ''}{_SEP}{state or ''}"


def splitKey(key):
    school, _, state = key.partition(_SEP)
    return school, (state or None)


def rankTeams(athletes):
    """[{school, state, rating, person_id, name}] -> ranked teams.

    One scope's worth of athlete-seasons in, one scored meet out. Pure: no
    database, no globals, so the whole ranking rule is testable without a
    corpus.

    ! THE SCORING IS scoreRows, NOT A SECOND COPY OF THE RULES. That function
      already handles the things this would get wrong on its own -- teams too
      short to score are lifted out and the places renumber around them,
      Unattached is not a team, ties break on the sixth runner. Passing the
      team KEY as `school` is what lets a shared function do the work: it
      never inspects the value beyond grouping and the isTeam test, and the
      key carries the real school name as its first field so that test still
      sees a school name.
    """
    squads = {}
    for a in athletes:
        rating = a.get("rating")
        if rating is None:
            continue
        squads.setdefault(teamKey(a.get("school"), a.get("state")), []).append(a)

    field = []
    for key, members in squads.items():
        members.sort(key=lambda m: -float(m["rating"]))
        for m in members[:SQUAD]:
            field.append((float(m["rating"]), key, m))

    # ! THE TIE-BREAK IS THE KEY, NOT chance. Two athletes on the same rating
    #   must not swap places between runs, or a team's points move when
    #   nothing about it changed.
    field.sort(key=lambda t: (-t[0], t[1], str(t[2].get("person_id"))))

    rows = [{"school": key, "place": i, "person_id": m.get("person_id"),
             "name": m.get("name"), "rating": rating}
            for i, (rating, key, m) in enumerate(field, start=1)]

    scored = scoreRows(rows)

    out = []
    for t in scored["teams"]:
        school, state = splitKey(t["school"])
        members = squads[t["school"]]
        top5 = [float(m["rating"]) for m in members[:SCORERS]]
        out.append({
            "school": school,
            "state": state,
            "rank": t["place"],
            "points": t["points"],
            "n_athletes": len(members),
            # Shown BESIDE the rank, not instead of it: when a team's average
            # disagrees with where it placed, that gap is the story -- a high
            # average and a poor rank is a top-heavy squad.
            "top5_mean": round(sum(top5) / len(top5), 2) if top5 else None,
            "fifth_rating": round(top5[-1], 2) if len(top5) == SCORERS else None,
            "best_rating": round(top5[0], 2) if top5 else None,
            "scorers": [{"person_id": r.get("person_id"), "name": r.get("name"),
                         "place": r["score_place"],
                         "rating": round(float(r["rating"]), 2)}
                        for r in t["runners"][:SQUAD]],
        })
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

    # ---- same name, two states ------------------------------------------
    print("\nsame name in two states is two teams")
    twins = rankTeams(squad("La Salle", [140, 139, 138, 137, 136], state="IL")
                      + squad("La Salle", [130, 129, 128, 127, 126], state="PA"))
    check("two rows", len(twins), 2)
    check("distinct states", sorted(t["state"] for t in twins), ["IL", "PA"])

    print("\nall cases pass" if not bad else f"\n{bad} FAILURES")
    return bad


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(_selfCheck())
