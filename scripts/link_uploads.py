#!/usr/bin/env python3
"""
link_uploads.py -- give every approved uploaded result a person.

    /srv/venv/bin/python scripts/link_uploads.py              # DRY RUN: counts + samples
    /srv/venv/bin/python scripts/link_uploads.py --apply      # link / mint (pipeline 04a, pass 4)
    /srv/venv/bin/python scripts/link_uploads.py --undo upload|upload_mint|all

    Run inside the pipeline as pass 4 of scripts/link_tfrrs_rows.py (step
    04a_link_tfrrs); XCP_LINK_UPLOADS=0 skips it there.

★ THE GAP (owner, 2026-10-10). racecast/uploads.py writes an approved
  upload into results / results_tf with source = 'upload' and NO person_id
  -- the name, grade and school as the file wrote them. Every linker was
  pinned to source = 'tfrrs' (link_tfrrs_rows, link_idless_by_name), so an
  uploaded result belonged to nobody: no athlete page, no place in the
  solve (speed_ratings_db reads person_id IS NOT NULL), no rating.

★ THE SAME RULES AS THE LINKERS THAT ALREADY RUN, IMPORTED, NOT RESTATED.
  An upload row has no feed athlete id, so the only identity it carries is
  (name, school, grade, date) -- the facts link_teamless and
  link_profile_school decide on. decide() asks, in order:
    1. NAME: link_freshmen.normName, two tokens at least; a namesake of the
       other gender (both known) is not him (link_teamless rule 1).
    2. SCHOOL: the row's school must IDENTIFY something
       (link_profile_school.identifyingSchool: not blank, not 'Unattached',
       not a hometown). A namesake HOLDS the school when one of his rows is
       at the same school (link_feed_twins.sameSchool: one school's words
       inside the other's -- 'Newtown' and 'Newtown High School').
    3. EXACTLY ONE HOLDER. Two namesakes at the school is a refusal
       (link_teamless rule 2, link_profile_school's two careers).
    4. FIT: no cross country race of the holder's on the same day (two
       runners in one field, link_teamless); a numeric grade past 12 reads as
       an age; the class the grade implies within CLASS_SLACK of the
       holder's (COLLEGE_DRIFT more when either side reads college --
       link_profile_school._rowClass); a high school grade never joins a
       holder racing college within WINDOW_DAYS (link_teamless rule 4); and
       the holder's seasons at the school plus this one fit one athlete's
       years there (MAX_SEASONS_AT_ONE_SCHOOL).
    5. ONE CLAIM PER PERSON PER RACE: two rows of one uploaded race that
       land on one person are both refused (link_teamless rule 6's shape).

★ NEW PERSONS WHERE THE LINKERS MINT ONE. link_tfrrs_rows mints a person for
  a tfrrs identity no person holds. An uploaded row with an identifying
  school, no namesake holding that school, and no namesake racing ANYWHERE
  within WINDOW_DAYS (a namesake at another school that autumn could be him
  under another spelling: doubtful) is a runner the corpus never had, and
  gets a person of its own: one per (name, school) across the uploads, in
  unlink.SPLIT_BASE's range (person_pins.nextFreshId). A group of rows that
  does not fit one athlete (the same tests, within the group) is refused.
  The next upload of the same runner links to that person by rule 3, since
  the minted person now holds the school.

! DOUBTFUL IS REPORT-ONLY. Every refusal is written to upload_link_report
  (rebuilt each run: result, upload, name, school, reason, the namesakes
  touched) and printed; the owner sees it on the upload's admin page. A
  doubtful row stays unlinked -- nothing guesses.

! LOGGED AND REVERSIBLE. person_link_log rule 'upload' (a link to an
  existing person) and 'upload_mint' (a new person), from_person 0; --undo
  puts those rows back to no person (link_tfrrs_rows.undo's shape). One row
  that is wrong is a "not mine" fix: person_pins' detach, which this step
  skips from then on.

Read-only without --apply / --undo.
"""
import argparse
import datetime
import os
import statistics
import sys
from collections import Counter, defaultdict, namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ★ ONE NAME RULE, ONE SCHOOL RULE, ONE GENERATION TEST: the linkers' own.
from link_freshmen import LOG_DDL, normName                     # noqa: E402
from link_teamless import (CLASS_SLACK, WINDOW_DAYS, _day, _g,   # noqa: E402
                           _numGrade, nameKeySql)
from link_profile_school import (COLLEGE_DRIFT,                  # noqa: E402
                                 MAX_SEASONS_AT_ONE_SCHOOL, _rowClass, _season,
                                 identifyingSchool)
