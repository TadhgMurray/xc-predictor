# Project: xc-predictor / tests
# File:    test_tf_venue_name.py
# Purpose: a TF venue is a place with a name, not our own id (owner,
#          2026-09-16: "make sure tf venue names go in so we can not label
#          our id as venue"). The name was never missing from the SCRAPE --
#          it was missing from the table the consumers join. No database.
#
#   python -m pytest -q tests/test_tf_venue_name.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DB = open(os.path.join(_ROOT, "scripts", "database.py")).read()
_SDB = open(os.path.join(_ROOT, "engine", "speed_ratings_db.py")).read()


def test_meets_tf_can_hold_the_venue_name_and_the_meet_url():
    ddl = _DB[_DB.index("CREATE TABLE IF NOT EXISTS meets_tf ("):]
    ddl = ddl[:ddl.index("PRIMARY KEY")]
    assert "venue_name      TEXT" in ddl
    assert "meet_url        TEXT" in ddl


def test_the_saver_writes_the_same_value_the_meta_table_already_stored():
    """★ IT WAS NEVER A SCRAPING PROBLEM. anet's Location.Name has been
    landing in meets_tf_meta.venue_name all along; meets_tf -- the table the
    engine and the site join -- had nowhere to put it."""
    save = _DB[_DB.index("def saveMeetTF(conn"):_DB.index("# saveResultTF")]
    assert "venue_name, meet_url" in save
    assert 'location.get("Name")' in save
    assert "meetUrlTF(meet_info)" in save
    # the placeholders must match the column list, or every TF meet fails
    cols = save[save.index("INSERT INTO meets_tf ("):save.index("ON CONFLICT")]
    n_cols = cols[cols.index("(") + 1:cols.index(")")].count(",") + 1
    body = cols[cols.index("VALUES"):]
    assert body.count("%s") == 20 and n_cols == 20


def test_the_old_rows_need_no_scrape():
    back = _DB[_DB.index("def backfillMeetsTFVenueNames("):_DB.index("def saveMeetTF(")]
    assert "FROM   meets_tf_meta m" in back
    assert "t.venue_name IS NULL" in back          # idempotent
    assert "conn.commit()" in back


def test_the_meet_url_is_derivable_and_never_a_bare_id():
    ns = {}
    exec(_DB[_DB.index("def meetUrlTF("):_DB.index("# ★ AND THE OLD ROWS")], ns)
    url = ns["meetUrlTF"]({"ID": 12345, "SeasonID": 2026})
    assert url == "https://www.athletic.net/TrackAndField/meet/12345/results?season=2026"
    assert ns["meetUrlTF"]({"ID": 12345}).endswith("/12345/results")
    assert ns["meetUrlTF"]({}) is None
    assert ns["meetUrlTF"]({"ID": ""}) is None


def test_a_nameless_id_keeps_its_id_and_an_idless_row_gets_a_name():
    """The location id stays the identity -- it is stable and two spellings
    of one track must be one cell. What was wrong is that a row with NO
    location id had NO VENUE AT ALL, so it fell into the sport's average
    instead of its own track."""
    q = _SDB[_SDB.index("def _tfQuery("):]
    q = q[:q.index("AS venue") + 8]
    assert q.index("'loc:' || m.location_id") < q.index("'tfv:' || lower(btrim(m.venue_name))")
    assert "ELSE NULL" in q                       # neither: still no venue
    # indoor and outdoor are never one cell, on either branch
    assert q.count("THEN ':in' ELSE ':out' END") == 2


# ⚠ THESE ARE PYTEST-STYLE BARE FUNCTIONS, and for that reason they ran NOWHERE
#   when the file was executed directly: `python tests/test_tf_venue_name.py`
#   did nothing and `python -m unittest` collected nothing, so five venue-name
#   assertions were silently dormant (found 2026-09-18, while answering "did we
#   ever fix the scraper not getting venue name").
#
# ! SO IT RUNS BOTH WAYS. pytest still collects the functions; run directly,
#   this calls every test_* in the module and reports the failures itself.
#   Converting them to unittest would work too, and would also rewrite five
#   working tests for no gain.
if __name__ == "__main__":
    import sys as _sys

    _fns = [(n, o) for n, o in sorted(globals().items())
            if n.startswith("test_") and callable(o)]
    _bad = []
    for _name, _fn in _fns:
        try:
            _fn()
            print(f"ok   {_name}")
        except Exception as _exc:                      # noqa: BLE001
            _bad.append((_name, _exc))
            print(f"FAIL {_name}: {type(_exc).__name__}: {_exc}")
    print(f"\nRan {len(_fns)} tests" + (f", {len(_bad)} failed" if _bad
                                        else " -- OK"))
    _sys.exit(1 if _bad else 0)
