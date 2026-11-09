"""The post composer (owner, 2026-11-09: "choose a post type, then a body
type, then a picture if needed"): racecast/social_compose.py."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused")

import social_compose as C  # noqa: E402

A = {"font": "f.woff2", "logo": "l.svg", "photo": None, "crest": lambda s, st, p: None}


def _rows(n, start=150.0):
    return [{"rank": i + 1, "name": f"Runner {i + 1}", "school": f"School {i + 1}", "state": "MI",
             "rating": start - i, "hs": start - i, "five_k": f"13:{30 + i:02d}", "cls": "SR",
             "move": [0, 2, -1, None][i % 4], "prev": i + 2, "now": i + 1, "change": 3}
            for i in range(n)]


def _post(kind, n=10, **kw):
    p = {"kind": kind, "sport": "XC", "pool": "hs_m", "scope": "MI", "scope_name": "Michigan",
         "week": "2026-11-07", "rows": _rows(n)}
    p.update(kw)
    return p


def test_every_body_of_every_type_draws():
    posts = {"rankings": _post("rankings"), "national": _post("national", 25, scope="US", scope_name="the country"),
             "alltime": _post("alltime"), "teams": _post("teams"),
             "top1": _post("top1", 1, row=_rows(1)[0], lead=2.0, streak=3),
             "risers": _post("risers", 3), "breakout": _post("breakout", 1, row=dict(_rows(1)[0], level=140.0))}
    for kind, p in posts.items():
        for body in C.bodiesFor(kind):
            for photo in C.PHOTOS:
                for fmt in C.FORMATS:
                    pages = C.pages(p, {"body": body, "photo": photo, "fmt": fmt}, A)
                    assert pages and all("<body>" in h for h in pages), (kind, body, photo, fmt)


def test_titles_are_capitals_and_fit():
    h = C.pages(_post("rankings"), {"body": "table"}, A)[0]
    assert "MICHIGAN’S TOP 10" in h
    long_ = C.pages(_post("teams", scope_name="North Carolina"), {"body": "table"}, A)[0]
    assert "NORTH CAROLINA’S TOP 10 TEAMS" in long_ and "font-size:72px" not in long_.split("<h1")[1][:60]


def test_the_national_list_is_a_carousel_and_its_later_pages_are_lists():
    p = _post("national", 25, scope="US", scope_name="the country")
    pages = C.pages(p, {"body": "leader"}, A)
    assert len(pages) == 3
    assert "No. 1 in" in pages[0] and "No. 1 in" not in pages[1]
    assert "NATIONAL TOP 25 · 11–18" in pages[1] and "Runner 25" in pages[2]


def test_states_get_different_bodies_and_the_pick_turns_over_by_week():
    states = ["MI", "TX", "CA", "NY", "PA", "IL", "OH", "GA", "VA", "WA"]
    bodies = {C.defaultSpec(_post("rankings", scope=s))["body"] for s in states}
    assert len(bodies) >= 3
    weeks = {C.defaultSpec(_post("rankings", week=w))["body"] for w in
             ("2026-10-03", "2026-10-10", "2026-10-17", "2026-10-24", "2026-10-31")}
    assert len(weeks) >= 2
    assert C.defaultSpec(_post("top1", 1, row=_rows(1)[0]))["body"] == "card"


def test_a_spec_is_cleaned_to_what_the_post_can_take():
    p = _post("top1", 1, row=_rows(1)[0])
    s = C.cleanSpec(p, {"body": "bars", "photo": "sideways", "fmt": "poster"})
    assert s == {"body": "card", "photo": "none", "file": None, "fmt": "feed",
                 "color": C.SITE_BLUE, "theme": "white", "title": "ink"}


def test_a_side_photo_drops_the_5k_column_and_no_photo_file_draws_an_empty_box():
    h = C.pages(_post("rankings"), {"body": "table", "photo": "right"}, A)[0]
    assert "5K equiv" not in h and "photo empty" in h
    h = C.pages(_post("rankings"), {"body": "table"}, A)[0]
    assert "5K equiv" in h


def test_ratings_are_drawn_hs_equivalent():
    p = _post("rankings")
    p["rows"][0]["hs"] = 133.3
    p["rows"][0]["rating"] = 99.0
    h = C.pages(p, {"body": "table"}, A)[0]
    assert "133.3" in h and ">99.0<" not in h


def test_colours_are_a_theme_an_accent_and_a_title():
    p = _post("rankings")
    s = C.cleanSpec(p, {"color": "#7A1F2B", "theme": "dark", "title": "accent"})
    assert (s["color"], s["theme"], s["title"]) == ("#7a1f2b", "dark", "accent")
    h = C.pages(p, s, A)[0]
    assert "--acc:#7a1f2b" in h and "--ttl:#7a1f2b" in h and "--bg:#111418" in h
    bad = C.cleanSpec(p, {"color": "red; }", "theme": "neon", "title": "x"})
    assert (bad["color"], bad["theme"], bad["title"]) == (C.SITE_BLUE, "white", "ink")
    # the site's blue lifted on dark; bars follow a chosen accent
    assert "--acc:#8db8ee" in C.pages(p, {"theme": "dark"}, A)[0]
    assert "#7a1f2b" in C.pages(p, {"body": "bars", "color": "#7a1f2b"}, A)[0].split("class=fill")[1][:80]


def test_picks_team_odds_and_the_recap_draw():
    picks = _post("picks", 8, division_label="Division 1", draws=10000)
    for r, p in zip(picks["rows"], (0.41, 0.22, 0.14, 0.09, 0.07, 0.04, 0.02, 0.004)):
        r["p_win"] = p
    h = C.pages(picks, {"body": "odds"}, A)[0]
    assert "WHO WINS STATE?" in h and "41%" in h and "&lt;1%" in h and "10,000 simulated state meets" in h
    tp = _post("teampicks", 6, division_label="Division 1")
    for i, r in enumerate(tp["rows"]):
        r.update(name=None, p_win=0.4 / (i + 1), score=100 + 20 * i)
    h = C.pages(tp, {"body": "odds"}, A)[0]
    assert "THE TEAM TITLE RACE" in h and "average score 100" in h
    teams = _post("teams")
    for r in teams["rows"]:
        r.update(name=None, points=200, top5=130.0, p_win=0.2)
    assert ">WIN<" in C.pages(teams, {"body": "table"}, A)[0].upper()
    rc = _post("recap", 8, meet_name="MHSAA LP Finals", race_label="Boys D1", course="MIS", distance=5000,
               teams=[{"rank": i + 1, "school": f"T{i}", "points": 100 + i} for i in range(5)])
    for r in rc["rows"]:
        r["time"] = "14:45.9"
    for body in C.bodiesFor("recap"):
        h = C.pages(rc, {"body": body}, A)[0]
        assert "MHSAA LP FINALS" in h and "14:45.9" in h
    assert "Points" in C.pages(rc, {"body": "recap"}, A)[0]


def _preview(kind="preview", **kw):
    p = _post(kind, 8, scope="US~anet501~7", scope_name="NCAA DI XC Championship", state=None,
              pool="college_m", meet_name="NCAA Division I Cross Country Championships 2025",
              meet_short="NCAA DI XC Championship", race_label="Men's 10K", meet_date="2026-11-21",
              distance=10000, teams=[{"rank": i + 1, "school": f"T{i}", "score": 80 + 30 * i,
                                      "p_win": 0.5 / (i + 1)} for i in range(5)])
    for i, r in enumerate(p["rows"]):
        r.update(time=f"28:{10 + i:02d}.0", p_win=0.3 / (i + 1))
        if kind == "teampreview":
            r.update(name=None, score=80 + 30 * i)
    p.update(kw)
    return p


def test_a_predicted_race_draws_every_body_with_the_meet_in_the_title():
    """Any predicted race (owner, 2026-11-09: "What about other predicted
    races like NCAAs?"): the picks, their times and win chances, the teams."""
    for kind in ("preview", "teampreview"):
        p = _preview(kind)
        for body in C.bodiesFor(kind):
            for photo in C.PHOTOS:
                for fmt in C.FORMATS:
                    pages = C.pages(p, {"body": body, "photo": photo, "fmt": fmt}, A)
                    assert pages and "NCAA DI XC CHAMPIONSHIP" in pages[0], (kind, body, photo, fmt)
    h = C.pages(_preview(), {"body": "both"}, A)[0]
    assert "WHO WINS NCAA DI XC CHAMPIONSHIP?" in h and "28:10.0" in h and "30%" in h
    assert "Men&#x27;s 10K · Nov 21 · <b>" in h and ">Score<" in h
    assert "NCAA DI XC CHAMPIONSHIP: THE TEAM RACE" not in h
    h = C.pages(_preview("teampreview"), {"body": "odds"}, A)[0]
    assert "predicted score 80" in h
    # a national race has no state for its crests; a state final keeps its own
    assert C._st(_preview()) is None
    assert C._st(_preview(state="MI", scope="MI~anet9~1")) == "MI"
