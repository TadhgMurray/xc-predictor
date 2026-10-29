"""A hand-made course split ("Mt. San Antonio College (rain course)") survives
a re-scrape: both meet upserts keep a protected name rather than writing the
venue's plain name back (owner, 2026-10-29).

    python -m pytest -q tests/test_keep_split_on_rescrape.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from course_merge import _PROTECTED                              # noqa: E402

_PG = r"\(rain course\)|\(alt\y|\(short\y|\(long\y"


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_both_upserts_keep_a_protected_name():
    db = read("scripts", "database.py")
    assert ("course_name = CASE WHEN meets.course_name ~* '\\\\(rain course\\\\)|\\\\(alt\\\\y|"
            "\\\\(short\\\\y|\\\\(long\\\\y'") in db
    assert "THEN meets.course_name ELSE EXCLUDED.course_name END" in db
    tf = read("tfrrs", "scraper", "save_tfrrs.py")
    assert "THEN meets_tfrrs.venue_name ELSE EXCLUDED.venue_name END" in tf


def test_the_sql_tags_are_course_merge_s():
    # Postgres' \y is Python's \b: the same names are protected on both sides
    py = re.compile(_PG.replace(r"\y", r"\b"), re.I)
    for name in ("Mt. San Antonio College (rain course)", "Mt. San Antonio College",
                 "Park (alt loop)", "Park (alternate)", "Field (short course)", "Field (longview)"):
        assert bool(py.search(name)) == bool(_PROTECTED.search(name)), name
