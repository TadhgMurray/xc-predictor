# Project: xc-predictor / racecast
# File:    build_social.py
# Purpose: pipeline step 13g_social -- this week's Instagram posts, images
#          and captions, from the boards (racecast/social.py says what is on
#          them and why). The owner picks them up at /account/admin/social.
#
#     python racecast/build_social.py                 # build and render
#     python racecast/build_social.py --no-render     # captions and data only
#     python racecast/build_social.py --state MI --pool hs_m   # one board
#     python racecast/build_social.py --html-dir out/ # also write the .html pages
#
# Environment:
#   XCP_SOCIAL_POOLS   pools to post, default "hs_m,hs_f"
#   XCP_SOCIAL_STATES  states to post, default every state whose board has a
#                      top 10 this week
#   XCP_SOCIAL_PHOTOS  a folder of the owner's photos (see social.py); none =
#                      a plain panel where the photo goes
#   XCP_SOCIAL_DIR     where the PNGs go, default /var/lib/xc-predictor/social
#   XCP_SOCIAL_RENDER  0 = no PNGs (the admin page then shows the live page)
#   CHROME_PATH        a Chrome/Chromium for the screenshots; else
#                      scripts/chrome_path.py's list, else Playwright's own
#
# ★ ONE WEEK'S POSTS ARE REWRITTEN WHOLE. social_post keeps (week, sport,
#   pool, scope, kind); every run deletes the current week's rows for the
#   sport and writes them again, so a run on Monday replaces Saturday's
#   numbers. Eight weeks are kept; PNG files of older weeks are deleted with
#   their rows.
#
# ! A REPORT STEP. Nothing later in the run reads social_post, and a
#   failure here (no browser, no snapshot history yet) prints and exits 0:
#   posts are not worth a red pipeline.
import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import social as S                                      # noqa: E402

KEEP_WEEKS = 8
MIN_STATE_ROWS = S.TOP_N

DDL = """
CREATE TABLE IF NOT EXISTS social_post (
    id        bigserial PRIMARY KEY,
    week      date  NOT NULL,
    sport     text  NOT NULL,
    pool      text  NOT NULL,
    scope     text  NOT NULL,          -- a state code, or 'US'
    kind      text  NOT NULL,
    data      jsonb NOT NULL,
    caption   text  NOT NULL,
    png_path  text,
    built_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (week, sport, pool, scope, kind)
);
"""


