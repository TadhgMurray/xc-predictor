"""At-large teams by season wins, the "Kolas" count (owner, 2026-11-16:
"do the kolas fix" -- the D3 projection sent too many West teams)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "racecast"))

import qualifying as Q  # noqa: E402


def _race(order):
    """Every finisher of a race: each school's five, interleaved in `order`
    (a school earlier in the list beats a later one)."""
    rows, t = [], 900.0
    for _lap in range(5):
        for s in order:
            rows.append((s, t))
            t += 1.0
    return rows


def test_season_record_counts_each_meeting():
    teams = {"A": "A", "B": "B", "C College": "C"}
    rec = Q.seasonRecord([_race(["A", "B"]), _race(["B", "A"]), _race(["A", "C College"])], teams)
    assert rec.get(("A", "B"), 0) == 0          # one each
    assert rec[("A", "C")] == 1 and rec[("C", "A")] == -1


def test_season_record_needs_five_from_each():
    rows = _race(["A"]) + [("B", 2000.0)] * 4
    assert Q.seasonRecord([rows], {"A": "A", "B": "B"}) == {}


def test_wins_beat_ratings():
    # West's head is rated higher, but East's head beat everyone already in
    queues = {"west": ["W1", "W2"], "east": ["E1"]}
    strength = {"W1": 60.0, "W2": 59.0, "E1": 50.0}
    record = {("E1", "X"): 1, ("X", "E1"): -1, ("E1", "Y"): 1, ("Y", "E1"): -1}
    got = Q.atLargeTeams(queues, 1, lambda k, s: strength[s], taken={"X", "Y"}, record=record)
    assert got == [("east", "E1")]
    # without a record the ratings decide, as before
    assert Q.atLargeTeams(queues, 1, lambda k, s: strength[s], taken={"X", "Y"}) == [("west", "W1")]


def test_regional_order_still_holds():
    # W2 beat everyone, but W1 beat it at their regional: W1 first
    queues = {"west": ["W1", "W2"], "east": ["E1"]}
    record = {("W2", "E1"): 1, ("E1", "W2"): -1, ("W2", "X"): 1, ("X", "W2"): -1}
    got = Q.atLargeTeams(queues, 1, lambda k, s: {"W1": 50, "W2": 49, "E1": 55}[s],
                         taken={"X"}, record=record)
    assert got == [("east", "E1")]


def test_select_field_uses_the_meetings():
    rule = {"teams_auto": 1, "teams_at_large": 1, "at_large_by": "wins",
            "ind_auto": 0, "ind_at_large": 0, "team_size": 7}

    def region(tag, schools, base):
        rows, pid, t = [], 0, base
        for _lap in range(7):
            for s in schools:
                pid += 1
                rows.append((f"{tag}{pid}", s, t, 50.0))
                t += 1.0
        return rows
    rounds = {"w": region("w", ["W1", "W2"], 900.0), "e": region("e", ["E1", "E2"], 950.0)}
    # W2's runners are faster, so by ratings/strength they tie; E2 beat W1 and E1 in season
    seen = {}

    def meetings(squads):
        seen.update(squads)
        return {("E2", "W1"): 1, ("W1", "E2"): -1, ("E2", "E1"): 1, ("E1", "E2"): -1}
    why = {}
    field = Q.selectField(rule, rounds, explain=why, meetings=meetings)
    assert set(seen) == {"W1", "W2", "E1", "E2"}
    assert why["at_large"] == [("e", "E2")] and why["by"] == "wins"
    assert "E2" in set(field.values()) and "W2" not in set(field.values())


class _Cur:
    """results and meets_tfrrs in memory, for seasonMeetings' three reads."""

    def __init__(self, results, names):
        self.results, self.names, self.out = results, names, []

    def execute(self, sql, args=None):
        if "FROM   results" in sql and "person_id = ANY" in sql:
            _src, pids, lo, hi = args
            self.out = [(p, m, d, s) for p, m, d, s, _t, day in self.results
                        if p in pids and lo <= day < hi]
        elif "meets_tfrrs" in sql:
            self.out = [(m, n) for m, n in self.names.items() if m in args[0]]
        else:
            self.out = [(m, d, s, t) for _p, m, d, s, t, _day in self.results if m in args[1]]

    def fetchall(self):
        return self.out


def test_season_meetings_follow_the_runners_and_skip_regionals():
    rule = Q.ruleFor("NCAA Division III Cross Country Championships", "tfrrs")
    res, t = [], 900.0
    # meet 1 (an invitational): WashU under its new spelling beats Chicago
    # meet 2 (a regional): Chicago beats WashU -- not counted
    for meet, order, day in ((1, ["Washington (Mo.)", "Chicago"], "2026-10-01"),
                             (2, ["Chicago", "Washington (Mo.)"], "2026-11-14")):
        for lap in range(5):
            for s in order:
                pid = (100 if s == "Chicago" else 200) + lap
                res.append((pid, meet, 1, s, t, day))
                t += 1.0
    names = {1: "Pre-Nationals Invitational",
             2: "NCAA Division III Midwest Region Cross Country Championships"}
    squads = {"Washington U.": set(range(200, 205)), "U. of Chicago": set(range(100, 105))}
    rec = Q.seasonMeetings(_Cur(res, names), rule, "2026-11-21", squads)
    assert rec == {("Washington U.", "U. of Chicago"): 1, ("U. of Chicago", "Washington U."): -1}


def test_modes_differ_on_a_busy_loser():
    # B raced everyone: 3 wins, 6 losses; A met two teams and beat both
    queues = {"x": ["A"], "y": ["B"]}
    rec = {}
    for t in ("T1", "T2"):
        rec[("A", t)], rec[(t, "A")] = 1, -1
    for t in ("T1", "T2", "T3"):
        rec[("B", t)], rec[(t, "B")] = 1, -1
    for t in ("T4", "T5", "T6", "T7", "T8", "T9"):
        rec[("B", t)], rec[(t, "B")] = -1, 1
    taken = {f"T{i}" for i in range(1, 10)}
    pick = lambda m: Q.atLargeTeams(queues, 1, lambda k, s: 50.0, taken=taken, record=rec, mode=m)[0][1]
    assert pick("wins") == "B"
    assert pick("net") == "A" and pick("pct") == "A"


def test_ratings_stay_the_default(monkeypatch):
    monkeypatch.delenv("XCP_ATLARGE_BY", raising=False)
    import inspect
    src = inspect.getsource(Q.fieldFromRounds)
    assert 'os.environ.get("XCP_ATLARGE_BY", "rating")' in src
