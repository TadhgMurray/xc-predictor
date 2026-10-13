"""The weekly Instagram posts (racecast/social.py, step 13g_social; owner,
2026-10-13). The pure rules offline; the board query against a real
Postgres when XCP_TEST_DSN is set.

    python -m pytest -q tests/test_social.py
    XCP_TEST_DSN='host=/var/tmp port=5433 user=postgres dbname=postgres' \
        python -m pytest -q tests/test_social.py
"""
import datetime
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, _d))

import social as S                                      # noqa: E402

D = datetime.date
ASSETS = {"font_bs": "f.woff2", "font_an": "g.woff2", "logo": "logo.svg", "photo": None}


def _rows(n=10):
    out = []
    for i in range(n):
        out.append({"person_id": 100 + i, "rank": i + 1, "name": f"Runner{i} Last{i}",
                    "school": f"School {i}", "cls": "SR", "rating": 140.0 - i * 0.7,
                    "five_k": f"14:{40 + i:02d}", "five_k_dist": "5K",
                    "prev": i + 1, "move": 0})
    return out


def _post(kind, **kw):
    p = {"kind": kind, "sport": "XC", "pool": "hs_m", "scope": "MI",
         "scope_name": "Michigan", "week": "2026-10-09"}
    p.update(kw)
    return p


# ---- small rules ------------------------------------------------------

def test_class_word():
    assert S.classWord("12", "hs_m") == "SR"
    assert S.classWord("9", "hs_f") == "FR"
    assert S.classWord("Jr", "hs_m") == "JR"
    assert S.classWord("JR-3", "college_m") == "JR"
    assert S.classWord(None, "hs_m") == ""
    assert S.classWord("2027", "hs_m") == ""          # a class year is not printed raw


def test_week_label_names_the_week_not_a_meet():
    # owner: "after Portage doesn't mean much, should say the week"
    assert S.weekLabel(D(2026, 10, 9)) == "WEEK OF OCT 9"
    assert S.weekLabel("2026-09-25") == "WEEK OF SEP 25"


def test_move():
    assert S.moveOf(4, 13) == 9
    assert S.moveOf(5, 3) == -2
    assert S.moveOf(6, 6) == 0
    assert S.moveOf(8, None) is None


def test_streak_counts_consecutive_weeks_only():
    w = [D(2026, 10, 9), D(2026, 10, 2), D(2026, 9, 25), D(2026, 9, 18)]
    assert S.streakAtTop(list(zip(w, [1, 1, 1, 2])), 1) == 3
    assert S.streakAtTop(list(zip(w, [2, 1, 1, 1])), 1) == 0
    # a missing week ends it
    gap = [D(2026, 10, 9), D(2026, 10, 2), D(2026, 9, 18)]
    assert S.streakAtTop(list(zip(gap, [1, 1, 1])), 1) == 2


def test_photo_prefers_the_state_and_is_stable():
    files = ["MI_pack.jpg", "CA_start.jpg", "generic.webp", "notes.txt"]
    assert S.pickPhoto(files, "k1", "MI") == "MI_pack.jpg"
    # another state's photo is never used for this one when a generic exists
    for k in ("a", "b", "c", "d"):
        assert S.pickPhoto(files, k, "TX") == "generic.webp"
    assert S.pickPhoto(files, "x", None) == S.pickPhoto(files, "x", None)
    assert S.pickPhoto(["notes.txt"], "x", "MI") is None


# ---- captions --------------------------------------------------------

def test_rankings_caption_leads_with_the_biggest_climb():
    rows = _rows()
    rows[3].update(prev=13, move=9)
    c = S.caption(_post("rankings", rows=rows, streak=6))
    first = c.splitlines()[0]
    assert first == "Runner3 Last3 jumps 9 spots to #4."
    assert "holds #1 for a sixth straight week" in c
    assert "within a point" in c                       # 140.0 vs 139.3
    assert c.splitlines()[-1] == "#michiganxc #hsxc #crosscountry"


def test_rankings_caption_new_entry_and_new_leader():
    rows = _rows()
    rows[7].update(prev=None, move=None)
    rows[0].update(prev=2, move=1)
    rows[1].update(prev=1, move=-1)
    c = S.caption(_post("rankings", rows=rows, streak=1))
    assert c.splitlines()[0] == "Runner7 Last7 enters the top 10 at #8."
    assert "New #1: Runner0 Last0" in c


