"""
build_sitemap.py -- the sitemap index and its files, for Google.

    python racecast/build_sitemap.py            # write racecast/static/sitemaps/

Pipeline step 13d. Google finds pages by links, and a site with millions
of athlete pages has most of them three or four clicks deep; the sitemap
hands Google the list. Files go under static/, which nginx serves
directly; app.py serves /sitemap.xml (the index) and /robots.txt.

  sitemap.xml                    the index: one <sitemap> per file below
  sitemap-pages.xml              the handful of fixed pages
  sitemap-schools-N.xml          /school/<name>, primary state per school
  sitemap-courses-N.xml          /course/<name>
  sitemap-meets-N.xml            /meet/xc/<id> and /meet/tf/<id>
  sitemap-athletes-N.xml         /athlete/<id>, athletes with a ranked
                                 season (three or more races), newest
                                 season's last race as lastmod

50,000 URLs per file is the protocol's cap; MAX_PER_FILE stays under it.
"""
import argparse
import os
import sys
from urllib.parse import quote
from xml.sax.saxutils import escape

sys.path.insert(0, "scripts")

MAX_PER_FILE = 45000
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "static", "sitemaps")
FIXED_PAGES = ("/", "/rankings", "/meets", "/conversions", "/predictions",
               "/about")


def chunk(items, n=MAX_PER_FILE):
    items = list(items)
    return [items[i:i + n] for i in range(0, len(items), n)] or [[]]


def urlsetXml(entries):
    """entries: iterable of (path, lastmod|None). Paths are site-relative
    and already URL-safe."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, lastmod in entries:
        out.append("<url><loc>{ORIGIN}" + escape(path) + "</loc>"
                   + (f"<lastmod>{escape(str(lastmod))}</lastmod>" if lastmod else "")
                   + "</url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def indexXml(files):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for name in files:
        out.append("<sitemap><loc>{ORIGIN}/static/sitemaps/" + escape(name)
                   + "</loc></sitemap>")
    out.append("</sitemapindex>")
    return "\n".join(out) + "\n"


def writeSitemaps(by_kind, out_dir, origin):
    """by_kind: {kind: [(path, lastmod), ...]}. Writes the files and the
    index, replacing what is there. Returns the file names written."""
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        if old.startswith("sitemap") and old.endswith(".xml"):
            os.remove(os.path.join(out_dir, old))
    files = []
    for kind, entries in by_kind.items():
        parts = chunk(entries)
        for i, part in enumerate(parts, 1):
            if not part:
                continue
            name = (f"sitemap-{kind}.xml" if len(parts) == 1
                    else f"sitemap-{kind}-{i}.xml")
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                fh.write(urlsetXml(part).replace("{ORIGIN}", origin))
            files.append(name)
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write(indexXml(files).replace("{ORIGIN}", origin))
    return files


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def collect(conn):
    by_kind = {"pages": [(p, None) for p in FIXED_PAGES]}
    with conn.cursor() as cur:
        if _exists(cur, "school_identity"):
            cur.execute("""SELECT DISTINCT school FROM school_identity
                           WHERE is_primary AND school IS NOT NULL""")
            by_kind["schools"] = [("/school/" + quote(r[0], safe=""), None)
                                  for r in cur.fetchall()]
        if _exists(cur, "course_difficulties"):
            cur.execute("""SELECT DISTINCT course_name FROM course_difficulties
                           WHERE course_name IS NOT NULL AND TRIM(course_name) <> ''""")
            by_kind["courses"] = [("/course/" + quote(r[0], safe=""), None)
                                  for r in cur.fetchall()]
        meets = []
        for table, fmt in (("meet_agg_xc", "/meet/xc/{}"), ("meet_agg_tf", "/meet/tf/{}")):
            if _exists(cur, table):
                cur.execute(f"SELECT meet_id FROM {table}")
                meets += [(fmt.format(r[0]), None) for r in cur.fetchall()]
        if meets:
            by_kind["meets"] = meets
        if _exists(cur, "athlete_season"):
            # ranked athletes only: a page Google should show is one with a
            # season on the boards. lastmod tells it which pages moved.
            cur.execute("""
                SELECT person_id, to_char(max(last_race), 'YYYY-MM-DD')
                FROM   athlete_season
                WHERE  n_races >= 3
                GROUP  BY person_id
            """)
            by_kind["athletes"] = [(f"/athlete/{pid}", lm) for pid, lm in cur.fetchall()]
    return by_kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default=os.environ.get("XCP_SITE_ORIGIN",
                                                       "https://racecast.co"))
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()
    from database import getConn
    with getConn() as conn:
        by_kind = collect(conn)
    files = writeSitemaps(by_kind, args.out, args.origin.rstrip("/"))
    for kind, entries in by_kind.items():
        print(f"  {kind:<9} {len(entries):>10,} urls")
    print(f"  {len(files)} files + sitemap.xml in {args.out}")


if __name__ == "__main__":
    main()
