"""
racecast/watchdog.py (owner, 2026-10-10): the nightly data-quality watchdog.
REPORT ONLY. Every check's judgement is pure and tested here on stub rows;
the board churn runs against a stub cursor; the pipeline checks against
stub log directories; the source is checked to write only its own tables.

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_watchdog.py
"""
import datetime
import os
import re
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("racecast", "scripts", "engine"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import watchdog as W                                              # noqa: E402
import link_profile_school as LPS                                 # noqa: E402
import rating_outliers as RO                                      # noqa: E402
import breakouts as BO                                            # noqa: E402

D = datetime.date


# ------------------------------------------------------------------ #
# the thresholds are the engine's, not this module's
# ------------------------------------------------------------------ #

def test_thresholds_are_imported_not_picked():
    assert W.UNUSUAL_SIGMA == RO.OWNER_RANGE[0]
    assert W.MIN_HISTORY == RO.MIN_NEIGHBOURS
    assert W.SEASON_CHECK_JUMP == BO.CHECK_JUMP and W.SEASON_MIN_PRIOR == BO.MIN_PRIOR
    assert W.MAX_SEASONS == LPS.MAX_SEASONS_AT_ONE_SCHOOL
    assert set(W.BOARD_POOLS) == {p for p, _w in BO.LEVELS.values()}


# ------------------------------------------------------------------ #
# 1. performances
# ------------------------------------------------------------------ #

def _cand(rid, t, d, pool="hs_m", g="M", source="anet", meet=1, div=2, ev=None):
    return {"result_id": rid, "source": source, "meet_id": meet, "div_id": div, "event_id": ev,
            "time_seconds": t, "distance": d, "rating_pool": pool, "gender": g}


def test_a_record_beating_race_is_one_item_with_a_race_link():
    rows = [_cand(1, 600.0, 5000), _cand(2, 610.0, 5000), _cand(3, 1000.0, 5000)]
    got = W.judgePerformances("XC", rows)
    assert len(got) == 1
    f = got[0]
    assert f.check == "performance" and f.key.startswith("record:XC:anet:1:2")
    assert ("race", "/race/xc/1/2") in f.links
    assert sorted(f.subject["result_ids"]) == [1, 2]


def test_college_is_exempt_and_already_condemned_rows_are_not_reported():
    assert W.judgePerformances("XC", [_cand(1, 600.0, 5000, pool="college_m")]) == []
    assert W.judgePerformances("XC", [_cand(1, 600.0, 5000)], already=frozenset({1})) == []


def test_a_row_under_the_floor_but_not_the_record_is_a_row_item():
    # just inside the record pace times PACE_FLOOR_SLACK * hs factor, but
    # slower than the open record * slack
    import record_pace as RP
    rec = RP.recordPace(5000, "M")
    pace = rec * RP.PACE_FLOOR_SLACK * (1 + (RP.FLOOR_FACTOR - 1) / 2)
    t = pace * 5.0
    got = W.judgePerformances("XC", [_cand(9, t, 5000)])
    assert [f.key for f in got] == ["floor:XC:9"]


def test_rows_outside_the_pace_band_group_by_race():
    band = lambda pool, sport: (100.0, 600.0)                     # noqa: E731
    rows = [{"result_id": 1, "normalized_time": 50.0, "rating_pool": "hs_m|XC", "source": "anet",
             "meet_id": 5, "div_id": 6, "event_id": None},
            {"result_id": 2, "normalized_time": 700.0, "rating_pool": "hs_m", "source": "anet",
             "meet_id": 5, "div_id": 6, "event_id": None},
            {"result_id": 3, "normalized_time": 300.0, "rating_pool": "hs_m", "source": "anet",
             "meet_id": 5, "div_id": 6, "event_id": None}]
    got = W.judgeBand("XC", rows, band)
    assert len(got) == 1 and "1 faster" in got[0].detail and "1 slower" in got[0].detail
    assert got[0].subject["result_ids"] == [1, 2]


def test_breakouts_check_rows_become_items():
    rows = [{"kind": "pr", "sport": "TF", "result_id": 7, "person_id": 70, "name": "A B",
             "race_date": D(2026, 10, 3), "meet_id": 1, "div_id": 2, "event_id": 3,
             "meet_name": "M", "jump": None, "gain": 0.14},
            {"kind": "jump", "sport": "XC", "result_id": 8, "person_id": 80, "name": "C D",
             "race_date": D(2026, 10, 3), "meet_id": 1, "div_id": 2, "event_id": None,
             "meet_name": "M", "jump": 24.0, "gain": None}]
    got = {f.key: f for f in W.judgeBreakoutChecks(rows)}
    assert got["pr:TF:7"].check == "performance" and "14%" in got["pr:TF:7"].title
    assert got["XC:8"].check == "jump" and ("athlete", "/athlete/80") in got["XC:8"].links


# ------------------------------------------------------------------ #
# 2. jumps
# ------------------------------------------------------------------ #

def _career(ratings, start=D(2025, 9, 1), sport="XC"):
    return [{"result_id": i + 1, "sport": sport, "d": start + datetime.timedelta(days=7 * i),
             "rating": r, "fast_flag": False, "person_id": 42} for i, r in enumerate(ratings)]


def test_a_race_far_above_its_neighbours_is_a_jump():
    c = _career([100, 101, 99, 100, 102, 140])
    calib = {"k": 5, "floor": 1.5, "fast_cut": 6.0, "slow_cut": 6.0}
    got = W.judgeJumps(c, {("XC", 6)}, calib)
    assert len(got) == 1 and got[0].key == "XC:6"
    assert "sigma above" in got[0].detail
    # breakouts' season rule fires too (40 over the season median)
    assert "earlier races this season" in got[0].detail


def test_an_ordinary_race_is_not_a_jump_and_unjudged_rows_are_ignored():
    c = _career([100, 101, 99, 100, 102, 103])
    calib = {"k": 5, "floor": 1.5, "fast_cut": 6.0, "slow_cut": 6.0}
    assert W.judgeJumps(c, {("XC", 6)}, calib) == []
    c = _career([100, 101, 99, 100, 102, 140])
    assert W.judgeJumps(c, {("XC", 1)}, calib) == []


def test_the_season_rule_alone_without_a_calibration():
    c = _career([100, 100, 100 + BO.CHECK_JUMP])
    got = W.judgeJumps(c, {("XC", 3)}, None)
    assert len(got) == 1 and "sigma" not in got[0].detail
    # one earlier race is under MIN_PRIOR: no opinion
    c = _career([100, 100 + BO.CHECK_JUMP + 5])
    assert W.judgeJumps(c, {("XC", 2)}, None) == []


def test_last_runs_fast_outliers_are_no_ones_neighbour():
    c = _career([100, 101, 99, 100, 102, 140])
    for r in c[:5]:
        r["fast_flag"] = True               # nothing left to compare with
    calib = {"k": 5, "floor": 1.5, "fast_cut": 6.0, "slow_cut": 6.0}
    assert W.judgeJumps(c, {("XC", 6)}, calib) == []


# ------------------------------------------------------------------ #
# 3. duplicates
# ------------------------------------------------------------------ #

R = LPS.Row


def test_two_persons_one_name_one_school_one_season_are_a_duplicate():
    season_rows = [(1, "Mead", "XC", 2026), (2, "Mead", "XC", 2026), (3, "Mead", "XC", 2026)]
    names = {1: "Ann Fox", 2: "ann  fox", 3: "Bea Ray"}
    groups = W.nameGroups(season_rows, names, touched={2})
    assert list(groups.values()) == [[1, 2]]
    key = next(iter(groups))
    members = [LPS.Member(1, "Ann Fox", ["F"], False, [R("2026-09-05", "XC", "11", school="Mead")]),
               LPS.Member(2, "Ann Fox", ["F"], False, [R("2026-09-12", "XC", "11", school="Mead")])]
    f = W.judgeDuplicate(key, members)
    assert f and f.key == "people:1-2" and ("athlete", "/athlete/2") in f.links


def test_untouched_groups_and_two_runners_in_one_race_are_not_duplicates():
    season_rows = [(1, "Mead", "XC", 2026), (2, "Mead", "XC", 2026)]
    assert W.nameGroups(season_rows, {1: "Ann Fox", 2: "Ann Fox"}, touched={9}) == {}
    members = [LPS.Member(1, "Ann Fox", ["F"], False, [R("2026-09-05", "XC", "11", school="Mead")]),
               LPS.Member(2, "Ann Fox", ["F"], False, [R("2026-09-05", "XC", "11", school="Mead")])]
    assert W.judgeDuplicate(("mead", "XC", 2026, "ann fox"), members) is None


def test_no_team_strings_identify_nobody():
    rows = [(1, "Unattached", "XC", 2026), (2, "Unattached", "XC", 2026)]
    assert W.nameGroups(rows, {1: "Ann Fox", 2: "Ann Fox"}, touched={1}) == {}


def _rr(rid, pid, place, t):
    return {"result_id": rid, "person_id": pid, "place": place, "time_seconds": t,
            "source": "anet", "meet_id": 1, "div_id": 2, "event_id": None}


def test_one_finish_under_two_ids_is_reported_unless_result_twin_has_it():
    rows = [_rr(10, 100, 3, 1000.04), _rr(11, 200, 3, 1000.01), _rr(12, 300, 4, 1001.0)]
    got = W.sameRaceTwins("XC", rows, set())
    assert len(got) == 1 and got[0].key == "twin:XC:10-11"
    assert W.sameRaceTwins("XC", rows, {("XC", 11)}) == []
    # one person's own double row is result_twin's dup_same_feed, not this
    rows = [_rr(10, 100, 3, 1000.0), _rr(11, 100, 3, 1000.0)]
    assert W.sameRaceTwins("XC", rows, set()) == []


# ------------------------------------------------------------------ #
# 4. careers
# ------------------------------------------------------------------ #

def _c(date, grade, school="Mead", sport="XC", race=None, twin=False):
    return W.CareerRow(date, sport, grade, None, None, school, race or (date, school), twin)


def test_grades_two_generations_apart_are_a_career_problem():
    rows = [_c("2020-10-01", "11"), _c("2021-10-01", "12"), _c("2026-10-01", "10")]
    f = W.careerProblems(5, rows, rules=("profile_school",))
    assert f and f.key == "person:5" and "two generations" in f.detail
    assert "profile_school" in f.detail


def test_too_many_seasons_at_one_school():
    rows = [_c(f"{y}-04-01", None, sport="TF") for y in range(2010, 2010 + W.MAX_SEASONS + 2)]
    f = W.careerProblems(6, rows)
    assert f and "seasons at Mead" in f.detail


def test_two_cross_country_races_on_one_day_but_not_a_twin():
    rows = [_c("2026-09-05", "11", race=("anet", 1, 1)), _c("2026-09-05", "11", race=("anet", 2, 9))]
    assert "two cross country races" in W.careerProblems(7, rows).detail
    rows[1] = rows[1]._replace(twin=True)
    assert W.careerProblems(7, rows) is None


def test_a_normal_career_is_fine():
    rows = [_c("2023-10-01", "9"), _c("2024-10-01", "10"), _c("2025-10-01", "11"),
            _c("2026-10-01", "12")]
    assert W.careerProblems(8, rows) is None


# ------------------------------------------------------------------ #
# 5. meets
# ------------------------------------------------------------------ #

def _race(n, t=lambda i: 900 + i, dist=5000.0, g=lambda i: "M", place=lambda i: i + 1,
          ev=None, relay=0, field=0):
    return [{"time_seconds": t(i), "place": place(i), "gender": g(i), "distance": dist,
             "event_short": ev, "is_relay": relay, "is_field": field} for i in range(n)]


def _kinds(fs):
    return sorted(f.key.split(":", 1)[0] for f in fs)


def test_a_clean_race_is_clean():
    assert W.judgeRace("XC", ("anet", 1, 2, None), _race(10)) == []


def test_no_distance_identical_times_missing_finishers_mixed_sexes():
    key = ("anet", 1, 2, None)
    assert _kinds(W.judgeRace("XC", key, _race(5, dist=None))) == ["distance"]
    assert _kinds(W.judgeRace("XC", key, _race(5, dist=-1.0))) == ["distance"]
    assert _kinds(W.judgeRace("XC", key, _race(W.SAME_TIME_MIN_FIELD, t=lambda i: 1200.0))) == ["sametime"]
    # two can tie
    assert W.judgeRace("XC", key, _race(2, t=lambda i: 1200.0)) == []
    assert _kinds(W.judgeRace("XC", key, _race(5, place=lambda i: i + 40))) == ["places"]
    got = W.judgeRace("XC", key, _race(6, g=lambda i: "F" if i % 2 else "M"))
    assert _kinds(got) == ["sexes"] and "M 3, F 3" in got[0].title


def test_track_stored_minus_one_where_the_event_reads_a_distance():
    meters = lambda ev: 1600.0 if ev == "1600m" else None          # noqa: E731
    key = ("anet", 1, 2, 3)
    got = W.judgeRace("TF", key, _race(5, dist=-1.0, ev="1600m"), meters)
    assert _kinds(got) == ["distance"] and got[0].links[0][1] == "/race/tf/1/3/2"
    # a field event has no distance to miss
    assert W.judgeRace("TF", key, _race(5, dist=-1.0, ev="Shot Put", field=1), meters) == []


# ------------------------------------------------------------------ #
# 6. boards: churn against measured history (stub cursor)
# ------------------------------------------------------------------ #

def test_churn_and_the_unusual_rule():
    assert W.churn([1, 2, 3, 4], [1, 2, 3, 5]) == 0.25
    assert W.churn([], [1]) is None
    # fewer than MIN_HISTORY past values: recorded, not judged
    assert W.unusual(0.9, [0.1, 0.1], 0.01) == (None, False)
    z, bad = W.unusual(0.9, [0.10, 0.12, 0.08, 0.11, 0.09], 1 / 60)
    assert bad and z >= W.UNUSUAL_SIGMA
    assert W.unusual(0.12, [0.10, 0.12, 0.08, 0.11, 0.09], 1 / 60)[1] is False


class _Cur:
    """Answers the board queries from dicts; records what it was asked."""

    def __init__(self, tops, prev, hist):
        self.tops, self.prev, self.hist, self._rows, self.sql = tops, prev, hist, [], []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "to_regclass" in sql:
            self._rows = [("x",)]
        elif "FROM athlete_season" in sql:
            self._rows = [(p,) for p in self.tops.get((params["sport"], params["pool"]), [])]
        elif "SELECT person_ids" in sql:
            got = self.prev.get((params[0], params[1]))
            self._rows = [(got,)] if got else []
        elif "SELECT churn" in sql:
            self._rows = [(c,) for c in self.hist.get((params[0], params[1]), [])]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def test_a_board_that_churns_past_its_history_is_an_item():
    top = list(range(1, 61))
    tops = {("XC", "hs_m"): top, ("XC", "hs_f"): top}
    prev = {("XC", "hs_m"): list(range(100, 160)),                 # all 60 new
            ("XC", "hs_f"): list(range(1, 58)) + [500, 501, 502]}  # 3 new
    hist = {("XC", "hs_m"): [0.05, 0.03, 0.04, 0.05, 0.06],
            ("XC", "hs_f"): [0.05, 0.03, 0.04, 0.05, 0.06]}
    cur = _Cur(tops, prev, hist)
    out, store = W.runBoards(cur, "nightly", 9)
    assert [f.key for f in out] == ["churn:XC:hs_m"]
    assert ("board", "/rankings/xc/hs_m") in out[0].links
    st = {(s, p): c for s, p, _y, _t, c in store}
    assert st[("XC", "hs_m")] == 1.0 and abs(st[("XC", "hs_f")] - 0.05) < 1e-9
    # the board's own floor and index order
    q = next(s for s in cur.sql if "FROM athlete_season" in s)
    assert "mean_rating DESC NULLS LAST" in q and "n_races >= %(min_races)s" in q


# ------------------------------------------------------------------ #
# 7. pipeline: steps, counts, 10a
# ------------------------------------------------------------------ #

FULL = """  00_integrity ok (12s)
  08b_ladder skipped (fresh; XCP_LADDER=1 forces it)
  10_rankings_finish ok (2370s)
  10a_board_sanity FAILED after 41s
  17c_report ok (3s)
  18_vacuum ok (100s)
"""
NIGHTLY = """  scrape_anet ok (3600s)
  scrape_tfrrs: nothing new to scrape (4s)
  04a_link_tfrrs FAILED (exit 1) after 30s
  05_normalize_new NOT RUN: a people step failed (see above); yesterday's boards stay up.
"""


def test_both_summary_formats_parse():
    assert W.parseSteps(FULL) == {"00_integrity": "ok", "08b_ladder": "skipped",
                                  "10_rankings_finish": "ok", "10a_board_sanity": "failed",
                                  "17c_report": "ok", "18_vacuum": "ok"}
    got = W.parseSteps(NIGHTLY)
    assert got == {"scrape_anet": "ok", "scrape_tfrrs": "quiet", "04a_link_tfrrs": "failed",
                   "05_normalize_new": "not run"}
    # a quiet scrape after an ok one is not a step that went missing
    prev = W.parseSteps("  scrape_tfrrs ok (9s)\n  04a_link_tfrrs ok (3s)\n")
    assert W.pipelineFindings("r", W.parseSteps("  scrape_tfrrs: nothing new to scrape (4s)\n"
                                                "  04a_link_tfrrs ok (3s)\n"), prev) == []
    # the full run's step logs carry a clock stamp; the summary may too
    assert W.parseSteps("01:02:03   x_step ok (3s)") == {"x_step": "ok"}


def test_failed_not_run_and_missing_steps_but_not_the_ones_after_the_watchdog():
    prev = W.parseSteps("  a ok (1s)\n  b ok (1s)\n  17d_watchdog ok (5s)\n  18_vacuum ok (9s)\n")
    now = W.parseSteps("  a FAILED after 1s\n")
    got = {f.key for f in W.pipelineFindings("r1", now, prev, self_step="17d_watchdog")}
    assert got == {"step:a:failed", "missing:b"}
    # before the watchdog's first run the cut is after the last step that ran
    prev = W.parseSteps("  a ok (1s)\n  b ok (1s)\n  18_vacuum ok (9s)\n")
    now = W.parseSteps("  a ok (1s)\n  b ok (1s)\n")
    assert W.pipelineFindings("r1", now, prev, self_step="17d_watchdog") == []


def test_counts_against_their_own_history():
    hist = [{"results": 1000 + 10 * i, "new_XC": 500} for i in range(6, 0, -1)]   # newest first
    # a corpus table that shrank by a fifth
    got = W.countFindings({"results": 850, "new_XC": 510}, hist, "nightly")
    assert [f.key for f in got] == ["count:results"]
    # a night with no new rows against a steady history
    hist = [{"new_XC": n} for n in (500, 520, 480, 510, 490)]
    assert [f.key for f in W.countFindings({"new_XC": 0}, hist, "nightly")] == ["count:new_XC"]
    assert W.countFindings({"new_XC": 505}, hist, "nightly") == []


SANITY_HARD = """[sanity] club teams from the rows...
== XC: top 60 of 6 pools, 360 rows ==
  pool    ok
  anchor  ok
  pace    2 finding(s) HARD:
      hs_m       #1 ...
  club    ok
  margin  1 finding(s):
== the sport level held? (log-time gap per level against XCP_SPORT_LEVEL_POOLS) ==
  hs          1,000 athlete-years   gap read -0.0036   target -0.0092   HARD: off by more than 0.005
  ms          1,000 athlete-years   gap read -0.0091   target -0.0092   ok
== the course scale and the tilt held? (implied/applied per band, after the scale) ==
  XC   120+     10,000 voters   implied/applied 1.120   HARD: off by more than 0.06
  XC   130+     10,000 voters   implied/applied 1.130   HARD: off by more than 0.06

[sanity] 4 hard finding(s), 1 soft finding(s)
[sanity] FAILED
"""


def test_10a_cause_classes():
    cls, detail = W.classify10a(SANITY_HARD)
    assert cls == "rows: pace + level + tilt"
    assert "level 1" in detail and "pace 2" in detail and "tilt 2" in detail
    # the pipeline's step log stamps every line
    stamped = "\n".join("03:04:05 " + ln for ln in SANITY_HARD.splitlines())
    assert W.classify10a(stamped)[0] == "rows: pace + level + tilt"
    assert W.classify10a(SANITY_HARD, ["10_rankings_finish"])[0] == "stale boards"
    assert W.classify10a("Traceback (most recent call last):\n  psycopg2.errors.X: boom\n")[0] == "crash"
    assert W.classify10a("[sanity] club teams from the rows...\n")[0] == "crash"
    assert W.classify10a("[sanity] 0 hard finding(s), 0 soft finding(s)\n[sanity] ok\n")[0] == "ok"
    assert W.classify10a("[sanity] 0 hard finding(s), 3 soft finding(s)\n[sanity] FAILED\n")[0] == "strict"


def test_the_newest_failed_10a_is_reported_with_its_class(tmp_path):
    old = tmp_path / "20261001_232626"
    new = tmp_path / "20261006_120609"
    for d in (old, new):
        d.mkdir()
    (old / "summary.log").write_text("  10_rankings_finish FAILED after 9s\n  10a_board_sanity FAILED after 4s\n")
    (old / "10a_board_sanity.log").write_text(SANITY_HARD)
    (new / "summary.log").write_text("  10_rankings_finish ok (9s)\n  10a_board_sanity FAILED after 4s\n")
    (new / "10a_board_sanity.log").write_text(SANITY_HARD)
    night = tmp_path / "nightly_20261010_011700"
    night.mkdir()
    (night / "SUMMARY.txt").write_text("  13c0_person_redirects ok (3s)\n")
    got = W.runPipeline(str(night), [], str(tmp_path), "13h_watchdog")
    keys = [f.key for f in got]
    assert keys == ["10a:rows: pace + level + tilt"]
    assert "20261006_120609" in got[0].detail
    # a later full run whose 10a passed closes it
    newer = tmp_path / "20261011_000000"
    newer.mkdir()
    (newer / "summary.log").write_text("  10a_board_sanity ok (30s)\n")
    (newer / "10a_board_sanity.log").write_text("[sanity] 0 hard finding(s), 0 soft finding(s)\n[sanity] ok\n")
    assert W.runPipeline(str(night), [], str(tmp_path), "13h_watchdog") == []


def test_previous_run_is_the_same_kind(tmp_path):
    for n in ("20261001_000000", "nightly_20261008_011700", "nightly_20261009_011700",
              "20261009_120000", "nightly_20261010_011700"):
        (tmp_path / n).mkdir()
    assert os.path.basename(W.previousRun(str(tmp_path / "nightly_20261010_011700"))) \
        == "nightly_20261009_011700"
    assert os.path.basename(W.previousRun(str(tmp_path / "20261009_120000"))) == "20261001_000000"


# ------------------------------------------------------------------ #
# persistence and the report
# ------------------------------------------------------------------ #

def test_reconcile_new_still_open_resolved():
    open_items = {("jump", "XC:1"): {}, ("jump", "XC:2"): {}, ("board", "churn:XC:hs_m"): {}}
    tonight = {("jump", "XC:2"): W.Finding("jump", "XC:2"), ("meet", "x"): W.Finding("meet", "x")}
    new, still, resolved = W.reconcile(open_items, tonight, ran_checks={"jump", "meet"})
    assert new == [("meet", "x")] and still == [("jump", "XC:2")]
    # the board check did not run tonight: its item stays open, untouched
    assert resolved == [("jump", "XC:1")]


def test_the_report_leads_with_new_items_and_ages_the_old():
    now = datetime.datetime(2026, 10, 10, 3, tzinfo=datetime.timezone.utc)
    a = W.Finding("meet", "a", "high", "race with no distance", "d", (("race", "/race/xc/1/2"),))
    b = W.Finding("meet", "b", "info", "both sexes", "", (("race", "/race/xc/3/4"),))
    items = {("meet", "a"): a, ("meet", "b"): b}
    meta = {("meet", "b"): now - datetime.timedelta(days=4)}
    text = W.renderText("nightly_x", items, {("meet", "a")}, meta, [("jump", "z")], now,
                        origin="https://racecast.co", notes=["one note"])
    assert text.splitlines()[0] == "Data watchdog, nightly_x: 1 new, 1 still open, 1 resolved"
    body = text[text.index("Broken meets"):]
    assert body.index("NEW race with no distance") < body.index("both sexes  [open 4 days]")
    assert "https://racecast.co/race/xc/1/2" in text and "! one note" in text


def test_the_email_carries_only_new_items_in_the_alert_look():
    a = W.Finding("meet", "a", "high", "race with no distance", "detail <b>", (("race", "/race/xc/1/2"),))
    b = W.Finding("jump", "b", "high", "old jump", "", ())
    items = {("meet", "a"): a, ("jump", "b"): b}
    subject, text, html = W.renderEmail("nightly_x", items, [("meet", "a")],
                                        datetime.datetime.now(), "https://racecast.co")
    assert subject == "[racecast] watchdog nightly_x: 1 new item"
    assert "race with no distance" in text and "old jump" not in text
    assert "https://racecast.co/race/xc/1/2" in html and "detail &lt;b&gt;" in html
    assert "RACECAST" in html and "/account/status/watchdog" in text


# ------------------------------------------------------------------ #
# report only: the module writes its own tables and nothing else
# ------------------------------------------------------------------ #

def test_the_watchdog_writes_only_its_own_tables():
    src = open(os.path.join(ROOT, "racecast", "watchdog.py"), encoding="utf-8").read()
    for verb, rx in (("INSERT", r"INSERT\s+INTO\s+(\w+)"), ("UPDATE", r"\bUPDATE\s+(\w+)\s+SET"),
                     ("DELETE", r"DELETE\s+FROM\s+(\w+)"), ("CREATE TABLE", r"CREATE TABLE IF NOT EXISTS (\w+)")):
        for table in re.findall(rx, src):
            assert table.startswith("watchdog_"), (verb, table)
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER)\s+TABLE", src)
    # its only other DDL is the BRIN scope indexes
    assert set(re.findall(r"CREATE INDEX CONCURRENTLY IF NOT EXISTS \{n\} ON \{t\} USING brin", src))


