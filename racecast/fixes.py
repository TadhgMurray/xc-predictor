# Project: xc-predictor / racecast
# File:    fixes.py
# Purpose: "Suggest a fix" (owner, 2026-10-10, approved). An athlete who has
#          CLAIMED their page (accounts.account_claim, kind athlete or
#          coach_self) can say what is wrong with it, in five structured
#          kinds: a wrong grade or class year, a wrong school, a result that
#          is not theirs, a missing result (a link to it), two pages that are
#          the same person. Each is stored as a request with the evidence --
#          the values the page showed when it was sent -- and waits in the
#          owner's queue at /account/admin/fixes, where Approve or Reject
#          decides it and the requester is emailed.
#
#     python racecast/fixes.py --init     # create the table (once)
#
# ★ APPROVE GOES THROUGH THE MECHANISM THAT ALREADY EXISTS, OR NOWHERE
#   (owner: "never ad-hoc UPDATEs to results"). What each kind does:
#
#     same_person  APPLIED through scripts/link_profile_school.py: the
#                  decision is written to profile_school_merge, the table
#                  that step keeps its sticky merges in, and its write()
#                  moves the rows (person_link_log rule 'profile_school',
#                  athletes repointed, person_redirect written). Undo is the
#                  tool's own: link_profile_school.py --undo <old id>.
#     missing      QUEUED when the link is an athletic.net or tfrrs meet:
#                  meet_queue.scraped = 0 for that meet, which is what
#                  scripts/find_meet.py --requeue does by hand; the nightly
#                  scrape reads it again. Any other link is recorded only --
#                  the app never fetches a link, and other sites are not
#                  scraped (owner's rule); results from them come in through
#                  /account/uploads.
#     grade        RECORDED ONLY. grade_fix is rebuilt by grade_sanity.py
#     school       every run, and no pin table exists for a person's grade,
#     not_mine     school or a single result. MISSING_SUPPORT says what each
#                  needs; the approved request is the record of the decision
#                  until it exists.
#
# ! WRONG-PERSON DETECTION STAYS REPORT-ONLY (owner's rule). A "not mine"
#   request is a person's own report, kept and shown; approving it moves
#   nothing.
#
# ! THE ATHLETE PAGE STAYS EDGE-CACHEABLE. The "Suggest a fix" link is in
#   the anonymous HTML, hidden, and shown by script only when /api/me says
#   the reader claims this page (_suggest_fix.html), the way the topbar
#   marks "Your page". The routes here check the claim again, server-side.

import json
import re
import sys
import urllib.parse

from flask import Blueprint, abort, redirect, render_template, request

import accounts as AC
from uploads import requireAdmin

bp = Blueprint("fixes", __name__)

KINDS = ("grade", "school", "not_mine", "missing", "same_person")
KIND_WORDS = {"grade": "Wrong grade or class year", "school": "Wrong school",
              "not_mine": "A result that is not mine", "missing": "A missing result",
              "same_person": "Two pages that are the same person"}
GRADES = ("5", "6", "7", "8", "9", "10", "11", "12", "FR", "SO", "JR", "SR", "GR")
FIXES_PER_DAY = 10
NOTE_MAX = 1000

MISSING_SUPPORT = {
    "grade": ("No persistent grade correction exists: grade_fix is rebuilt by "
              "engine/grade_sanity.py on every run. Needed: a grade pin (person_id, sport, "
              "season, grade) that grade_sanity applies before its rules."),
    "school": ("No per-athlete school correction exists: school_name_alias and "
               "merge_school_names.py correct a school's spelling everywhere, not one athlete's "
               "row. Needed: a school pin (person_id, season, school) that "
               "build_ranking_results.py reads."),
    "not_mine": ("No per-result correction the pipeline honours exists: the linkers move whole "
                 "people and person_collision.py / unlink.py are detectors. Needed: a result "
                 "detach table (sport, result_id) the linkers skip, and a step that moves the row "
                 "to a fresh person (unlink.SPLIT_BASE), logged in person_link_log with --undo."),
    "missing_other": ("Only athletic.net and tfrrs meets can be queued for the scrape; for any "
                      "other site the results come in as an upload (/account/uploads)."),
}

