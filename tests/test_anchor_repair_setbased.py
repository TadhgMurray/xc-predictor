# Project: xc-predictor / tests
# File:    test_anchor_repair_setbased.py
# Purpose: the set-based anchor_repair writes the SAME rows with the SAME
#          values, and reports the same counts, as the row-by-row walk it
#          replaced.
#
# ⚠⚠ WHY THIS IS AN OLD-AGAINST-NEW TEST AND NOT A SPEC TEST (2026-09-26).
#    The walk moved from ~300 keyset batches through repairRow in Python to
#    two SQL passes that only hand Python the rows outside the band. That is
#    a speed change and nothing else, so the only claim worth testing is
#    "identical". The old walk is kept below VERBATIM (_oldWalk) and both
#    run on the same synthetic corpus, twice per sport: once with the
#    columns REAL, as on the server, and once DOUBLE, where the written
#    values can be compared bit for bit.
#
# ★ THE CORPUS IS BUILT TO HIT EVERY WAY THE TWO COULD PART:
#   * rows a hair either side of the 10% line (float4 decode vs SQL);
#   * a metre holding two factors -- "Mile" (1609.344) and "1609m" (1609) --
#     with rows between them, so the decision depends on which distance
#     reached anchor_check's _FACTOR slot first;
#   * distances with no factor, a NaN factor, and a metre where only some
#     distances have one (the 'python' and 'uncheckable' classes);
#   * a NaN stored value (Postgres calls NaN > 0; Python calls it right);
#   * no distance, unparseable events, rating_pool NULL with the board's
#     pool, no pool at all, and pools outside anchor_check._POOLS.
#
#   python -m unittest tests.test_anchor_repair_setbased
#
#   The comparison needs a scratch Postgres: XCP_TEST_PG_HOST (a socket dir
#   or host), XCP_TEST_PG_PORT, XCP_TEST_PG_DB. It creates and drops
#   results / results_tf / ranking_results / meets / dist_override there --
#   NEVER point it at the site's database. Without the variables it skips.
import math
import os
import random
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PG = os.environ.get("XCP_TEST_PG_HOST")
for _d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))

import anchor_check as ac                                         # noqa: E402
import anchor_repair as ar                                        # noqa: E402


# ---- the walk this replaced, verbatim but for the prints ---------------- #
_OLD_SELECT = """
    SELECT {pool_expr}                          AS pool,
           r.result_id, r.person_id, r.time_seconds,
           {dist}                               AS distance,
           {event}                              AS event_short,
           r.normalized_time
    FROM   {table} r
    LEFT   JOIN ranking_results k ON k.result_id = r.result_id
                                 AND k.sport = %(sport)s
    {joins}
    WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
      AND  r.time_seconds > 0
      AND  {pool_expr} IS NOT NULL
      AND  r.result_id > %(after)s
    ORDER  BY r.result_id
    LIMIT  %(scan)s
"""


def _oldWalk(conn, sport, batch):
    import psycopg2.extras
    table = "results_tf" if sport == "TF" else "results"
    is_tf = sport == "TF"
    sql = _OLD_SELECT.format(
        table=table, joins="" if is_tf else ac._XC_JOINS,
        dist="NULL::float" if is_tf else "COALESCE(dov.distance, m.distance)",
        event="r.event_short" if is_tf else "NULL::text",
        pool_expr="COALESCE(r.rating_pool, k.pool)")
    moved, skipped, biggest, n_seen = {}, {}, [], 0
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        after = -1
        while True:
            cur.execute(sql, {"sport": sport, "after": after, "scan": batch})
            rows = cur.fetchall()
            if not rows:
                break
            after = rows[-1]["result_id"]
            n_seen += len(rows)
            writes = []
            for r in rows:
                d = r["distance"]
                if d is None and r["event_short"]:
                    got = ar.distanceFromEventShort(r["event_short"])
                    d = got[0] if isinstance(got, (tuple, list)) else got
                new, why = ar.repairRow(r["time_seconds"], d,
                                        r["normalized_time"], r["pool"],
                                        sport, ac._POOLS)
                if new is None:
                    skipped[why] = skipped.get(why, 0) + 1
                    continue
                writes.append((r["result_id"], float(new)))
                moved[r["pool"]] = moved.get(r["pool"], 0) + 1
                biggest.append((abs(new / r["normalized_time"] - 1.0), r,
                                new, why))
            if writes:
                with conn.cursor() as wcur:
                    psycopg2.extras.execute_values(
                        wcur, ar._UPDATE.format(table=table), writes,
                        page_size=10_000)
                conn.commit()
            if len(rows) < batch:
                break
    biggest.sort(reverse=True, key=lambda x: x[0])
    return n_seen, moved, skipped, biggest[:15]