def _json(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return float(v) if hasattr(v, "__float__") else str(v)


def liveSport(cur):
    import breakouts as B
    return B.defaultSport(B.latestDates(cur))


def boardStates(cur, sport, pool, year, week):
    cur.execute("""SELECT state FROM season_rank_weekly
                   WHERE week = %s AND sport = %s AND pool = %s AND year = %s
                     AND state IS NOT NULL AND state_rank <= %s
                   GROUP BY state HAVING count(*) >= %s ORDER BY state""",
                (week, sport, pool, year, S.TOP_N, MIN_STATE_ROWS))
    return [r["state"] for r in cur.fetchall()]


def browserPath():
    p = os.environ.get("CHROME_PATH")
    if p and os.path.exists(p):
        return p
    try:
        from chrome_path import chromePath
        return chromePath()
    except Exception:                                   # noqa: BLE001
        return None


class Renderer:
    """One headless browser for the run; render(html, png) screenshots a
    1080x1350 page. The page loads its fonts, logo and photo from file://
    paths, so nothing is fetched from the network."""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = browserPath()
        kw = {"headless": True}
        if exe:
            kw["executable_path"] = exe
        self._b = self._pw.chromium.launch(**kw)
        self._pg = self._b.new_page(viewport={"width": 1080, "height": 1350})
        self._tmp = tempfile.mkdtemp(prefix="social_")

    def render(self, html, png):
        path = os.path.join(self._tmp, "post.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        self._pg.goto("file://" + path)
        self._pg.evaluate("document.fonts.ready")
        self._pg.wait_for_timeout(50)
        self._pg.screenshot(path=png)

    def close(self):
        try:
            self._b.close()
            self._pw.stop()
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)


def fileAssets(photo_folder, photo_name):
    st = os.path.join(_ROOT, "racecast", "static")
    return {"font_bs": "file://" + os.path.join(st, "social", "BigShouldersDisplay-900.woff2"),
            "font_an": "file://" + os.path.join(st, "social", "ArchivoNarrow.woff2"),
            "logo": "file://" + os.path.join(st, "logo.svg"),
            "photo": "file://" + os.path.join(photo_folder, photo_name) if photo_name else None}


def main():
    ap = argparse.ArgumentParser(description="Build this week's Instagram posts.")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--state", help="one state (with --pool)")
    ap.add_argument("--pool")
    ap.add_argument("--html-dir", help="also write each post's .html here")
    a = ap.parse_args()
    t0 = time.time()
    pools = [a.pool] if a.pool else [p.strip() for p in
                                    (os.environ.get("XCP_SOCIAL_POOLS") or "hs_m,hs_f").split(",") if p.strip()]
    want = ([a.state.upper()] if a.state else
            [s.strip().upper() for s in (os.environ.get("XCP_SOCIAL_STATES") or "").split(",") if s.strip()])
    render = not a.no_render and os.environ.get("XCP_SOCIAL_RENDER", "1") != "0"
    folder, photos = S.photoFiles()
    print(f"[social] pools {','.join(pools)}; photos: {len(photos)} in {folder or '(XCP_SOCIAL_PHOTOS unset)'}")

    import psycopg2.extras
    from database import getConn
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.season_rank_weekly') AS t")
            if cur.fetchone()["t"] is None:
                print("[social] season_rank_weekly missing (10g2_rank_snapshot) -- nothing to post")
                return
            sport = liveSport(cur)
            from predict import _currentSeason
            year = int(_currentSeason(cur, sport))
            posts = []
            week0 = None
            for pool in pools:
                cur.execute("""SELECT DISTINCT week FROM season_rank_weekly
                               WHERE sport = %s AND pool = %s AND year = %s
                               ORDER BY week DESC LIMIT 2""", (sport, pool, year))
                weeks = [r["week"] for r in cur.fetchall()]
                if not weeks:
                    print(f"[social] {sport} {pool} {year}: no snapshot week yet")
                    continue
                if len(weeks) < 2:
                    print(f"[social] {sport} {pool}: one week stored -- moves read as unchanged")
                states = want or boardStates(cur, sport, pool, year, weeks[0])
                if not a.state:
                    try:
                        p = S.nationalPost(cur, sport, pool, year, weeks)
                        if p:
                            posts.append(p)
                    except Exception as exc:            # noqa: BLE001
                        conn.rollback()
                        print(f"[social] national {pool}: {type(exc).__name__}: {exc}")
                for st in states:
                    try:
                        posts += S.buildPosts(cur, sport, pool, st, year, weeks)
                    except Exception as exc:            # noqa: BLE001
                        conn.rollback()
                        print(f"[social] {st} {pool}: {type(exc).__name__}: {exc}")
                    # ★ THE TEAM BOARD AND THE ALL-TIME LIST (owner, 2026-11-09):
                    #   two more post types per state, each in its own savepoint
                    #   so a slow or failed board costs only that post
                    # ★ AND THE STATE MEET'S ODDS AND THE WEEK'S BIGGEST RACE
                    #   (owner, 2026-11-09: "predictions, recap and team
                    #   win-chance posts ... build these rn")
                    for build in (lambda: S.teamsPost(cur, sport, pool, st, year, weeks[0]),
                                  lambda: S.alltimePost(cur, sport, pool, st, weeks[0]),
                                  lambda: S.picksPosts(cur, sport, pool, st, weeks[0]),
                                  lambda: S.recapPost(cur, sport, pool, st, weeks[0])):
                        try:
                            cur.execute("SAVEPOINT sp_extra")
                            cur.execute("SET LOCAL statement_timeout = '60s'")
                            got = build()
                            cur.execute("RELEASE SAVEPOINT sp_extra")
                            if isinstance(got, list):
                                posts += got
                            elif got:
                                posts.append(got)
                        except Exception as exc:        # noqa: BLE001
                            cur.execute("ROLLBACK TO SAVEPOINT sp_extra")
                            print(f"[social] {st} {pool} extra: {type(exc).__name__}: {str(exc)[:160]}")
                print(f"[social] {sport} {pool} week of {weeks[0]}: {len(states)} states")
                week0 = max(week0, weeks[0]) if week0 else weeks[0]
            # ★ ANY PREDICTED RACE (owner, 2026-11-09: "What about other
            #   predicted races like NCAAs?"): the championships of the next
            #   week (NCAA, NXN, NAIA, state finals) and each state's biggest
            #   stored forecast, as "who wins" posts (social.previewPosts).
            #   A championship with no stored forecast runs the model once
            #   per race, so there is a cap and a time budget.
            if (sport == "XC" and week0 and not a.state
                    and os.environ.get("XCP_SOCIAL_PREVIEWS", "1") != "0"):
                t2 = time.time()
                cap = int(os.environ.get("XCP_SOCIAL_PREVIEW_MAX") or 40)
                budget = 60 * float(os.environ.get("XCP_SOCIAL_PREVIEW_MINUTES") or 15)
                try:
                    conn.rollback()
                    targets = S.previewTargets(cur)
                except Exception as exc:                # noqa: BLE001
                    conn.rollback()
                    print(f"[social] preview races: {type(exc).__name__}: {str(exc)[:160]}")
                    targets = []
                n_prev = 0
                for t in targets[:cap]:
                    if time.time() - t2 > budget:
                        print(f"[social] previews: out of time after {n_prev} race(s)")
                        break
                    # ! NO SAVEPOINT HERE (the server, 2026-11-09: "savepoint
                    #   sp_prev does not exist"): the prediction code ends
                    #   the transaction itself on a failure it handles
                    #   (upcoming_preview._endTransaction, predict), which
                    #   drops any savepoint around it. These are reads, so a
                    #   whole-transaction rollback loses nothing.
                    try:
                        conn.rollback()
                        cur.execute("SET statement_timeout = '180s'")
                        got = S.previewPosts(cur, sport, week0, *t)
                        posts += got
                        n_prev += bool(got)
                        if not got:
                            print(f"[social] preview {t}: no post (no prediction, or under "
                                  f"{S.PREVIEW_MIN_FIELD} runners)")
                    except Exception as exc:            # noqa: BLE001
                        conn.rollback()
                        print(f"[social] preview {t}: {type(exc).__name__}: {str(exc)[:160]}")
                conn.rollback()
                cur.execute("RESET statement_timeout")
                print(f"[social] previews: {n_prev} of {len(targets)} race(s) "
                      f"[{time.time() - t2:.0f}s]")
            conn.rollback()
    if not posts:
        print("[social] no posts this run")
        return

    for p in posts:
        p["photo"] = S.pickPhoto(photos, S.postKey(p), S.scopeState(p))
        p["caption"] = S.caption(p)

    # ★ THE COMPOSER DRAWS THEM (racecast/social_compose.py, owner-approved
    #   2026-11-09): each post's body is defaultSpec's pick, which turns over by
    #   state and week; the owner re-composes any of them on
    #   /account/admin/social/compose. Crests come from the start-up cache.
    import social_compose as C
    try:
        import school_logo as SL
        SL.loadCrests(getConn)
    except Exception as exc:                            # noqa: BLE001
        print(f"[social] crests not loaded ({type(exc).__name__}: {exc}); posts draw without them")
    out = S.outDir()
    rend = None
    if render:
        try:
            os.makedirs(out, exist_ok=True)
            rend = Renderer()
        except Exception as exc:                        # noqa: BLE001
            print(f"[social] no PNGs this run -- the browser did not start "
                  f"({type(exc).__name__}: {str(exc)[:200]}); the admin page shows the live pages")
            rend = None
    n_png = 0
    t1 = time.time()
    for p in posts:
        p["png_path"] = None
        p["spec"] = C.defaultSpec(p)
        htmls = C.pages(p, p["spec"], C.fileAssets(None))
        name = f'{p["week"]}_{p["sport"]}_{p["pool"]}_{p["scope"].replace("~", "_")}_{p["kind"]}'
        if a.html_dir:
            os.makedirs(a.html_dir, exist_ok=True)
            for i, html in enumerate(htmls):
                with open(os.path.join(a.html_dir, f"{name}{'_' + str(i + 1) if i else ''}.html"), "w",
                          encoding="utf-8") as f:
                    f.write(html)
            with open(os.path.join(a.html_dir, name + ".txt"), "w", encoding="utf-8") as f:
                f.write(p["caption"] + "\n")
        if rend:
            try:
                paths = []
                for i, html in enumerate(htmls):
                    path = os.path.join(out, f"{name}{'_' + str(i + 1) if i else ''}.png")
                    rend.render(html, path)
                    paths.append(path)
                p["png_path"] = paths[0]
                p["png_pages"] = paths
                n_png += len(paths)
            except Exception as exc:                    # noqa: BLE001
                print(f"[social] {name}: render failed ({type(exc).__name__}: {str(exc)[:160]})")
    if rend:
        rend.close()
        print(f"[social] {n_png:,} PNGs in {out} [{time.time() - t1:.0f}s]")

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            week = posts[0]["week"]
            sports = sorted({p["sport"] for p in posts})
            if a.state:
                cur.execute("""DELETE FROM social_post WHERE week = %s AND scope = %s AND pool = ANY(%s)""",
                            (week, a.state.upper(), pools))
            else:
                cur.execute("""DELETE FROM social_post WHERE week = %s AND sport = ANY(%s)
                                 AND (pool = ANY(%s) OR kind IN ('preview', 'teampreview'))""",
                            (week, sports, pools))
            for p in posts:
                cur.execute("""INSERT INTO social_post (week, sport, pool, scope, kind, data, caption, png_path)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (week, sport, pool, scope, kind) DO UPDATE
                               SET data = EXCLUDED.data, caption = EXCLUDED.caption,
                                   png_path = EXCLUDED.png_path, built_at = now()""",
                            (p["week"], p["sport"], p["pool"], p["scope"], p["kind"],
                             json.dumps(p, default=_json), p["caption"], p["png_path"]))
            cur.execute("""DELETE FROM social_post
                           WHERE week < (SELECT max(week) FROM social_post) - %s * 7
                           RETURNING png_path""", (KEEP_WEEKS,))
            gone = [r[0] for r in cur.fetchall() if r[0]]
            conn.commit()
    for path in gone:
        try:
            os.remove(path)
        except OSError:
            pass
    by = {}
    for p in posts:
        by[p["kind"]] = by.get(p["kind"], 0) + 1
    print(f"[social] {len(posts):,} posts for the week of {posts[0]['week']}: "
          + ", ".join(f"{k} {by.get(k, 0)}" for k in S.KINDS)
          + f" -- /account/admin/social [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    try:
        main()
    except Exception:                                   # noqa: BLE001
        traceback.print_exc()
        print("[social] failed -- report step, the run goes on")