DDL = """
CREATE TABLE IF NOT EXISTS fix_request (
    id             bigserial PRIMARY KEY,
    account_id     bigint REFERENCES account(id) ON DELETE SET NULL,
    person_id      bigint NOT NULL,
    kind           text NOT NULL,
    status         text NOT NULL DEFAULT 'open',
    detail         jsonb NOT NULL,
    evidence       jsonb,
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    decided_at     timestamptz,
    decided_by     text,
    decision_note  text,
    outcome        text,
    applied        jsonb
);
CREATE INDEX IF NOT EXISTS fix_request_status_idx ON fix_request (status, created_at);
CREATE INDEX IF NOT EXISTS fix_request_account_idx ON fix_request (account_id, created_at);
CREATE INDEX IF NOT EXISTS fix_request_person_idx ON fix_request (person_id);
"""


def initTables(conn):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(DDL)
    conn.commit()


_READY = {"checked": False, "ready": False}


def tablesReady(cur):
    if not _READY["checked"]:
        try:
            cur.execute("SELECT to_regclass('public.fix_request') AS t")
            row = AC._one(cur)
            _READY["ready"] = bool(row and row.get("t"))
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            _READY["ready"] = False
        _READY["checked"] = True
        if not _READY["ready"]:
            print("[fixes] table is not there: run racecast/fixes.py --init", flush=True)
    return _READY["ready"]


# ---- pure pieces ------------------------------------------------------------

_ANET = re.compile(r"^https?://(?:www\.)?athletic\.net/(CrossCountry|TrackAndField|cross-country|"
                   r"track-and-field)/meet/(\d{1,9})\b", re.I)
_TFRRS = re.compile(r"^https?://(?:www\.|xc\.|m\.)?tfrrs\.org/results/(xc/)?(\d{1,9})\b", re.I)


def queueTarget(url):
    """(meet_id, sport, source) the scrape queue can take, or None. Only a
    meet page of the two feeds the nightly scrape already reads."""
    u = (url or "").strip()
    m = _ANET.match(u)
    if m:
        sport = "XC" if m.group(1).lower().startswith("cross") else "TF"
        return int(m.group(2)), sport, "anet"
    m = _TFRRS.match(u)
    if m:
        return int(m.group(2)), ("XC" if m.group(1) else "TF"), "tfrrs"
    return None


def _season(v):
    s = (v or "").strip()
    if not re.fullmatch(r"\d{4}", s) or not (1990 <= int(s) <= 2035):
        return None
    return int(s)


def parseFixForm(form, person_id):
    """The form -> (detail, error). detail is what the request stores."""
    kind = (form.get("kind") or "").strip()
    if kind not in KINDS:
        return None, "Pick what is wrong."
    note = (form.get("note") or "").strip()[:NOTE_MAX]
    if kind == "grade":
        season = _season(form.get("grade_season") or form.get("season"))
        grade = (form.get("grade") or "").strip().upper()
        cls = (form.get("class_year") or "").strip()
        if season is None:
            return None, "Pick the season that is wrong."
        if grade not in GRADES and not re.fullmatch(r"\d{4}", cls):
            return None, "Pick the right grade, or give your class year."
        if cls and not (1990 <= int(cls) <= 2040):
            return None, "That class year is not a year."
        return {"kind": kind, "season": season, "sport": _sport(form, kind), "grade": grade or None,
                "class_year": int(cls) if cls else None, "note": note}, None
    if kind == "school":
        season = _season(form.get("school_season") or form.get("season"))
        school = " ".join((form.get("school") or "").split())[:120]
        state = (form.get("state") or "").strip().upper()
        if season is None:
            return None, "Pick the season that is wrong."
        if len(school) < 2:
            return None, "Type the right school."
        if state and (len(state) != 2 or not state.isalpha()):
            return None, "The state is two letters."
        return {"kind": kind, "season": season, "sport": _sport(form, kind), "school": school,
                "state": state or None, "note": note}, None
    if kind == "not_mine":
        rid = (form.get("result") or "").strip()
        m = re.fullmatch(r"(XC|TF):(-?\d{1,19})", rid)
        if not m:
            return None, "Pick the result that is not yours."
        return {"kind": kind, "sport": m.group(1), "result_id": int(m.group(2)), "note": note}, None
    if kind == "missing":
        url = (form.get("url") or "").strip()[:500]
        if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url):
            return None, "Paste the link to the result (http:// or https://)."
        return {"kind": kind, "url": url, "meet": (form.get("meet") or "").strip()[:200] or None,
                "note": note, "queue": list(queueTarget(url) or []) or None}, None
    other = (form.get("other_person_id") or "").strip()
    m = re.search(r"(?:/athlete/)?(\d{1,12})\s*$", other)
    if not m:
        return None, "Paste the link to the other page (racecast.co/athlete/...)."
    oid = int(m.group(1))
    if oid == int(person_id):
        return None, "That is this page."
    return {"kind": kind, "other_person_id": oid, "note": note}, None


