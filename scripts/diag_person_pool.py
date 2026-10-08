#!/usr/bin/env python3
"""
diag_person_pool.py -- how a person is gendered, pooled and rated, from every
table that decides it. READ-ONLY.

    /srv/venv/bin/python scripts/diag_person_pool.py "Eli Whetsone" "Isaiah Lanoy"
    /srv/venv/bin/python scripts/diag_person_pool.py 29603086
    /srv/venv/bin/python scripts/diag_person_pool.py "John Rivera" --school brooks

★ WHY (owner, 2026-10-07): at Fairborn Community Park (Sep 4 2026, a college
  men's 5k) club and freshman runners rated 170-214 -- an 18:58 at 172.7 --
  while returning college runners showed no rating, and Eli Whetsone
  (Wittenberg, a man) was predicted first at the women's NCAA championships.
  A rating that high for that time is another pool's scale, and a man in a
  women's field means his pool ends in _f. This prints, per person: every
  scraped profile (name, gender, school), person_gender's verdict, the season
  rows the boards hold (pool, rating, races), and the season's raw rows with
  their division labels -- so which table put them in which pool is read, not
  guessed.

★ --twins: ONE RUNNER UNDER SEVERAL PERSONS (owner, 2026-10-08: "Seems
  there is a lot of duplication recently!" -- Rachael Withrow, Marshall,
  on the 2026 college women's board as herself and twice as "Unknown"):

    /srv/venv/bin/python scripts/diag_person_pool.py "Rachael Withrow" --school marshall --twins

  A tfrrs person has no athletes row (a minted id, 1,000,000,000 + the
  tfrrs id), so with --school the name is also looked up on the tfrrs
  result rows of that school. --twins prints, per person, each cross
  country row of this season with its feed, canon_meet_id, result_twin
  verdict and person_link_log rule, then link_feed_twins' dry run for the
  person: the other-feed persons holding the same runs (same day, same
  time as printed, same school, one partner), their kind (stray anet
  placeholder / minted tfrrs / career) and the verdict. Read-only.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _persons(cur, arg):
    if arg.isdigit():
        return [int(arg)]
    parts = arg.split()
    first, last = parts[0], " ".join(parts[1:])
    cur.execute("""SELECT DISTINCT person_id FROM athletes
                   WHERE lower(first_name) = lower(%s) AND lower(last_name) = lower(%s)
                     AND person_id IS NOT NULL ORDER BY 1""", (first, last))
    return [r[0] for r in cur.fetchall()]


def _tfrrsPersons(cur, name, school_q):
    """Persons whose tfrrs rows carry this name at a school matching
    school_q. The school strings come from the profiles and the tfrrs rows'
    own, so the lookup rides idx_results_school rather than a scan."""
    cur.execute("""SELECT DISTINCT school FROM athletes
                   WHERE lower(school) LIKE %s""", (f"%{school_q}%",))
    schools = [r[0] for r in cur.fetchall()] + [school_q.title()]
    cur.execute("""SELECT DISTINCT person_id FROM results
                   WHERE school = ANY(%s) AND source = 'tfrrs'
                     AND person_id IS NOT NULL
                     AND lower(btrim(athlete_name)) = lower(%s) ORDER BY 1""",
                (schools, name.strip()))
    return [r[0] for r in cur.fetchall()]


def _twins(conn, cur, pid):
    """This season's XC rows with their dedup facts, then link_feed_twins'
    group for the person (dry run)."""
    import link_feed_twins as F
    since = F.seasonStart(1)
    has = {t: F._hasTable(cur, t) for t in ("result_twin", "person_link_log")}
    tw = ("(SELECT reason FROM result_twin x WHERE x.sport = 'XC' "
          "AND x.result_id = r.result_id)" if has["result_twin"] else "NULL")
    lg = ("(SELECT string_agg(rule, ',') FROM person_link_log l WHERE l.sport = 'XC' "
          "AND l.result_id = r.result_id)" if has["person_link_log"] else "NULL")
    cur.execute(f"""
        SELECT r.date, r.source, r.result_id, r.meet_id, r.canon_meet_id,
               r.time_seconds, r.school, r.athlete_id, r.native_id,
               NULLIF(btrim(r.athlete_name), ''), {tw}, {lg}
        FROM   results r
        WHERE  r.person_id = %s AND r.date >= %s ORDER BY r.date, r.source""",
                (pid, since))
    print(f"    XC rows since {since} (feed, canon meet, twin verdict, link rule):")
    for d, src, rid, meet, canon, ts, sch, aid, nat, nm, why, rule in cur.fetchall():
        print(f"      {d} {src:5} result {rid} meet {meet} canon={canon} "
              f"t={F._clock(ts)} {sch!r} athlete={aid} tfrrs_id={nat} "
              f"name={nm!r} twin={why} link={rule}")
    conn.rollback()
    cur.execute("SET statement_timeout = 0")
    groups, pairs = F.gather(cur, since, pid)
    conn.rollback()
    cur.execute("SET statement_timeout = '120s'")
    if not groups:
        print("    link_feed_twins: no other-feed person holds this person's runs")
        return
    print("    link_feed_twins --person (dry run):")
    F.report(groups, pairs, F.judge(groups), show=5)


def main():
    from database import getConn
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    # --school SUBSTR picks, among many people of one name, those whose
    # profiles name a matching school ("John Rivera" is 40 people).
    twins = "--twins" in args
    args = [x for x in args if x != "--twins"]
    school_q = None
    if "--school" in args:
        i = args.index("--school")
        school_q = args[i + 1].lower() if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '120s'")
            for arg in args:
                pids = _persons(cur, arg)
                print(f"\n=== {arg}: {len(pids)} person id(s) {pids[:10]}")
                if len(pids) > 5 or school_q:
                    cur.execute("""SELECT person_id, string_agg(DISTINCT school, ' | ')
                                   FROM athletes WHERE person_id = ANY(%s)
                                   GROUP BY 1 ORDER BY 1""", (pids,))
                    schools = dict(cur.fetchall())
                    if not school_q:
                        for pid in pids:
                            print(f"    {pid}: {schools.get(pid)}")
                    else:
                        pids = [p for p in pids if school_q in (schools.get(p) or "").lower()]
                        print(f"    matching school {school_q!r}: {pids}")
                if school_q and not arg.isdigit():
                    extra = [p for p in _tfrrsPersons(cur, arg, school_q) if p not in pids]
                    print(f"    tfrrs rows named {arg!r} at {school_q!r}: persons {extra}")
                    pids += extra
                for pid in pids[:5]:
                    print(f"\n  person {pid}")
                    cur.execute("""SELECT athlete_id, first_name, last_name, gender, school
                                   FROM athletes WHERE person_id = %s ORDER BY athlete_id""", (pid,))
                    for a in cur.fetchall():
                        print(f"    profile {a[0]}: {a[1]} {a[2]}  gender={a[3]!r}  school={a[4]!r}")
                    cur.execute("SELECT to_regclass('person_gender')")
                    if cur.fetchone()[0]:
                        cur.execute("SELECT gender, n_m, n_f, split FROM person_gender WHERE person_id = %s",
                                    (pid,))
                        print(f"    person_gender: {cur.fetchone()}")
                    cur.execute("""SELECT sport, year, pool, round(mean_rating::numeric, 1),
                                          n_races, school, grade
                                   FROM athlete_season WHERE person_id = %s
                                   ORDER BY year DESC, sport LIMIT 12""", (pid,))
                    for s in cur.fetchall():
                        print(f"    season {s[0]} {s[1]}: pool={s[2]}  rating={s[3]}  races={s[4]}  "
                              f"school={s[5]!r} grade={s[6]!r}")
                    cur.execute("""
                        SELECT r.date, r.meet_id, r.div_id, m.division, r.school, r.grade,
                               r.time_seconds, r.speed_rating, r.source
                        FROM   results r
                        LEFT   JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
                        WHERE  r.person_id = %s ORDER BY r.date DESC LIMIT 8""", (pid,))
                    for r in cur.fetchall():
                        print(f"    XC {r[0]} meet {r[1]} div {r[2]} [{r[3]}] {r[4]!r} gr={r[5]!r} "
                              f"t={r[6]} rating={r[7]} ({r[8]})")
                    cur.execute("""
                        SELECT r.date, r.meet_id, r.event_short, r.school, r.grade,
                               r.time_seconds, r.speed_rating, r.source
                        FROM   results_tf r
                        WHERE  r.person_id = %s ORDER BY r.date DESC LIMIT 8""", (pid,))
                    for r in cur.fetchall():
                        print(f"    TF {r[0]} meet {r[1]} {r[2]!r} {r[3]!r} gr={r[4]!r} "
                              f"t={r[5]} rating={r[6]} ({r[7]})")
                    cur.execute("SELECT to_regclass('grade_fix')")
                    if cur.fetchone()[0]:
                        cur.execute("""SELECT season, grade, level, method, trust FROM grade_fix
                                       WHERE person_id = %s ORDER BY season DESC LIMIT 6""", (pid,))
                        for g in cur.fetchall():
                            print(f"    grade_fix {g[0]}: grade={g[1]!r} level={g[2]} "
                                  f"method={g[3]} trust={g[4]}")
                    cur.execute("SELECT to_regclass('pro_athlete_season')")
                    if cur.fetchone()[0]:
                        cur.execute("""SELECT season, pro_races FROM pro_athlete_season
                                       WHERE person_id = %s ORDER BY season DESC""", (pid,))
                        print(f"    pro seasons: {cur.fetchall()}")
                    cur.execute("""SELECT pool, count(*), round(avg(speed_rating)::numeric, 1)
                                   FROM ranking_results WHERE person_id = %s AND year >= 2025
                                   GROUP BY pool ORDER BY 2 DESC""", (pid,))
                    for p in cur.fetchall():
                        print(f"    boards 2025+: pool={p[0]} rows={p[1]} mean rating={p[2]}")
                    if twins:
                        _twins(conn, cur, pid)
        conn.rollback()


if __name__ == "__main__":
    main()
