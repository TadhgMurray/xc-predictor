"""
activity.py -- the owner's activity feed: what the site's machinery did,
newest first. Served at /account/status/activity (admins only).

★ WHY (owner, 2026-10-22: "something on the site that shows notifications,
  like when a pipeline step runs, what changed and how it changed accuracy,
  the latest scraped races"). Each of these was a log folder, a line in
  engine/data/run_history.jsonl or a query on the server. One feed:

    pipeline   each full run (logs/2*/summary.log) and nightly update
               (logs/nightly_*/SUMMARY.txt): steps done, failed, how long
    accuracy   each full run's fair tests and the owner's sentinel cases
               against the run before (run_history.jsonl, written by
               scripts/run_report.py), each marked better / worse
    scraped    meets whose results were scraped or re-scraped, per day,
               both feeds and both sports
    kailo      our predictions recorded before a Kailo race (the clean test)

  "New" is everything since the reader's last visit (a cookie), so the page
  works as a notification list without a table to keep.

  Two audiences: the admin page shows every event with its detail; the
  public page /updates (publicFeed) says the same things for a reader.

! EVERY SOURCE STANDS ALONE, as on the status page: a missing log folder,
  an unreadable history line or a slow query costs that source, never the
  page. READ ONLY.
"""
import datetime
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_ROOT = os.environ.get("XCP_LOG_ROOT") or os.path.join(ROOT, "logs")
HISTORY = os.path.join(ROOT, "engine", "data", "run_history.jsonl")
KAILO_STORE = os.path.join(ROOT, "engine", "data", "kailo")

_NIGHTLY_LINE = re.compile(r"^\s+(\S+) (ok|FAILED)\s*(?:\(exit (\d+)\))?\s*(?:after\s*)?\(?(\d+)s\)?")


# ---- pure parts ----------------------------------------------------------

def parseNightly(text):
    """[{"step", "ok", "seconds"}] from a nightly SUMMARY.txt: "  name ok
    (12s)" / "  name FAILED (exit 1) after 12s". Pure."""
    out = []
    for line in (text or "").splitlines():
        m = _NIGHTLY_LINE.match(line)
        if m:
            out.append({"step": m.group(1), "ok": m.group(2) == "ok",
                        "seconds": int(m.group(4))})
    return out


def runStarted(dirname):
    """The start time in a run folder's name (20261022_135400 or
    nightly_20261022_020000), or None. Pure."""
    m = re.search(r"(\d{8})_(\d{6})", dirname or "")
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def runEvent(kind, dirname, steps, open_steps=()):
    """One pipeline run as a feed event. Pure."""
    failed = [s["step"] for s in steps if not s["ok"]]
    secs = sum(s.get("seconds") or 0 for s in steps)
    if failed:
        level = "bad"
    elif open_steps:
        level = "warn"
    else:
        level = "ok"
    name = "Full pipeline" if kind == "pipeline" else "Nightly update"
    bits = [f"{len(steps)} step{'s' if len(steps) != 1 else ''} done"]
    if failed:
        bits.append(f"{len(failed)} failed: {', '.join(failed[:6])}"
                    + (" ..." if len(failed) > 6 else ""))
    if open_steps:
        bits.append(f"running or stopped in {', '.join(open_steps[:3])}")
    if secs:
        bits.append(f"{secs // 3600}h {secs % 3600 // 60}m" if secs >= 3600
                    else f"{secs // 60}m {secs % 60}s")
    return {"at": runStarted(dirname), "kind": kind, "level": level,
            "title": f"{name} {dirname}", "summary": " · ".join(bits),
            "detail": [f"{'ok  ' if s['ok'] else 'FAIL'} {s['step']} ({s.get('seconds', 0)}s)"
                       for s in steps]}


def verdict(old, new, better, tol=1e-9):
    """'better' / 'worse' / 'same' / None (no direction) for one metric.
    better: 'low', 'high', 'abs' (closer to 0) or None. Pure."""
    if old is None or new is None or better is None:
        return None
    if abs(new - old) <= tol:
        return "same"
    if better == "low":
        return "better" if new < old else "worse"
    if better == "high":
        return "better" if new > old else "worse"
    if better == "abs":
        return "better" if abs(new) < abs(old) else "worse"
    return None


def metricDeltas(prev, cur, specs):
    """[(label, old, new, verdict)] for every metric either run has, in the
    specs' order; specs: [(key, label, better)]. Pure."""
    out = []
    for key, label, better in specs:
        o, n = (prev or {}).get(key), (cur or {}).get(key)
        if o is None and n is None:
            continue
        out.append((label, o, n, verdict(o, n, better)))
    return out