def test_captions_never_tag_and_stay_short():
    rows = _rows()
    posts = [_post("rankings", rows=rows, streak=2),
             _post("top1", row=rows[0], rows=[rows[0]], streak=3, lead=0.7, second=rows[1]["name"]),
             _post("risers", rows=[dict(rows[3], change=9, now=4, prev=13)]),
             _post("breakout", row=dict(rows[3], level=124.3, rating=138.2, vs_level=11.2,
                                        meet_name="Portage Invitational", time="15:02.4"),
                   sigma=3.1),
             _post("national", scope="US", scope_name="the country",
                   rows=[dict(r, state="MI") for r in _rows(25)])]
    for p in posts:
        c = S.caption(p)
        assert "@" not in c, p["kind"]
        assert len(c.splitlines()) <= 7, p["kind"]
        assert len(c.splitlines()[0]) <= 125, p["kind"]


def test_breakout_caption_reads_its_row():
    b = dict(_rows()[3], level=124.3, rating=138.2, vs_level=11.2,
             meet_name="Portage Invitational", time="15:02.4", five_k="14:58")
    c = S.caption(_post("breakout", row=b, sigma=3.1))
    assert c.startswith("Runner3 Last3 ran 11% above their level at Portage Invitational.")
    assert "124.3 coming in, 138.2 in this race (15:02.4, 14:58 for a flat 5K)." in c


def test_national_caption_counts_states():
    rows = [dict(r, state=("CA" if i < 4 else "MI" if i < 6 else f"S{i}")) for i, r in enumerate(_rows(25))]
    c = S.caption(_post("national", scope="US", scope_name="the country", rows=rows))
    assert "National top 25, Week of Oct 9." in c
    assert "CA has 4" in c
    assert c.splitlines()[-1] == "#hsxc #crosscountry"


# ---- the image -------------------------------------------------------

def _html(kind, **kw):
    return S.postHtml(_post(kind, **kw), ASSETS)


def test_every_kind_renders_its_numbers():
    rows = _rows()
    rows[3].update(prev=13, move=9)
    h = _html("rankings", rows=rows, streak=2, five_k_dist="5K")
    for r in rows:
        assert r["name"] in h and f'{r["rating"]:.1f}' in h and r["five_k"] in h
    assert "+9" in h and "WEEK OF OCT 9" in h
    h = _html("top1", row=rows[0], rows=[rows[0]], streak=4, lead=0.7, second="x")
    assert "140.0" in h and "+0.7" in h and "WEEKS AT #1" in h
    h = _html("risers", rows=[dict(rows[3], change=9, now=4, prev=13)])
    assert "#13" in h and "#4" in h and "+9" in h
    b = dict(rows[3], level=124.3, rating=138.2, vs_level=11.2, meet_name="Portage", time="15:02.4")
    h = _html("breakout", row=b)
    assert "124.3" in h and "138.2" in h and "+11% OVER LEVEL" in h
    h = _html("national", scope="US", scope_name="the country", rows=[dict(r, state="MI") for r in _rows(25)])
    assert "NATIONAL<br>TOP 25" in h and h.count("class=r>") == 25


def test_names_are_escaped():
    rows = _rows()
    rows[0]["name"] = "<script>x</script> O'Neil"
    h = _html("rankings", rows=rows, streak=1)
    assert "<script>x" not in h and "&lt;script&gt;" in h


def test_no_italics_and_no_grain():
    # owner: "I don't really like the italics on the athlete names" and
    # "not a big fan of the font or effects"
    rows = _rows()
    for kind, kw in (("rankings", {"rows": rows}), ("national", {"rows": [dict(r, state="MI") for r in _rows(25)], "scope": "US"})):
        h = _html(kind, **kw)
        assert "font-style:italic" not in h
        assert "feTurbulence" not in h and "mask-image" not in h


def test_improvement_is_never_red():
    # owner: "134.3 -> 138.2 in red doesn't make much sense": each kind's
    # accent is not a direction colour; red is only for a fall
    reds = {"#ff6b5e", "#c42b1c", "#c8102e", "#ff0000"}
    greens = {"#4fd27f", "#118a3e"}
    for kind, t in S.THEMES.items():
        assert t["acc"].lower() not in reds | greens, kind


def test_without_a_photo_the_frame_says_the_state():
    h = _html("rankings", rows=_rows())
    assert "class=none>MI<" in h and "background-image" not in h
    h = S.postHtml(_post("rankings", rows=_rows()), dict(ASSETS, photo="/p/MI_x.jpg"))
    assert "background-image:url('/p/MI_x.jpg')" in h


