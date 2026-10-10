# Project: xc-predictor / racecast
# File:    follow_alerts.py
# Purpose: The follow digest (owner, 2026-10-10, approved): after the nightly
#          update, for every followed athlete and team, what is new since the
#          last alert -- rated races, PRs, season bests, breakouts -- mailed as
#          ONE digest per person, at the cadence they chose.
#
#     python racecast/follow_alerts.py               # what would be sent (no writes)
#     python racecast/follow_alerts.py --send        # find, record and mail
#     python racecast/follow_alerts.py --send --prewarm   # and fill My page's cache
#
#   Pipeline: an OPTIONAL step, off unless XCP_ALERTS=1 (deploy/nightly_update.sh
#   step 13g_follow_alerts, deploy/run_pipeline.sh the same), after the
#   breakouts build it reads.
#
# ★ "NEW" IS "NOT ALERTED BEFORE", RECORDED, NOT INFERRED FROM DATES. Every
#   thing a digest could say has a key -- 'race:XC:<result_id>' for one
#   athlete's race, 'team:<school>|<state>|<level>:XC:<meet_id>' for a
#   team's meet -- and alert_item holds (account, key) once, as its primary
#   key. A key already there is never mailed again, whatever the data does
#   afterwards: the same race re-rated by a full pipeline run, a breakout
#   that appears a night late, a second run in one day. Race dates would not
#   do: results land days after they are run, and a late-scraped race is
#   still news.
#
# ★ A NEW FOLLOW STARTS QUIET. The first scan after a follow records what is
#   already there as 'baseline' without mailing it (account_follow.primed_at):
#   following a senior must not mail four years of races. An account whose
#   setting is off has its items recorded as 'muted', so turning mail back on
#   sends what is new from then, not the backlog.
#
# ★ ONE DIGEST, AT MOST ONCE A DAY ('daily') OR ONCE A WEEK ('weekly'). The
#   items wait as 'pending' until the account is due; a digest takes all of
#   them. The day and the week are the two cadences the owner named, not a
#   tuning: the data itself changes once a night.
#
# ! NEVER THE SAME ALERT TWICE, EVEN ON A CRASH. A digest's items are marked
#   'sent' and committed BEFORE the mail goes; a provider error puts them
#   back to 'pending' for the next run. A crash between the two leaves them
#   'sent' -- one digest lost is the failure this design accepts, a duplicate
#   is the one it does not.
#
# ! NO EMAIL ADDRESS IN A LOG. Accounts are named by id; the address only
#   leaves this process as the mail's recipient.
#
# ! ONE RUN AT A TIME: a Postgres advisory lock (the pipeline's flock covers
#   the pipeline; this covers a hand run beside it).
import argparse
import datetime
import json
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import accounts as AC                                   # noqa: E402
import follows as F                                     # noqa: E402

# ★ THE PROVIDERS' OWN SEND RATE. Resend's default API limit is two requests
#   a second; Postmark's is higher. Half a second between digests stays under
#   the lower one, so a big night never trips a 429.
SEND_PAUSE_S = 0.5
WEEK = datetime.timedelta(days=7)
# the advisory lock's key: the bytes of the job's name, so it is stable and greppable
_LOCK_KEY = int.from_bytes(b"rcalerts", "big") & 0x7FFFFFFFFFFFFFFF


# ------------------------------------------------------------------ #
#  the pure rules (tests/test_follow_alerts.py)
# ------------------------------------------------------------------ #

def seasonBests(rows):
    """{result_id} of the races that beat every EARLIER race's rating that
    season (same person, sport, pool), with at least one earlier race --
    the season best ("SR") the athlete page flags. Same-day races do not
    count as earlier."""
    out = set()
    by = {}
    for r in rows:
        by.setdefault((r["person_id"], r["sport"], r.get("pool")), []).append(r)
    for rs in by.values():
        rs.sort(key=lambda r: (str(r["race_date"]), r["result_id"]))
        best, best_day, prior_day_best = None, None, None
        for r in rs:
            day = str(r["race_date"])
            if day != best_day:
                prior_day_best = best          # best before this day
                best_day = day
            v = float(r["speed_rating"])
            if prior_day_best is not None and v > prior_day_best:
                out.add(r["result_id"])
            best = v if best is None else max(best, v)
    return out


