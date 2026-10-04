# Project: xc-predictor / tests
# File:    test_champ_course.py
# Purpose: a championship that runs its own course at a shared venue gets its
#          own course key, by meet name (engine/champ_course.py). Foot Locker
#          Nationals at Morley Field read +4.7% (owner, 2026-09-28) because its
#          one day a year shared the park's cell with the local San Diego meets
#          on other loops. The rule, its SQL twin, the pack query that uses it,
#          and the key's shape through every parser that reads course keys.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_champ_course.py
import io
import contextlib
import os
import re
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import champ_course as cc                                      # noqa: E402

CASES = {
    # the national final, every sponsor and spelling
    "Foot Locker Cross Country Championships": "champ:footlocker-final",
    "Foot Locker Nationals": "champ:footlocker-final",
    "FootLocker XC Championships Finals": "champ:footlocker-final",
    "Champs Sports Cross Country Championships": "champ:footlocker-final",
    "Eastbay Cross Country Championships": "champ:footlocker-final",
    # the regionals, each its own course
    "Foot Locker West Regional": "champ:footlocker-west",
    "Foot Locker Midwest Regional Championships": "champ:footlocker-midwest",
    "Foot Locker Northeast Regional": "champ:footlocker-northeast",
    "Foot Locker North East Regional - Seeded": "champ:footlocker-northeast",
    "Champs Sports South Regional": "champ:footlocker-south",
    "Eastbay XC Championships - West Region": "champ:footlocker-west",
    # everything else at the park, and look-alikes: the venue's key as before
    "Mission Bay Invitational": None,
    "San Diego Section Championships": None,
    "Morley Field Classic": None,
    "East Bay Athletic League Championships": None,       # a league, not Eastbay
    "Foot Locker Regional": None,                          # which one? not the final
    "Foot Locker Southwest Regional": None,                # no such region
    "Nike Cross Nationals": None,
    "Mt. SAC Invitational": None,
    "": None,
    None: None,
}


def test_the_name_rule_on_names_that_matter():
    for name, want in CASES.items():
        assert cc.courseKey(name) == want, (name, cc.courseKey(name), want)


def test_the_sql_is_the_same_rule_in_the_same_order():
    s = cc.sql("name")
    assert "%" not in s and "{" not in s and "}" not in s
    # the gate, then the regions in table order, the final last
    order = [s.index(f"'{cc.SPONSOR}'"), s.index(f"'{cc.CHAMP_WORD}'")]
    for slug, rx, _name in cc.CHAMP_COURSES:
        order.append(s.index(f"THEN 'champ:{slug}'"))
        if rx is not None:
            assert f"~* '{rx}' THEN 'champ:{slug}'" in s
    assert order == sorted(order)
    assert f"!~* '{cc.NOT_FINAL}' THEN 'champ:footlocker-final'" in s
    # the SQL's regexes ARE the Python's: evaluate the SQL's CASE by hand
    def sqlEval(name):
        n = name or ""
        if not (re.search(cc.SPONSOR, n, re.I) and re.search(cc.CHAMP_WORD, n, re.I)):
            return None
        for m in re.finditer(r"WHEN name (!?)~\* '([^']*)' THEN '([^']*)'", s):
            neg, rx, key = m.groups()
            if bool(re.search(rx, n, re.I)) != bool(neg):
                return key
        return None
    for name, want in CASES.items():
        assert sqlEval(name) == want, name


def test_slugs_survive_every_key_parser():
    """'XC:champ:<slug>:d5000' must split at the distance like any key, and
    read as a venue with no canonical id and no coordinates everywhere."""
    import region_lookup as rl
    import xc_reference as xr
    import run_joint as rj
    import bracket_engine as be
    for slug, _rx, _name in cc.CHAMP_COURSES:
        assert ":d" not in slug and "@" not in slug and ":" not in slug
        key = f"XC:champ:{slug}:d5000"
        head, tag, dist = key.rpartition(":d")
        assert (head, dist) == (f"XC:champ:{slug}", "5000")
        assert rl.splitXcVenue(key) == (None, None)        # no region: unmapped, not wrong
        assert xr.canonicalId(key + "@e3") is None
        assert not key[3:].split(":d", 1)[0].isdigit()   # loadCourseCoords: no place
        assert be._courseAndDistance(key) == (f"XC:champ:{slug}", 5000)
    v = rj.venueOfCell(["XC:champ:footlocker-final:d5000", "XC:champ:footlocker-final:d4800",
                        "XC:22029:d5000"])
    assert v[0] == v[1] != v[2]