def test_tags_list_the_schools_once():
    rows = _rows(3)
    rows[2]["school"] = rows[0]["school"]
    assert S.tagsFor(_post("rankings", rows=rows)) == ["School 0", "School 1"]


# ---- the pipeline wiring ---------------------------------------------

def test_step_in_pipeline_and_report_only():
    sh = open(os.path.join(ROOT, "deploy", "run_pipeline.sh"), encoding="utf-8").read()
    assert re.search(r'step 13g_social\s+"\$PY" -u racecast/build_social.py', sh)
    assert sh.index("13f_breakouts") < sh.index("13g_social")
    src = open(os.path.join(ROOT, "racecast", "build_social.py"), encoding="utf-8").read()
    assert 'print("[social] failed -- report step, the run goes on")' in src


def test_admin_routes_are_gated():
    src = open(os.path.join(ROOT, "racecast", "social.py"), encoding="utf-8").read() + "\n"
    routes = re.findall(r'@bp.route\("([^"]+)"\)\ndef \w+\([^)]*\):\n((?:    .*\n)+?)\n', src)
    assert len(routes) == 4
    for path, body in routes:
        assert path.startswith("/account/admin/social"), path
        assert "requireAdmin()" in body.splitlines()[1], path


# ---- the board query, against Postgres ---------------------------------

DSN = os.environ.get("XCP_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="set XCP_TEST_DSN to a throwaway Postgres")
def test_board_query(monkeypatch):
    import psycopg2
    import psycopg2.extras
    import build_rank_snapshot as SNAP
    import team_tracker
    monkeypatch.setattr(team_tracker, "_names", lambda cur, pids: {p: f"P{p}" for p in pids})
    monkeypatch.setattr(S, "_fiveK", lambda rating, pool, sport: ("15:00", "5K"))
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(SNAP.DDL.replace("CREATE TABLE IF NOT EXISTS season_rank_weekly",
                                     "CREATE TEMP TABLE season_rank_weekly")
                    .split("CREATE INDEX")[0])
        cur.execute("""CREATE TEMP TABLE athlete_season (person_id bigint, sport text, pool text,
                       year int, grade text)""")
        now, prev = D(2026, 10, 9), D(2026, 10, 2)
        rows = []
        for pid, r_now, r_prev, st in ((1, 1, 1, "MI"), (2, 2, 3, "MI"), (3, 3, None, "MI"),
                                       (4, 4, 2, "MI"), (5, 1, 1, "OH")):
            rows.append((now, pid, st, r_now, 140.0 - r_now))
            if r_prev:
                rows.append((prev, pid, st, r_prev, 139.0 - r_prev))
        # someone ranked last week in another pool must not join
        rows.append((prev, 3, "MI", 9, 120.0))
        for wk, pid, st, rk, rt in rows:
            pool = "hs_f" if (wk == prev and pid == 3) else "hs_m"
            cur.execute("""INSERT INTO season_rank_weekly (week, person_id, pool, sport, year, state,
                           school, nation, state_rank, mean_rating)
                           VALUES (%s,%s,%s,'XC',2026,%s,'S',%s,%s,%s)""",
                        (wk, pid, pool, st, pid, rk, rt))
        cur.execute("INSERT INTO athlete_season VALUES (1,'XC','hs_m',2026,'12')")
        got = S._board(cur, "XC", "hs_m", 2026, [now, prev], "state_rank", 10, "MI")
        assert [r["person_id"] for r in got] == [1, 2, 3, 4]
        assert [r["move"] for r in got] == [0, 1, None, -2]
        assert got[0]["cls"] == "SR" and got[0]["name"] == "P1" and got[0]["five_k"] == "15:00"
        nat = S._board(cur, "XC", "hs_m", 2026, [now, prev], "nation", 25)
        assert [r["person_id"] for r in nat] == [1, 2, 3, 4, 5]
        cur.execute("INSERT INTO season_rank_weekly (week, person_id, pool, sport, year, state, state_rank)"
                    " VALUES (%s, 1, 'hs_m', 'XC', 2026, 'MI', 1), (%s, 1, 'hs_m', 'XC', 2026, 'MI', 1)"
                    " ON CONFLICT DO NOTHING", (D(2026, 9, 25), D(2026, 9, 11)))
        lead = S._leaders(cur, "XC", "hs_m", 2026, "MI")
        assert S.streakAtTop(lead, 1) == 3                 # Oct 9, Oct 2, Sep 25; Sep 11 is past a gap
    finally:
        conn.rollback()
        conn.close()