def test_the_pipelines_run_it_off_the_chain_and_after_the_boards():
    nightly = open(os.path.join(ROOT, "deploy", "nightly_update.sh"), encoding="utf-8").read()
    full = open(os.path.join(ROOT, "deploy", "run_pipeline.sh"), encoding="utf-8").read()
    assert nightly.index("step 13h_watchdog") > nightly.index("step 13c0_person_redirects")
    assert nightly.index("step 13h_watchdog") < nightly.index("# ---- summary")
    assert full.index("step 17d_watchdog") > full.index("step 10_rankings_finish")
    assert full.index("step 17d_watchdog") < full.index("step 18_vacuum")
    for sh in (nightly, full):
        line = sh[sh.index("racecast/watchdog.py"):].split("\n")[0]
        assert "&&" not in line


def test_notify_owner_carries_the_watchdog_summary(tmp_path):
    import notify_owner
    (tmp_path / "SUMMARY.txt").write_text("  04a_link_tfrrs FAILED (exit 1) after 3s\n")
    (tmp_path / "WATCHDOG.txt").write_text("Data watchdog, x: 2 new, 0 still open, 0 resolved\n")
    _subject, text = notify_owner.message(str(tmp_path), ["04a_link_tfrrs"])
    assert "Data watchdog, x: 2 new" in text
    assert "== SUMMARY.txt ==" in text and "04a_link_tfrrs FAILED" in text


