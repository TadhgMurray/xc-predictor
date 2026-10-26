#!/usr/bin/env python3
"""
link_tfrrs_hs.py -- a high schooler's TFRRS rows (fl.tfrrs.org,
indiana.tfrrs.org meets) join their athletic.net career.

    /srv/venv/bin/python scripts/link_tfrrs_hs.py              # DRY RUN: counts + samples
    /srv/venv/bin/python scripts/link_tfrrs_hs.py --apply      # link (link_tfrrs_rows pass 2b)
    /srv/venv/bin/python scripts/link_tfrrs_hs.py --undo       # take every one back

★ THE GAP (2026-10-25, docs/PLAN-MULTISOURCE.md step 2). TFRRS's high school
  state sites are views of TFRRS's one database, so their meets arrive
  through the ordinary TFRRS scrape as source 'tfrrs' (sources/tfrrs_hs.py).
  04a then gives each row a person by its TFRRS athlete id: the fan-out
  (an id already linked), the freshman pass (college first-years) or a MINT
  (person 1,000,000,000 + the TFRRS id). A high schooler is neither of the
  first two, so they are minted -- a second person beside their athletic.net
  career, with half their races on each. link_feed_twins joins the two only
  where both feeds carry the same race; a Florida district meet that is on
  TFRRS alone never pairs.

★ THE RULE: link_uploads' (name, school, generation -- the teamless and
  profile_school linkers' rules, imported), judged per row, decided per
  TFRRS IDENTITY. A TFRRS athlete id at a high school meet in
  tfrrs_hs_meets, whose rows are on no person or on its own minted person,
  joins a career when
    - at least one of its rows MATCHES that career (same name, the career
      holds the school, the grade fits, no same-day cross country race of
      the career's own), and
    - every other row of the id matches the same career or has no namesake
      at all. One row refused for any other reason (two namesakes at the
      school, a namesake elsewhere that season, a generation that does not
      fit) refuses the whole id: a wrong merge is worse than a missed one.
  The id's own minted person is not a namesake (it is the one being
  replaced). Nothing is minted here: pass 3 mints what is left, as before.
  Once joined, the fan-out (pass 1) sends the id's later rows to the career.

! LOGGED AND REVERSIBLE: person_link_log rule 'tfrrs_hs', from_person the
  minted id (or 0 for a row on nobody); --undo puts every row back.
  A detached row (person_pins) is never read.

! NIGHTLY, ONLY WHAT RACED LATELY. Judging an id reads every namesake in
  both result tables; inside link_tfrrs_rows the pass judges only ids with a
  high school row scraped in the last XCP_LINK_TFRRS_HS_DAYS (14) days. A run
  by hand with no --days judges every id (the first run after the state
  sites are queued, and now and then after that).

Read-only without --apply / --undo.
"""
import argparse
import datetime
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import link_uploads as LU                                        # noqa: E402
from link_tfrrs_rows import MINT_BASE, _sys                      # noqa: E402

RULE = "tfrrs_hs"
TABLES = (("XC", "results"), ("TF", "results_tf"))
HS_MEETS_DDL = """
CREATE TABLE IF NOT EXISTS tfrrs_hs_meets (
    meet_id  bigint NOT NULL,
    sport    text   NOT NULL,
    host     text,
    date     text,
    name     text,
    PRIMARY KEY (meet_id, sport)
)"""


def _mintSql(sport, a="r"):
    """The id's own minted person (link_tfrrs_rows.mint's formula)."""
    return (f"(CASE {_sys(sport, a)} WHEN 'directathletics' THEN {MINT_BASE['directathletics']} "
            f"ELSE {MINT_BASE['tfrrs']} END + {a}.native_id)")


def _slugGender(a="r"):
    return (f"(CASE WHEN {a}.team_slug ~ '(^|_)m(_|$)' THEN 'M' "
            f"WHEN {a}.team_slug ~ '(^|_)f(_|$)' THEN 'F' END)")


def _hsGrade(a="r"):
    """A state site prints a high schooler's year as TFRRS prints a
    collegian's (FR / SO / JR / SR). At a high school meet it is grade 9-12,
    which is what link_uploads' generation and level tests should read."""
    return (f"(CASE upper(btrim({a}.grade)) WHEN 'FR' THEN '9' WHEN 'SO' THEN '10' "
            f"WHEN 'JR' THEN '11' WHEN 'SR' THEN '12' ELSE {a}.grade END)")


# ★ AND THE STORED GRADE SAYS SO TOO (pooling review P0, 2026-10-26). A
#   state-site row's "SR" / "SR-4" / "Jr." reached the pooling as a college
#   class word, and poolFor's last resort calls an unresolved tfrrs row
#   college -- so a Florida senior could be rated college_m. At a high school
#   meet the word IS grade 9-12; the row is rewritten to the number once,
#   here, so every reader (the pack, grade_sanity, the backfill, the fill and
#   the boards) sees a high school grade without a special case of its own.
#   Idempotent: a numeric grade no longer matches.
_HS_GRADE_FIX = """
    UPDATE {table} r
    SET    grade = CASE left(upper(btrim(r.grade)), 2)
                       WHEN 'FR' THEN '9' WHEN 'SO' THEN '10'
                       WHEN 'JR' THEN '11' WHEN 'SR' THEN '12' END
    FROM   tfrrs_hs_meets h
    WHERE  r.source = 'tfrrs' AND h.meet_id = r.meet_id AND h.sport = %s
      AND  upper(btrim(r.grade)) ~ '^(FR|SO|JR|SR)([^A-Z]|$)'
"""


