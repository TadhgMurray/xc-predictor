"""TFRRS cross country ids that showed nothing are kept and re-asked below
the watermark (owner, 2026-10-30: the Mike Woods Classic, XC 28447, had
been deleted from the queue and was never asked again).

    python -m pytest -q tests/test_tfrrs_holes.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


class Cur:
    def __init__(self):
        self.sql, self.rowcount = [], 3

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return (7,)


def test_the_hole_pass_queues_the_missing_and_wakes_rested_state_4():
    import queue_meets as Q
    cur = Cur()
    assert Q.tfrrsHoles(cur, 29018, write=False, span=1500, rest_hours=20) == (7, 7)
    cur = Cur()
    assert Q.tfrrsHoles(cur, 29018, write=True, span=1500, rest_hours=20) == (3, 3)
    ins, upd = cur.sql
    assert ins[0].startswith("INSERT INTO meet_queue") and "ON CONFLICT (meet_id, sport, source) DO NOTHING" in ins[0]
    assert ins[1] == (27518, 29018)
    assert "scraped = 4" in upd[0] and "requeued_at < now()" in upd[0] and upd[1] == (27518, 29018, 20)


def test_wired_into_the_seed_and_the_drain():
    q = read("scripts", "queue_meets.py")
    assert 'if source == "tfrrs" and sport == "XC" and os.environ.get("SEED_TFRRS_HOLES"' in q and "tfrrsHoles(cur, top, write=write)" in q
    r = read("tfrrs", "driver", "run_tfrrs.py")
    assert "if sport == SPORT_XC:\n            await asyncio.to_thread(_markNotAMeet" in r
    assert "SET scraped = 4, requeued_at = now()" in r


def test_the_latest_index_queues_first_and_the_daily_hole_pass_is_off():
    # owner's run, 2026-10-30: the hole pass found 758 ids in its window;
    # www.tfrrs.org's newest-results index names late-posted meets instead
    q = read("scripts", "queue_meets.py")
    assert 'os.environ.get("SEED_TFRRS_HOLES", "") == "1"' in q
    hs = read("sources", "tfrrs_hs.py")
    assert 'walk(("www.tfrrs.org",), a.pages)' in hs and '"latest": cmdLatest' in hs
    assert "ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 0, priority = 1" in hs
    assert "ORDER BY COALESCE(priority, 0) DESC, meet_id DESC" in read("scripts", "database.py")
    assert "sources.tfrrs_hs latest --pages 3" in read("deploy", "nightly_update.sh")


def test_one_meet_runs_and_a_slower_night():
    # owner, 2026-10-30: "get just Mike Woods and rate it in under 30 mins",
    # "slow the rate"
    db = read("scripts", "database.py")
    assert 'os.environ.get("TFRRS_ONLY")' in db and "meet_id = ANY(%s::bigint[])" in db
    sh = read("deploy", "nightly_update.sh")
    assert "--no-publish) PUBLISH=0" in sh and 'if [ "$PUBLISH" -eq 0 ]; then' in sh
    assert 'TFRRS_SESSIONS="${XCP_NIGHTLY_TFRRS_SESSIONS:-1}"' in sh
    assert 'TFRRS_PER_MEET_DELAY="${XCP_NIGHTLY_TFRRS_DELAY:-10,20}"' in sh