# ---- a normalizeTime with holes in it, the same for both walks ---------- #
_REAL_NT = ac.normalizeTime


def _holedNormalizeTime(t, d, pool, sport=None, **kw):
    if 3000.3 < d < 3000.5:                 # metre 3000: some distances only
        return None
    if round(d) == 2998:                    # no factor anywhere in the metre
        return None
    if round(d) == 2999 and pool == "ms_f":  # a factor that is not a number
        return float("nan")
    return _REAL_NT(t, d, pool, sport, **kw)


_POOLS = ac._POOLS + ("pro_m",)
_EVENTS = ["Mile", "1609m", "1600m", "3200m", "2 Mile", "800m", "1500m",
           "5000m", "Long Jump", "4x400", "", None, "1609.3m", "3000.4m",
           "3000m", "2998m", "2999m"]
_XC_DIST = [5000.0, 8000.0, 4828.032, 3218.688, 3000.4, 3000.0, 2999.0,
            2998.0, 1609.344, 1609.0, 0.0, None]


def _corpus(sport, seed, n=4000):
    """[(result_id, person_id, t, nt, event_or_meet, rating_pool,
    board_pool)]. Deterministic."""
    rnd = random.Random(seed)
    ids = rnd.sample(range(1, 10 * n), n)
    f = lambda d, p, s=sport: ac._factorOf(d, p, s)            # noqa: E731
    mile = (f(1609.0, "hs_m"), f(1609.344, "hs_m"))
    out = []
    for rid in ids:
        pool = rnd.choice(_POOLS)
        if sport == "TF":
            where = rnd.choice(_EVENTS)
            d = ar.rowDistance(None, where)
        else:
            where = rnd.randrange(len(_XC_DIST))
            d = _XC_DIST[where]
        t = rnd.uniform(100, 2000)
        base = f(float(d), pool) if ar._usable(d) else None
        base = base if base and math.isfinite(base) else 1.0
        mode = rnd.random()
        if mode < 0.45:                                # right, with noise
            nt = t * base * rnd.uniform(0.95, 1.05)
        elif mode < 0.60:                              # the wrong pool's scale
            other = rnd.choice(_POOLS)
            s = rnd.choice(("XC", "TF", None))
            fo = f(float(d), other, s) if ar._usable(d) else None
            nt = t * (fo or 1.0) * rnd.uniform(0.99, 1.01)
        elif mode < 0.70:                              # no known scale
            nt = t * base * rnd.uniform(0.4, 2.2)
        elif mode < 0.85:                              # on the line
            nt = t * base * (rnd.choice((0.9, 1.1)) + rnd.choice(
                (0, 1e-7, -1e-7, 1e-6, -1e-6, 5e-5, -5e-5, 2e-4, -2e-4)))
        else:                                          # between the mile's two
            pool = "hs_m"
            if sport == "TF":
                where = rnd.choice(("Mile", "1609m"))
            else:
                where = rnd.choice((8, 9))
            nt = t * (mile[0] + mile[1]) / 2 * rnd.choice((1.1, 0.9))
        if rnd.random() < 0.02:
            nt = rnd.choice((None, 0.0, -5.0))
        if rnd.random() < 0.01:
            t = rnd.choice((0.0, -1.0))
        rating_pool, board_pool = pool, None
        r = rnd.random()
        if r < 0.3:
            rating_pool, board_pool = None, pool
        elif r < 0.35:
            rating_pool, board_pool = None, None       # no pool: not read
        elif r < 0.45:
            board_pool = rnd.choice(_POOLS)            # the row's pool wins
        out.append((rid, rnd.randrange(1, 300), t, nt, where, rating_pool,
                    board_pool))
    return out