def fixHsGrades(cur):
    """Rewrite class words to grades 9-12 on every tfrrs row at a state
    site's high school meet. Returns rows changed per table."""
    out = {}
    for table, sport in (("results", "XC"), ("results_tf", "TF")):
        cur.execute(_HS_GRADE_FIX.format(table=table), (sport,))
        out[table] = cur.rowcount
    return out


def rowsSql(skip_xc="", skip_tf="", recent=False):
    """The rows to judge, in link_uploads.upRowsSql's columns; the identity
    (id system, TFRRS id) rides in the last one as 'sys:native'. recent:
    only identities in th_recent (stageRecent)."""
    from person_gender import labelExpr
    parts = []
    for sport, table in TABLES:
        g = _slugGender() if sport == "XC" else f"COALESCE({labelExpr('r.event_short')}, {_slugGender()})"
        relay = "" if sport == "XC" else "AND COALESCE(r.is_relay, 0) = 0 AND COALESCE(r.is_field, 0) = 0"
        skip = skip_xc if sport == "XC" else skip_tf
        parts.append(f"""
        SELECT '{sport}' AS sport, r.result_id, r.date, r.athlete_name, r.school, {_hsGrade()},
               {g} AS gender, r.meet_id, r.div_id,
               {_sys(sport)} || ':' || r.native_id AS ident
        FROM   {table} r
        JOIN   tfrrs_hs_meets h ON h.meet_id = r.meet_id AND h.sport = '{sport}'
        WHERE  r.source = 'tfrrs' AND r.native_id IS NOT NULL
          AND  (r.person_id IS NULL OR r.person_id = {_mintSql(sport)}) {relay} {skip}
          {f"AND ({_sys(sport)}, r.native_id) IN (SELECT sys, native_id FROM th_recent)" if recent else ""}""")
    return " UNION ALL ".join(parts)


def stageRecent(cur, days):
    """th_recent: the TFRRS ids with a row at a high school meet scraped in
    the last `days` days -- what a nightly run judges. An id judged before
    and left alone is judged again only when it races again (or by a run
    with no --days)."""
    cur.execute("DROP TABLE IF EXISTS th_recent")
    parts = [f"""SELECT {_sys(sport)} AS sys, r.native_id FROM {table} r
                 JOIN tfrrs_hs_meets h ON h.meet_id = r.meet_id AND h.sport = '{sport}'
                 WHERE r.source = 'tfrrs' AND r.native_id IS NOT NULL
                   AND r.scraped_at >= now() - make_interval(days => %(days)s)"""
             for sport, table in TABLES]
    cur.execute(f"CREATE TEMP TABLE th_recent AS SELECT DISTINCT sys, native_id FROM ({' UNION ALL '.join(parts)}) x",
                {"days": int(days)})
    cur.execute("CREATE INDEX ON th_recent (native_id, sys)")


def mintedPid(ident):
    sysname, _c, native = str(ident).partition(":")
    return MINT_BASE.get(sysname, MINT_BASE["tfrrs"]) + int(native)


def judgeIdentities(rows, by_key):
    """{ident: (target | None, reason)} and the per-row verdicts. Pure."""
    by_ident = defaultdict(list)
    for u in rows:
        by_ident[u.upload_id].append(u)
    verdicts = {}
    for ident, us in by_ident.items():
        own = mintedPid(ident)

        def ns(key):
            return [p for p in by_key.get(key, []) if p.pid != own]
        for u in us:
            key = LU.normName(u.name)
            v = LU.decide(u, ns(key))
            nick = [p for k in LU.nameVariants(key) for p in ns(k)]
            verdicts[u.result_id] = LU.decideNick(u, v, nick)
    verdicts = LU.resolveClaims(rows, verdicts)
    out = {}
    for ident, us in by_ident.items():
        vs = [verdicts[u.result_id] for u in us]
        targets = {v.target for v in vs if v.reason == LU.MATCH}
        other = [v.reason for v in vs if v.reason not in (LU.MATCH, LU.MINT)]
        if len(targets) == 1 and not other:
            out[ident] = (targets.pop(), LU.MATCH)
        elif len(targets) > 1:
            out[ident] = (None, "rows match two careers")
        elif targets:
            out[ident] = (None, f"a row refused: {other[0]}")
        else:
            out[ident] = (None, other[0] if other else "no career holds the name and school")
    return out, verdicts


