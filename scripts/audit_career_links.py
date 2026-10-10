#!/usr/bin/env python3
"""
audit_career_links.py -- which (name, school) merges already written by
link_profile_school.py would be REFUSED under today's checks. READ ONLY.

    /srv/venv/bin/python scripts/audit_career_links.py 2>&1 | tee -a audit_career_links.txt
    /srv/venv/bin/python scripts/audit_career_links.py --show 200 2>&1 | tee -a audit_career_links.txt
    /srv/venv/bin/python scripts/audit_career_links.py --all          # strays too
    /srv/venv/bin/python scripts/audit_career_links.py --target 32309981

★ WHY (sweep 2026-10-10, A6). `link_profile_school --careers --write` joined
  5,506 groups before the era check existed: its generation test read only
  numeric high-school grades (link_teamless.classYear), so two COLLEGE or
  UNGRADED careers under one (name, school) key were joined with no check
  that they were one athlete's years. link_profile_school now reads the
  college eligibility spelling (FR-1 .. SR-n) and refuses careers whose
  seasons at the shared school span more than one athlete can compete there
  (MAX_SEASONS_AT_ONE_SCHOOL -- see that file for the derivation). This
  lists the groups already on file that the new checks refuse.

! REPORT ONLY -- IT CHANGES NOTHING (owner's rule: wrong-person detection is
  report-only). The transaction is READ ONLY and rolled back; there is no
  --write. A group listed here is put back by hand, one at a time, after a
  look:
      /srv/venv/bin/python scripts/link_profile_school.py --undo <old_id>
  (which also vetoes it), the same way person_collision --write stays a
  human's call.

★ HOW A WRITTEN GROUP IS REBUILT. A merge moved every row of the old person
  to the target and logged each one in person_link_log (rule
  'profile_school', from_person = the old person). So:
    - a mover's rows: the logged rows from it, plus any row still under its
      id (a row the target already held a copy of stayed behind);
    - the target's rows: everything under the target that no merge moved in;
    - a mover was a CAREER (not a lone stray) when the merge moved another
      profile with it (profile_school_athletes) or a row that was not its
      own anet row -- gather's own definition of lone, read after the fact;
    - name and sex: each person's own athletes row.
  The group is then judged by link_profile_school.decideGroup itself, with
  allow_careers -- one rule, so this report cannot disagree with the step.
! WHICH TARGET decideGroup picks is not the point: only whether it refuses,
  and why. A reason other than the two new ones ("generation mismatch" from
  a college class, "careers too far apart") means the group would fail an
  OLD check on today's rows (rows re-scraped since) -- listed too, marked.
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import link_profile_school as L                                   # noqa: E402
from link_freshmen import normName                                # noqa: E402

RULE = L.RULE
# the refusals the 2026-10-10 checks added; anything else is an old check
NEW_REASONS = ("generation mismatch", "careers too far apart")


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def load(cur, target=None):
    """{target: {"movers": {old: (name, school)}, ...}} plus the rows and
    facts to judge them. Read only."""
    if not _hasTable(cur, "profile_school_merge"):
        return None
    veto = set()
    if _hasTable(cur, "profile_school_veto"):
        cur.execute("SELECT old_id FROM profile_school_veto")
        veto = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT old_id, new_id, name, school FROM profile_school_merge")
    groups = defaultdict(dict)
    for old, new, name, school in cur.fetchall():
        if old in veto or (target is not None and new != target):
            continue
        groups[new][old] = (name, school)
    if not groups:
        return {}, {}, {}, {}, set()
    targets = sorted(groups)
    movers = sorted({o for g in groups.values() for o in g})
    everyone = sorted(set(targets) | set(movers))

    # every logged move, by (sport, result_id) -> the person it came from
    cur.execute("""SELECT sport, result_id, from_person FROM person_link_log
                   WHERE rule = %s AND from_person = ANY(%s)""", (RULE, movers))
    moved = {(s, rid): fp for s, rid, fp in cur.fetchall()}

    mover_set = set(movers)
    rows = defaultdict(list)
    careerish = set()        # movers a row or a profile shows were careers
    for sport, table, tf in (("XC", "results", False), ("TF", "results_tf", True)):
        extra = ("r.meet_id, lower(btrim(r.event_short))" if tf
                 else "NULL::bigint, NULL::text")
        cur.execute(f"""
            SELECT r.result_id, r.person_id, r.athlete_id, r.source, r.date,
                   r.grade, {extra}, r.school
            FROM   {table} r
            WHERE  r.person_id = ANY(%s)""", (everyone,))
        for rid, pid, aid, src, d, g, meet, ev, sch in cur.fetchall():
            owner = moved.get((sport, rid), pid)
            rows[owner].append(L.Row(d, sport, g, meet, ev, sch))
            if owner in mover_set and not (src == "anet" and aid == owner):
                careerish.add(owner)
    if _hasTable(cur, "profile_school_athletes"):
        cur.execute("""SELECT DISTINCT from_person FROM profile_school_athletes
                       WHERE from_person = ANY(%s) AND athlete_id <> from_person""",
                    (movers,))
        careerish |= {r[0] for r in cur.fetchall()}

    cur.execute("""
        SELECT a.athlete_id, a.gender,
               concat_ws(' ', btrim(a.first_name), btrim(a.last_name))
        FROM   athletes a WHERE a.athlete_id = ANY(%s)""", (everyone,))
    facts = defaultdict(lambda: ([], None))
    for aid, g, nm in cur.fetchall():
        gs, name = facts[aid]
        if g in ("M", "F") and g not in gs:
            gs = gs + [g]
        facts[aid] = (gs, name or nm)
    return groups, rows, facts, careerish, set(targets)


def judgeWritten(groups, rows, facts, careerish, all_groups=False):
    """[(target, verdict, members, keys, is_career_group)] for every written
    group decideGroup now refuses."""
    out = []
    for t, movs in sorted(groups.items()):
        is_career = any(o in careerish for o in movs)
        if not is_career and not all_groups:
            continue
        members = []
        # the target is a career by construction when careers were joined;
        # in a stray-only group it was the member with the most rows
        for pid in [t] + sorted(movs):
            gs, nm = facts[pid]
            name = nm or (movs.get(pid) or ("", ""))[0]
            lone = pid != t and pid not in careerish
            members.append(L.Member(pid, name, gs, lone,
                                    sorted(rows.get(pid, []),
                                           key=lambda r: str(r.date))))
        schools = set()
        for _old, (name, school) in movs.items():
            school = school or ""
            schools.add(school.strip().lower())
            schools |= {s.strip().lower() for s in school.split(", ") if s.strip()}
        nm = normName(next((n for n, _s in movs.values() if n), "") or "")
        keys = [(nm, sk) for sk in sorted(schools) if sk]
        v = L.decideGroup(members, allow_careers=True, keys=keys)
        if v.reason != L.MATCH:
            out.append((t, v, members, keys, is_career))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, default=60,
                    help="how many refused groups to print in full")
    ap.add_argument("--all", action="store_true",
                    help="also audit groups that joined strays only")
    ap.add_argument("--target", type=int, help="only the group merged into this person")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn:
        conn.rollback()          # SET TRANSACTION must open the transaction
        with conn.cursor() as cur:
            # ! READ ONLY, and rolled back below: this script cannot write
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '900s'")
            got = load(cur, a.target)
        conn.rollback()
    if got is None:
        print("[audit-career-links] no profile_school_merge table: nothing written")
        return
    groups, rows, facts, careerish, _t = got
    n_groups = len(groups)
    n_career = sum(1 for movs in groups.values() if any(o in careerish for o in movs))
    refused = judgeWritten(groups, rows, facts, careerish, all_groups=a.all)
    print(f"[audit-career-links] {n_groups:,} written groups "
          f"({sum(len(m) for m in groups.values()):,} profiles moved); "
          f"{n_career:,} joined two careers or more"
          + ("" if a.all else " (audited; --all adds the stray-only groups)"))
    by = Counter(v.reason for _t, v, _m, _k, _c in refused)
    n_prof = Counter()
    for _t, v, members, _k, _c in refused:
        n_prof[v.reason] += len(members) - 1
    print(f"[audit-career-links] {len(refused):,} would now be REFUSED "
          f"({sum(n_prof.values()):,} profiles):")
    for why, n in by.most_common():
        tag = "new check" if why in NEW_REASONS else "old check, today's rows"
        print(f"    {n:>7,} groups  {n_prof[why]:>7,} profiles  {why}  [{tag}]")
    for t, v, members, keys, is_career in refused[:a.show]:
        print(f"\n  {v.reason}  target /athlete/{t}  "
              f"{', '.join(sk for _n, sk in keys)[:60]!r}"
              f"{'' if is_career else '  (strays only)'}")
        for m in members:
            seasons = sorted({L._season(r.date) for r in m.rows} - {None})
            span = f"{seasons[0]}-{seasons[-1]}" if seasons else "-"
            grades = sorted({str(r.grade) for r in m.rows if r.grade})[:6]
            print(f"    /athlete/{m.pid} {m.name or '?'} "
                  f"{'career' if not m.lone else 'stray'} {len(m.rows)} rows "
                  f"seasons {span} grades {','.join(grades) or '-'}"
                  f"{'  TARGET' if m.pid == t else ''}")
    print("\n[audit-career-links] REPORT ONLY -- nothing was changed. To put a "
          "group back: scripts/link_profile_school.py --undo <old_id>")


if __name__ == "__main__":
    main()