def _sport(form, kind):
    s = (form.get(f"{kind}_sport") or form.get("sport") or "").strip().upper()
    return s if s in ("XC", "TF") else None


def summary(detail):
    """One line a person reads: what the request says."""
    k = detail.get("kind")
    if k == "grade":
        what = (f"grade {detail['grade']}" if detail.get("grade") else "") + \
               (f" (class of {detail['class_year']})" if detail.get("class_year") else "")
        return f"{detail['season']}{' ' + detail['sport'] if detail.get('sport') else ''}: should be {what.strip()}"
    if k == "school":
        st = f" ({detail['state']})" if detail.get("state") else ""
        return f"{detail['season']}{' ' + detail['sport'] if detail.get('sport') else ''}: should be {detail['school']}{st}"
    if k == "not_mine":
        return f"{detail['sport']} result {detail['result_id']} is not theirs"
    if k == "missing":
        return f"missing: {detail['url']}"
    if k == "same_person":
        return f"same person as /athlete/{detail['other_person_id']}"
    return k or ""


# ---- database pieces -------------------------------------------------------

def claimsPerson(cur, account_id, person_id):
    cur.execute("""SELECT 1 AS x FROM account_claim WHERE account_id = %s AND person_id = %s
                     AND kind IN ('athlete', 'coach_self') LIMIT 1""", (account_id, person_id))
    return AC._one(cur) is not None


def _regclass(cur, name):
    cur.execute("SELECT to_regclass(%s) AS t", (name,))
    row = AC._one(cur)
    return bool(row and row.get("t"))


