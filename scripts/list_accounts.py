#!/usr/bin/env python3
"""
list_accounts.py -- the site's accounts, and the profiles each one has
claimed (and follows). READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/list_accounts.py                    # every account + its claims
    /srv/venv/bin/python scripts/list_accounts.py --email gmail      # emails containing 'gmail'
    /srv/venv/bin/python scripts/list_accounts.py --claims           # only accounts with a claim
    /srv/venv/bin/python scripts/list_accounts.py --follows          # also what each one follows
    /srv/venv/bin/python scripts/list_accounts.py --person 26196653  # who claims / follows one runner

★ WHY (owner, 2026-10-27: "how can I check accounts list and the profiles
  they're attached to?"). Accounts live in racecast/accounts.py's tables:
  account (email, name, role, created, last login, deleted), account_claim
  (a runner, a parent or a coach saying a profile -- or a team -- is theirs:
  kind, person_id or school/state/level, status, verified) and
  racecast/follows.py's account_follow. A claimed person_id is shown with the
  name and school on its athletes rows, and followed through person_redirect
  when a merge has since moved it.

! EMAILS ARE PRINTED. This is the owner's own tool on the server; do not
  paste its output anywhere public.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _has(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0] is not None


def _who(cur, pids):
    """{person_id: 'First Last (school, ...)'}, following redirects."""
    pids = sorted({p for p in pids if p})
    if not pids:
        return {}
    moved = {}
    if _has(cur, "person_redirect"):
        cur.execute("SELECT old_id, new_id FROM person_redirect WHERE old_id = ANY(%s)", (pids,))
        moved = dict(cur.fetchall())
    look = sorted(set(pids) | set(moved.values()))
    cur.execute("""SELECT person_id,
                          min(btrim(concat_ws(' ', first_name, last_name))),
                          string_agg(DISTINCT school, ', ')
                   FROM   athletes WHERE person_id = ANY(%s) GROUP BY 1""", (look,))
    names = {p: f"{n} ({s})" for p, n, s in cur.fetchall()}
    out = {}
    for p in pids:
        if p in moved:
            out[p] = f"-> {moved[p]} {names.get(moved[p], '(no profile)')} [merged]"
        else:
            out[p] = names.get(p, "(no profile with this id)")
    return out


def _target(row):
    kind, pid, school, state, level = row
    if pid:
        return f"person {pid}"
    return " / ".join(x for x in (school, state, level) if x) or "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", help="only emails containing this")
    ap.add_argument("--claims", action="store_true", help="only accounts with a claim")
    ap.add_argument("--follows", action="store_true", help="also list follows")
    ap.add_argument("--person", type=int, help="who claims or follows this person_id")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        if not _has(cur, "account"):
            print("no account table on this database")
            return
        if a.person:
            for table in ("account_claim", "account_follow"):
                if not _has(cur, table):
                    continue
                cur.execute(f"""SELECT x.account_id, ac.email, x.kind, x.created_at
                                FROM {table} x JOIN account ac ON ac.id = x.account_id
                                WHERE x.person_id = %s ORDER BY x.created_at""", (a.person,))
                rows = cur.fetchall()
                print(f"{table}: {len(rows)}")
                for aid, email, kind, at in rows:
                    print(f"    account {aid} {email}  {kind}  {str(at)[:16]}")
            return
        cur.execute("""SELECT id, email, name, role, created_at, last_login_at, deleted_at
                       FROM account
                       WHERE (%(e)s::text IS NULL OR email ILIKE '%%' || %(e)s || '%%')
                       ORDER BY created_at""", {"e": a.email})
        accounts = cur.fetchall()
        claims, follows = {}, {}
        if _has(cur, "account_claim"):
            cur.execute("""SELECT account_id, kind, person_id, school, state, level, status,
                                  verified_at, created_at
                           FROM account_claim ORDER BY created_at""")
            for aid, *rest in cur.fetchall():
                claims.setdefault(aid, []).append(rest)
        if a.follows and _has(cur, "account_follow"):
            cur.execute("""SELECT account_id, kind, person_id, school, state, level
                           FROM account_follow ORDER BY created_at""")
            for aid, *rest in cur.fetchall():
                follows.setdefault(aid, []).append(rest)
        who = _who(cur, [c[1] for cs in claims.values() for c in cs]
                   + [f[1] for fs in follows.values() for f in fs])
        shown = 0
        for aid, email, name, role, created, last, deleted in accounts:
            mine = claims.get(aid, [])
            if a.claims and not mine:
                continue
            shown += 1
            print(f"\naccount {aid}  {email}  name={name!r}  role={role}  "
                  f"created {str(created)[:10]}  last login {str(last)[:10] if last else '-'}"
                  + ("  DELETED" if deleted else ""))
            for kind, pid, school, state, level, status, verified, at in mine:
                print(f"    claims {kind:<8} {_target((kind, pid, school, state, level)):<16} "
                      f"{who.get(pid, '') if pid else ''}  status={status}"
                      f"{'  verified ' + str(verified)[:10] if verified else ''}  ({str(at)[:10]})")
            for kind, pid, school, state, level in follows.get(aid, []):
                print(f"    follows {kind:<7} {_target((kind, pid, school, state, level)):<16} "
                      f"{who.get(pid, '') if pid else ''}")
        n_claims = sum(len(v) for v in claims.values())
        print(f"\n{shown} of {len(accounts)} accounts shown; {n_claims} claims in all")
        conn.rollback()


if __name__ == "__main__":
    main()