# ------------------------------------------------------------------ #
# the whole pass over a stub database: plumbing, persistence, scope
# ------------------------------------------------------------------ #

class _Db:
    """A stub Postgres connection: answers each query the pass sends by
    its shape, and records the writes."""

    def __init__(self, open_items=(), indexed=True):
        self.inserted, self.resolved, self.runs, self.sql = [], [], [], []
        self.open_items, self.indexed, self.autocommit = list(open_items), indexed, False

    def cursor(self):
        return _DbCur(self)

    def commit(self):
        pass

    def rollback(self):
        pass


_SCOPE = ("result_id", "person_id", "source", "meet_id", "div_id", "event_id", "date",
          "time_seconds", "normalized_time", "speed_rating", "rating_pool", "school", "place")
_CAND = ("result_id", "source", "meet_id", "div_id", "event_id", "time_seconds",
         "distance", "rating_pool", "gender")
_RACE = ("result_id", "source", "meet_id", "div_id", "event_id", "person_id", "time_seconds",
         "place", "gender", "distance", "event_short", "is_relay", "is_field")
_COLS = {"results": ["source", "rating_pool", "school", "place", "scraped_at"],
         "results_tf": ["source", "rating_pool", "school", "place", "scraped_at",
                        "event_id", "event_short", "is_relay", "is_field"]}