from link_feed_twins import sameSchool, schoolWords              # noqa: E402

SOURCE = "upload"
RULE = "upload"
RULE_MINT = "upload_mint"
RULES = (RULE, RULE_MINT)
TABLES = (("XC", "results"), ("TF", "results_tf"))
MATCH = "match"
MINT = "mint"
NATIVE_PER_UPLOAD = 100000      # uploads.applyUpload: native_id = upload * 100000 + n

# an uploaded row; gender is its own division / event label's
UpRow = namedtuple("UpRow", "result_id sport date name school grade gender meet_id div_id upload_id",
                   defaults=(None, None, None))
# a namesake's row (level: level_conflict's 'college' / 'hs' / None)
Row = namedtuple("Row", "date sport grade school level", defaults=(None,))
Person = namedtuple("Person", "pid name gender rows")
Verdict = namedtuple("Verdict", "target reason touched", defaults=((),))


# ------------------------------------------------------------------ #
#  THE DECISION -- pure
# ------------------------------------------------------------------ #
def _near(d, rows, days=WINDOW_DAYS):
    return [r for r in rows if d and _day(r.date) and abs((_day(r.date) - d).days) <= days]


def _atSchool(rows, school):
    return [r for r in rows if r.school and sameSchool(r.school, school)]


def fits(u, rows, hold_rows=None):
    """None when the uploaded row u fits one athlete with `rows` (the
    holder's, or the other rows of a mint group); else the reason it does
    not. hold_rows: the rows at the shared school (the era check)."""
    d = _day(u.date)
    if u.sport == "XC" and any(r.sport == "XC" and str(r.date)[:10] == str(u.date)[:10]
                               for r in rows):
        return "same cross country day as the namesake's own race"
    g = _numGrade(u.grade)
    if g is not None and g > 12:
        return "grade reads as an age"
    read = [_rowClass(r) for r in rows]
    cls = [c for c, _col in read if c]
    if cls:
        med = statistics.median(cls)
        cy, college = _rowClass(u)
        slack = CLASS_SLACK + (COLLEGE_DRIFT if college or any(col for c, col in read if c) else 0)
        if cy is not None and abs(cy - med) > slack:
            return "generation mismatch"
    if g is not None and 9 <= g <= 12 and any(r.level == "college" for r in _near(d, rows)):
        return "level mismatch (college namesake)"
    seasons = [s for s in (_season(r.date) for r in (hold_rows if hold_rows is not None else rows))
               if s is not None]
    s = _season(u.date)
    if seasons and s is not None:
        if max(seasons + [s]) - min(seasons + [s]) + 1 > MAX_SEASONS_AT_ONE_SCHOOL:
            return "too far from the namesake's years at the school"
    return None


def decide(u, namesakes):
    """Verdict(target pid | None, reason, touched pids) for one uploaded row.
    reason MATCH (link to target), MINT (a new person), or why it was
    refused. namesakes: Persons of any name (filtered here)."""
    key = normName(u.name)
    if not key:
        return Verdict(None, "no usable name")
    d = _day(u.date)
    if d is None:
        return Verdict(None, "no dated row")
    if not identifyingSchool(u.school):
        return Verdict(None, "school identifies nothing")
    gu = _g(u.gender)
    pool = [t for t in namesakes
            if normName(t.name) == key and not (gu and _g(t.gender) and gu != _g(t.gender))]
    holders = [t for t in pool if _atSchool(t.rows, u.school)]
    tids = tuple(sorted(t.pid for t in holders))
    if len(holders) > 1:
        return Verdict(None, "ambiguous: namesakes at the school", tids)
    if not holders:
        near = tuple(sorted(t.pid for t in pool if _near(d, t.rows)))
        if near:
            return Verdict(None, "namesake at another school in the window", near)
        return Verdict(None, MINT)
    t = holders[0]
    why = fits(u, t.rows, _atSchool(t.rows, u.school))
    if why:
        return Verdict(None, why, tids)
    return Verdict(t.pid, MATCH, tids)


def raceKey(u):
    return (u.sport, u.meet_id, u.div_id)


def resolveClaims(rows, verdicts):
    """Two rows of one uploaded race on one person are both refused."""
    by = defaultdict(list)
    for u in rows:
        v = verdicts[u.result_id]
        if v.target is not None:
            by[(raceKey(u), v.target)].append(u.result_id)
    out = dict(verdicts)
    for (_race, t), rids in by.items():
        if len(rids) > 1:
            for rid in rids:
                out[rid] = Verdict(None, "two rows of one race claim one person", (t,))
    return out