def raceItem(r, prs, jumps, sbs):
    """One athlete's race as an alert item (key, payload)."""
    flags = {}
    pr = prs.get((r["sport"], r["result_id"]))
    if pr:
        flags["pr"] = {"prev": pr.get("prev_best")}
    if r["result_id"] in sbs:
        flags["sr"] = True
    jp = jumps.get((r["sport"], r["result_id"]))
    if jp and jp.get("jump") is not None:
        flags["jump"] = round(float(jp["jump"]), 1)
    payload = {"type": "race", "sport": r["sport"], "person_id": r["person_id"],
               "name": r.get("name"), "pool": r.get("pool"), "date": str(r["race_date"])[:10],
               "meet_name": r.get("meet_name"), "distance": r.get("distance"),
               "time": r.get("time_seconds"), "rating": round(float(r["speed_rating"]), 1),
               "href": r.get("race_href"), "flags": flags}
    return f"race:{r['sport']}:{r['result_id']}", payload


def teamItems(subject, rows, prs, jumps):
    """A followed team's new races, one item per (sport, meet): how many of
    its runners were rated there, the best of them, and the PRs and
    breakouts among them."""
    by = {}
    for r in rows:
        by.setdefault((r["sport"], r["meet_id"]), []).append(r)
    out = []
    for (sport, meet_id), rs in by.items():
        best = max(rs, key=lambda r: float(r["speed_rating"]))
        n_pr = sum(1 for r in rs if (sport, r["result_id"]) in prs)
        n_jump = sum(1 for r in rs if (sport, r["result_id"]) in jumps)
        day = min(str(r["race_date"])[:10] for r in rs)
        payload = {"type": "team", "sport": sport, "meet_id": meet_id, "date": day,
                   "meet_name": best.get("meet_name"), "n_rated": len({r["person_id"] for r in rs}),
                   "best": {"person_id": best["person_id"], "name": best.get("name"),
                            "time": best.get("time_seconds"),
                            "rating": round(float(best["speed_rating"]), 1)},
                   "n_pr": n_pr, "n_jump": n_jump, "href": best.get("race_href")}
        out.append((f"{subject}:{sport}:{meet_id}", payload))
    return out


def isDue(cadence, last_sent_at, now):
    """Whether an account with pending items gets a digest this run."""
    if cadence == "off":
        return False
    if last_sent_at is None:
        return True
    if cadence == "weekly":
        return now - last_sent_at >= WEEK
    return last_sent_at.date() < now.date()


def digest(groups, origin, unsub, account_name=None, today=None):
    """(subject, text, html) for one account: alert_email.compose, the
    sentences a person would write. groups: [(title, href, [payload], kind)]."""
    import alert_email
    return alert_email.compose(groups, origin, unsub, account_name, today)


# ------------------------------------------------------------------ #
#  the database half
# ------------------------------------------------------------------ #

def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s) AS t", (f"public.{name}",))
    return bool((AC._one(cur) or {}).get("t"))


def loadFollows(cur):
    cur.execute("""SELECT f.id, f.account_id, f.kind, f.person_id, f.school, f.state, f.level,
                          f.primed_at, COALESCE(p.cadence, %s) AS cadence, p.last_sent_at
                   FROM   account_follow f
                   JOIN   account a ON a.id = f.account_id AND a.deleted_at IS NULL
                   LEFT JOIN alert_pref p ON p.account_id = f.account_id
                   ORDER  BY f.account_id, f.created_at, f.id""", (F.DEFAULT_CADENCE,))
    return [dict(r) for r in cur.fetchall()]


def _breakoutsFor(cur, pairs):
    """({(sport, rid): row} PRs, {(sport, rid): row} jumps) for result ids."""
    prs, jumps = {}, {}
    if not pairs or not _tableExists(cur, "breakout_rows"):
        return prs, jumps
    for sport in ("XC", "TF"):
        ids = sorted({rid for sp, rid in pairs if sp == sport})
        if not ids:
            continue
        cur.execute("""SELECT kind, result_id, prev_best, jump FROM breakout_rows
                       WHERE sport = %s AND result_id = ANY(%s)""", (sport, ids))
        for r in cur.fetchall():
            (prs if r["kind"] == "pr" else jumps)[(sport, r["result_id"])] = dict(r)
    return prs, jumps


def _fill(cur, rows):
    import breakouts as B
    for sport in ("XC", "TF"):
        B._fillMeets(cur, sport, [r for r in rows if r["sport"] == sport])
    B._fillNames(cur, rows)
    return rows


_RR_COLS = """result_id, person_id, sport, pool, race_date, speed_rating, time_seconds,
              distance, meet_id, div_id, event_id, school, state"""


