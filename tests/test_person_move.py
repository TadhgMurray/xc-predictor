"""person_move.dropCollisions: a merge leaves behind the rows the target
already holds, so the server's per-person unique index cannot fail the move
(link_feed_twins --write, 2026-10-08: idx_results_tf_nodup)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
from test_link_profile_school import _pg                        # noqa: E402

FIXTURE = """
DROP TABLE IF EXISTS pm_tf;
CREATE TABLE pm_tf (result_id bigint PRIMARY KEY, meet_id bigint, div_id int,
  event_id int, person_id bigint, round text, time_seconds double precision);
CREATE UNIQUE INDEX pm_tf_nodup ON pm_tf (meet_id, div_id, event_id, person_id,
  round, round(time_seconds::numeric, 1));
INSERT INTO pm_tf VALUES
  (1, 663826, 4, 22, 29567439, 'F', 165.5),   -- the target's own row
  (2, 663826, 4, 22, 1000000001, 'F', 165.52), -- the mover's copy of it
  (3, 663826, 4, 22, 1000000001, 'P', 167.0),  -- the mover's prelim: moves
  (4, 700000, 1, 5, 1000000002, 'F', 300.0),   -- two movers, one copy each
  (5, 700000, 1, 5, 1000000003, 'F', 300.0),
  (6, 700000, 1, 5, 1000000003, NULL, 301.0);  -- a NULL key never collides
"""


def test_copies_stay_and_the_rest_move():
    from person_move import MOVE_DDL, dropCollisions
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(FIXTURE)
            cur.execute(MOVE_DDL)
            cur.execute("""INSERT INTO pm_move VALUES (2, 29567439), (3, 29567439),
                           (4, 29567439), (5, 29567439), (6, 29567439)""")
            kept = dropCollisions(cur, "pm_tf")
            assert kept == {"pm_tf_nodup": 2}
            cur.execute("SELECT result_id FROM pm_move ORDER BY 1")
            assert [r[0] for r in cur.fetchall()] == [3, 4, 6]
            cur.execute("""UPDATE pm_tf r SET person_id = mv.new_id FROM pm_move mv
                           WHERE r.result_id = mv.result_id""")
            assert cur.rowcount == 3
    finally:
        conn.rollback()
        conn.close()