def snapshot(cur, person_id):
    """What the page says about a person now: name, seasons, row counts."""
    cur.execute("""SELECT NULLIF(TRIM(concat_ws(' ', first_name, last_name)), '') AS name, school, gender
                   FROM athletes WHERE person_id = %s
                   ORDER BY (COALESCE(TRIM(first_name), '') <> '') DESC LIMIT 1""", (person_id,))
    a = AC._one(cur) or {}
    name = a.get("name")
    if not name:
        cur.execute("""SELECT athlete_name AS name FROM results WHERE person_id = %(p)s
                         AND NULLIF(btrim(athlete_name), '') IS NOT NULL
                       UNION ALL
                       SELECT athlete_name FROM results_tf WHERE person_id = %(p)s
                         AND NULLIF(btrim(athlete_name), '') IS NOT NULL
                       LIMIT 1""", {"p": person_id})
        r = AC._one(cur)
        name = r["name"] if r else None
    seasons = []
    if _regclass(cur, "athlete_season"):
        cur.execute("""SELECT sport, year, grade, school, state, n_races, pool
                       FROM athlete_season WHERE person_id = %s
                       ORDER BY year DESC, sport LIMIT 24""", (person_id,))
        seasons = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT (SELECT count(*) FROM results WHERE person_id = %(p)s) AS xc,
                          (SELECT count(*) FROM results_tf WHERE person_id = %(p)s) AS tf""",
                {"p": person_id})
    n = AC._one(cur) or {}
    return {"person_id": person_id, "name": name, "school": a.get("school"), "gender": a.get("gender"),
            "seasons": seasons, "n_xc": n.get("xc") or 0, "n_tf": n.get("tf") or 0}


_RESULT_SQL = {
    "XC": """SELECT 'XC' AS sport, r.result_id, r.date, r.time_seconds, r.place, r.school, r.grade,
                    r.athlete_name, r.source, r.athlete_id, r.meet_id,
                    COALESCE(m.meet_name, mt.meet_name) AS meet_name
             FROM results r
             LEFT JOIN LATERAL (SELECT meet_name FROM meets m WHERE m.div_id = r.div_id
                                  AND r.source <> 'tfrrs' LIMIT 1) m ON true
             LEFT JOIN LATERAL (SELECT meet_name FROM meets_tfrrs t WHERE t.meet_id = r.meet_id
                                  AND r.source = 'tfrrs' AND t.sport = 'XC' LIMIT 1) mt ON true
             WHERE {where}""",
    "TF": """SELECT 'TF' AS sport, r.result_id, r.date, r.time_seconds, r.place, r.school, r.grade,
                    r.athlete_name, r.source, r.athlete_id, r.meet_id, r.event_short,
                    COALESCE(m.meet_name, mt.meet_name) AS meet_name
             FROM results_tf r
             LEFT JOIN LATERAL (SELECT meet_name FROM meets_tf_meta m WHERE m.meet_id = r.meet_id
                                  AND r.source <> 'tfrrs' LIMIT 1) m ON true
             LEFT JOIN LATERAL (SELECT meet_name FROM meets_tfrrs t WHERE t.meet_id = r.meet_id
                                  AND r.source = 'tfrrs' AND t.sport = 'TF' LIMIT 1) mt ON true
             WHERE {where}""",
}


def recentResults(cur, person_id, limit=80):
    """The person's rows, newest first, for the "not mine" picker."""
    out = []
    for sport in ("XC", "TF"):
        cur.execute(_RESULT_SQL[sport].format(where="r.person_id = %s") + " ORDER BY r.date DESC LIMIT %s",
                    (person_id, limit))
        out += [dict(r) for r in cur.fetchall()]
    out.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return out[:limit]


def resultRow(cur, sport, result_id):
    cur.execute(_RESULT_SQL[sport].format(where="r.result_id = %s") + " LIMIT 1", (result_id,))
    return AC._one(cur)


def evidenceFor(cur, person_id, detail):
    """The values the page showed when the request was sent, kept with it."""
    ev = {"athlete": snapshot(cur, person_id)}
    k = detail["kind"]
    if k == "not_mine":
        row = resultRow(cur, detail["sport"], detail["result_id"])
        ev["result"] = row
        if row and _regclass(cur, "person_link_log"):
            cur.execute("""SELECT rule, from_person, to_person, linked_at FROM person_link_log
                           WHERE sport = %s AND result_id = %s ORDER BY linked_at""",
                        (detail["sport"], detail["result_id"]))
            ev["linked_by"] = [dict(r) for r in cur.fetchall()]
    elif k == "same_person":
        ev["other"] = snapshot(cur, detail["other_person_id"])
    elif k == "missing" and detail.get("queue"):
        mid, sport, src = detail["queue"]
        if _regclass(cur, "meet_queue"):
            cur.execute("SELECT scraped FROM meet_queue WHERE meet_id = %s AND sport = %s AND source = %s",
                        (mid, sport, src))
            q = AC._one(cur)
            ev["queue_state"] = q["scraped"] if q else None
    return json.loads(json.dumps(ev, default=str))


def recentCount(cur, account_id):
    cur.execute("""SELECT count(*) AS n FROM fix_request
                   WHERE account_id = %s AND created_at > now() - interval '1 day'""", (account_id,))
    return int((AC._one(cur) or {}).get("n") or 0)


