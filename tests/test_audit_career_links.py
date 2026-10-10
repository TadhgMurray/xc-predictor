"""
scripts/audit_career_links.py (sweep 2026-10-10, A6): the written
link_profile_school groups the new era checks refuse -- REPORT ONLY.
The judgement is pure and tested here; the script's SQL is checked to
contain no write.
"""
import os
import re
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import audit_career_links as A                                    # noqa: E402
import link_profile_school as L                                   # noqa: E402

R = L.Row


def _written():
    # target 100 took two careers: 200 a generation later at the same club
    # (refused now), and 300 within the same seasons (fine); target 400
    # took one stray only
    groups = {100: {200: ("Pat Lee", "Boston AA"), 300: ("Pat Lee", "Boston AA")},
              400: {500: ("Ann Fox", "Mead")}}
    rows = {100: [R("2010-05-01", "TF", None, school="Boston AA"),
                  R("2011-05-01", "TF", None, school="Boston AA")],
            200: [R("2019-05-01", "TF", None, school="Boston AA")],
            300: [R("2012-05-01", "TF", None, school="Boston AA")],
            400: [R("2020-10-01", "XC", "11", school="Mead")],
            500: [R("2030-10-01", "XC", "9", school="Mead")]}
    facts = {p: (["M"] if p < 400 else ["F"],
                 "Pat Lee" if p < 400 else "Ann Fox")
             for p in (100, 200, 300, 400, 500)}
    return groups, rows, facts, {200, 300}


def test_career_groups_the_new_checks_refuse_are_listed():
    groups, rows, facts, careerish = _written()
    out = A.judgeWritten(groups, rows, facts, careerish)
    assert [(t, v.reason) for t, v, _m, _k, _c in out] == \
        [(100, "careers too far apart")]


def test_stray_groups_only_with_all():
    groups, rows, facts, careerish = _written()
    out = A.judgeWritten(groups, rows, facts, careerish, all_groups=True)
    got = {t: v.reason for t, v, _m, _k, _c in out}
    assert got == {100: "careers too far apart", 400: "generation mismatch"}


def test_a_group_that_still_passes_is_not_listed():
    groups, rows, facts, careerish = _written()
    rows[200] = [R("2013-05-01", "TF", None, school="Boston AA")]
    assert A.judgeWritten(groups, rows, facts, careerish) == []


def test_the_script_never_writes():
    src = open(os.path.join(ROOT, "scripts", "audit_career_links.py"),
               encoding="utf-8").read()
    sql = " ".join(re.findall(r'"""(.*?)"""', src, re.S)[1:])   # not the docstring
    sql += " ".join(re.findall(r'cur\.execute\(\s*f?"([^"]*)"', src))
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                 "TRUNCATE"):
        assert not re.search(rf"\b{verb}\b", sql), verb
    assert "SET TRANSACTION READ ONLY" in src
    assert "conn.commit" not in src and "--write" not in src.split('"""')[2]
