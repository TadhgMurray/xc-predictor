# Project: xc-predictor / racecast
# File:    follows.py
# Purpose: Follow athletes and teams, and how often to hear about them
#          (owner, 2026-10-10, approved: "Follow athletes and teams, with
#          email alerts"). The tables, the Follow button's two endpoints, the
#          alert setting and the signed unsubscribe link. The nightly digest
#          itself is follow_alerts.py; the page that shows it all is
#          my_page.py.
#
#     python racecast/follows.py --init     # create the tables (once)
#     python racecast/follows.py --check    # are they there, is the secret set
#
# ★ THE PAGES STAY EDGE-CACHEABLE, as accounts.py requires. The athlete and
#   school pages are served signed-out to everyone; follow.js asks
#   /api/follow/state after load (no-store, under /api/) and draws the button
#   from the answer, exactly as the topbar asks /api/me. A reader with no
#   rc_si hint cookie is not signed in and costs no request at all.
#
# ★ DDL ONCE, NEVER PER REQUEST (accounts.py's rule). --init creates the
#   tables in a transaction of its own under a lock timeout; a request only
#   checks, once per process, that they are there (tablesReady), and a server
#   that has not run --init answers "not set up yet" instead of paying an
#   exception and a rollback on every page.
#
# ! EVERY STATE CHANGE IS A POST WITH THE SESSION'S CSRF TOKEN FROM THIS
#   ORIGIN (accounts.csrfOk: the token and sameOrigin). The one exception is
#   the unsubscribe link, which a person opens from their mail with no
#   session: it is authorised by its own signature instead (unsubToken), the
#   standard for a one-click unsubscribe (RFC 8058), and it can only ever
#   turn the mail OFF.
#
# ! NO EMAIL ADDRESS IN A URL OR A LOG. The unsubscribe link names the
#   account by its id and an HMAC; the alert script logs account ids.
#
# Env:
#   XCP_ALERT_SECRET=<random, 32+ chars>   signs the unsubscribe links; the
#                                          digest is not sent without it

import hashlib
import hmac
import os
import sys
import urllib.parse

from flask import Blueprint, jsonify, make_response, redirect, render_template, request

import accounts as AC

bp = Blueprint("follows", __name__)

KINDS = ("athlete", "team")
# ★ THE THREE SETTINGS THE OWNER NAMED: a digest after each nightly run that
#   found something, one a week, or none. 'daily' is the default -- a person
#   who pressed Follow asked to hear.
CADENCES = ("daily", "weekly", "off")
CADENCE_WORDS = {"daily": "After each nightly update", "weekly": "Once a week",
                 "off": "Off"}
DEFAULT_CADENCE = "daily"
# the signed-in hint the pages read before asking the server anything
# (topbar-search.js sets it too; the same name and attributes, so the two
# writers agree): not a credential, only "worth asking /api/..."
HINT_COOKIE = "rc_si"

DDL = """
CREATE TABLE IF NOT EXISTS account_follow (
    id          bigserial PRIMARY KEY,
    account_id  bigint NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    kind        text NOT NULL,             -- 'athlete' | 'team'
    person_id   bigint,
    school      text,
    state       text,
    level       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    primed_at   timestamptz                -- first alert scan done (follow_alerts)
);
CREATE UNIQUE INDEX IF NOT EXISTS account_follow_unique ON account_follow
    (account_id, kind, coalesce(person_id, 0), coalesce(school, ''), coalesce(state, ''), coalesce(level, ''));
CREATE INDEX IF NOT EXISTS account_follow_person_idx ON account_follow (person_id);
CREATE INDEX IF NOT EXISTS account_follow_school_idx ON account_follow (school, state);
CREATE TABLE IF NOT EXISTS alert_pref (
    account_id   bigint PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    cadence      text NOT NULL DEFAULT 'daily',
    last_sent_at timestamptz,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS alert_item (
    account_id  bigint NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    item_key    text NOT NULL,             -- 'race:XC:<result_id>', 'team:<...>'
    season      integer NOT NULL,
    subject     text NOT NULL,             -- 'athlete:<id>' | 'team:<school>|<state>|<level>'
    payload     jsonb NOT NULL,
    status      text NOT NULL DEFAULT 'pending',   -- pending | sent | baseline | muted
    found_at    timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    PRIMARY KEY (account_id, item_key)
);
CREATE INDEX IF NOT EXISTS alert_item_pending_idx ON alert_item (account_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS follow_snapshot (
    subject   text NOT NULL,               -- 'athlete:<id>' | 'team:<school>|<state>|<pool>'
    sport     text NOT NULL,
    pool      text NOT NULL,
    year      integer NOT NULL,
    taken_on  date NOT NULL,
    rating    real,
    nation    integer,
    state_rank integer,
    PRIMARY KEY (subject, sport, pool, year, taken_on)
);
CREATE TABLE IF NOT EXISTS next_meet_cache (
    subject     text PRIMARY KEY,          -- subjectKey: an athlete or a team
    computed_on date NOT NULL,             -- good for that day (my_page.NEXT_MEET)
    payload     jsonb NOT NULL             -- the likely meets and the auto prediction
);
CREATE TABLE IF NOT EXISTS account_shortlist (
    account_id  bigint NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    school      text NOT NULL,
    state       text NOT NULL DEFAULT '',
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, school, state)
);
"""
TABLES = ("account_follow", "alert_pref", "alert_item", "follow_snapshot",
          "next_meet_cache", "account_shortlist")


