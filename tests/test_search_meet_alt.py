# Project: xc-predictor / tests
# File:    test_search_meet_alt.py
# Purpose: a search result for a meet lands on the meet it names. The anet
#          and tfrrs meet ids collide, so the index is keyed (meet_id,
#          source) and each link carries the page's own ?alt= index, ranked
#          exactly as app.meet_sources ranks (biggest first, source as the
#          tie-break); the race page resolves the same alt.
#
#   python -m pytest -q tests/test_search_meet_alt.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import search_index as si                                      # noqa: E402


def _src(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_alt_index_is_the_pages_order_biggest_first_then_source():
    rows = [{"meet_id": 7, "source": "tfrrs", "n_rank": 900},
            {"meet_id": 7, "source": "anet", "n_rank": 120},
            {"meet_id": 8, "source": "anet", "n_rank": 50},
            {"meet_id": 9, "source": "anet", "n_rank": 10},
            {"meet_id": 9, "source": "tfrrs", "n_rank": 10}]      # a tie: source decides
    alt = si.meetAltIndex(rows)
    assert alt[(7, "tfrrs")] == 0 and alt[(7, "anet")] == 1
    assert alt[(8, "anet")] == 0
    assert alt[(9, "anet")] == 0 and alt[(9, "tfrrs")] == 1
    assert si.meetLink("/meet/xc/{mid}", 7, 0) == "/meet/xc/7"
    assert si.meetLink("/meet/xc/{mid}", 7, 1) == "/meet/xc/7?alt=1"


def test_the_index_is_keyed_by_meet_and_source_and_named_like_the_page():
    s = _src("racecast", "search_index.py")
    agg = s[s.index("_MEET_AGG = {"):s.index("def _ensure_meet_agg(")]
    assert "GROUP  BY meet_id, source" in agg
    assert "meets_tfrrs" in agg and "COALESCE(nm.meet_name, tn.meet_name)" in agg
    assert "PRIMARY KEY (meet_id, source)" in s
    assert "swapTable(conn, table" in s[s.index("def _ensure_meet_agg("):s.index("def meetAltIndex(")]
    lm = s[s.index("def _load_meets("):s.index("def _load_venues(")]
    assert "meetAltIndex(rows)" in lm and "meetLink(link_fmt" in lm


def test_the_page_side_ranks_the_same_way_and_the_race_page_carries_alt():
    a = _src("racecast", "app.py")
    ms = a[a.index("def meet_sources("):a.index("def pick_source(")]
    assert "ORDER BY count(*) DESC, source" in ms
    rx = a[a.index('@app.route("/race/xc/<int:meet_id>/<int:div_id>")'):
           a.index('@app.route("/race/xc/<int:meet_id>/compiled/')]
    assert 'pick_source(' in rx and "get_race_header(cur, meet_id, div_id, source=src)" in rx
    assert "get_race_results(cur, meet_id, div_id, source=src)" in rx
    hdr = a[a.index("def get_race_header("):a.index("def raceExtras(")]
    assert hdr.count("%(src)s::text IS NULL OR") == 2
    res = a[a.index("def get_race_results("):a.index("_REC_DIST_TOL =")]
    assert "%(src)s::text IS NULL OR r.source = %(src)s" in res
    meet = _src("racecast", "templates", "meet.html")
    # the race links carry ?alt= through meet.html's race_q() macro
    assert "{% macro race_q() %}{% set _q = [] %}{% if other_sources %}{% set _ = _q.append('alt=' ~ alt_idx) %}" in meet
    assert "/race/xc/{{ header.meet_id }}/{{ d.div_id }}{{ race_q() }}" in meet
    # ★ EACH LEVEL COMPILES APART since e485bc3: the compiled link reads
    #   race_q() once into _rq and joins it to ?level= on a non-primary group
    assert "{% set _rq = race_q()|string %}" in meet
    assert ("/compiled/{{ g.distance }}/{{ g.gender|urlencode }}"
            "{% if g.primary %}{{ _rq }}{% else %}?level={{ g.level|urlencode }}"
            "{% if _rq %}&amp;{{ _rq[1:] }}{% endif %}{% endif %}") in meet
    race = _src("racecast", "templates", "race.html")
    assert '/meet/xc/{{ header.meet_id }}{% if other_sources %}?alt={{ alt_idx }}{% endif %}' in race
    sm = _src("racecast", "build_sitemap.py")
    assert "meetAltIndex" in sm and "meetLink(fmt" in sm


def test_the_other_feeds_copy_is_left_out_but_alt_numbering_holds():
    """Owner, 2026-10-08: "two versions of each race on the website, one
    from each source ... predictions and races/search". An entry whose rows
    are mostly result_twin copies is skipped; ?alt= is still counted over
    every entry, as the meet page resolves it."""
    import search_index as S
    rows = [{"meet_id": 7, "source": "anet", "n_rank": 300, "n_ath": 300, "n_twin": 0},
            {"meet_id": 7, "source": "tfrrs", "n_rank": 290, "n_ath": 290, "n_twin": 285},
            {"meet_id": 9, "source": "tfrrs", "n_rank": 50, "n_ath": 50, "n_twin": 3}]
    alt = S.meetAltIndex(rows)
    kept = [r for r in rows if not S.isTwinCopy(r)]
    assert [(r["meet_id"], r["source"]) for r in kept] == [(7, "anet"), (9, "tfrrs")]
    assert alt[(9, "tfrrs")] == 0 and alt[(7, "anet")] == 0
    assert not S.isTwinCopy({"n_ath": 0, "n_twin": 0})