class _DbCur:
    def __init__(self, db):
        self.db, self.rows, self.description = db, [], None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _set(self, rows, names=None):
        self.rows = list(rows)
        self.description = [(n,) for n in names] if names else []

    def execute(self, sql, params=None):
        db, s = self.db, " ".join(sql.split())
        db.sql.append(s)
        xc = "FROM results r" in s
        if "to_regclass" in s and "reltuples" not in s:
            self._set([("x",)])
        elif "information_schema.columns" in s:
            self._set([(c, "timestamp with time zone" if c == "scraped_at" else "text")
                       for c in _COLS.get(params[0], [])])
        elif "FROM pg_index" in s:
            self._set([(1,)] if db.indexed else [])
        elif s.startswith("SELECT max(started_at) FROM watchdog_run"):
            self._set([(None,)])
        elif s.startswith("INSERT INTO watchdog_run"):
            self._set([(7,)])
        elif "FROM watchdog_finding WHERE still_open" in s:
            self._set(db.open_items)
        elif "WHERE r.scraped_at >=" in s:
            self._set([(1, 10, "anet", 1, 2, None, "2026-10-09", 600.0, 400.0, 150.0, "hs_m|XC",
                        "Mead", 1),
                       (2, 11, "anet", 1, 2, None, "2026-10-09", 1000.0, 1000.0, 100.0, "hs_m|XC",
                        "Mead", 2)] if xc else [], _SCOPE)
        elif "rating_outlier_calib" in s or "FROM person_link_log" in s:
            self._set([])
        elif "CROSS JOIN LATERAL (SELECT" in s and "AND r.result_id = ANY" in s:
            self._set([(1, "anet", 1, 2, None, 600.0, 5000.0, "hs_m|XC", "M")] if xc else [], _CAND)
        elif "WHERE r.meet_id = ANY" in s:
            self._set([(1, "anet", 1, 2, None, 10, 600.0, 1, "M", 5000.0, None, 0, 0),
                       (2, "anet", 1, 2, None, 11, 1000.0, 2, "M", 5000.0, None, 0, 0)]
                      if xc else [], _RACE)
        elif "FROM athlete_season WHERE school" in s:
            self._set([(10, "Mead", "XC", 2026), (11, "Mead", "XC", 2026)]
                      if params[1] == "XC" else [])
        elif "FROM athletes WHERE athlete_id" in s:
            self._set([(p, "M", "Sam Lee") for p in params[0]])
        elif "WHERE r.person_id = ANY" in s:
            self._set([(p, 100 + p, f"2026-09-{p - 5:02d}", 100.0, "11", "Mead", 1, p, None,
                        None, "anet", 1000.0) for p in params[0] if p in (10, 11)] if xc else [])
        elif "FROM athlete_season s WHERE" in s:
            self._set([(p,) for p in range(1, 61)])
        elif "reltuples" in s:
            self._set([(1000,)])
        elif s.startswith("INSERT INTO watchdog_finding"):
            db.inserted.append((params[0], params[1]))
            self._set([])
        elif s.startswith("UPDATE watchdog_finding SET still_open = false"):
            db.resolved.append((params[1], params[2]))
            self._set([])
        elif s.startswith("UPDATE watchdog_run SET finished_at"):
            db.runs.append(params)
            self._set([])
        else:
            self._set([])

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


