# Project: xc-predictor / racecast
# File:    monitor.py
# Purpose: the wiring for usage counts (usage.py) and error monitoring
#          (app_errors.py), owner-approved 2026-10-10: the beacon endpoint
#          POST /api/b, the two admin pages under /account/status, the
#          request hooks, and the one background thread per worker that
#          writes both buffers and sends the batched error mail.
#
#     python racecast/monitor.py --init     # create usage_daily + app_error (once)
#     python racecast/monitor.py --check    # are they there; mail on; the slow line
#
# ★ DDL ONCE, NEVER PER REQUEST (accounts.py's and follows.py's rule). A
#   server that has not run --init loses its counts at each flush with one
#   log line per worker (not per hit), and every page works exactly as before.
#
# ! NOTHING HERE CAN BREAK A PAGE. The request side only appends to two
#   in-memory buffers. The database writes and the mail happen on the
#   flusher thread, each step in its own try; a failure is printed once per
#   flush and the buffers keep (a capped amount of) what they held.
#
# ! THE BEACON SETS NO COOKIE AND LOGS NOTHING. 204, no body, no
#   Set-Cookie, nothing printed per hit; the IP and user agent are never
#   read beyond isBot's yes/no. nginx limits it per visitor
#   (deploy/nginx_limits.sh, zone xcp_beacon).

import atexit
import json
import os
import sys
import threading
import time

from flask import Blueprint, Response, abort, g, redirect, render_template, request

import app_errors as E
import usage as U

bp = Blueprint("monitor", __name__)

_STATE = {"gate": None, "known": None, "slow_ms": E.slowRequestMs(),
          "thread": None, "pid": None, "notify_hint": False,
          "wake": threading.Event(), "woke": -1e9, "warned": set()}
WAKE_GAP = 10


def _log(msg):
    print(f"[monitor] {msg}", flush=True)


def _warnOnce(tag, msg):
    """One line per kind of failure per worker until it recovers -- a
    database that is down for an hour is not 60 lines a minute."""
    if tag not in _STATE["warned"]:
        _STATE["warned"].add(tag)
        _log(msg)


# ---- the flusher thread -------------------------------------------------------

def flushNow():
    """Write both buffers and, when something new is waiting, try the mail.
    Called by the thread every FLUSH_SECONDS and at exit. Never raises."""
    try:
        from database import getConn
    except Exception as exc:                             # noqa: BLE001
        _warnOnce("import", f"no database module ({exc})")
        return
    for name, fn in (("usage", U.flush), ("errors", E.flush)):
        if not (len(U.BUFFER) if name == "usage" else len(E.BUFFER)):
            continue
        try:
            with getConn() as conn:
                got = fn(conn)
            if name == "errors" and got[1]:
                _STATE["notify_hint"] = True
            _STATE["warned"].discard(name)
        except Exception as exc:                         # noqa: BLE001
            _warnOnce(name, f"{name} flush failed, kept for the next try "
                            f"({type(exc).__name__}: {str(exc).strip()[:160]})")
    if _STATE["notify_hint"]:
        _STATE["notify_hint"] = _tryNotify(getConn)


def _tryNotify(getConn):
    """True while a mail is still owed (try again next flush)."""
    try:
        import accounts
        to = sorted(accounts.adminEmails())
        if not to or not accounts.mailEnabled():
            _warnOnce("mailoff", "new error groups, but no XCP_ADMIN_EMAILS or "
                                 "mail provider: listed at /account/status/errors only")
            return False
        with getConn() as conn:
            got = E.notifyPending(
                conn, lambda a, s, t: accounts.sendMail(a, s, t, log_as="the admins"),
                to, origin=accounts.siteOrigin())
        if got in ("sent", "none"):
            _STATE["warned"].discard("notify")
        if got == "failed":
            _warnOnce("notify", "error mail failed; will retry next window")
        return got in ("wait", "busy", "failed")
    except Exception as exc:                             # noqa: BLE001
        _warnOnce("notify", f"error mail step failed ({type(exc).__name__}: "
                            f"{str(exc).strip()[:160]})")
        return True


def _loop():
    while True:
        _STATE["wake"].wait(U.FLUSH_SECONDS)
        _STATE["wake"].clear()
        flushNow()


def _ensureThread():
    """Start this worker's flusher on first use. Lazily, and re-checked by
    pid, because gunicorn forks workers from a parent that may have imported
    this module: a thread does not survive a fork."""
    pid = os.getpid()
    if _STATE["pid"] == pid and _STATE["thread"] is not None:
        return
    _STATE["pid"] = pid
    t = threading.Thread(target=_loop, name="rc-monitor-flush", daemon=True)
    _STATE["thread"] = t
    t.start()


atexit.register(flushNow)


# ---- the request hooks ------------------------------------------------------------

def _template():
    rule = getattr(request, "url_rule", None)
    return U.normalizeRule(rule.rule) if rule is not None else U.UNKNOWN_TPL


def _onException(sender, exception, **_kw):
    """got_request_exception: an exception no handler took (a 500).
    ! A psycopg2 blip handled by app._db_blip, and every abort(4xx), never
      gets here -- they are handled, and are not bugs."""
    try:
        held = len(E.BUFFER)
        E.recordException(exception, _template())
        _ensureThread()
        # ★ A NEW GROUP IS WRITTEN NOW, NOT IN A MINUTE, so its mail is not
        #   held back by the flush interval -- but only a NEW one, and at most
        #   every WAKE_GAP seconds: a 500 on every request must not become a
        #   database write per request.
        now = time.monotonic()
        if len(E.BUFFER) > held and now - _STATE["woke"] >= WAKE_GAP:
            _STATE["woke"] = now
            _STATE["wake"].set()
    except Exception:                                    # noqa: BLE001
        pass


