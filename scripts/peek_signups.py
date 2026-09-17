# Project: xc-predictor / scripts
# File:    peek_signups.py
# Purpose: who has signed up and what they have reported. Read-only, no
#          arguments, seconds to run.
#
#     python scripts/peek_signups.py
#
# ! EVERY BLOCK IS INDEPENDENT AND SURVIVES A MISSING TABLE. issue_reports is
#   created lazily by the report route, so on a server where nobody has ever
#   filed one the table does not exist -- which must print "not available"
#   rather than take the accounts half down with it. The rollback is why: one
#   failed statement poisons the transaction for every later one.
#
# ⚠ IT PRINTS EMAIL ADDRESSES, because knowing who signed up is the point.
#   Treat the output as personal data: do not paste it anywhere public.
# what people have actually done: accounts, and the issues they reported
import sys
sys.path[:0] = ["scripts", "racecast", "engine"]
from database import getConn


def show(cur, title, sql, args=()):
    try:
        cur.execute(sql, args)
        rows = cur.fetchall()
    except Exception as exc:                                  # noqa: BLE001
        cur.connection.rollback()
        print(f"\n== {title} ==\n  (not available: {exc})")
        return
    print(f"\n== {title} ==")
    if not rows:
        print("  (none)")
    for r in rows:
        print("  " + "  ".join("-" if v is None else str(v) for v in r))


with getConn() as conn:
    with conn.cursor() as cur:
        show(cur, "SIGNUPS", """
            SELECT count(*) AS accounts,
                   count(*) FILTER (WHERE deleted_at IS NULL) AS live,
                   count(*) FILTER (WHERE last_login_at IS NOT NULL) AS logged_in,
                   count(*) FILTER (WHERE created_at > now() - interval '7 days') AS last_7d,
                   count(*) FILTER (WHERE google_sub IS NOT NULL) AS via_google
            FROM account""")
        show(cur, "SIGNUPS BY ROLE", """
            SELECT role, count(*) FROM account WHERE deleted_at IS NULL
            GROUP BY role ORDER BY 2 DESC""")
        show(cur, "NEWEST 20 ACCOUNTS", """
            SELECT created_at::date, COALESCE(role,'-'),
                   COALESCE(name,'-'), email,
                   COALESCE(last_login_at::date::text,'never')
            FROM account WHERE deleted_at IS NULL
            ORDER BY created_at DESC LIMIT 20""")
        show(cur, "REPORTS", """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE NOT resolved) AS open,
                   count(*) FILTER (WHERE created_at > now() - interval '7 days') AS last_7d
            FROM issue_reports""")
        show(cur, "OPEN REPORTS, NEWEST FIRST", """
            SELECT report_id, created_at::date, kind,
                   COALESCE(page,'-'), COALESCE(email,'-'),
                   left(replace(detail, chr(10), ' '), 90)
            FROM issue_reports WHERE NOT resolved
            ORDER BY created_at DESC LIMIT 30""")
