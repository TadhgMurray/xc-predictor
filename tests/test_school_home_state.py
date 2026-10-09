"""meet_compile.stampSchoolStates: a runner the school-state inference never
placed takes their home state where their school's name exists there
(owner, 2026-10-09: Hamilton, Chandler AZ read "Hamilton (CA)" at Arcadia)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "racecast"))
from test_link_profile_school import _pg                        # noqa: E402

FIXTURE = """
DROP TABLE IF EXISTS school_athlete_state, person_home_state, school_identity;
CREATE TABLE school_athlete_state (school text, person_id bigint, state text);
CREATE TABLE person_home_state (person_id bigint PRIMARY KEY, state text);
CREATE TABLE school_identity (school text, state text, is_primary boolean);
INSERT INTO school_identity VALUES ('Hamilton', 'AZ', false), ('Hamilton', 'CA', true),
                                   ('Club X', 'CA', true);
INSERT INTO person_home_state VALUES (1, 'AZ'), (2, 'NV'), (3, 'AZ');
INSERT INTO school_athlete_state VALUES ('Hamilton', 3, 'CA');
"""


def test_home_state_where_the_name_exists_there():
    from meet_compile import stampSchoolStates
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(FIXTURE)
            rows = [{"school": "Hamilton", "person_id": 1},     # home AZ, Hamilton AZ exists
                    {"school": "Club X", "person_id": 2},       # home NV, no Club X there
                    {"school": "Hamilton", "person_id": 3}]     # the inference said CA
            stampSchoolStates(cur, rows)
            assert rows[0].get("school_state") == "AZ"
            assert rows[1].get("school_state") is None
            assert rows[2].get("school_state") == "CA"
    finally:
        conn.rollback()
        conn.close()