def accuracyEvent(rec, prev, specs):
    """One run's metrics against the run before, as a feed event. Pure."""
    d = metricDeltas((prev or {}).get("metrics"), rec.get("metrics"), specs)
    moved = [x for x in d if x[3] in ("better", "worse")]
    n_b = sum(1 for x in moved if x[3] == "better")
    n_w = len(moved) - n_b
    level = "bad" if n_w > n_b else ("ok" if n_b else "info")
    top = sorted(moved, key=lambda x: -abs((x[2] or 0) - (x[1] or 0)))[:3]
    summary = (f"{n_b} better, {n_w} worse than the run before"
               + (": " + "; ".join(f"{lab} {_fmt(o)} -> {_fmt(n)}" for lab, o, n, _v in top)
                  if top else "") if prev else "first run on record")
    try:
        at = datetime.datetime.fromisoformat(rec.get("at") or "")
    except ValueError:
        at = runStarted(rec.get("run"))
    return {"at": at, "kind": "accuracy", "level": level,
            "metrics": rec.get("metrics") or {},
            "prev_metrics": (prev or {}).get("metrics") or {},
            "title": f"Accuracy after run {rec.get('run') or '?'}",
            "summary": summary,
            "detail": [f"{'+' if v == 'better' else '-' if v == 'worse' else ' '} {lab}: "
                       f"{_fmt(o)} -> {_fmt(n)}" for lab, o, n, v in d]}


def _fmt(x):
    if x is None:
        return "-"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.3g}" if abs(x) < 0.01 else f"{x:.2f}"


def markNew(events, seen):
    """Flag events after `seen` (a datetime or None = everything is new).
    Pure; returns how many are new."""
    n = 0
    for e in events:
        e["new"] = bool(e.get("at") and (seen is None or e["at"] > seen))
        n += e["new"]
    return n


# ---- the sources -----------------------------------------------------------

def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def pipelineEvents(log_root=LOG_ROOT, runs=10):
    import site_status
    out = []
    for d in site_status.runDirs(log_root)[:runs]:
        r = site_status.pipelineRun(d)
        out.append(runEvent("pipeline", r["dir"], r["steps"],
                            [s["step"] for s in r["open"]]))
    nightly = sorted((d for d in glob.glob(os.path.join(log_root, "nightly_*"))
                      if os.path.isdir(d)), reverse=True)[:runs]
    for d in nightly:
        text = _read(os.path.join(d, "SUMMARY.txt"))
        if text is None:
            continue
        out.append(runEvent("nightly", os.path.basename(d), parseNightly(text)))
    return out