def _start():
    if "_mon_t0" not in g:
        g._mon_t0 = time.perf_counter()
        g._mon_wall = time.time()


def _finish(_exc=None):
    try:
        t0 = g.pop("_mon_t0", None)
        if t0 is None:
            return
        ms = (time.perf_counter() - t0) * 1000.0
        if ms < _STATE["slow_ms"] or request.path.startswith("/static/"):
            return
        since = g.pop("_mon_wall", 0)
        try:
            import db_timing
            qs = sorted((q for q in list(db_timing.RECENT)
                         if q["ts"] >= since and q["path"] != "-"),
                        key=lambda q: -q["ms"])[:3]
        except Exception:                                # noqa: BLE001
            qs = []
        E.recordSlow(_template(), ms, qs, _STATE["slow_ms"])
        _ensureThread()
    except Exception:                                    # noqa: BLE001
        pass


def install(app, gate):
    """Wire it in. `gate` is app._statusAdmin: () -> (session, redirect)."""
    from flask import got_request_exception
    _STATE["gate"] = gate
    app.register_blueprint(bp)
    got_request_exception.connect(_onException, app, weak=False)
    app.before_request(_start)
    app.teardown_request(_finish)


# ---- POST /api/b ----------------------------------------------------------------------

def _known():
    if _STATE["known"] is None:
        from flask import current_app
        _STATE["known"] = U.knownTemplates(current_app.url_map)
    return _STATE["known"]


@bp.route("/api/b", methods=["POST"])
def beacon():
    """The beacon. Always 204 and empty -- a dropped beacon looks exactly
    like a counted one, so there is nothing to probe."""
    resp = Response(status=204)
    try:
        import accounts
        if (request.content_length or 0) > U.MAX_BODY:
            return resp
        if not accounts.sameOrigin():
            return resp
        if U.beaconOptedOut(request.headers) or U.isBot(request.headers.get("User-Agent")):
            return resp
        payload = json.loads(request.get_data(cache=False)[:U.MAX_BODY] or b"null")
        rows = U.parseBeacon(payload, _known(), request.host)
        if rows:
            U.BUFFER.add(rows)
            _ensureThread()
        js = E.parseJsError(payload)
        if js:
            tpl = U.pathTemplate((payload or {}).get("t"), _known())
            if E.recordJs(*js, tpl):
                _ensureThread()
    except Exception:                                    # noqa: BLE001
        pass
    return resp


# ---- the admin pages ----------------------------------------------------------------

def _admin():
    gate = _STATE["gate"]
    if gate is None:
        abort(404)
    return gate()


def _cursor(conn):
    import psycopg2.extras
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


@bp.route("/account/status/usage")
def usage_page():
    sess, go = _admin()
    if go:
        return go
    days = 30 if request.args.get("days") == "30" else 7
    from database import getConn
    rep, error = None, None
    try:
        with getConn() as conn:
            with _cursor(conn) as cur:
                cur.execute("SET LOCAL statement_timeout = 8000")
                rep = U.report(cur, days)
    except Exception as exc:                             # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''}"[:200]
    return render_template("status_usage.html", rep=rep, error=error, days=days,
                           held=len(U.BUFFER))


@bp.route("/account/status/errors")
def errors_page():
    sess, go = _admin()
    if go:
        return go
    resolved = request.args.get("show") == "resolved"
    from database import getConn
    data, error = None, None
    try:
        with getConn() as conn:
            with _cursor(conn) as cur:
                cur.execute("SET LOCAL statement_timeout = 8000")
                data = E.listGroups(cur, resolved=resolved)
    except Exception as exc:                             # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''}"[:200]
    return render_template("status_errors.html", data=data, error=error,
                           resolved=resolved, csrf=sess["csrf"],
                           slow_s=_STATE["slow_ms"] / 1000.0,
                           window_min=int(E.NOTIFY_WINDOW.total_seconds() // 60),
                           done=request.args.get("done", "")[:16])


@bp.route("/account/status/errors/resolve", methods=["POST"])
def errors_resolve():
    import accounts
    sess, go = _admin()
    if go:
        return go
    if not accounts.csrfOk(sess):
        abort(403)
    fp = (request.form.get("fingerprint") or "").strip()
    if not (len(fp) == 16 and all(c in "0123456789abcdef" for c in fp)):
        abort(400)
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            E.resolve(cur, fp)
        conn.commit()
    return redirect("/account/status/errors?done=" + fp)


# ---- --init / --check ---------------------------------------------------------------

TABLES = ("usage_daily", "app_error", "app_error_notify")


def initTables(conn):
    """The DDL alone, in its own transaction, under a lock timeout."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(U.DDL)
        cur.execute(E.DDL)
    conn.commit()


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    for d in ("scripts", "racecast"):
        p = os.path.join(os.path.dirname(here), d)
        if p not in sys.path:
            sys.path.insert(0, p)
    if "--init" in argv:
        from database import getConn
        with getConn() as conn:
            initTables(conn)
        print("  " + ", ".join(TABLES) + ": ready")
        return 0
    if "--check" in argv:
        import accounts
        from database import getConn
        with getConn() as conn:
            with conn.cursor() as cur:
                for t in TABLES:
                    cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
                    print(f"  {t:18s} {'ok' if cur.fetchone()[0] else 'MISSING (run --init)'}")
            conn.rollback()
        print(f"  admins:            {len(accounts.adminEmails())} in XCP_ADMIN_EMAILS")
        print(f"  mail:              {'on' if accounts.mailEnabled() else 'OFF (XCP_MAIL_PROVIDER + XCP_MAIL_KEY)'}")
        print(f"  slow request line: {E.slowRequestMs() / 1000:.1f} s")
        return 0
    print("python racecast/monitor.py --init | --check")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
