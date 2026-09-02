"""Issue 34: championships and their units on meets, filterable and searchable.

    python -m pytest -q tests/test_meet_units.py
"""
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
for name in ("database", "config", "psycopg2", "psycopg2.extras",
             "psycopg2.errors"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["database"].getConn = lambda: None
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]

import build_meet_units as BMU                                   # noqa: E402
import meets_filter as MF                                        # noqa: E402
import search_index as SI                                        # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_championships_are_the_parsers_gate():
    assert BMU.isChampionship("EBAL Championships")
    assert BMU.isChampionship("CIF North Coast Section Championships")
    assert not BMU.isChampionship("Stanford Invitational")
    assert not BMU.isChampionship("Nike Cross Nationals")
    assert not BMU.isChampionship("OFSAA Championships", state="ON")


def test_units_for_meet():
    champ, facts = BMU.unitsForMeet("EBAL Championships", "Varsity Boys", "anet", "CA")
    assert champ and ("league", "EBAL") in facts
    champ, facts = BMU.unitsForMeet("NCS Tri-Valley Area Championships", "Division 2", "anet", "CA")
    assert champ and ("area", "TRI-VALLEY") in facts and ("section", "NCS") in facts
    champ, facts = BMU.unitsForMeet("SEC Cross Country Championships", "Men 8k", "tfrrs", "AR", college=True)
    assert champ and ("conference", "SEC") in facts
    champ, facts = BMU.unitsForMeet("2024 CIF State Cross Country Championships", "Division II Boys", "anet", "CA")
    assert champ and ("state", "CA") in facts, "a bare STATE fact takes the meet's state"
    champ, facts = BMU.unitsForMeet("CIF North Coast Section Championships", "Division 2 Boys", "anet", "CA")
    assert champ and ("section", "NCS") in facts, "the section alias merge applies here too"
    champ, facts = BMU.unitsForMeet("Stanford Invitational", "Seeded Boys", "anet", "CA")
    assert not champ and facts == []


def test_meets_filter_reads_champ_and_unit():
    f = MF.parseFilters({"sport": "XC", "champ": "1"})
    assert f["champ"] and f["unit"] == "" and f["active"]
    f = MF.parseFilters({"sport": "XC", "unit": "ncs"})
    assert f["champ"] and f["unit"] == "NCS", "a unit implies championships"
    assert MF.describe(f).startswith("NCS championships")
    f = MF.parseFilters({"sport": "TF"})
    assert not f["champ"] and not f["active"]

    params = {}
    clause, cols = MF.unitSql(MF.parseFilters({"unit": "EBAL"}), "m", params)
    assert "u.meet_id = m.meet_id AND u.unit = %(unit)s" in clause
    assert params["unit"] == "EBAL" and params["sport"] == "XC"
    assert "AS units" in cols
    clause, cols = MF.unitSql(MF.parseFilters({}), "m", {}, present=False)
    assert clause == "" and cols == "", "no table, no filter"
    assert "{unit_clause}" in MF._BROWSE_SQL and "{unit_cols}" in MF._SCHOOL_SQL


def test_unit_search_links_land_on_the_board():
    assert SI.unitLink("section", "NCS", "CA", False) == \
        "/rankings?board=ability&pool=hs_m&section=NCS&state=CA"
    assert SI.unitLink("conference", "SEC", "AR", True) == \
        "/rankings?board=ability&pool=college_m&conference=SEC"
    assert SI._KIND["units"] == "unit" and "units" in SI._LOADERS


def test_pipeline_and_template_carry_the_feature():
    sh = read("deploy", "run_pipeline.sh")
    assert "10e_meet_units" in sh and "13c_search_index" in sh
    tpl = read("racecast", "templates", "meets.html")
    assert 'name="champ"' in tpl and 'name="unit"' in tpl and "Championship of" in tpl