def submit(cur, account_id, person_id, detail):
    from psycopg2.extras import Json
    ev = evidenceFor(cur, person_id, detail)
    if detail["kind"] == "not_mine" and not (ev.get("result") and
                                             str(ev["result"].get("result_id")) == str(detail["result_id"])):
        return None, "That result is not on this page."
    if detail["kind"] == "not_mine":
        cur.execute(f"SELECT person_id FROM {'results' if detail['sport'] == 'XC' else 'results_tf'} "
                    "WHERE result_id = %s", (detail["result_id"],))
        r = AC._one(cur)
        if not r or r.get("person_id") != person_id:
            return None, "That result is not on this page."
    if detail["kind"] == "same_person" and not (ev["other"]["n_xc"] or ev["other"]["n_tf"] or ev["other"]["name"]):
        return None, "There is no athlete page at that link."
    cur.execute("""INSERT INTO fix_request (account_id, person_id, kind, detail, evidence, note)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (account_id, person_id, detail["kind"], Json(detail), Json(ev), detail.get("note") or None))
    return AC._one(cur)["id"], None


# ---- applying an approved request -------------------------------------------

def applySamePerson(conn, fx, keep):
    """Through link_profile_school: the decision into profile_school_merge,
    then its write() (which commits). keep: the person_id that survives."""
    sys.path.insert(0, "scripts")
    import link_profile_school as LPS
    a, b = int(fx["person_id"]), int(fx["detail"]["other_person_id"])
    if keep not in (a, b):
        raise ValueError("keep must be one of the two pages")
    old = b if keep == a else a
    ev = fx.get("evidence") or {}
    names = {a: (ev.get("athlete") or {}).get("name"), b: (ev.get("other") or {}).get("name")}
    out = LPS.write(conn, [(old, keep, names.get(old) or names.get(keep), f"fix request #{fx['id']}")])
    return {"mechanism": "link_profile_school", "old_id": old, "new_id": keep,
            "rule": LPS.RULE, "result": out,
            "undo": f"python scripts/link_profile_school.py --undo {old}"}


def applyMissing(cur, fx):
    q = fx["detail"].get("queue")
    if not q:
        return None
    mid, sport, src = q
    cur.execute("""INSERT INTO meet_queue (meet_id, sport, scraped, source) VALUES (%s, %s, 0, %s)
                   ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 0""", (mid, sport, src))
    return {"mechanism": "meet_queue (find_meet.py --requeue)", "meet_id": mid, "sport": sport,
            "source": src}


def outcomeWords(kind, applied):
    """One line for the requester and the queue: what approval did."""
    if kind == "same_person" and applied:
        return (f"The two pages are being joined: /athlete/{applied['old_id']} now leads to "
                f"/athlete/{applied['new_id']}; the ratings follow after the next nightly update.")
    if kind == "missing" and applied:
        return ("The meet is queued for the next nightly scrape; the result shows after it "
                "and the nightly update that follows.")
    if kind == "missing":
        return "Recorded. Results from that site come in as an upload, which the owner reviews."
    return "Recorded with the owner's approval; the correction is made by hand for now."


def decide(conn, cur, fid, action, admin_email, note=None, keep=None):
    """-> (status, error, outcome). Writes the request's own row; for an
    approved same_person or missing request, applies it through the
    existing tool. The caller commits, except that same_person's tool
    commits the whole transaction itself (decision row included)."""
    from psycopg2.extras import Json
    cur.execute("SELECT * FROM fix_request WHERE id = %s FOR UPDATE", (fid,))
    fx = AC._one(cur)
    if fx is None:
        return None, "No such request.", None
    if fx["status"] != "open":
        return fx["status"], "This request has been decided.", None
    if action == "reject":
        cur.execute("""UPDATE fix_request SET status = 'rejected', decided_at = now(), decided_by = %s,
                              decision_note = %s WHERE id = %s""", (admin_email, note, fid))
        return "rejected", None, None
    if action != "approve":
        return fx["status"], "Unknown action.", None
    kind = fx["kind"]
    applied, gap, k = None, None, None
    if kind == "same_person":
        a, b = int(fx["person_id"]), int(fx["detail"]["other_person_id"])
        k = int(keep) if keep else a
        if k not in (a, b):
            return fx["status"], "Keep one of the two pages.", None
        old = b if k == a else a
        applied = {"mechanism": "link_profile_school", "old_id": old, "new_id": k}
        outcome = outcomeWords(kind, applied)
    elif kind == "missing":
        applied = applyMissing(cur, fx)
        gap = None if applied else MISSING_SUPPORT["missing_other"]
        outcome = outcomeWords(kind, applied)
    else:
        gap = MISSING_SUPPORT[kind]
        outcome = outcomeWords(kind, None)
    cur.execute("""UPDATE fix_request SET status = 'approved', decided_at = now(), decided_by = %s,
                          decision_note = %s, outcome = %s, applied = %s WHERE id = %s""",
                (admin_email, note, outcome, Json({"applied": applied, "missing_support": gap}), fid))
    if kind == "same_person":
        res = applySamePerson(conn, fx, k)      # commits, with the row above
        cur.execute("UPDATE fix_request SET applied = %s WHERE id = %s",
                    (Json(json.loads(json.dumps({"applied": res, "missing_support": None}, default=str))), fid))
    return "approved", None, outcome


def notifyRequester(cur, fx, status, outcome, note):
    import decision_email as DE
    cur.execute("SELECT email, name FROM account WHERE id = %s", (fx.get("account_id"),))
    acct = AC._one(cur)
    if not acct:
        return False
    name = ((fx.get("evidence") or {}).get("athlete") or {}).get("name")
    subject, text, html = DE.fixDecision(fx, status, outcome, note, AC.siteOrigin(), athlete_name=name,
                                         account_name=acct.get("name"))
    return AC.sendMail(acct["email"], subject, text, html=html, log_as=f"account {fx.get('account_id')}")


# ---- routes: the claimant ------------------------------------------------------

def _back(path, **q):
    q = {k: v for k, v in q.items() if v}
    return redirect(path + ("?" + urllib.parse.urlencode(q) if q else ""))


def _pid():
    p = (request.values.get("person") or "").strip()
    return int(p) if p.isdigit() and int(p) > 0 else None


@bp.route("/account/fixes")
def fixes_page():
    sess, go = AC._requireSession()
    if go:
        return go
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return render_template("fixes.html", mode="unready", csrf="", kind_words=KIND_WORDS), 503
        cur.execute("""SELECT id, person_id, kind, status, detail, created_at, decided_at, outcome,
                              decision_note
                       FROM fix_request WHERE account_id = %s ORDER BY created_at DESC LIMIT 50""",
                    (sess["account"]["id"],))
        mine = [dict(r, summary=summary(r["detail"])) for r in cur.fetchall()]
        conn.commit()
    return render_template("fixes.html", mode="list", mine=mine, csrf=sess["csrf"], kind_words=KIND_WORDS,
                           notice=request.args.get("notice", "")[:200], error=request.args.get("error", "")[:200])


@bp.route("/account/fixes/new")
def fix_new():
    sess, go = AC._requireSession()
    if go:
        return go
    pid = _pid()
    if pid is None:
        return _back("/account", error="Open your athlete page and use Suggest a fix there.")
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return render_template("fixes.html", mode="unready", csrf="", kind_words=KIND_WORDS), 503
        if not claimsPerson(cur, sess["account"]["id"], pid):
            conn.commit()
            return render_template("fixes.html", mode="unclaimed", person_id=pid, csrf=sess["csrf"],
                                   kind_words=KIND_WORDS), 403
        snap = snapshot(cur, pid)
        results = recentResults(cur, pid)
        conn.commit()
    kind = request.args.get("kind") if request.args.get("kind") in KINDS else "grade"
    return render_template("fixes.html", mode="new", person_id=pid, snap=snap, results=results,
                           kind=kind, kinds=KINDS, kind_words=KIND_WORDS, grades=GRADES, csrf=sess["csrf"],
                           note_max=NOTE_MAX, error=request.args.get("error", "")[:200])


@bp.route("/account/fixes/new", methods=["POST"])
def fix_post():
    sess, go = AC._requireSession()
    if go:
        return go
    pid = _pid()
    if pid is None:
        abort(400)
    kind = (request.form.get("kind") or "")[:20]

    def again(err):
        return _back("/account/fixes/new", person=pid, kind=kind, error=err)
    if not AC.csrfOk(sess):
        return again("That request did not come from this site."), 400
    detail, err = parseFixForm(request.form, pid)
    if err:
        return again(err), 400
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            abort(503)
        if not claimsPerson(cur, sess["account"]["id"], pid):
            abort(403)
        if recentCount(cur, sess["account"]["id"]) >= FIXES_PER_DAY:
            return again("That is a lot of fixes in a day: the owner reads every one; "
                         "try again tomorrow."), 429
        fid, err = submit(cur, sess["account"]["id"], pid, detail)
        if err:
            conn.rollback()
            return again(err), 400
        AC.logEvent(cur, "fix_request", sess["account"]["id"], detail={"fix": fid, "kind": detail["kind"]})
        conn.commit()
    return _back("/account/fixes", notice="Sent. The site owner reads every fix; you will get an email "
                                          "when it is decided.")


# ---- routes: the owner's queue ------------------------------------------------

@bp.route("/account/admin/fixes")
def admin_fixes():
    sess, go = requireAdmin()
    if go:
        return go
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return render_template("fixes.html", mode="unready", csrf="", kind_words=KIND_WORDS), 503
        cur.execute("""SELECT f.*, a.email AS account_email FROM fix_request f
                       LEFT JOIN account a ON a.id = f.account_id
                       WHERE f.status = 'open' ORDER BY f.created_at LIMIT 200""")
        open_ = [dict(r, summary=summary(r["detail"])) for r in cur.fetchall()]
        # the page as it is NOW beside the evidence as it was sent
        for f in open_:
            try:
                f["now"] = snapshot(cur, f["person_id"])
                if f["kind"] == "same_person":
                    f["other_now"] = snapshot(cur, f["detail"]["other_person_id"])
                if f["kind"] == "not_mine":
                    f["result_now"] = resultRow(cur, f["detail"]["sport"], f["detail"]["result_id"])
            except Exception:                           # noqa: BLE001
                conn.rollback()
        cur.execute("""SELECT f.id, f.person_id, f.kind, f.status, f.detail, f.decided_at, f.decided_by,
                              f.outcome, f.applied, a.email AS account_email
                       FROM fix_request f LEFT JOIN account a ON a.id = f.account_id
                       WHERE f.status <> 'open' ORDER BY f.decided_at DESC NULLS LAST LIMIT 40""")
        decided = [dict(r, summary=summary(r["detail"])) for r in cur.fetchall()]
        conn.commit()
    return render_template("admin_fixes.html", open_=open_, decided=decided, csrf=sess["csrf"],
                           kind_words=KIND_WORDS, missing=MISSING_SUPPORT,
                           notice=request.args.get("notice", "")[:200], error=request.args.get("error", "")[:200])


@bp.route("/account/admin/fixes/<int:fid>/decide", methods=["POST"])
def admin_fix_decide(fid):
    sess, go = requireAdmin()
    if go:
        return go
    if not AC.csrfOk(sess):
        abort(403)
    action = (request.form.get("action") or "").strip()
    note = (request.form.get("note") or "").strip()[:NOTE_MAX] or None
    keep = (request.form.get("keep") or "").strip()
    keep = int(keep) if keep.isdigit() else None
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            abort(503)
        try:
            status, err, outcome = decide(conn, cur, fid, action, sess["account"]["email"], note, keep)
        except Exception as exc:                        # noqa: BLE001
            conn.rollback()
            print(f"[fixes] decide {fid} failed ({type(exc).__name__}: {exc})", flush=True)
            return _back("/account/admin/fixes", error=f"Nothing was changed: {type(exc).__name__}: "
                                                       f"{str(exc)[:200]}"), 500
        if err:
            conn.rollback()
            return _back("/account/admin/fixes", error=err), 400
        AC.logEvent(cur, f"fix_{status}", sess["account"]["id"], detail={"fix": fid})
        conn.commit()
        cur.execute("SELECT * FROM fix_request WHERE id = %s", (fid,))
        fx = AC._one(cur)
        mailed = False
        try:
            mailed = notifyRequester(cur, fx, status, outcome, note)
        except Exception as exc:                        # noqa: BLE001
            print(f"[fixes] mail for {fid} failed ({type(exc).__name__}: {exc})", flush=True)
        conn.commit()
    return _back("/account/admin/fixes", notice=f"#{fid} {status}" + ("; the requester was emailed." if mailed else "."))


def main(argv):
    sys.path.insert(0, "scripts")
    sys.path.insert(0, "racecast")
    from database import getConn
    if "--init" in argv:
        with getConn() as conn:
            initTables(conn)
        print("  fix_request: ready")
        return 0
    print("python racecast/fixes.py --init")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