def _load(conn, sport, rows, coltype):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS results_tf, results, "
                    "ranking_results, meets, dist_override")
        cur.execute("CREATE TABLE ranking_results "
                    "(result_id bigint, sport text, pool text)")
        if sport == "TF":
            cur.execute(f"""CREATE TABLE results_tf (
                result_id bigint PRIMARY KEY, person_id bigint,
                time_seconds {coltype}, normalized_time {coltype},
                event_short text, rating_pool text)""")
        else:
            cur.execute(f"""CREATE TABLE results (
                result_id bigint PRIMARY KEY, person_id bigint,
                meet_id bigint, div_id bigint, source text,
                time_seconds {coltype}, normalized_time {coltype},
                rating_pool text)""")
            cur.execute("CREATE TABLE meets (div_id bigint PRIMARY KEY, "
                        "meet_id bigint, source text, distance real)")
            cur.execute("CREATE TABLE dist_override "
                        "(meet_id bigint, div_id bigint, distance real)")
            for i, d in enumerate(_XC_DIST):
                # every other meet's distance comes from an override
                if i % 2:
                    cur.execute("INSERT INTO meets VALUES (%s, %s, 'x', 1.0)",
                                (100 + i, i))
                    cur.execute("INSERT INTO dist_override VALUES (%s,%s,%s)",
                                (i, 100 + i, d))
                else:
                    cur.execute("INSERT INTO meets VALUES (%s, %s, 'x', %s)",
                                (100 + i, i, d))
        for rid, pid, t, nt, where, rp, bp in rows:
            if sport == "TF":
                cur.execute("INSERT INTO results_tf VALUES (%s,%s,%s,%s,%s,%s)",
                            (rid, pid, t, nt, where, rp))
            else:
                cur.execute("INSERT INTO results VALUES "
                            "(%s,%s,%s,%s,'x',%s,%s,%s)",
                            (rid, pid, where, 100 + where, t, nt, rp))
            if bp:
                cur.execute("INSERT INTO ranking_results VALUES (%s,%s,%s)",
                            (rid, sport, bp))
            elif rp is None and rid % 7 == 0:
                # a board row for the OTHER sport must not lend its pool
                cur.execute("INSERT INTO ranking_results VALUES (%s,%s,%s)",
                            (rid, "XC" if sport == "TF" else "TF", "hs_m"))
    conn.commit()


def _column(conn, sport):
    with conn.cursor() as cur:
        cur.execute(f"SELECT result_id, normalized_time FROM "
                    f"{'results_tf' if sport == 'TF' else 'results'}")
        # repr, so a NaN the corpus carries on purpose compares equal
        out = {k: repr(v) for k, v in cur.fetchall()}
    conn.commit()
    return out


class TheFactorIsReadFromEveryDistanceInTheMetre(unittest.TestCase):
    """classifyGroups, without a database."""

    def test_the_mile_holds_two_factors(self):
        groups = [(1, "Mile", "hs_m", 5, 10), (2, "1609m", "hs_m", 5, 3)]
        cls, dist = ar.classifyGroups(groups, "TF", True)
        self.assertEqual(cls[1][0], "sql")
        self.assertLess(cls[1][1], cls[1][2], "lo..hi must span both")
        self.assertEqual(cls[1], cls[2])
        self.assertEqual(dist[1], 1609.344)

    def test_no_distance_is_uncheckable(self):
        groups = [(1, None, "hs_m", 5, 1), (2, "Long Jump", "hs_m", 5, 2),
                  (3, "", "hs_m", 1, 3)]
        cls, _ = ar.classifyGroups(groups, "TF", True)
        self.assertEqual({c[0] for c in cls.values()}, {"uncheckable"})


# ! THE REAL psycopg2 FOR THE LENGTH OF A TEST. Several test modules put
#   stand-ins for psycopg2 / database into sys.modules when they are
#   collected (setdefault, so whoever is first wins), and under a full
#   pytest run those stand-ins are what `import psycopg2` returns here --
#   no connect, no execute_values. Swap the real package in, put the
#   stand-ins back afterwards so their owners keep what they installed.
def _realPsycopg2(test):
    names = [k for k in list(sys.modules)
             if k == "psycopg2" or k.startswith("psycopg2.")]
    if all(hasattr(sys.modules.get("psycopg2"), a)
           for a in ("connect", "extensions")):
        import psycopg2
        return psycopg2
    saved = {k: sys.modules.pop(k) for k in names}

    def restore():
        for k in [k for k in sys.modules
                  if k == "psycopg2" or k.startswith("psycopg2.")]:
            del sys.modules[k]
        sys.modules.update(saved)
    test.addCleanup(restore)
    import psycopg2
    import psycopg2.extras                                        # noqa: F401
    return psycopg2


