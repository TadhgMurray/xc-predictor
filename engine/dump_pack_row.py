"""dump_pack_row.py -- show what the PACKER sees for one athlete's rows, and
what each pooling input resolves to.

WHY THIS EXISTS
    Every argument to resolvePool checks out ON ITS OWN for person 27072849:
    grade 8, gender F, school 'Louisiana' resolves ms_f, grade_fix says 8,
    college_first_season is null. resolvePool called by hand with those
    values returns ms_f|TF. Yet two of her rows solve as college_f.

    So the fault is not in any single value. It is in what the PACKER hands
    to poolOf -- a positional tuple whose column order nothing enforces:

        _RID, _PID, _NORM, _GRADE, _SRC, _SCHOOL, _DATE, _SPORT, _VENUE,
        _GENDER = range(10)

    This prints that tuple, labelled and typed, plus the dict lookups poolOf
    performs and whether they HIT, plus the pool produced. No inference.

★ USES THE ENGINE'S OWN QUERY, BOUNDED TO ONE PERSON. _tfQuery / _xcQuery are
  what streamResults runs, so the time bounds, the relay/field/wheelchair
  filters, the meets_tf event-key join and the gender LATERAL all apply
  exactly as they do in a real pack. A hand-written SELECT would skip those
  and show a row that looks fine.

⚠ CROSS-SOURCE DEDUP IS OFF BY DEFAULT. streamResults builds twin keys first
  (one full scan plus a hash aggregate); this passes tw="" so the query runs
  as an indexed lookup instead. Pass --twin to include it -- slow, but it is
  the one packer step this otherwise skips, and duplicate rows are a live
  suspect elsewhere in this codebase.

Usage:
    python tools/dump_pack_row.py --person 27072849
    python tools/dump_pack_row.py --person 27072849 --sport XC
    python tools/dump_pack_row.py --person 27072849 --date 2026-05
"""
import sys

sys.path.insert(0, "engine"); sys.path.insert(0, "scripts")

COLS = ("result_id", "person_id", "normalized_time", "grade", "source",
        "school", "date", "sport", "venue", "gender")


# ------------------------------------------------------------------ #
#  ARGS
# ------------------------------------------------------------------ #

def parseArgs(argv):
    """--person N [--sport TF] [--date 2026-05] [--twin]. No deps."""
    out = {"person": None, "sport": "TF", "date": None, "twin": False}
    for i, a in enumerate(argv):
        if a == "--twin":
            out["twin"] = True
        elif a in ("--person", "--sport", "--date") and i + 1 < len(argv):
            out[a[2:]] = argv[i + 1]
    if out["person"] is None:
        sys.exit("need --person <id>")
    out["person"] = int(out["person"])
    out["sport"] = out["sport"].upper()
    return out


# ------------------------------------------------------------------ #
#  FETCH -- the engine's query, filtered to one person
# ------------------------------------------------------------------ #

def fetchRows(person_id, sport, twin):
    """Packed rows for one athlete, via the loader's own SQL.

    ! WRAPPED, NOT REWRITTEN. _tfQuery has no top-level ORDER BY or LIMIT, so
      SELECT * FROM (<it>) q WHERE q.person_id = ... is legal and pushes the
      filter down to an index. The alternative -- streaming 121M rows and
      filtering in Python -- reads the whole corpus to find sixty rows.
    """
    from speed_ratings_db import _xcQuery, _tfQuery
    from database import getConn

    tw = ""
    if twin:
        from speed_ratings_db import _buildTwinKeys
        tw, n = _buildTwinKeys({"XC": "results", "TF": "results_tf"}[sport],
                               sport)
        tw = tw or ""
        print(f"[dump] twin keys: {n:,}")

    inner = {"XC": _xcQuery, "TF": _tfQuery}[sport](600.0, 3600.0, tw)
    sql = f"SELECT * FROM ({inner}) q WHERE q.person_id = %s ORDER BY q.date"
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(sql, (person_id,))
        return cur.fetchall()


# ------------------------------------------------------------------ #
#  DISPLAY -- one concern per helper
# ------------------------------------------------------------------ #

def showRow(r):
    """The tuple as the packer built it, with the TYPE of every field.

    ★ TYPES MATTER. loadGradeUntrusted keys its dict (int(p), int(s)). A
      person_id arriving as str or Decimal misses that dict SILENTLY -- no
      error, no warning, just fixed_grade=None and the school deciding.
    """
    print("    " + "-" * 66)
    for name, val in zip(COLS, r):
        print(f"    {name:<16} {str(val)[:36]:<38} {type(val).__name__}")


def showGate(r):
    """The dict lookups poolOf makes, and whether each HITS."""
    from speed_ratings import (_PID, _DATE, loadGradeUntrusted, _asDate,
                               _academicYear)

    pid = None if r[_PID] is None else int(r[_PID])
    d = _asDate(r[_DATE])
    cal = None if d is None else d.year
    print(f"    pid={pid}  date={d}  calendar={cal}  academic={_academicYear(d)}")

    gate = loadGradeUntrusted()
    key = (pid, cal)
    if key in gate:
        print(f"    grade_fix {key} -> HIT {gate[key]}")
    else:
        print(f"    grade_fix {key} -> MISS   (fixed_grade stays None)")
        if pid is not None:
            print(f"        seasons present for pid: "
                  f"{sorted(s for (p, s) in gate if p == pid)}")

    try:
        from speed_ratings import loadSeasonLevels
        akey = (pid, _academicYear(d))
        print(f"    season_level {akey} -> {loadSeasonLevels().get(akey)}")
    except Exception as exc:
        print(f"    season_level unavailable ({exc})")


def showPool(r):
    """The pool poolOf returns for this exact row -- the bottom line."""
    from speed_ratings import (poolOf, _PID, _DATE, _GRADE, _SRC, _SCHOOL,
                               _GENDER, _SPORT, _asDate)

    d = _asDate(r[_DATE])
    for merge in (False, True):
        pool = poolOf(r[_GRADE], r[_GENDER], r[_SRC], r[_SCHOOL], r[_SPORT],
                      merge, person_id=r[_PID],
                      season=None if d is None else d.year, race_date=d)
        print(f"    ==> poolOf(merge={merge!s:<5}) = {pool!r}")


def main():
    args = parseArgs(sys.argv[1:])
    rows = fetchRows(args["person"], args["sport"], args["twin"])
    print(f"\n[dump] {len(rows)} packed {args['sport']} rows for "
          f"person {args['person']}\n")

    from speed_ratings import _DATE
    for r in rows:
        if args["date"] and args["date"] not in str(r[_DATE]):
            continue
        showRow(r)
        showGate(r)
        showPool(r)
        print()


if __name__ == "__main__":
    main()