# ---- pure ----------------------------------------------------------------

def parseFollow(values):
    """A follow request -> (row, error). kind=athlete needs a person_id;
    kind=team a school, a two-letter state (or none) and a claim level (or
    none) -- the school page's own identifiers (?state=, ?level=)."""
    kind = (values.get("kind") or "").strip()
    if kind not in KINDS:
        return None, "Pick what to follow."
    if kind == "athlete":
        pid = str(values.get("person_id") or "").strip()
        if not pid.isdigit() or int(pid) <= 0:
            return None, "Which athlete?"
        return {"kind": "athlete", "person_id": int(pid), "school": None,
                "state": None, "level": None}, None
    school = (values.get("school") or "").strip()[:200]
    if not school:
        return None, "Which team?"
    state = (values.get("state") or "").strip().upper()
    if state and (len(state) != 2 or not state.isalpha()):
        return None, "The state must be two letters."
    level = (values.get("level") or "").strip().lower()
    if level and level not in AC.LEVELS:
        return None, "Unknown level."
    return {"kind": "team", "person_id": None, "school": school,
            "state": state or None, "level": level or None}, None


def subjectKey(row):
    """'athlete:<id>' or 'team:<school>|<state>|<level>' -- what an alert
    item and a snapshot are about."""
    if row["kind"] == "athlete":
        return f"athlete:{int(row['person_id'])}"
    return f"team:{row['school']}|{row.get('state') or ''}|{row.get('level') or ''}"


def alertSecret():
    return AC._env("XCP_ALERT_SECRET")


def unsubToken(account_id, secret=None):
    """The HMAC that authorises one account's unsubscribe link. No expiry:
    an unsubscribe link in a year-old mail must still work."""
    secret = alertSecret() if secret is None else secret
    if not secret:
        return ""
    msg = f"racecast-alerts-unsubscribe:{int(account_id)}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def unsubOk(account_id, token, secret=None):
    want = unsubToken(account_id, secret)
    return bool(want) and bool(token) and hmac.compare_digest(want, str(token))


def unsubUrl(account_id, origin=None, secret=None):
    """The link in every digest: the account id and its signature, never
    the address."""
    q = urllib.parse.urlencode({"a": int(account_id), "t": unsubToken(account_id, secret)})
    return f"{origin or AC.siteOrigin()}/account/unsubscribe?{q}"


# ---- database ------------------------------------------------------------

def initTables(conn):
    """accounts.initTables' rule: the DDL alone, under a lock timeout."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(DDL)
    conn.commit()


_READY = {"checked": False, "ready": False}


def tablesReady(cur):
    """Once per process: are this module's tables there? (photoTablesReady's
    pattern.) A missing table is said once in the log, then every route
    answers 'not set up' without touching the database."""
    if not _READY["checked"]:
        try:
            cur.execute("SELECT to_regclass('public.account_shortlist') AS t, "
                        "to_regclass('public.account_follow') AS f")
            row = AC._one(cur)
            _READY["ready"] = bool(row and row.get("t") and row.get("f"))
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            _READY["ready"] = False
        _READY["checked"] = True
        if not _READY["ready"]:
            print("[follows] tables are not there: run racecast/follows.py --init", flush=True)
    return _READY["ready"]


_WHERE = """account_id = %s AND kind = %s AND coalesce(person_id, 0) = coalesce(%s, 0)
            AND coalesce(school, '') = coalesce(%s, '') AND coalesce(state, '') = coalesce(%s, '')
            AND coalesce(level, '') = coalesce(%s, '')"""


def _args(account_id, row):
    return (account_id, row["kind"], row["person_id"], row["school"], row["state"], row["level"])


def isFollowing(cur, account_id, row):
    cur.execute(f"SELECT 1 AS x FROM account_follow WHERE {_WHERE}", _args(account_id, row))
    return AC._one(cur) is not None


def follow(cur, account_id, row):
    cur.execute("""INSERT INTO account_follow (account_id, kind, person_id, school, state, level)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""", _args(account_id, row))
    # the setting row, so the digest has one to read (the default)
    cur.execute("""INSERT INTO alert_pref (account_id, cadence) VALUES (%s, %s)
                   ON CONFLICT (account_id) DO NOTHING""", (account_id, DEFAULT_CADENCE))


def dropPending(cur, account_id, subject):
    """An unfollowed subject's waiting items go with it (the sent ones stay:
    they are what stops a re-follow from mailing them again)."""
    cur.execute("DELETE FROM alert_item WHERE account_id = %s AND subject = %s AND status = 'pending'",
                (account_id, subject))


def unfollow(cur, account_id, row):
    cur.execute(f"DELETE FROM account_follow WHERE {_WHERE}", _args(account_id, row))
    dropPending(cur, account_id, subjectKey(row))


def followsFor(cur, account_id):
    """The account's follows, oldest first, athletes named."""
    from rankings import nameLateral
    from school_identity import schoolLabelIn
    cur.execute(f"""SELECT f.id, f.kind, f.person_id, f.school, f.state, f.level, f.created_at, a.name
                    FROM   account_follow f
                    {nameLateral('f')}
                    WHERE  f.account_id = %s
                    ORDER  BY f.created_at, f.id""", (account_id,))
    out = [dict(r) for r in cur.fetchall()]
    for f in out:
        f["school_label"] = schoolLabelIn(f["school"], f["state"]) if f.get("school") else ""
        f["level_label"] = AC.LEVEL_WORDS.get(f.get("level") or "", "")
    return out