def _args(**kw):
    import argparse
    a = argparse.Namespace(log_dir=None, log_root=None, kind="nightly", failed="", since=None,
                           send=False, ensure_indexes=False, dry_run=False, classify_10a=None,
                           step_name="13h_watchdog")
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_the_whole_pass_finds_reports_and_stores(tmp_path):
    night = tmp_path / "nightly_20261010_011700"
    night.mkdir()
    (night / "SUMMARY.txt").write_text("  13c0_person_redirects ok (3s)\n")
    db = _Db(open_items=[("meet", "distance:XC:anet:9:9:None",
                          datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc), 3,
                          {"meet": ["XC", "anet", 9]})])
    logs = []
    report = W.run(db, _args(log_dir=str(night), log_root=str(tmp_path)), log=logs.append)
    keys = {k for _c, k in db.inserted}
    # the record-beating race, and the two Sam Lees at Mead who never met
    assert "record:XC:anet:1:2:None" in keys, report
    assert "people:10-11" in keys, report
    assert "faster than the world record" in report
    # the open meet item was judged again (its meet re-read) and is gone
    assert ("meet", "distance:XC:anet:9:9:None") in db.resolved
    assert db.runs and db.runs[0][0] == len(db.inserted)            # every item new
    assert (night / "WATCHDOG.txt").read_text() == report
    # the scope came through the scraped_at index and the primary key
    assert any("WHERE r.scraped_at >= %(since)s OR r.result_id = ANY" in s for s in db.sql)