def test_the_pack_keys_the_championship_before_the_venue():
    import speed_ratings_db as sdb
    xc = sdb._xcQuery(200, 6000)
    champ = sdb._champKeySql()
    assert champ in xc
    # first in the COALESCE, ahead of the canonical id and the name fallback
    i = xc.index(champ)
    assert i < xc.index("cc.canonical_id::text,") < xc.index("'name:' || btrim(")
    # looked up per name; the in-line rule only for a name the table lacks
    inner = ("CASE WHEN mc.name IS NOT NULL THEN mc.champ ELSE "
             + cc.sql("COALESCE(m.meet_name, mt.meet_name, '')") + " END")
    # ...and only where the race was run on that championship's course
    # (the venue gate, 2026-10-02)
    assert champ == cc.venueSql(inner, "COALESCE(m.course_name, mt.venue_name, '')")
    for sport in ("XC", "TF"):
        assert cc.sql("name") + " AS champ" in sdb._packMeetClassSql(sport)
    # and it publishes under its own name, with no canonical id
    names = {"22029": "Morley Field Sports Complex"}
    assert sdb._splitVenueKey("XC:champ:footlocker-final:d5000@e3", names) == \
        ("XC:Foot Locker Nationals", None, 5000)
    assert sdb._splitVenueKey("XC:22029:d5000", names) == \
        ("XC:Morley Field Sports Complex", 22029, 5000)


def test_its_own_cell_reads_the_final_not_the_park():
    """★ THE OWNER'S CASE, in the bracket engine. A park raced by local meets
    thirty days at +2%, the national final there three days (three years) at
    +9%. Keyed as one venue the final's days are outvoted; keyed on its own
    it reads its own course, and the park keeps the park's number."""
    import bracket_engine as be
    rng = np.random.default_rng(29)
    n_ath, n_ord = 3000, 40
    a = rng.normal(0, 0.12, n_ath)
    yr = rng.integers(2022, 2025, n_ath)
    PARK, FINAL = n_ord, n_ord + 1
    rows = []
    for i in range(n_ath):
        for k in range(8):
            rows.append((i, rng.integers(0, n_ord), 10 + 7 * k + rng.integers(0, 3), 0.0))
    for y in (2022, 2023, 2024):
        pool = np.flatnonzero(yr == y)
        for r in range(10):
            for i in rng.choice(pool, 20, replace=False):
                rows.append((i, PARK, 12 + 4 * r, 0.02))
        for i in rng.choice(pool, 30, replace=False):
            rows.append((i, FINAL, 62, 0.09))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    day = np.array([r[2] for r in rows], dtype=np.float64)
    eff = np.array([r[3] for r in rows])
    y = a[ath] + eff + rng.normal(0, 0.03, ath.size)
    year = yr[ath]
    keys = [f"XC:{100 + c}:d5000" for c in range(n_ord)]
    base = {"athlete": ath, "year": year, "days": (2025 - year) * 365.0 + day,
            "sport": np.zeros(ath.size, dtype=np.int64), "norm": np.exp(y),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)]}
    shared = dict(base, course=np.where(course == FINAL, PARK, course),
                  course_keys=keys + ["XC:22029:d5000"])
    own = dict(base, course=course,
               course_keys=keys + ["XC:22029:d5000", "XC:champ:footlocker-final:d5000"])
    kw = dict(window=21, top=1.0, prior_group=1.0)
    with contextlib.redirect_stdout(io.StringIO()):
        f_shared = be.fit(shared, None, **kw)
        f_own = be.fit(own, None, **kw)
    d_shared = f_shared["D"][PARK]
    d_final, d_park = f_own["D"][FINAL], f_own["D"][PARK]
    print(f"  one cell {100 * d_shared:+.2f}%; own cell: final {100 * d_final:+.2f}%, "
          f"park {100 * d_park:+.2f}%")
    assert d_shared < 0.035, d_shared                   # the final diluted
    assert d_final > 0.07, d_final                      # its own course
    assert abs(d_park - 0.02) < 0.01, d_park            # the park is the park