def mintGroups(rows, verdicts):
    """{(name key, school words): [UpRow]} for the rows to mint, and the
    refusals of groups that do not fit one athlete ({result_id: Verdict})."""
    groups = defaultdict(list)
    for u in rows:
        if verdicts[u.result_id].reason == MINT:
            groups[(normName(u.name), frozenset(schoolWords(u.school)))].append(u)
    refused = {}
    out = {}
    for k, us in groups.items():
        races = Counter(raceKey(u) for u in us)
        why = "two runners of one name and school in one race" if any(n > 1 for n in races.values()) \
            else None
        if why is None:
            as_rows = [Row(u.date, u.sport, u.grade, u.school, None) for u in us]
            for i, u in enumerate(us):
                why = fits(u, as_rows[:i] + as_rows[i + 1:])
                if why:
                    break
        if why:
            for u in us:
                refused[u.result_id] = Verdict(None, f"new person: {why}")
            continue
        out[k] = us
    return out, refused


def judge(rows, namesakes_by_key):
    """{result_id: Verdict} and the mint groups."""
    vs = {u.result_id: decide(u, namesakes_by_key.get(normName(u.name), [])) for u in rows}
    vs = resolveClaims(rows, vs)
    groups, refused = mintGroups(rows, vs)
    vs.update(refused)
    return vs, groups


# ------------------------------------------------------------------ #
#  THE CORPUS SIDE
# ------------------------------------------------------------------ #
def nameKeyTextSql(col):
    """normName(<one name column>) in SQL: 'Smith, Jake' -> 'jake smith'.
    Pinned to normName by the tests."""
    swapped = (f"CASE WHEN position(',' in {col}) > 0 "
               f"THEN substr({col}, position(',' in {col}) + 1) || ' ' || split_part({col}, ',', 1) "
               f"ELSE {col} END")
    return (f"btrim(regexp_replace(regexp_replace(lower(replace({swapped}, '-', ' ')), "
            f"'[^a-z ]+', '', 'g'), '\\s+', ' ', 'g'))")


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def upRowsSql(skip_xc="", skip_tf=""):
    """The uploaded rows no person holds yet, both sports, with the gender
    their own label gives (person_gender.labelExpr: the division for cross
    country, the event title for track)."""
    from person_gender import labelExpr
    return f"""
        SELECT 'XC' AS sport, r.result_id, r.date, r.athlete_name, r.school, r.grade,
               {labelExpr('m.division')} AS gender, r.meet_id, r.div_id,
               r.native_id / {NATIVE_PER_UPLOAD} AS upload_id
        FROM   results r
        LEFT   JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
        WHERE  r.source = '{SOURCE}' AND r.person_id IS NULL {skip_xc}
        UNION ALL
        SELECT 'TF', r.result_id, r.date, r.athlete_name, r.school, r.grade,
               {labelExpr('r.event_short')}, r.meet_id, r.div_id,
               r.native_id / {NATIVE_PER_UPLOAD}
        FROM   results_tf r
        WHERE  r.source = '{SOURCE}' AND r.person_id IS NULL
          AND  COALESCE(r.is_relay, 0) = 0 {skip_tf}"""