def test_without_the_scope_index_the_row_checks_are_skipped_not_scanned():
    db = _Db(indexed=False)
    report = W.run(db, _args())
    assert "no index on results.scraped_at" in report
    assert not any("WHERE r.scraped_at >=" in s for s in db.sql)


def test_the_admin_page_lists_items_new_first_and_hides_from_readers():
    import contextlib
    import app as A
    import site_status as S
    now = datetime.datetime.now(datetime.timezone.utc)
    run_at = now - datetime.timedelta(hours=2)
    wd = {"titles": W.CHECK_TITLES, "order": [c for c, _t in W.CHECKS],
          "runs": [{"run_id": 7, "started_at": run_at, "finished_at": now, "kind": "nightly",
                    "log_dir": "x", "n_new": 1, "n_open": 2, "n_resolved": 1}],
          "items": [
              {"check_name": "meet", "key": "a", "severity": "high", "title": "race with no distance",
               "detail": "d <b>", "links": [["race", "/race/xc/1/2"]], "first_seen": run_at,
               "last_seen": now, "still_open": True, "resolved": None, "nights": 1},
              {"check_name": "jump", "key": "b", "severity": "high", "title": "old jump",
               "detail": "", "links": [["athlete", "/athlete/5"]],
               "first_seen": now - datetime.timedelta(days=3), "last_seen": now,
               "still_open": True, "resolved": None, "nights": 3},
              {"check_name": "jump", "key": "c", "severity": "high", "title": "fixed jump",
               "detail": "", "links": [], "first_seen": now - datetime.timedelta(days=5),
               "last_seen": now, "still_open": False, "resolved": now - datetime.timedelta(days=1),
               "nights": 4}]}
    saved = (A._accounts.currentSession, S._block, A.getConn)
    S._block = lambda conn, fn, *a: wd
    A.getConn = lambda: contextlib.nullcontext(None)
    try:
        client = A.app.test_client()
        os.environ["XCP_ADMIN_EMAILS"] = "owner@example.com"
        A._accounts.currentSession = lambda: {"csrf": "t", "account": {"email": "someone@example.com"}}
        assert client.get("/account/status/watchdog").status_code == 404
        A._accounts.currentSession = lambda: {"csrf": "t", "account": {"email": "owner@example.com"}}
        r = client.get("/account/status/watchdog")
        assert r.status_code == 200, r.data[:400]
        html = r.get_data(as_text=True)
        assert "NEW</span> <b>race with no distance" in html
        assert "d &lt;b&gt;" in html and 'href="/race/xc/1/2"' in html
        assert "old jump" in html and "3d" in html and "(3 runs)" in html
        assert "resolved</span> 1d ago" in html
        assert html.index("Broken meets") > html.index("Sudden rating jumps")   # check order
    finally:
        A._accounts.currentSession, S._block, A.getConn = saved


def test_the_status_page_headline_counts_new_watchdog_items():
    import contextlib
    import app as A
    import site_status as S
    import test_site_status as TS
    st = TS._fake_status()
    st["watchdog"] = {"open": 4, "new": 2, "at": datetime.datetime.now()}
    saved = (A._accounts.currentSession, S.gather, A.getConn)
    S.gather = lambda conn: st
    A.getConn = lambda: contextlib.nullcontext(None)
    try:
        os.environ["XCP_ADMIN_EMAILS"] = "owner@example.com"
        A._accounts.currentSession = lambda: {"csrf": "t", "account": {"email": "owner@example.com"}}
        html = A.app.test_client().get("/account/status").get_data(as_text=True)
        assert "2 new watchdog items" in html
        assert 'href="/account/status/watchdog"' in html and "4 open" in html
    finally:
        A._accounts.currentSession, S.gather, A.getConn = saved


def test_dry_run_stores_nothing():
    db = _Db()
    W.run(db, _args(dry_run=True))
    assert db.inserted == [] and db.runs == []
    assert any(s.startswith("DELETE FROM watchdog_run") for s in db.sql)
