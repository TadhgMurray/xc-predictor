#!/usr/bin/env python3
"""
check_sitemap.py -- does every kind of URL the sitemap hands Google
actually open? Fetches a sample of each sitemap file through the app itself
(no network, no nginx) and counts the answers.

    cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/check_sitemap.py               # 40 per file
    /srv/venv/bin/python scripts/check_sitemap.py --per-file 200 --kind schools

★ WHY (owner's crawl report, 2026-09-26): Googlebot got 1,385 404s under
  /school/ in two weeks, on days it was re-reading the sitemaps. A sitemap
  that sends the crawler to pages that 404 costs crawl budget and the
  sitemap's credibility with it. This says which kind of URL, and prints
  the ones that failed so the cause can be read off them. READ-ONLY.
"""
import argparse
import collections
import gzip
import os
import random
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")

DIR = os.path.join(_ROOT, "racecast", "static", "sitemaps")
LOC = re.compile(r"<loc>([^<]+)</loc>")


def urls(name):
    path = os.path.join(DIR, name)
    opener = gzip.open if name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return LOC.findall(fh.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--per-file", type=int, default=40)
    ap.add_argument("--kind", default=None, help="only files named sitemap-<kind>...")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    index = os.path.join(DIR, "sitemap.xml")
    if not os.path.exists(index):
        print(f"no sitemap index at {index}")
        return
    with open(index, encoding="utf-8") as fh:
        files = [u.rsplit("/", 1)[1] for u in LOC.findall(fh.read())]
    missing = [f for f in files if not os.path.exists(os.path.join(DIR, f))]
    print(f"index names {len(files)} files; {len(missing)} of them are missing on disk"
          + (": " + ", ".join(missing[:10]) if missing else ""))
    import app as A
    client = A.app.test_client()
    rnd = random.Random(a.seed)
    by_kind = collections.defaultdict(collections.Counter)
    bad = collections.defaultdict(list)
    for f in files:
        if f in missing:
            continue
        kind = re.sub(r"^sitemap-|(-\d+)?\.xml(\.gz)?$", "", f)
        if a.kind and kind != a.kind:
            continue
        us = urls(f)
        for u in rnd.sample(us, min(a.per_file, len(us))):
            path = re.sub(r"^https?://[^/]+", "", u)
            code = client.get(path).status_code
            by_kind[kind][code] += 1
            if code >= 400 and len(bad[kind]) < 15:
                bad[kind].append((code, path))
        print(f"  {f}: {len(us):,} urls, sampled", flush=True)
    print("\nanswers by kind (sampled):")
    for kind, c in sorted(by_kind.items()):
        n = sum(c.values())
        fails = n - c[200]
        print(f"  {kind:<10} {n:>6,} fetched  " + "  ".join(f"{k}: {v:,}" for k, v in sorted(c.items()))
              + (f"   <-- {100.0 * fails / n:.0f}% not 200" if fails else ""))
    for kind, rows in bad.items():
        print(f"\n  {kind}: examples that failed")
        for code, path in rows:
            print(f"    {code}  {path}")


if __name__ == "__main__":
    main()