def gather(cur):
    """([UpRow], {name key: [Person]}). Temp tables only; the caller rolls
    back. Every person who carries one of the uploaded names -- in the anet
    registry (athletes) or on a result row (a tfrrs person, a minted one, an
    earlier upload's) -- with every row of theirs."""
    from level_conflict import levelSql, stageCollegeSchools
    from psycopg2.extras import execute_values
    import person_pins as PP
    det = PP.present(cur, "result_detach")
    cur.execute(upRowsSql(PP.skipSql("XC", "r", det), PP.skipSql("TF", "r", det)))
    rows = [UpRow(rid, sport, d, nm, sch, gr, g, mid, did, uid)
            for sport, rid, d, nm, sch, gr, g, mid, did, uid in cur.fetchall()]
    keys = sorted({normName(u.name) for u in rows} - {None})
    if not keys:
        return rows, {}
    cur.execute("SET LOCAL work_mem = '512MB'")
    stageCollegeSchools(cur)
    for t in ("lu_keys", "lu_p"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute("CREATE TEMP TABLE lu_keys (nm text PRIMARY KEY)")
    execute_values(cur, "INSERT INTO lu_keys VALUES %s", [(k,) for k in keys])
    # ⚠ THE WHOLE REGISTRY AND BOTH RESULT TABLES, NOT A SURNAME PREFILTER
    #   (link_teamless gather's rule): a namesake missed here is a link that
    #   looks unique and is not. Grouped before the name key is computed.
    cur.execute(f"""
        CREATE TEMP TABLE lu_p AS
        SELECT DISTINCT person_id, nm, name FROM (
            SELECT a.person_id, {nameKeySql('a.first_name', 'a.last_name')} AS nm,
                   concat_ws(' ', btrim(a.first_name), btrim(a.last_name)) AS name
            FROM   athletes a WHERE a.person_id IS NOT NULL
            UNION ALL
            SELECT g.person_id, {nameKeyTextSql('g.athlete_name')}, g.athlete_name
            FROM   (SELECT DISTINCT person_id, athlete_name FROM results
                    WHERE person_id IS NOT NULL AND NULLIF(btrim(athlete_name), '') IS NOT NULL
                    UNION
                    SELECT DISTINCT person_id, athlete_name FROM results_tf
                    WHERE person_id IS NOT NULL AND NULLIF(btrim(athlete_name), '') IS NOT NULL
                      AND COALESCE(is_relay, 0) = 0) g
        ) x WHERE x.nm IN (SELECT nm FROM lu_keys)""")
    cur.execute("CREATE INDEX ON lu_p (person_id)")
    gender = ("(SELECT pg.gender FROM person_gender pg WHERE pg.person_id = p.person_id)"
              if _hasTable(cur, "person_gender") else
              "(SELECT min(a.gender) FROM athletes a WHERE a.person_id = p.person_id "
              "AND a.gender IN ('M', 'F') HAVING count(DISTINCT a.gender) = 1)")
    cur.execute(f"SELECT DISTINCT ON (p.person_id, p.nm) p.person_id, p.nm, p.name, {gender} "
                f"FROM lu_p p ORDER BY p.person_id, p.nm")
    facts = [(pid, nm, name, g) for pid, nm, name, g in cur.fetchall()]
    cur.execute(f"""
        SELECT 'XC', r.person_id, r.date, r.grade, r.school, {levelSql('r')}
        FROM   results r WHERE r.person_id IN (SELECT person_id FROM lu_p)
        UNION ALL
        SELECT 'TF', r.person_id, r.date, r.grade, r.school, {levelSql('r')}
        FROM   results_tf r WHERE r.person_id IN (SELECT person_id FROM lu_p)
          AND  COALESCE(r.is_relay, 0) = 0""")
    prow = defaultdict(list)
    for sport, pid, d, gr, sch, lvl in cur.fetchall():
        prow[pid].append(Row(d, sport, gr, sch, lvl))
    by_key = defaultdict(list)
    for pid, nm, name, g in facts:
        by_key[nm].append(Person(pid, name, g, sorted(prow.get(pid, []), key=lambda r: str(r.date))))
    return rows, dict(by_key)


# ------------------------------------------------------------------ #
#  WRITING
# ------------------------------------------------------------------ #
REPORT_DDL = """
CREATE TABLE IF NOT EXISTS upload_link_report (
    sport      text   NOT NULL,
    result_id  bigint NOT NULL,
    upload_id  bigint,
    name       text,
    school     text,
    reason     text   NOT NULL,
    touched    bigint[],
    judged_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sport, result_id)
)"""


def plan(rows, verdicts, groups, fresh):
    """[(sport, result_id, to_person, rule)] -- links, then one fresh id per
    mint group (fresh: a callable that hands out the next id)."""
    out = [(u.sport, u.result_id, verdicts[u.result_id].target, RULE)
           for u in rows if verdicts[u.result_id].reason == MATCH]
    for _k, us in sorted(groups.items(), key=lambda kv: min(u.result_id for u in kv[1])):
        pid = fresh()
        out += [(u.sport, u.result_id, pid, RULE_MINT) for u in us]
    return out


def write(cur, moves):
    """Log then stamp each move, only onto a row still on nobody. -> counts."""
    from psycopg2.extras import execute_values
    cur.execute(LOG_DDL)
    cur.execute("DROP TABLE IF EXISTS lu_map")
    cur.execute("CREATE TEMP TABLE lu_map (sport text, result_id bigint, to_person bigint, rule text, "
                "PRIMARY KEY (sport, result_id))")
    if moves:
        execute_values(cur, "INSERT INTO lu_map VALUES %s", moves)
    out = {}
    for sport, table in TABLES:
        cur.execute(f"""
            INSERT INTO person_link_log (sport, result_id, from_person, to_person, rule)
            SELECT m.sport, r.result_id, 0, m.to_person, m.rule
            FROM   lu_map m JOIN {table} r ON r.result_id = m.result_id
            WHERE  m.sport = %s AND r.source = '{SOURCE}' AND r.person_id IS NULL
            ON CONFLICT DO NOTHING""", (sport,))
        cur.execute(f"""
            UPDATE {table} r SET person_id = m.to_person
            FROM   lu_map m
            WHERE  m.sport = %s AND r.result_id = m.result_id
              AND  r.source = '{SOURCE}' AND r.person_id IS NULL""", (sport,))
        out[sport] = cur.rowcount
    return out


def writeReport(cur, rows, verdicts):
    """upload_link_report holds this run's refusals, and only those."""
    from psycopg2.extras import execute_values
    cur.execute(REPORT_DDL)
    cur.execute("DELETE FROM upload_link_report")
    rep = [(u.sport, u.result_id, u.upload_id, u.name, u.school, v.reason, list(v.touched) or None)
           for u in rows for v in (verdicts[u.result_id],)
           if v.target is None and v.reason not in (MATCH, MINT)]
    if rep:
        execute_values(cur, """INSERT INTO upload_link_report
                               (sport, result_id, upload_id, name, school, reason, touched)
                               VALUES %s ON CONFLICT DO NOTHING""", rep)
    return len(rep)


def undo(cur, which):
    rules = RULES if which == "all" else (which,)
    back = {}
    for sport, table in TABLES:
        cur.execute(f"""
            UPDATE {table} r SET person_id = NULLIF(l.from_person, 0)
            FROM   person_link_log l
            WHERE  l.sport = %s AND l.rule = ANY(%s)
              AND  r.result_id = l.result_id AND r.person_id = l.to_person""", (sport, list(rules)))
        back[sport] = cur.rowcount
    cur.execute("DELETE FROM person_link_log WHERE rule = ANY(%s)", (list(rules),))
    return back


def report(rows, verdicts, groups, show=20):
    reasons = Counter(v.reason for v in verdicts.values())
    print(f"[upload-link] {len(rows):,} uploaded rows with no person judged:")
    for why, n in reasons.most_common():
        print(f"    {n:>8,}  {why}")
    print(f"[upload-link] {len(groups):,} new persons would be made")
    by = {u.result_id: u for u in rows}
    shown = 0
    for rid, v in verdicts.items():
        if v.reason in (MATCH, MINT) or shown >= show:
            continue
        u = by[rid]
        print(f"    refused ({v.reason}): {u.sport} {rid} {u.name} [{u.school}; gr {u.grade or '-'}] "
              f"{str(u.date)[:10]}" + (f" touched {list(v.touched)[:4]}" if v.touched else ""))
        shown += 1


def run(conn, apply, show=20):
    """The whole step on one connection: gather, judge, report, and with
    apply, link + mint + the report table. Commits when applying."""
    import person_pins as PP
    with conn.cursor() as cur:
        if apply:
            PP.ensure(cur)
        rows, by_key = gather(cur)
        if not rows:
            print("[upload-link] no uploaded row without a person")
            conn.rollback()
            return {}
        verdicts, groups = judge(rows, by_key)
        report(rows, verdicts, groups, show)
        if not apply:
            conn.rollback()
            print("[upload-link] dry run -- nothing written; --apply to link")
            return {}
        state = {"next": None}

        def fresh():
            if state["next"] is None:
                state["next"] = PP.nextFreshId(cur)
            else:
                state["next"] += 1
            return state["next"]
        moves = plan(rows, verdicts, groups, fresh)
        out = write(cur, moves)
        out["reported"] = writeReport(cur, rows, verdicts)
    conn.commit()
    print(f"[upload-link] {out}; person_link_log rules '{RULE}' / '{RULE_MINT}' "
          f"(--undo upload|upload_mint|all); doubtful rows in upload_link_report")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=20)
    ap.add_argument("--undo", choices=RULES + ("all",))
    a = ap.parse_args(argv)
    if os.environ.get("XCP_LINK_UPLOADS", "1") in ("0", "false"):
        print("[upload-link] XCP_LINK_UPLOADS=0 -- skipped")
        return 0
    from database import getConn
    with getConn() as conn:
        if a.undo:
            with conn.cursor() as cur:
                back = undo(cur, a.undo)
            conn.commit()
            print(f"[upload-link] undone {a.undo}: rows put back {back}")
            return 0
        print(f"[upload-link] {'APPLY' if a.apply else 'DRY RUN'} {datetime.date.today()}")
        run(conn, a.apply, a.show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