def cadenceFor(cur, account_id):
    cur.execute("SELECT cadence FROM alert_pref WHERE account_id = %s", (account_id,))
    row = AC._one(cur)
    return row["cadence"] if row and row["cadence"] in CADENCES else DEFAULT_CADENCE


def setCadence(cur, account_id, cadence):
    cur.execute("""INSERT INTO alert_pref (account_id, cadence, updated_at) VALUES (%s, %s, now())
                   ON CONFLICT (account_id) DO UPDATE SET cadence = EXCLUDED.cadence, updated_at = now()""",
                (account_id, cadence))


# ---- the hint cookie -----------------------------------------------------

def setHint(resp):
    """rc_si=1 beside the session cookie: readable by the page scripts
    (not httponly), carrying nothing but "a session exists, ask"."""
    resp.set_cookie(HINT_COOKIE, "1", max_age=AC.SESSION_DAYS * 86400, httponly=False,
                    secure=AC.isSecure(), samesite="Lax", path="/")
    return resp


# ---- routes --------------------------------------------------------------

def _json(payload, status=200):
    return jsonify(payload), status


@bp.route("/api/follow/state")
def api_follow_state():
    """What the Follow button draws: {signed_in, ready, following, csrf,
    cadence}. Under /api/, so app._headers makes it no-store."""
    sess = AC.currentSession()
    if not sess:
        return _json({"signed_in": False})
    row, err = parseFollow(request.args)
    if err:
        return _json({"signed_in": True, "error": err}, 400)
    aid = sess["account"]["id"]
    try:
        with AC._db() as (conn, cur):
            if not tablesReady(cur):
                return _json({"signed_in": True, "ready": False})
            following = isFollowing(cur, aid, row)
            cadence = cadenceFor(cur, aid)
            conn.commit()
    except Exception as exc:                            # noqa: BLE001
        print(f"[follows] state failed ({type(exc).__name__}: {exc})", flush=True)
        return _json({"signed_in": True, "ready": False})
    return _json({"signed_in": True, "ready": True, "following": following,
                  "cadence": cadence, "csrf": sess["csrf"]})


@bp.route("/api/follow", methods=["POST"])
def api_follow():
    """Follow (action=follow) or unfollow (action=unfollow). The session's
    CSRF token in X-CSRF or the form, from this origin."""
    sess = AC.currentSession()
    if not sess:
        return _json({"error": "Sign in to follow."}, 401)
    if not AC.csrfOk(sess):
        return _json({"error": "That request did not come from this site."}, 400)
    row, err = parseFollow(request.form)
    if err:
        return _json({"error": err}, 400)
    action = (request.form.get("action") or "follow").strip()
    if action not in ("follow", "unfollow"):
        return _json({"error": "Unknown action."}, 400)
    aid = sess["account"]["id"]
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _json({"error": "Following is not set up on this server yet."}, 503)
        if action == "follow":
            if row["person_id"] and not AC.personExists(cur, row["person_id"]):
                return _json({"error": "That athlete is not on Racecast."}, 400)
            follow(cur, aid, row)
        else:
            unfollow(cur, aid, row)
        AC.logEvent(cur, action, aid, detail={"subject": subjectKey(row)})
        following = isFollowing(cur, aid, row)
        conn.commit()
    return _json({"ok": True, "following": following})