def athleteRows(cur, person_ids, year):
    if not person_ids:
        return []
    cur.execute(f"""SELECT {_RR_COLS} FROM ranking_results
                    WHERE person_id = ANY(%s) AND year = %s AND speed_rating IS NOT NULL""",
                (sorted(person_ids), year))
    return [dict(r) for r in cur.fetchall()]


def teamRows(cur, school, state, level, year):
    from school_identity import primaryState, stateFilterSql
    sf, sfp = stateFilterSql("rr", state, primaryState(school), school)
    lvl = ""
    if level:
        lvl = "AND rr.pool LIKE %(lvl)s"
        sfp["lvl"] = f"{level}\\_%"
    cur.execute(f"""SELECT {', '.join('rr.' + c.strip() for c in _RR_COLS.split(','))}
                    FROM ranking_results rr
                    WHERE rr.school = %(school)s AND rr.year = %(year)s
                      AND rr.speed_rating IS NOT NULL {sf} {lvl}""",
                {"school": school, "year": year, **sfp})
    return [dict(r) for r in cur.fetchall()]


def findItems(cur, follows, year):
    """{follow_id: [(key, subject, payload)]} for every follow."""
    out = {f["id"]: [] for f in follows}
    pids = {f["person_id"] for f in follows if f["kind"] == "athlete"}
    arows = _fill(cur, athleteRows(cur, pids, year))
    prs, jumps = _breakoutsFor(cur, [(r["sport"], r["result_id"]) for r in arows])
    sbs = seasonBests(arows)
    by_pid = {}
    for r in arows:
        by_pid.setdefault(r["person_id"], []).append(r)
    teams = {}
    for f in follows:
        if f["kind"] == "athlete":
            subj = F.subjectKey(f)
            for r in by_pid.get(f["person_id"], []):
                key, payload = raceItem(r, prs, jumps, sbs)
                out[f["id"]].append((key, subj, payload))
        else:
            tk = (f["school"], f.get("state"), f.get("level"))
            if tk not in teams:
                rows = _fill(cur, teamRows(cur, *tk, year))
                tp, tj = _breakoutsFor(cur, [(r["sport"], r["result_id"]) for r in rows])
                teams[tk] = teamItems(F.subjectKey(f), rows, tp, tj)
            subj = F.subjectKey(f)
            out[f["id"]] += [(k, subj, p) for k, p in teams[tk]]
    return out


def recordItems(cur, follows, found, year):
    """Insert every found item once per account; returns the number of new
    pending ones. Unprimed follows record as 'baseline', an account with
    alerts off as 'muted'."""
    n_new = 0
    for f in follows:
        status = ("baseline" if f.get("primed_at") is None
                  else "muted" if f.get("cadence") == "off" else "pending")
        for key, subj, payload in found.get(f["id"], []):
            cur.execute("""INSERT INTO alert_item (account_id, item_key, season, subject, payload, status,
                                                   sent_at)
                           VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s = 'pending' THEN NULL ELSE now() END)
                           ON CONFLICT (account_id, item_key) DO NOTHING""",
                        (f["account_id"], key, year, subj, json.dumps(payload, default=str),
                         status, status))
            if status == "pending":
                n_new += cur.rowcount
        if f.get("primed_at") is None:
            cur.execute("UPDATE account_follow SET primed_at = now() WHERE id = %s", (f["id"],))
    return n_new


def _titles(cur, follows):
    """{subject: (title, href)} for the digest's headings."""
    from school_identity import schoolHref, schoolLabelIn
    from rankings import nameLateral
    pids = sorted({f["person_id"] for f in follows if f["kind"] == "athlete"})
    names = {}
    if pids:
        cur.execute(f"""SELECT p.person_id, a.name FROM unnest(%s::bigint[]) AS p(person_id)
                        {nameLateral('p')}""", (pids,))
        names = {r["person_id"]: r["name"] for r in cur.fetchall()}
    out = {}
    for f in follows:
        s = F.subjectKey(f)
        if f["kind"] == "athlete":
            out[s] = (names.get(f["person_id"]) or "An athlete you follow", f"/athlete/{f['person_id']}")
        else:
            out[s] = (schoolLabelIn(f["school"], f.get("state")) if f.get("state") else f["school"],
                      schoolHref(f["school"], f.get("state")))
    return out


