"""Issues 59, 49 and 42, pinned by the pure helpers and by source.

    python -m pytest -q tests/test_nonfinish_pool_600.py
"""
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
for name in ("psycopg2", "psycopg2.extras", "psycopg2.errors", "psycopg2.pool"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]
sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def _statusHelper():
    """database.py imports config, which refuses to load without a
    password; lift the two pure definitions out of its source instead."""
    import re
    src = read("scripts", "database.py")
    vocab = re.search(r"^_STATUS_VOCAB = \{.*?\}\n", src, re.S | re.M).group(0)
    fn = re.search(r"^def _statusOf\(resultData: dict\):\n(?:    .*\n|\n)+?    return None\n",
                   src, re.M).group(0)
    ns = {}
    exec(vocab + "\n" + fn, ns)
    return types.SimpleNamespace(_statusOf=ns["_statusOf"])


def test_status_letters_are_kept_and_nothing_else():
    DB = _statusHelper()
    assert DB._statusOf({"Result": "DNF"}) == "DNF"
    assert DB._statusOf({"Result": "dns "}) == "DNS"
    assert DB._statusOf({"Result": "DSQ"}) == "DQ"
    assert DB._statusOf({"Result": "16:14.1"}) is None
    assert DB._statusOf({"Result": None}) is None
    assert DB._statusOf({}) is None


def test_scraper_and_saver_carry_the_status():
    db = read("scripts", "database.py")
    assert "ADD COLUMN IF NOT EXISTS status TEXT" in db
    assert "status        = COALESCE(EXCLUDED.status, results.status)" in db
    assert "mark = _statusOf(result) if time_seconds is None else None" in db
    sc = read("scripts", "scrape_results.py")
    assert sc.count("and not _statusOf(result)") == 2, "both TF collectors keep named non-finishes"
    brr = read("racecast", "build_ranking_results.py")
    assert "AND NOT (COALESCE(r.is_field, 0) = 0 AND r.time_seconds IS NULL)" in brr
    app = read("racecast", "app.py")
    assert "_hasResultsStatus(cur)" in app and "{status_sql}" in app
    assert "r.time_seconds IS NULL THEN r.mark" in app


def test_600_is_admitted_to_rating_but_not_to_the_fit():
    import event_parse as EP
    assert EP._MIN_DISTANCE == 600.0
    assert EP._MAX_DISTANCE > 600
    d, _g = EP._resolveDistanceGender("Men's 600 Meters") \
        if hasattr(EP, "_resolveDistanceGender") else (600.0, None)
    assert d == 600.0
    bf = read("backfill", "backfill_normalize.py")
    assert "_MIN_DISTANCE_M = 600.0" in bf
    fit = read("engine", "fit_distance_exponent.py")
    assert "MIN_DISTANCE_METERS = 800" in fit, "the fit floor stays at 800"
    brr = read("racecast", "build_ranking_results.py")
    assert "_SPRINT_MAX_DISTANCE = 800.0" in brr, "the times-board whitelist keeps 600"


def test_conversions_follow_the_athlete_and_the_scale():
    app = read("racecast", "app.py")
    assert '@app.route("/api/athlete_pool")' in app
    assert "prefill_pool=prefill_pool" in app
    conv = read("racecast", "conversions.py")
    assert 'source.get("scale") == "hs"' in conv and '"base_rating_hs"' in conv
    js = read("racecast", "static", "conversions.js")
    assert "adoptAthletePool(" in js and "src.scale = hsMode()" in js
    assert "rc-scale-change" in js
    tpl = read("racecast", "templates", "conversions.html")
    assert "scale_toggle()" in tpl and "data-prefill-pool" in tpl
    assert (tpl.index("static_v('scale-view.js')")
            < tpl.index("static_v('conversions.js')"))