def _toMyPage(notice=None, error=None, anchor=""):
    q = {k: v for k, v in (("notice", notice), ("error", error)) if v}
    return redirect("/account/me" + ("?" + urllib.parse.urlencode(q) if q else "") + anchor)


@bp.route("/account/follow/remove", methods=["POST"])
def account_follow_remove():
    """Unfollow from My page (a plain form, so it works without script)."""
    sess, go = AC._requireSession()
    if go:
        return go
    if not AC.csrfOk(sess):
        return _toMyPage(error="That request did not come from this site."), 400
    fid = (request.form.get("id") or "").strip()
    if not fid.isdigit():
        return _toMyPage(error="Nothing to remove."), 400
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _toMyPage(error="Following is not set up on this server yet."), 503
        cur.execute("""DELETE FROM account_follow WHERE id = %s AND account_id = %s
                       RETURNING kind, person_id, school, state, level""",
                    (int(fid), sess["account"]["id"]))
        gone = AC._one(cur)
        if gone:
            dropPending(cur, sess["account"]["id"], subjectKey(gone))
        AC.logEvent(cur, "unfollow", sess["account"]["id"], detail={"id": int(fid)})
        conn.commit()
    return _toMyPage(notice="Unfollowed.", anchor="#following")


@bp.route("/account/alerts", methods=["POST"])
def account_alerts():
    """The alert setting: daily digest, weekly, or off."""
    sess, go = AC._requireSession()
    if go:
        return go
    if not AC.csrfOk(sess):
        return _toMyPage(error="That request did not come from this site."), 400
    cadence = (request.form.get("cadence") or "").strip()
    if cadence not in CADENCES:
        return _toMyPage(error="Pick how often."), 400
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _toMyPage(error="Alerts are not set up on this server yet."), 503
        setCadence(cur, sess["account"]["id"], cadence)
        AC.logEvent(cur, "alerts", sess["account"]["id"], detail={"cadence": cadence})
        conn.commit()
    return _toMyPage(notice=f"Alert emails: {CADENCE_WORDS[cadence].lower()}.", anchor="#alerts")


@bp.route("/account/unsubscribe", methods=["GET", "POST"])
def account_unsubscribe():
    """The link in every digest. GET shows one button (a mail scanner that
    fetches the link must not unsubscribe anyone); POST, from that button or
    from the mail client's one-click List-Unsubscribe-Post, turns the alert
    mail off. Authorised by the signature, not a session."""
    aid = str(request.values.get("a") or "").strip()
    token = str(request.values.get("t") or "").strip()
    valid = aid.isdigit() and unsubOk(int(aid), token)
    if request.method == "GET":
        return render_template("unsubscribe.html", valid=valid, done=False, a=aid, t=token)
    if not valid:
        return render_template("unsubscribe.html", valid=False, done=False, a="", t=""), 400
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return render_template("unsubscribe.html", valid=True, done=False, a=aid, t=token,
                                   error="Alerts are not set up on this server."), 503
        setCadence(cur, int(aid), "off")
        AC.logEvent(cur, "alerts_unsubscribed", int(aid))
        conn.commit()
    return render_template("unsubscribe.html", valid=True, done=True, a="", t="")


@bp.route("/account/return")
def account_return():
    """Where a signed-out Follow or Save button sends a reader: sign in
    (accounts' own redirect), then back to the page with the hint cookie
    set, so the button there can ask the server. Changes nothing else."""
    sess, go = AC._requireSession()
    if go:
        return redirect("/login?" + urllib.parse.urlencode({"next": request.full_path.rstrip("?")}))
    return setHint(make_response(redirect(AC.safeNext(request.args.get("next"), "/account/me"))))


# ---- command line --------------------------------------------------------

def main(argv):
    sys.path.insert(0, "scripts")
    sys.path.insert(0, "racecast")
    if "--init" in argv:
        from database import getConn
        with getConn() as conn:
            initTables(conn)
        print("  " + ", ".join(TABLES) + ": ready")
        return 0
    if "--check" in argv:
        from database import getConn
        with getConn() as conn:
            with conn.cursor() as cur:
                for t in TABLES:
                    cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
                    print(f"  {t:18s} {'ok' if cur.fetchone()[0] else 'MISSING (run --init)'}")
            conn.rollback()
        print(f"  alert secret:      {'set' if alertSecret() else 'MISSING (XCP_ALERT_SECRET): no digest is sent'}")
        print(f"  mail:              {'on' if AC.mailEnabled() else 'OFF (XCP_MAIL_PROVIDER + XCP_MAIL_KEY)'}")
        return 0
    print("python racecast/follows.py --init | --check")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