def _metricSpecs():
    """[(key, label, better)] from scripts/run_report.py -- the same list the
    pipeline's REPORT.txt prints -- or [] when it cannot be imported."""
    scripts = os.path.join(ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    try:
        import run_report
    except Exception:                                   # noqa: BLE001
        return []
    specs = [(k, lab, b) for k, lab, _f, _rx, b in getattr(run_report, "LOG_METRICS", [])]
    specs += [(k, lab, b) for k, lab, _sql, b in getattr(run_report, "SENTINELS", [])]
    return specs


def accuracyEvents(history=HISTORY, runs=10):
    text = _read(history)
    if not text:
        return []
    recs = []
    for line in text.splitlines():
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    specs = _metricSpecs()
    out = []
    for i in range(max(0, len(recs) - runs), len(recs)):
        out.append(accuracyEvent(recs[i], recs[i - 1] if i else None, specs))
    return out


_SCRAPED_SQL = """
    SELECT %(sport)s AS sport, source, meet_id, min(date), count(*),
           max(scraped_at)::text
    FROM   {table}
    WHERE  scraped_at >= %(since)s
    GROUP  BY source, meet_id
    ORDER  BY max(scraped_at) DESC
    LIMIT  %(lim)s
"""


def _meetNames(cur, rows):
    """{(sport, source, meet_id): name} for the scraped meets, each feed's
    own table; a table that fails costs only its names."""
    names = {}
    want = {}
    for sport, source, mid, *_ in rows:
        want.setdefault((sport, source), set()).add(mid)
    queries = {
        ("XC", "anet"): "SELECT meet_id, max(meet_name) FROM meets WHERE meet_id = ANY(%s) GROUP BY meet_id",
        ("TF", "anet"): "SELECT meet_id, max(meet_name) FROM meets_tf WHERE meet_id = ANY(%s) GROUP BY meet_id",
        ("XC", "tfrrs"): "SELECT meet_id, max(meet_name) FROM meets_tfrrs WHERE sport = 'XC' AND meet_id = ANY(%s) GROUP BY meet_id",
        ("TF", "tfrrs"): "SELECT meet_id, max(meet_name) FROM meets_tfrrs WHERE sport = 'TF' AND meet_id = ANY(%s) GROUP BY meet_id",
    }
    for (sport, source), ids in want.items():
        sql = queries.get((sport, source))
        if not sql:
            continue
        try:
            cur.execute("SAVEPOINT names")
            cur.execute(sql, (list(ids),))
            for mid, name in cur.fetchall():
                names[(sport, source, mid)] = name
            cur.execute("RELEASE SAVEPOINT names")
        except Exception:                               # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT names")
    return names


def scrapeEvents(conn, days=3, limit=400):
    """One event per day: the meets scraped (or re-scraped) that day, both
    sports and both feeds, the biggest first."""
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    rows = []
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '8s'")
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            cur.execute(_SCRAPED_SQL.format(table=table),
                        {"sport": sport, "since": since, "lim": limit})
            rows += cur.fetchall()
        names = _meetNames(cur, rows)
    conn.rollback()
    return scrapeDays(rows, names)


def scrapeDays(rows, names):
    """rows: (sport, source, meet_id, race_date, n, scraped_at text). Pure."""
    by_day = {}
    for sport, source, mid, day, n, scraped in rows:
        key = (scraped or "")[:10]
        by_day.setdefault(key, []).append((n, sport, source, mid, day, scraped))
    out = []
    for key, items in by_day.items():
        items.sort(reverse=True)
        n_rows = sum(i[0] for i in items)
        last = max((i[5] or "") for i in items)
        try:
            at = datetime.datetime.fromisoformat(last[:19].replace(" ", "T"))
        except ValueError:
            at = None
        by_feed = {}
        for i in items:
            by_feed[(i[1], i[2])] = by_feed.get((i[1], i[2]), 0) + 1
        n_xc = sum(1 for i in items if i[1] == "XC")
        n_tf = len(items) - n_xc
        pub_bits = ([f"cross country {n_xc} meet{'s' if n_xc != 1 else ''}"] if n_xc else []) + \
                   ([f"track {n_tf} meet{'s' if n_tf != 1 else ''}"] if n_tf else [])
        out.append({"at": at, "kind": "scraped", "level": "info",
                    "pub_title": f"New results: {len(items):,} meet{'s' if len(items) != 1 else ''}, "
                                 f"{n_rows:,} results",
                    "pub_summary": " · ".join(pub_bits),
                    "pub_detail": [f"{'XC' if sp == 'XC' else 'Track'} · {(day or '')[:10]} · "
                                   f"{names.get((sp, src, mid)) or 'meet ' + str(mid)} ({n:,})"
                                   for n, sp, src, mid, day, _s in items[:40]],
                    "title": f"Scraped {key}: {len(items):,} meet{'s' if len(items) != 1 else ''}, "
                             f"{n_rows:,} results",
                    "summary": " · ".join(f"{sp} {src} {k}" for (sp, src), k in sorted(by_feed.items())),
                    "detail": [f"{n:>5} {sp} {src} {(day or '')[:10]}  {names.get((sp, src, mid)) or 'meet ' + str(mid)}"
                               for n, sp, src, mid, day, _s in items[:40]],
                    "links": [((names.get((sp, src, mid)) or f"meet {mid}")[:40],
                               f"/meet/{sp.lower()}/{mid}")
                              for n, sp, src, mid, day, _s in items[:8]]})
    return out


def kailoEvents(store=KAILO_STORE, days=14):
    out = []
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    for path in glob.glob(os.path.join(store, "*.json")):
        name = os.path.basename(path)
        if name.startswith("ratings_"):
            kind_title = f"Ratings snapshot saved ({name[8:-5]})"
        else:
            kind_title = f"Predictions recorded before {name[:-5]}"
        at = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        if at >= cutoff:
            out.append({"at": at, "kind": "kailo", "level": "info", "title": kind_title,
                        "summary": "for the clean test against Kailo (kailo_compare / kailo_ratings)",
                        "detail": []})
    return out


# ---- the public feed ----------------------------------------------------

# ★ WHAT READERS SEE (owner, 2026-10-22: "why would it be admin only, it's
#   supposed to inform the ppl"). The same events, said for a reader: new
#   results with their meets, when ratings were recomputed, and the one
#   accuracy number that needs no glossary. NOT on it: step names and logs,
#   the owner's sentinel cases (named athletes' results), competitor
#   comparisons, the Kailo recordings -- those stay on the admin page.
PUBLIC_METRICS = {
    # key: (label, scale, better)
    "holdout_race_sd": ("Typical miss on a race the ratings had not seen", 100.0, "low"),
}


def publicEvent(e):
    """The reader's version of one feed event, or None to leave it out. Pure."""
    kind, level = e.get("kind"), e.get("level")
    base = {"at": e.get("at"), "kind": kind, "level": level, "links": e.get("links") or []}
    if kind == "scraped":
        return dict(base, title=e.get("pub_title") or e.get("title"),
                    summary=e.get("pub_summary") or "", detail=e.get("pub_detail") or [])
    if kind == "pipeline":
        if level == "bad":
            return dict(base, title="A full ratings update ran into a problem",
                        summary="The site keeps the last good numbers until it is fixed.",
                        detail=[], links=[])
        if level == "warn":
            return dict(base, title="Full ratings update in progress",
                        summary="Every result is being re-rated; pages update when it finishes.",
                        detail=[], links=[])
        return dict(base, title="Every rating recomputed",
                    summary="A full update: every result re-rated, courses and conditions refitted.",
                    detail=[], links=[])
    if kind == "nightly":
        if level == "bad":
            return dict(base, title="Last night's update ran into a problem",
                        summary="Some new results may wait for the next one.", detail=[], links=[])
        return dict(base, title="New results rated",
                    summary="Last night's update: newly scraped races rated and the boards refreshed.",
                    detail=[], links=[])
    if kind == "accuracy":
        lines = []
        for key, (label, scale, better) in PUBLIC_METRICS.items():
            new, old = (e.get("metrics") or {}).get(key), (e.get("prev_metrics") or {}).get(key)
            if new is None:
                continue
            line = f"{label}: {new * scale:.2f}%"
            v = verdict(old, new, better)
            if v in ("better", "worse") and old is not None:
                line += f" ({'down' if new < old else 'up'} from {old * scale:.2f}%, {v})"
            lines.append(line)
        if not lines:
            return None
        return dict(base, title="Accuracy after the latest full update", summary=lines[0],
                    detail=lines[1:], links=[])
    return None


def publicFeed(conn, seen=None):
    """The reader's feed: {"events", "n_new", "latest"}; a source that fails
    is left out quietly (the admin page says why)."""
    fd = feed(conn, None)
    events = [p for p in (publicEvent(e) for e in fd["events"]) if p and p.get("at")]
    n_new = markNew(events, seen)
    return {"events": events, "n_new": n_new,
            "latest": events[0]["at"] if events else None}


_PUBLIC_CACHE = {"at": None, "data": None}
PUBLIC_TTL_S = 300


def publicFeedCached(get_conn):
    """publicFeed, built at most every PUBLIC_TTL_S per process (the page is
    public; the logs and the scrape query are not free). `seen` is applied
    by the caller, per reader."""
    now = datetime.datetime.now()
    if _PUBLIC_CACHE["data"] is None or _PUBLIC_CACHE["at"] is None or \
            (now - _PUBLIC_CACHE["at"]).total_seconds() > PUBLIC_TTL_S:
        with get_conn() as conn:
            _PUBLIC_CACHE["data"] = publicFeed(conn, None)
        _PUBLIC_CACHE["at"] = now
    return _PUBLIC_CACHE["data"]


def feed(conn, seen=None):
    """{"events": newest first, "n_new", "errors": {source: message}}."""
    events, errors = [], {}
    sources = (("pipeline", lambda: pipelineEvents()),
               ("accuracy", lambda: accuracyEvents()),
               ("scraped", lambda: scrapeEvents(conn)),
               ("kailo", lambda: kailoEvents()))
    for name, fn in sources:
        try:
            events += fn()
        except Exception as exc:                        # noqa: BLE001
            errors[name] = f"{type(exc).__name__}: {exc}"[:300]
            try:
                conn.rollback()
            except Exception:                           # noqa: BLE001
                pass
    events = [e for e in events if e.get("at")]
    events.sort(key=lambda e: e["at"], reverse=True)
    n_new = markNew(events, seen)
    return {"events": events, "n_new": n_new, "errors": errors}