@unittest.skipUnless(_PG, "set XCP_TEST_PG_HOST to a scratch Postgres")
class OldAgainstNew(unittest.TestCase):

    def setUp(self):
        psycopg2 = _realPsycopg2(self)
        kw = dict(host=_PG, port=os.environ.get("XCP_TEST_PG_PORT", "5432"),
                  dbname=os.environ.get("XCP_TEST_PG_DB", "xcp_test"),
                  user=os.environ.get("XCP_TEST_PG_USER", "postgres"),
                  password=os.environ.get("XCP_TEST_PG_PASSWORD", "x"))
        self.conn = psycopg2.connect(**kw)
        self.wconn = psycopg2.connect(**kw)
        ac.normalizeTime = _holedNormalizeTime

    def tearDown(self):
        ac.normalizeTime = _REAL_NT
        ac._FACTOR.clear()
        with self.conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS results_tf, results, "
                        "ranking_results, meets, dist_override")
        self.conn.commit()
        self.conn.close()
        self.wconn.close()

    def _both(self, sport, coltype, seed):
        rows = _corpus(sport, seed)
        _load(self.conn, sport, rows, coltype)
        before = _column(self.conn, sport)
        ac._FACTOR.clear()
        old = _oldWalk(self.conn, sport, batch=97)
        after_old = _column(self.conn, sport)

        _load(self.conn, sport, rows, coltype)
        ac._FACTOR.clear()
        new = ar.walk(self.conn, self.wconn, sport, batch=50)
        self.conn.rollback()
        after_new = _column(self.conn, sport)

        n_old, moved_old, skip_old, big_old = old
        n_new, moved_new, skip_new, big_new = new
        self.assertEqual(n_new, n_old)
        self.assertEqual(moved_new, moved_old)
        self.assertEqual(skip_new, skip_old)
        self.assertEqual(
            [(o, r["result_id"], r["pool"], new_, was)
             for o, r, new_, was in big_new],
            [(o, r["result_id"], r["pool"], new_, was)
             for o, r, new_, was in big_old])
        changed = {k for k in before if before[k] != after_old[k]}
        self.assertEqual(after_new, after_old,
                         "the column must come out identical")
        # the corpus must actually exercise what this file claims it does
        self.assertGreater(len(changed), 20, "too few moves to prove much")
        self.assertIn("scale not identified", skip_old)
        self.assertIn("uncheckable", skip_old)
        return changed

    def test_track_real(self):
        self._both("TF", "real", 1)

    def test_track_double(self):
        self._both("TF", "double precision", 2)

    def test_cross_country_real(self):
        self._both("XC", "real", 3)

    def test_cross_country_double(self):
        self._both("XC", "double precision", 4)

    def test_the_first_distance_into_the_slot_decides(self):
        """★ THE CASE A PURE PER-METRE FACTOR GETS WRONG. Two rows between
        the mile's two factors: whichever of 'Mile' and '1609m' comes first
        in result_id order fills the slot, and the second row is judged by
        it. Both orders, both walks."""
        f9, f344 = ac._factorOf(1609.0, "hs_m", "TF"), \
            ac._factorOf(1609.344, "hs_m", "TF")
        t = 300.0
        nt = t * (f9 + f344) / 2 * 1.1           # bad under 1609.344 only
        for first in ("Mile", "1609m"):
            second = "1609m" if first == "Mile" else "Mile"
            rows = [(1, 1, t, t * (f344 if first == "Mile" else f9),
                     first, "hs_m", None),
                    (2, 1, t, nt, second, "hs_m", None)]
            _load(self.conn, "TF", rows, "double precision")
            ac._FACTOR.clear()
            old = _oldWalk(self.conn, "TF", batch=97)
            _load(self.conn, "TF", rows, "double precision")
            ac._FACTOR.clear()
            new = ar.walk(self.conn, self.wconn, "TF", batch=50)
            self.conn.rollback()
            self.assertEqual(new[2], old[2], first)
            self.assertEqual(new[1], old[1], first)
        # and the two orders really do differ, or this proved nothing
        self.assertTrue(abs(nt / (t * f344) - 1) > ac.TOLERANCE
                        > abs(nt / (t * f9) - 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
