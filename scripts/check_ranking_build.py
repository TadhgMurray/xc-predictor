"""No-database check of build_ranking_results' index and phase machinery.

    python scripts/check_ranking_build.py

Run from the PROJECT ROOT. Touches no database: the connection is faked, so
what is actually asserted is the SQL the build EMITS.

★ WHY THIS EXISTS. buildIndexes now runs its CREATE INDEX statements on
  several connections at once, and the failure mode of getting that wrong is
  silent and expensive: an index built on the LIVE table instead of the
  shadow, or a name collapsed at Postgres' 63-character identifier limit, or a
  definition quietly dropped by the thread pool. None of those raise; they
  just leave the swapped-in table missing an index, and the site gets slow
  weeks later with no event to point at.

  So the contract asserted here is: the same DDL as the serial version emitted,
  every statement aimed at the shadow, one per definition, and ANALYZE always.
"""
import os
import sys
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import build_ranking_results as B   # noqa: E402

_LOCK = threading.Lock()
SEEN = []
THREADS = set()


class FakeCur:
    """Records what it is asked to run; answers the two reads indexDefs does."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        with _LOCK:
            SEEN.append(" ".join(sql.split()))
            THREADS.add(threading.get_ident())

    def fetchall(self):
        # One index already in the catalogue, under the LIVE table's name --
        # the case that must come back renamed for the shadow.
        return [("ranking_results_pkey",
                 "CREATE UNIQUE INDEX ranking_results_pkey ON "
                 "public.ranking_results USING btree (sport, result_id)")]

    def fetchone(self):
        return (0,)


class FakeConn:
    def cursor(self, *a, **k):
        return FakeCur()

    def commit(self):
        pass

    def rollback(self):
        pass


class FakePooled:
    def __enter__(self):
        return FakeConn()

    def __exit__(self, *exc):
        return False


_BAD = 0


def check(label, got, want=True):
    global _BAD
    ok = got == want
    _BAD += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def creates():
    return [s for s in SEEN if s.startswith("CREATE INDEX")
            or s.startswith("CREATE UNIQUE INDEX")]


def main():
    B.getConn = lambda: FakePooled()

    print("buildIndexes on the ranking_results shadow")
    B.buildIndexes(FakeConn(), "ranking_results_new", "ranking_results")
    made = creates()

    check("one CREATE per definition (4 canonical + 1 from the catalogue)",
          len(made), 5)
    check("every index is built on the SHADOW, never the live table",
          all(" ON public.ranking_results_new " in s
              or " ON ranking_results_new " in s for s in made))
    check("none is left under the live table's own index name",
          any("INDEX ranking_results_pkey " in s for s in made), False)
    check("the catalogue index is renamed for the shadow",
          any("ranking_results_new_pkey" in s for s in made))
    check("all are IF NOT EXISTS, so a resumed run skips what built",
          all("IF NOT EXISTS" in s for s in made))
    check("no name exceeds Postgres' 63-character identifier limit",
          max(len(s.split("IF NOT EXISTS ")[1].split(" ")[0])
              for s in made) <= 63)
    check("ANALYZE ran, on the shadow",
          "ANALYZE ranking_results_new" in SEEN)
    check("more than one thread was actually used", len(THREADS) > 1)

    print("\n  the four the site cannot run without")
    for cols in ("(person_id)",
                 "(pool, sport, year, speed_rating DESC)",
                 "(pool, sport, year, time_seconds)",
                 "(school, sport, speed_rating DESC)"):
        check(cols, any(cols in s for s in made))

    print("\nan empty definition list still ANALYZEs")
    SEEN.clear()
    real = B.indexDefs
    B.indexDefs = lambda conn, like: []
    try:
        B.buildIndexes(FakeConn(), "athlete_season_new", "athlete_season")
    finally:
        B.indexDefs = real
    check("the early return no longer skips ANALYZE",
          "ANALYZE athlete_season_new" in SEEN)

    print("\nthe phase clock")
    B._PHASES.clear()
    with B.phase("a"):
        pass
    with B.phase("b"):
        pass
    check("one entry per phase, in order", [l for l, _ in B._PHASES],
          ["a", "b"])

    raised = False
    try:
        with B.phase("c"):
            raise ValueError("boom")
    except ValueError:
        raised = True
    check("an exception inside a phase is not swallowed", raised)
    check("and the phase is still recorded", B._PHASES[-1][0], "c")

    print("\nall cases pass" if not _BAD else f"\n{_BAD} FAILURES")
    return 1 if _BAD else 0


if __name__ == "__main__":
    sys.exit(main())