def sendDigests(cur, conn, follows, now, origin, send=True, log=print):
    """Mail every due account its pending items. Returns (sent, failed)."""
    titles = _titles(cur, follows)
    order = {}
    for f in follows:
        order.setdefault(f["account_id"], []).append(F.subjectKey(f))
    cur.execute("""SELECT i.account_id, i.item_key, i.subject, i.payload, a.email, a.name AS account_name,
                          COALESCE(p.cadence, %s) AS cadence, p.last_sent_at
                   FROM   alert_item i
                   JOIN   account a ON a.id = i.account_id AND a.deleted_at IS NULL
                   LEFT JOIN alert_pref p ON p.account_id = i.account_id
                   WHERE  i.status = 'pending'
                   ORDER  BY i.account_id, i.found_at""", (F.DEFAULT_CADENCE,))
    by = {}
    for r in cur.fetchall():
        by.setdefault(r["account_id"], []).append(dict(r))
    conn.commit()
    sent = failed = 0
    for aid, items in by.items():
        if not isDue(items[0]["cadence"], items[0]["last_sent_at"], now):
            continue
        groups = []
        for subj in order.get(aid, []) + sorted({i["subject"] for i in items}):
            mine = [i for i in items if i["subject"] == subj]
            if not mine or any(g[4] == subj for g in groups):
                continue
            title, href = titles.get(subj, (subj.split(":", 1)[-1], "/account/me"))
            groups.append((title, href, [i["payload"] if isinstance(i["payload"], dict)
                                         else json.loads(i["payload"]) for i in mine],
                           subj.split(":", 1)[0], subj))
        subject, text, html = digest([g[:4] for g in groups], origin, F.unsubUrl(aid, origin),
                                     items[0].get("account_name"), now.date())
        keys = [i["item_key"] for i in items]
        if not send:
            log(f"  account {aid}: would send '{subject}' ({len(keys)} items)")
            continue
        # ! MARKED SENT FIRST (the header's "never twice")
        cur.execute("""UPDATE alert_item SET status = 'sent', sent_at = now()
                       WHERE account_id = %s AND item_key = ANY(%s) AND status = 'pending'""", (aid, keys))
        conn.commit()
        unsub = F.unsubUrl(aid, origin)
        ok = AC.sendMail(items[0]["email"], subject, text, log_as=f"account {aid}", html=html,
                         headers={"List-Unsubscribe": f"<{unsub}>",
                                  "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        if ok:
            cur.execute("""INSERT INTO alert_pref (account_id, cadence, last_sent_at) VALUES (%s, %s, now())
                           ON CONFLICT (account_id) DO UPDATE SET last_sent_at = now()""",
                        (aid, items[0]["cadence"]))
            sent += 1
            log(f"  account {aid}: sent ({len(keys)} items)")
        else:
            cur.execute("""UPDATE alert_item SET status = 'pending', sent_at = NULL
                           WHERE account_id = %s AND item_key = ANY(%s) AND status = 'sent'""", (aid, keys))
            failed += 1
            log(f"  account {aid}: FAILED, kept for the next run")
        conn.commit()
        time.sleep(SEND_PAUSE_S)
    return sent, failed


def writeSnapshots(cur, follows, claims, today, year):
    """Tonight's rating and ranks for every followed or claimed subject
    (my_page's movement line reads them)."""
    pids = {f["person_id"] for f in follows if f["kind"] == "athlete"}
    pids |= {c["person_id"] for c in claims if c.get("person_id")}
    n = 0
    if pids and _tableExists(cur, "season_rank"):
        cur.execute("""INSERT INTO follow_snapshot (subject, sport, pool, year, taken_on, rating, nation, state_rank)
                       SELECT 'athlete:' || s.person_id, s.sport, s.pool, s.year, %s, s.mean_rating,
                              r.nation, r.state_rank
                       FROM   athlete_season s
                       LEFT JOIN season_rank r USING (person_id, pool, sport, year)
                       WHERE  s.person_id = ANY(%s) AND s.year = %s AND s.mean_rating IS NOT NULL
                       ON CONFLICT (subject, sport, pool, year, taken_on) DO UPDATE
                           SET rating = EXCLUDED.rating, nation = EXCLUDED.nation,
                               state_rank = EXCLUDED.state_rank""", (today, sorted(pids), year))
        n += cur.rowcount
    teams = {(f["school"], f.get("state")) for f in follows if f["kind"] == "team"}
    teams |= {(c["school"], c.get("state")) for c in claims if c.get("school")}
    if teams and _tableExists(cur, "team_season"):
        from school_identity import primaryState
        for school, state in sorted(teams, key=lambda t: (t[0], t[1] or "")):
            state = state or primaryState(school)
            cur.execute("""INSERT INTO follow_snapshot (subject, sport, pool, year, taken_on, rating, nation, state_rank)
                           SELECT 'team:' || %s || '|' || COALESCE(%s, '') || '|' || t.pool, t.sport, t.pool,
                                  t.year, %s, max(t.top5_mean),
                                  max(t.rank) FILTER (WHERE t.scope = 'usa'),
                                  max(t.rank) FILTER (WHERE t.scope <> 'usa')
                           FROM   team_season t
                           WHERE  t.span = 'season' AND t.school = %s AND t.year = %s
                             AND  (%s::text IS NULL OR t.state = %s)
                           GROUP  BY t.pool, t.sport, t.year
                           ON CONFLICT (subject, sport, pool, year, taken_on) DO UPDATE
                               SET rating = EXCLUDED.rating, nation = EXCLUDED.nation,
                                   state_rank = EXCLUDED.state_rank""",
                        (school, state, today, school, year, state, state))
            n += cur.rowcount
    # ! ONLY THE SEASON THE MOVEMENT LINE COMPARES IN is kept
    cur.execute("DELETE FROM follow_snapshot WHERE year < %s", (year,))
    return n


def prewarm(cur, conn, follows, claims, today, log=print):
    """My page's next-meet rows for everyone followed or claimed, so the
    first view of the day reads instead of computing."""
    import my_page as M
    pids = {f["person_id"] for f in follows if f["kind"] == "athlete"}
    pids |= {c["person_id"] for c in claims if c.get("person_id")}
    n = 0
    for pid in sorted(pids):
        try:
            season = M.latestSeason(cur, pid)
            if season:
                M.athleteNext(cur, pid, season, today)
                conn.commit()
                n += 1
        except Exception as exc:                        # noqa: BLE001
            conn.rollback()
            log(f"  prewarm athlete {pid}: {type(exc).__name__}: {exc}")
    teams = {(f["school"], f.get("state"), f.get("level")) for f in follows if f["kind"] == "team"}
    teams |= {(c["school"], c.get("state"), c.get("level")) for c in claims if c.get("school")}
    for school, state, level in sorted(teams, key=lambda t: tuple(x or "" for x in t)):
        try:
            from school_identity import primaryState
            st = state or primaryState(school)
            M.teamNext(cur, school, st, level, M.teamBoards(cur, school, st, level), today)
            conn.commit()
            n += 1
        except Exception as exc:                        # noqa: BLE001
            conn.rollback()
            log(f"  prewarm team {school}: {type(exc).__name__}: {exc}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="The follow digest.")
    ap.add_argument("--send", action="store_true", help="record the items and mail the digests")
    ap.add_argument("--prewarm", action="store_true", help="fill My page's next-meet cache too")
    args = ap.parse_args(argv)
    import psycopg2.extras
    from database import getConn
    from season_year import academicYear
    now = datetime.datetime.now(datetime.timezone.utc)
    today = datetime.date.today()
    year = academicYear(today)
    origin = AC.siteOrigin()
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not F.tablesReady(cur):
                print("  the follow tables are not there: python racecast/follows.py --init")
                return 0
            cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (_LOCK_KEY,))
            if not AC._one(cur)["ok"]:
                print("  another alert run holds the lock; skipping")
                return 0
            follows = loadFollows(cur)
            cur.execute("SELECT kind, person_id, school, state, level FROM account_claim")
            claims = [dict(r) for r in cur.fetchall()]
            print(f"  {len(follows)} follows across {len({f['account_id'] for f in follows})} accounts; season {year}")
            found = findItems(cur, follows, year)
            print(f"  {sum(len(v) for v in found.values())} items found")
            if not args.send:
                conn.rollback()
                print("  dry run: nothing recorded or sent (--send to do it)")
                return 0
            n_new = recordItems(cur, follows, found, year)
            cur.execute("DELETE FROM alert_item WHERE season < %s AND status <> 'pending'", (year,))
            n_snap = writeSnapshots(cur, follows, claims, today, year)
            conn.commit()
            print(f"  {n_new} new pending items; {n_snap} snapshots")
            if not AC.mailEnabled():
                print("  no mail provider (XCP_MAIL_PROVIDER / XCP_MAIL_KEY): items wait as pending")
            elif not F.alertSecret():
                print("  no XCP_ALERT_SECRET (it signs the unsubscribe link): items wait as pending")
            else:
                sent, failed = sendDigests(cur, conn, follows, now, origin)
                print(f"  digests: {sent} sent, {failed} failed")
            if args.prewarm:
                print(f"  prewarmed {prewarm(cur, conn, follows, claims, today)} next-meet rows")
            cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
            conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