def test_pages_read_the_championship_cell_first():
    import champ_course as C
    sql = C.displaySql("x.n")
    assert "'Foot Locker Nationals'" in sql and "%" not in sql and "{" not in sql
    app_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "racecast", "app.py")).read()
    assert app_src.count("{_champ_join('r')}") == 2          # athlete rows + race header
    # the championship cell first; the athlete rows put the race's own era
    # number between it and the venue's (2026-10-04)
    assert app_src.count("COALESCE(cdc.difficulty,") == 2


def test_ern_region_names_find_their_regional():
    # 2026-10-02: "Footlocker Western Regional" matched no region and fell
    # back to the park's own cell
    import champ_course as c
    assert c.courseKey("Footlocker Western Regional") == "champ:footlocker-west"
    assert c.courseKey("Foot Locker Southern Regional") == "champ:footlocker-south"
    assert c.courseKey("Foot Locker Midwestern Regional Championships") == "champ:footlocker-midwest"
    assert c.courseKey("Foot Locker Northeastern Regional") == "champ:footlocker-northeast"


VENUE_CASES = [
    # (meet name, course name, the cell)
    ("Foot Locker West Regionals", "Mt. San Antonio College", "champ:footlocker-west"),
    ("Foot Locker West Regionals", "Mt. San Antonio College (rain course)", "champ:footlocker-west"),
    ("Footlocker Western Regional", "Foot Locker (Western Regional)", "champ:footlocker-west"),
    ("1995 Footlocker West Regional Championships (Seeded)", "Woodward Park", None),
    ("Brooks West XC Championships", "Mt. San Antonio College", "champ:footlocker-west"),
    ("Brooks West XC Championships", "Hilmer Lodge Stadium", "champ:footlocker-west"),
    ("Brooks Midwest XC Championships", "Dannehl XC Course, Univ. of Wisconsin Parkside", "champ:footlocker-midwest"),
    ("Brooks South XC Championships", "McAlpine Creek Park", "champ:footlocker-south"),
    ("Foot Locker Northeast Regional", "Van Cortlandt Park", "champ:footlocker-northeast"),
    ("Foot Locker Northeast Regional XC Championships", "Franklin Park", "champ:footlocker-northeast-franklin"),
    ("Brooks Northeast XC Championships", "Franklin Park", "champ:footlocker-northeast-franklin"),
    ("Brooks XC National Championships", "Morley Field Sports Complex", "champ:footlocker-final"),
    ("Foot Locker Nationals", "Shades of Green", None),
    ("Brooks Pre-National Invitational 2005", "Laverne Gibson Course", None),
    ("Mt. SAC Invitational", "Mt. San Antonio College", None),
]


def test_the_venue_gate_keeps_one_course_per_cell():
    for name, course, want in VENUE_CASES:
        assert cc.courseKey(name, course) == want, (name, course, cc.courseKey(name, course), want)
    # without a course the name rule alone, as before
    assert cc.courseKey("Foot Locker Nationals") == "champ:footlocker-final"
    assert cc.displayName("champ:footlocker-northeast-franklin").endswith("(Franklin Park)")


def test_the_venue_sql_is_the_python_rule():
    import psycopg2
    try:
        conn = psycopg2.connect(host="/tmp/pgtest", port=54329, user="postgres", dbname="postgres",
                                connect_timeout=3)
    except Exception:                                             # noqa: BLE001
        import pytest
        pytest.skip("no scratch postgres")
    cur = conn.cursor()
    for name, course, want in VENUE_CASES:
        cur.execute(f"SELECT {cc.venueSql(cc.sql('%(n)s'), '%(c)s')}".replace("'%(n)s'", "%(n)s")
                    .replace("'%(c)s'", "%(c)s"), {"n": name, "c": course})
        got = cur.fetchone()[0]
        assert got == want, (name, course, got, want)
    conn.close()
