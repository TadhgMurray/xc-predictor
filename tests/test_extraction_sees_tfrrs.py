"""The training corpus could not see a quarter of college cross country.

    python -m pytest -q tests/test_extraction_sees_tfrrs.py

⚠ MEASURED, NOT SUSPECTED (scripts/diag_engine_counts.py section C,
  2026-09-17, 1% sample):

      source   level      rated   in `meets`   REACHES TRAINING
      anet     college   19,389       19,389            100.0%
      tfrrs    college    6,505            0              0.0%

  6,505 of 25,894 -- **25.1% of all rated college XC rows** -- were rated by
  the engine, shown on the site, and invisible to the model. College is the
  level the model performs worst on.

★ AND IT TOOK FOUR SEPARATE FILTERS TO LET THEM IN, which is why this file
  pins all four. Fixing any one alone changes nothing and looks like a fix:

    1. `JOIN meets` was an INNER join, and `meets` is the ANET table. tfrrs
       XC venues live in meets_tfrrs, one row per (meet_id, sport).
    2. the WHERE clause required a distance from the same two-term COALESCE,
       so a tfrrs row would have been filtered straight back out.
    3. `r.athlete_id IS NOT NULL` -- and tfrrs XC has athlete_id NULL on
       100% of rows (speed_ratings_db's header). Written to drop profile-less
       AAU entries; it deleted a whole source.
    4. gender came off a LATERAL keyed on athlete_id, so every tfrrs row
       would have had NULL gender -- and gender feeds resolvePool, which
       picks the pool the target was normalized in.

! THE ENGINE ALREADY DID ALL OF THIS. speed_ratings_db._xcQuery LEFT JOINs
  both meet tables and COALESCEs them, and its own header lists this exact
  bug among ones it fixed once: "INNER JOIN meets -- anet-only table;
  deleted tfrrs again." The extraction never got the same fix.

⚠ THIS SQL HAS NOT BEEN EXECUTED. `corrections` is server-only and 51 MB, so
  the query cannot be built off the server. These are structural assertions;
  a smoke extraction on a few chunks is still required before a full run.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "model", "feature_extraction.py"),
              encoding="utf-8").read()


def xcSql():
    """The XC corpus query, with its `--` comments stripped.

    ! STRIPPED, because this file explains the bug BY QUOTING IT and a bare
      substring check would pass on the explanation. Three tests in this
      session have already been bitten by that.
    """
    body = SRC[SRC.index("_XC_SQL = f\"\"\""):SRC.index("def streamXCResults")]
    return "\n".join(l.split("--")[0] for l in body.splitlines())


def tfSql():
    body = SRC[SRC.index("_TF_SQL = f\"\"\""):SRC.index("def streamTFResults")]
    return "\n".join(l.split("--")[0] for l in body.splitlines())


def test_1_the_meets_join_is_left_and_tfrrs_sits_beside_it():
    sql = xcSql()
    assert "LEFT JOIN meets m" in sql, sql
    assert re.search(r"LEFT JOIN meets_tfrrs mt", sql), sql
    # guarded on source, so an anet row never picks up a tfrrs meet
    i = sql.index("LEFT JOIN meets_tfrrs mt")
    assert "r.source = 'tfrrs'" in sql[i:i + 220], sql[i:i + 220]
    assert "mt.sport   = 'XC'" in sql[i:i + 220], sql[i:i + 220]
    # ! NO INNER JOIN TO meets LEFT ANYWHERE in the XC query
    assert "\n        JOIN meets " not in sql, sql


def test_2_every_meet_fact_falls_back_to_the_tfrrs_row():
    sql = xcSql()
    assert "COALESCE(m.course_name, mt.venue_name)" in sql, sql
    assert "COALESCE(m.gps_lat,  mt.gps_lat)" in sql, sql
    assert "COALESCE(m.gps_long, mt.gps_long)" in sql, sql
    # the meet id comes off `results`, since m is NULL for tfrrs
    assert "r.meet_id," in sql, sql
    # ...and the venue join uses the coalesced pair, or a tfrrs venue is
    # unreachable even though build_course_canonical put it in the table
    i = sql.index("LEFT JOIN course_canonical cc")
    block = sql[i:i + 400]
    assert "COALESCE(m.course_name, mt.venue_name)" in block, block
    assert "COALESCE(m.gps_lat,  mt.gps_lat)" in block, block


def test_2b_the_distance_is_one_expression_used_everywhere():
    """! THE TARGET WAS NORMALIZED AT THIS DISTANCE AND THE DIFFICULTY CELL IS
    KEYED BY IT. Two copies of a four-term COALESCE is two chances to
    disagree."""
    assert "_XC_DIST = (" in SRC, SRC[:200]
    i = SRC.index("_XC_DIST = (")
    expr = SRC[i:i + 400]
    assert "division_distances" in expr, expr    # tfrrs, per division, JSONB
    assert "mt.distance" in expr, expr           # and its meet-level fallback
    sql = xcSql()
    assert sql.count("{_XC_DIST}") >= 3, sql     # select, difficulty, WHERE


def test_3_the_identity_filter_accepts_a_person_id_from_tfrrs():
    sql = xcSql()
    assert "r.source = 'tfrrs' AND r.person_id IS NOT NULL" in sql, sql
    # ...and an ANET row still needs athlete_id, so the AAU/junior rows this
    # filter was written for are still dropped
    assert "r.athlete_id IS NOT NULL" in sql, sql
    # the TF stream is untouched: a different source, a different question
    assert "r.source = 'tfrrs'" not in tfSql()


def test_4_gender_comes_from_the_cross_source_table():
    sql = xcSql()
    assert "COALESCE(pg.gender, a.gender) AS gender" in sql, sql
    assert "LEFT JOIN person_gender pg ON pg.person_id = r.person_id" in sql
    # ! AND THE TF QUERY MUST NOT REFERENCE pg -- it has no such join, and a
    #   stray reference is a hard SQL error rather than a silent one. This
    #   very edit introduced it once.
    assert "pg.gender" not in tfSql(), "TF references pg with no join"


def test_the_optional_table_is_stubbed_like_weather():
    """A database that has not run engine/person_gender.py --write must still
    extract, falling back to the profile lateral -- which is exactly what
    packGenderExpr(available=False) does."""
    assert "def genderlessSql(" in SRC, SRC[:200]
    assert "def hasPersonGender(" in SRC, SRC[:200]
    i = SRC.index("def _corpusSql(")
    body = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "hasPersonGender" in body, body
    assert "genderlessSql" in body, body


if __name__ == "__main__":
    for fn in [test_1_the_meets_join_is_left_and_tfrrs_sits_beside_it,
               test_2_every_meet_fact_falls_back_to_the_tfrrs_row,
               test_2b_the_distance_is_one_expression_used_everywhere,
               test_3_the_identity_filter_accepts_a_person_id_from_tfrrs,
               test_4_gender_comes_from_the_cross_source_table,
               test_the_optional_table_is_stubbed_like_weather]:
        fn()
    print("  ok")
