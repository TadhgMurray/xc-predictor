# Project: xc-predictor / racecast
# File:    admin_accounts.py
# Purpose: The owner's list of accounts at /account/admin/accounts: every
#          account (email, name, role, signed up, last login, deleted), the
#          profiles each one has CLAIMED (a runner, a parent, a coach -- the
#          runner's name and school, followed through a merge redirect) and
#          how many athletes and teams it follows. Admins only
#          (uploads.requireAdmin: signed out -> login, anyone else -> 404).
#
# ★ WHY (owner, 2026-10-27: "Can I get a tab on the admin website for
#   this?", after scripts/list_accounts.py). The same tables the script
#   reads -- accounts.py's account / account_claim, follows.py's
#   account_follow -- read-only; nothing on the page changes an account.
#
# ! UNDER /account, SO PRIVATE: no-store and not crawled like every other
#   /account route, and the page carries noindex too.

from flask import Blueprint, render_template, request

import accounts as AC
from uploads import requireAdmin

bp = Blueprint("admin_accounts", __name__)

PAGE_MAX = 500          # accounts on one page; the search narrows past that


def _has(cur, table):
    cur.execute("SELECT to_regclass(%s) AS t", (table,))
    row = cur.fetchone()
    return bool(row and row["t"])


def accountsPage(cur, q="", claims_only=False, limit=PAGE_MAX):
    """-> (accounts, totals). accounts: dicts with the account's columns,
    `claims` (each with `who`: the runner's name and school, or where a
    merge sent them) and `n_follows`. q matches email or name."""
    q = (q or "").strip()
    cur.execute("""
        SELECT a.id, a.email, a.name, a.role, a.created_at, a.last_login_at, a.deleted_at
        FROM   account a
        WHERE  (%(q)s = '' OR a.email ILIKE '%%' || %(q)s || '%%'
                           OR a.name  ILIKE '%%' || %(q)s || '%%')
          AND  (NOT %(co)s OR EXISTS (SELECT 1 FROM account_claim c WHERE c.account_id = a.id))
        ORDER  BY a.created_at DESC
        LIMIT  %(lim)s""", {"q": q, "co": bool(claims_only), "lim": limit})
    accounts = [dict(r, claims=[], n_follows=0) for r in cur.fetchall()]
    by_id = {a["id"]: a for a in accounts}
    ids = list(by_id)
    if ids and _has(cur, "account_claim"):
        cur.execute("""SELECT account_id, kind, person_id, school, state, level, status,
                              verified_at, verified_by, created_at
                       FROM account_claim WHERE account_id = ANY(%s)
                       ORDER BY created_at""", (ids,))
        for c in cur.fetchall():
            by_id[c["account_id"]]["claims"].append(dict(c))
    if ids and _has(cur, "account_follow"):
        cur.execute("""SELECT account_id, count(*) AS n FROM account_follow
                       WHERE account_id = ANY(%s) GROUP BY 1""", (ids,))
        for r in cur.fetchall():
            by_id[r["account_id"]]["n_follows"] = r["n"]
    who = _who(cur, [c["person_id"] for a in accounts for c in a["claims"]])
    for a in accounts:
        for c in a["claims"]:
            c["who"] = who.get(c["person_id"]) if c["person_id"] else None
    cur.execute("""SELECT count(*) AS n,
                          count(*) FILTER (WHERE deleted_at IS NULL) AS live,
                          count(*) FILTER (WHERE last_login_at > now() - interval '30 days') AS recent
                   FROM account""")
    totals = dict(cur.fetchone())
    totals["claims"] = 0
    if _has(cur, "account_claim"):
        cur.execute("SELECT count(*) AS n FROM account_claim")
        totals["claims"] = cur.fetchone()["n"]
    return accounts, totals


def _who(cur, pids):
    """{person_id: {"person_id", "name", "schools", "merged_into"}} for the
    claimed ids; a merged id points at where its rows went."""
    pids = sorted({p for p in pids if p})
    if not pids:
        return {}
    moved = {}
    if _has(cur, "person_redirect"):
        cur.execute("SELECT old_id, new_id FROM person_redirect WHERE old_id = ANY(%s)", (pids,))
        moved = {r["old_id"]: r["new_id"] for r in cur.fetchall()}
    look = sorted(set(pids) | set(moved.values()))
    cur.execute("""SELECT person_id,
                          min(btrim(concat_ws(' ', first_name, last_name))) AS name,
                          string_agg(DISTINCT school, ', ') AS schools
                   FROM   athletes WHERE person_id = ANY(%s) GROUP BY 1""", (look,))
    found = {r["person_id"]: r for r in cur.fetchall()}
    out = {}
    for p in pids:
        target = moved.get(p, p)
        row = found.get(target) or {}
        out[p] = {"person_id": target, "name": row.get("name"), "schools": row.get("schools"),
                  "merged_into": moved.get(p)}
    return out


@bp.route("/account/admin/accounts")
def admin_accounts():
    sess, go = requireAdmin()
    if go:
        return go
    q = request.args.get("q", "")[:100]
    claims_only = request.args.get("claims") == "1"
    with AC._db() as (conn, cur):
        if not AC.tablesReady(cur):
            accounts, totals = [], {"n": 0, "live": 0, "recent": 0, "claims": 0}
        else:
            accounts, totals = accountsPage(cur, q, claims_only)
        conn.commit()
    return render_template("admin_accounts.html", accounts=accounts, totals=totals, q=q,
                           claims_only=claims_only, page_max=PAGE_MAX)
