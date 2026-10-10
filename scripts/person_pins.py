#!/usr/bin/env python3
"""
person_pins.py -- the owner's approved corrections to ONE athlete, kept where
the nightly rebuilds read them, so a rebuild cannot undo them.

    /srv/venv/bin/python scripts/person_pins.py --list              # every pin and detach
    /srv/venv/bin/python scripts/person_pins.py --apply             # pipeline 04a3: (re)apply the detaches
    /srv/venv/bin/python scripts/person_pins.py --undo-detach 555   # one result back where it was
    /srv/venv/bin/python scripts/person_pins.py --undo-detach all
    /srv/venv/bin/python scripts/person_pins.py --unpin grade 7 2025
    /srv/venv/bin/python scripts/person_pins.py --unpin school 7 2025 [XC|TF]

★ WHY (owner, 2026-10-10: "Suggest a fix" -- racecast/fixes.py). Three of the
  five kinds an athlete can report were "recorded only": nothing persistent
  existed to hold the answer. grade_fix is rebuilt by engine/grade_sanity.py
  every run, ranking_results by build_ranking_results.py, and a result moved
  by hand is moved back by whichever linker put it there. The owner's
  approval now writes one of three PINS, and the step that rebuilds the
  thing reads the pin:

    grade_pin      (person_id, season) -> grade. grade_sanity reads it BEFORE
                   its rules (a pinned season is evidence its neighbours are
                   checked against) and lays it over the verdicts again AFTER
                   them, so no rule can overwrite it. method 'pinned', trust
                   'high'. season is the academic year, season_year's clock --
                   grade_fix's own key.
    school_pin     (person_id, season, sport) -> school. build_ranking_results
                   files the season's rows under it before anything else
                   reads the school (the boards, athlete_season, the athlete
                   page's season header, the school pages' rosters). sport ''
                   is both sports. The raw rows keep the feed's string (the
                   project's rule: the raw tables keep the raw strings).
    result_detach  (sport, result_id) -> the row is NOT from_person's. The
                   row moves to a FRESH person id (unlink.SPLIT_BASE's range,
                   allocated once, at the decision), logged in person_link_log
                   rule 'detach' like every other move. The linkers skip a
                   detached row (skipSql), and this file's --apply (pipeline
                   04a3, after the linkers) moves it again if a re-scrape or a
                   relink ever put it back.

! THE OWNER DECIDES; NOTHING HERE DETECTS. A pin exists because an athlete
  asked and the owner approved (fix_request); wrong-person DETECTION stays
  report-only (level_conflict, person_collision, twin_flag). A pin is written
  only by fixes.decide or by hand.

! EVERY CHANGE IS LOGGED AND UNDOABLE. person_pin_log holds every pin, unpin,
  detach and undo with who and which request; a detached row's move is in
  person_link_log. --unpin takes a pin away (the next rebuild reverts the
  season); --undo-detach puts the row back on from_person and drops the
  decision, so no step re-applies it.

Read-only without --apply / --undo-detach / --unpin.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RULE_DETACH = "detach"
TABLES = (("XC", "results"), ("TF", "results_tf"))
# a pin's grade, in the fix form's words (racecast/fixes.GRADES)
WORD_GRADES = ("FR", "SO", "JR", "SR", "GR")
# ! ONE LOCK FOR EVERY FRESH ID drawn here: the owner's Approve (a web
#   worker) and the nightly upload mint (link_uploads) both count up from
#   max(); the lock makes the two reads one after the other.
FRESH_ID_LOCK = 0x70696E73          # 'pins'

DDL = """
CREATE TABLE IF NOT EXISTS grade_pin (
    person_id      bigint NOT NULL,
    season         int    NOT NULL,
    grade          text   NOT NULL,
    level          text,
    fix_request_id bigint,
    pinned_by      text,
    pinned_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, season)
);
CREATE TABLE IF NOT EXISTS school_pin (
    person_id      bigint NOT NULL,
    season         int    NOT NULL,
    sport          text   NOT NULL DEFAULT '',
    school         text   NOT NULL,
    state          text,
    fix_request_id bigint,
    pinned_by      text,
    pinned_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, season, sport)
);
CREATE TABLE IF NOT EXISTS result_detach (
    sport          text   NOT NULL,
    result_id      bigint NOT NULL,
    from_person    bigint NOT NULL,
    to_person      bigint NOT NULL,
    fix_request_id bigint,
    decided_by     text,
    decided_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sport, result_id)
);
CREATE TABLE IF NOT EXISTS person_pin_log (
    id             bigserial PRIMARY KEY,
    kind           text   NOT NULL,
    action         text   NOT NULL,
    person_id      bigint,
    season         int,
    sport          text,
    result_id      bigint,
    value          text,
    fix_request_id bigint,
    actor          text,
    at             timestamptz NOT NULL DEFAULT now()
)"""


def _v(row, key, idx=0):
    """A column off a tuple cursor (the pipeline's) or a dict cursor (the
    site's) alike."""
    if row is None:
        return None
    return row[key] if isinstance(row, dict) else row[idx]


def ensure(cur):
    """The pin tables, through ensureTable (one CREATE at a time, so a
    column added to the DDL later reaches tables made before it)."""
    from link_freshmen import LOG_DDL
    from scrape_school_logos import ensureTable
    for one in DDL.split(";"):
        if one.strip():
            ensureTable(cur, one.strip())
    cur.execute(LOG_DDL)


def present(cur, table):
    cur.execute("SELECT to_regclass(%s) AS t", (f"public.{table}",))
    return _v(cur.fetchone(), "t") is not None


def _log(cur, kind, action, person_id=None, season=None, sport=None, result_id=None,
         value=None, fix_id=None, actor=None):
    cur.execute("""INSERT INTO person_pin_log (kind, action, person_id, season, sport, result_id,
                                               value, fix_request_id, actor)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (kind, action, person_id, season, sport, result_id, value, fix_id, actor))


# ------------------------------------------------------------------ #
#  GRADE PINS -- pure pieces, then the table
# ------------------------------------------------------------------ #
def gradeFromRequest(detail):
    """(grade, level) a grade request pins, or None. A grade the athlete
    picked wins; a class year alone gives the numeric grade it implies in
    that season (link_teamless.classYear's arithmetic backwards: the class
    of Y is in grade 12 - (Y - academic year end)). A class word is a
    college class: the form offers 9-12 as numbers, so a word was chosen to
    mean college."""
    season = detail.get("season")
    grade = (detail.get("grade") or "").strip().upper()
    if grade:
        return grade, ("college" if grade in WORD_GRADES else None)
    cls = detail.get("class_year")
    if season is None or cls is None:
        return None
    g = 12 - (int(cls) - (int(season) + 1))
    if 1 <= g <= 12:
        return str(g), None
    return None


def pinGrade(cur, person_id, season, grade, level=None, fix_id=None, actor=None):
    cur.execute("""
        INSERT INTO grade_pin (person_id, season, grade, level, fix_request_id, pinned_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (person_id, season) DO UPDATE SET grade = EXCLUDED.grade,
               level = EXCLUDED.level, fix_request_id = EXCLUDED.fix_request_id,
               pinned_by = EXCLUDED.pinned_by, pinned_at = now()""",
                (person_id, season, grade, level, fix_id, actor))
    _log(cur, "grade", "pin", person_id, season, None, None, grade, fix_id, actor)


def loadGradePins(cur):
    """{(person_id, season): verdict} -- grade_fix's own shape -- or {}."""
    if not present(cur, "grade_pin"):
        return {}
    cur.execute("SELECT person_id, season, grade, level FROM grade_pin")
    out = {}
    for r in cur.fetchall():
        out[(int(_v(r, "person_id", 0)), int(_v(r, "season", 1)))] = {
            "grade": _v(r, "grade", 2), "level": _v(r, "level", 3),
            "method": "pinned", "trust": "high"}
    return out


def applyGradePins(acad, pins):
    """Lay the pins over grade_sanity's verdicts, in place. Returns how many."""
    for key, v in pins.items():
        acad[key] = dict(v)
    return len(pins)


# ------------------------------------------------------------------ #
#  SCHOOL PINS
# ------------------------------------------------------------------ #
def pinSchool(cur, person_id, season, school, sport=None, state=None, fix_id=None, actor=None):
    sport = sport if sport in ("XC", "TF") else ""
    cur.execute("""
        INSERT INTO school_pin (person_id, season, sport, school, state, fix_request_id, pinned_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (person_id, season, sport) DO UPDATE SET school = EXCLUDED.school,
               state = EXCLUDED.state, fix_request_id = EXCLUDED.fix_request_id,
               pinned_by = EXCLUDED.pinned_by, pinned_at = now()""",
                (person_id, season, sport, school, state, fix_id, actor))
    _log(cur, "school", "pin", person_id, season, sport, None, school, fix_id, actor)


def loadSchoolPins(cur):
    """{(person_id, season, sport or ''): school} or {}."""
    if not present(cur, "school_pin"):
        return {}
    cur.execute("SELECT person_id, season, sport, school FROM school_pin")
    return {(int(_v(r, "person_id", 0)), int(_v(r, "season", 1)), _v(r, "sport", 2) or ""):
            _v(r, "school", 3) for r in cur.fetchall()}


def schoolPinFor(pins, person_id, season, sport):
    """The pinned school of one row's season, or None: the sport's own pin,
    else the both-sports one."""
    if not pins or person_id is None:
        return None
    return pins.get((person_id, season, sport)) or pins.get((person_id, season, ""))


def unpin(cur, kind, person_id, season, sport=None, actor=None):
    if kind == "grade":
        cur.execute("DELETE FROM grade_pin WHERE person_id = %s AND season = %s", (person_id, season))
    else:
        cur.execute("DELETE FROM school_pin WHERE person_id = %s AND season = %s AND sport = %s",
                    (person_id, season, sport if sport in ("XC", "TF") else ""))
    n = cur.rowcount
    _log(cur, kind, "unpin", person_id, season, sport, None, None, None, actor)
    return n


# ------------------------------------------------------------------ #
#  RESULT DETACH
# ------------------------------------------------------------------ #
def nextFreshId(cur):
    """One new person id, above every mint: unlink.SPLIT_BASE's range,
    counted up from the largest id there (person_collision.targets' rule),
    the pending detaches included. Under FRESH_ID_LOCK."""
    from unlink import SPLIT_BASE
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (FRESH_ID_LOCK,))
    det = ("COALESCE((SELECT max(to_person) FROM result_detach), 0)"
           if present(cur, "result_detach") else "0")
    cur.execute(f"""
        SELECT GREATEST(
            COALESCE((SELECT max(person_id) FROM results WHERE person_id >= %(b)s), 0),
            COALESCE((SELECT max(person_id) FROM results_tf WHERE person_id >= %(b)s), 0),
            {det}) AS top
    """, {"b": SPLIT_BASE})
    return max(int(_v(cur.fetchone(), "top") or 0) + 1, SPLIT_BASE)


def skipSql(sport, alias="r", on=True):
    """The linkers' clause: not a detached row. '' when the table is absent
    (on=False) -- a database that never detached anything links as before."""
    if not on:
        return ""
    return (f"AND NOT EXISTS (SELECT 1 FROM result_detach dt WHERE dt.sport = '{sport}' "
            f"AND dt.result_id = {alias}.result_id)")


def detachedPersonsSql():
    """The fresh persons detaches made: a linker that joins whole persons
    (link_freshmen) must not join one back to anybody."""
    return "SELECT to_person FROM result_detach"


def detach(cur, sport, result_id, from_person, fix_id=None, actor=None):
    """Record the owner's decision and move the row now. -> the fresh
    person id, or None when the row is not on from_person any more (it was
    moved since the request; nothing is written)."""
    table = dict(TABLES)[sport]
    ensure(cur)
    cur.execute(f"SELECT person_id FROM {table} WHERE result_id = %s FOR UPDATE", (result_id,))
    now = _v(cur.fetchone(), "person_id")
    if now is None or int(now) != int(from_person):
        return None
    to = nextFreshId(cur)
    cur.execute("""INSERT INTO result_detach (sport, result_id, from_person, to_person,
                                              fix_request_id, decided_by)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (sport, result_id) DO NOTHING""",
                (sport, result_id, from_person, to, fix_id, actor))
    if cur.rowcount == 0:                       # decided before: keep that id
        cur.execute("SELECT to_person FROM result_detach WHERE sport = %s AND result_id = %s",
                    (sport, result_id))
        to = int(_v(cur.fetchone(), "to_person"))
    _log(cur, "detach", "detach", from_person, None, sport, result_id, str(to), fix_id, actor)
    applyDetaches(cur, only=(sport, result_id))
    return to


def applyDetaches(cur, only=None):
    """Move every detached row still on from_person (or on nobody) to its
    fresh person, logged. Idempotent. -> {sport: rows moved}."""
    out = {}
    for sport, table in TABLES:
        if only is not None and only[0] != sport:
            continue
        one = "AND d.result_id = %(rid)s" if only is not None else ""
        args = {"sport": sport, "rule": RULE_DETACH, "rid": only[1] if only else None}
        cur.execute(f"""
            INSERT INTO person_link_log (sport, result_id, from_person, to_person, rule)
            SELECT d.sport, r.result_id, COALESCE(r.person_id, 0), d.to_person, %(rule)s
            FROM   result_detach d JOIN {table} r ON r.result_id = d.result_id
            WHERE  d.sport = %(sport)s {one}
              AND  (r.person_id IS NULL OR r.person_id = d.from_person)
            ON CONFLICT DO NOTHING""", args)
        cur.execute(f"""
            UPDATE {table} r SET person_id = d.to_person
            FROM   result_detach d
            WHERE  d.sport = %(sport)s AND r.result_id = d.result_id {one}
              AND  (r.person_id IS NULL OR r.person_id = d.from_person)""", args)
        out[sport] = cur.rowcount
    return out


def undoDetach(cur, which, actor=None):
    """which: a result id, or 'all'. The row goes back to from_person, the
    log row and the decision go, so nothing re-applies it."""
    one = None if which == "all" else int(which)
    cond = "" if one is None else "AND l.result_id = %(one)s"
    back = {}
    for sport, table in TABLES:
        cur.execute(f"""
            UPDATE {table} r SET person_id = NULLIF(l.from_person, 0)
            FROM   person_link_log l
            WHERE  l.sport = %(sport)s AND l.rule = %(rule)s {cond}
              AND  r.result_id = l.result_id AND r.person_id = l.to_person""",
                    {"sport": sport, "rule": RULE_DETACH, "one": one})
        back[sport] = cur.rowcount
    cur.execute(f"DELETE FROM person_link_log l WHERE l.rule = %(rule)s {cond}",
                {"rule": RULE_DETACH, "one": one})
    dcond = "" if one is None else "WHERE result_id = %(one)s"
    cur.execute(f"DELETE FROM result_detach {dcond}", {"one": one})
    _log(cur, "detach", "undo", None, None, None, one, str(which), None, actor)
    return back


# ------------------------------------------------------------------ #
#  THE COMMAND LINE
# ------------------------------------------------------------------ #
def _list(cur):
    for table, cols in (("grade_pin", "person_id, season, grade, level, fix_request_id, pinned_by"),
                        ("school_pin", "person_id, season, sport, school, fix_request_id, pinned_by"),
                        ("result_detach", "sport, result_id, from_person, to_person, fix_request_id")):
        if not present(cur, table):
            print(f"[pins] {table}: absent")
            continue
        cur.execute(f"SELECT {cols} FROM {table} ORDER BY 1, 2")
        rows = cur.fetchall()
        print(f"[pins] {table}: {len(rows):,}")
        for r in rows[:200]:
            print("    " + "  ".join(str(x) for x in (r.values() if isinstance(r, dict) else r)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="(re)apply every detach (pipeline 04a3)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--undo-detach", metavar="RESULT_ID|all")
    ap.add_argument("--unpin", nargs="+", metavar=("KIND", "ARGS"),
                    help="grade PERSON SEASON | school PERSON SEASON [XC|TF]")
    a = ap.parse_args(argv)
    from database import getConn
    actor = os.environ.get("USER") or "cli"
    with getConn() as conn:
        with conn.cursor() as cur:
            if a.undo_detach:
                ensure(cur)
                back = undoDetach(cur, a.undo_detach, actor)
                conn.commit()
                print(f"[pins] detach {a.undo_detach} undone: rows put back {back}")
                return 0
            if a.unpin:
                kind = a.unpin[0]
                if kind not in ("grade", "school") or len(a.unpin) < 3:
                    ap.error("--unpin grade PERSON SEASON | school PERSON SEASON [XC|TF]")
                ensure(cur)
                n = unpin(cur, kind, int(a.unpin[1]), int(a.unpin[2]),
                          a.unpin[3].upper() if len(a.unpin) > 3 else None, actor)
                conn.commit()
                print(f"[pins] {kind} pin removed ({n}); the next rebuild reverts that season")
                return 0
            if a.apply:
                ensure(cur)
                out = applyDetaches(cur)
                conn.commit()
                print(f"[pins] detaches applied: {out}; person_link_log rule '{RULE_DETACH}' "
                      f"(--undo-detach <result id>|all)")
                return 0
            _list(cur)
            conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