def write(cur, rows, idents):
    """Move each joined identity -- EVERY row of that TFRRS id on nobody or on
    its own minted person, whatever the meet, so the id is one person and
    pass 1's fan-out keeps working -- to the career; log first. -> rows moved
    per sport."""
    from link_freshmen import LOG_DDL
    from psycopg2.extras import execute_values
    cur.execute(LOG_DDL)
    cur.execute("DROP TABLE IF EXISTS th_ident")
    cur.execute("CREATE TEMP TABLE th_ident (sys text, native_id bigint, to_person bigint, "
                "PRIMARY KEY (sys, native_id))")
    joined = sorted({(i.partition(":")[0], int(i.partition(":")[2]), t)
                     for i, (t, _r) in idents.items() if t is not None})
    if joined:
        execute_values(cur, "INSERT INTO th_ident VALUES %s ON CONFLICT DO NOTHING", joined)
    out = {}
    for sport, table in TABLES:
        own = _mintSql(sport)
        match = (f"m.native_id = r.native_id AND m.sys = {_sys(sport)} AND r.source = 'tfrrs' "
                 f"AND (r.person_id IS NULL OR r.person_id = {own})")
        cur.execute(f"""
            INSERT INTO person_link_log (sport, result_id, from_person, to_person, rule)
            SELECT '{sport}', r.result_id, COALESCE(r.person_id, 0), m.to_person, '{RULE}'
            FROM   th_ident m JOIN {table} r ON {match}
            ON CONFLICT DO NOTHING""")
        cur.execute(f"UPDATE {table} r SET person_id = m.to_person FROM th_ident m WHERE {match}")
        out[sport] = cur.rowcount
    return out


def undo(cur):
    back = {}
    for sport, table in TABLES:
        cur.execute(f"""
            UPDATE {table} r SET person_id = NULLIF(l.from_person, 0)
            FROM   person_link_log l
            WHERE  l.sport = %s AND l.rule = %s
              AND  r.result_id = l.result_id AND r.person_id = l.to_person""", (sport, RULE))
        back[sport] = cur.rowcount
    cur.execute("DELETE FROM person_link_log WHERE rule = %s", (RULE,))
    return back


def run(conn, apply, show=20, days=None):
    """days: judge only the ids that raced at a high school meet scraped in
    the last `days` days (the nightly pass); None judges every one."""
    import person_pins as PP
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.tfrrs_hs_meets')")
        if cur.fetchone()[0] is None:
            print("[tfrrs-hs-link] no tfrrs_hs_meets yet (python -m sources.tfrrs_hs queue --apply "
                  "writes it) -- nothing to do")
            conn.rollback()
            return {}
        if apply:
            PP.ensure(cur)
            fixed = fixHsGrades(cur)
            print(f"[tfrrs-hs-link] class words read as grades 9-12: {fixed}")
        if days:
            stageRecent(cur, days)
        rows, by_key = LU.gather(cur, rows_sql=lambda sx, st: rowsSql(sx, st, recent=bool(days)))
        if not rows:
            print("[tfrrs-hs-link] no high school tfrrs row on nobody or on its minted person")
            conn.rollback()
            return {}
        idents, verdicts = judgeIdentities(rows, by_key)
        why = Counter(r for _t, r in idents.values())
        n_rows = Counter(u.upload_id for u in rows)
        print(f"[tfrrs-hs-link] {len(idents):,} tfrrs identities at high school meets "
              f"({len(rows):,} rows) judged:")
        for w, n in why.most_common():
            print(f"    {n:>8,}  {w}  ({sum(n_rows[i] for i, (_t, r) in idents.items() if r == w):,} rows)")
        shown = 0
        for u in rows:
            t, r = idents[u.upload_id]
            if shown >= show:
                break
            if t is not None:
                print(f"    join: {u.sport} {u.name} [{u.school}; gr {u.grade or '-'}] {str(u.date)[:10]} "
                      f"tfrrs {u.upload_id} -> person {t}")
                shown += 1
        if not apply:
            conn.rollback()
            print("[tfrrs-hs-link] dry run -- nothing written; --apply to link")
            return {}
        out = write(cur, rows, idents)
    conn.commit()
    print(f"[tfrrs-hs-link] moved {out}; person_link_log rule '{RULE}' (--undo)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--show", type=int, default=20)
    ap.add_argument("--days", type=int, help="only ids with a high school row scraped in the last N days "
                                             "(the nightly pass uses XCP_LINK_TFRRS_HS_DAYS, default 14)")
    a = ap.parse_args(argv)
    if os.environ.get("XCP_LINK_TFRRS_HS", "1") in ("0", "false"):
        print("[tfrrs-hs-link] XCP_LINK_TFRRS_HS=0 -- skipped")
        return 0
    from database import getConn
    with getConn() as conn:
        if a.undo:
            with conn.cursor() as cur:
                back = undo(cur)
            conn.commit()
            print(f"[tfrrs-hs-link] undone: rows put back {back}")
            return 0
        print(f"[tfrrs-hs-link] {'APPLY' if a.apply else 'DRY RUN'} {datetime.date.today()}")
        run(conn, a.apply, a.show, a.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
