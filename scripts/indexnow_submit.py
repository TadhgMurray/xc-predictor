"""
indexnow_submit.py -- tell Bing (and everyone on IndexNow) what changed.
    python scripts/indexnow_submit.py [--days 3] [--dry-run]
Pipeline step 13e, after the sitemap. Reads the sitemap files under
racecast/static/sitemaps/, takes every URL whose lastmod is within --days
(plus the fixed and landing pages, which have none and always change), and
POSTs them to api.indexnow.org in batches of 10,000. Google ignores
IndexNow; Bing, Yandex, Seznam and Naver act on it within hours, where the
sitemap alone can take weeks.
Needs XCP_INDEXNOW_KEY (the key app.py serves at /<key>.txt); without it
the script says so and exits 0, so a box without the key does not fail
the run.

A URL with no lastmod (schools, courses) counts as changed only when it is
new since the last run -- see STATE below (2026-10-10).
"""
import argparse
import html
import datetime as dt
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SITEMAPS = os.path.join(HERE, "..", "racecast", "static", "sitemaps")
ORIGIN = os.environ.get("XCP_SITE_ORIGIN", "https://racecast.co").rstrip("/")
ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 10000
_URL = re.compile(r"<url><loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?</url>")


# ⚠ WHAT "CHANGED" MEANS FOR A URL WITH NO lastmod (2026-10-10). Schools,
#   courses and (until today) meets carry none, and the old test --
#   "no lastmod: submit it" -- posted every one of them, hundreds of
#   thousands of unchanged URLs, every night. IndexNow is for changed pages;
#   an engine that is sent the same unchanged list daily learns to discount
#   the key. Now a URL without a lastmod is submitted when it is one of the
#   fixed and landing pages (sitemap-pages*: the boards, which change every
#   run) or when it is NEW -- not in the set seen on the previous run, kept
#   in STATE (outside static/, so nginx never serves it). The first run
#   with no state file seeds it and submits none of them.
STATE = os.environ.get("XCP_INDEXNOW_STATE",
                       os.path.join(HERE, "..", "data", "indexnow_seen.txt.gz"))


def _readSeen(path):
    import gzip
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return {line.rstrip("\n") for line in fh if line.strip()}
    except (OSError, EOFError):
        return None


def _writeSeen(path, urls):
    import gzip
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for u in sorted(urls):
            fh.write(u + "\n")
    os.replace(tmp, path)


def pick(entries, cutoff, seen):
    """entries: [(file name, loc, lastmod or "")]. Returns (urls to submit,
    the no-lastmod set to remember). `seen` None means no state yet."""
    out, undated = [], set()
    for name, loc, lastmod in entries:
        if lastmod:
            if lastmod >= cutoff:
                out.append(loc)
            continue
        if name.startswith("sitemap-pages"):
            out.append(loc)
            continue
        undated.add(loc)
        if seen is not None and loc not in seen:
            out.append(loc)
    return out, undated


def collect(days, state=None, update_state=True):
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    if not os.path.isdir(SITEMAPS):
        return out
    entries = []
    # ! THE FILES ARE GZIPPED NOW (build_sitemap, 2026-09-13). Reading only
    #   ".xml" silently found nothing at all -- and this script's whole job
    #   is to be the fast path, so finding nothing looks exactly like
    #   having nothing to say.
    import gzip
    for name in sorted(os.listdir(SITEMAPS)):
        if not name.startswith("sitemap-"):
            continue
        path = os.path.join(SITEMAPS, name)
        if name.endswith(".xml.gz"):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                text = fh.read()
        elif name.endswith(".xml"):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        else:
            continue
        for loc, lastmod in _URL.findall(text):
            entries.append((name, html.unescape(loc), lastmod or ""))
    state = state or STATE
    seen = _readSeen(state)
    out, undated = pick(entries, cutoff, seen)
    if seen is None:
        print(f"indexnow: no state at {state}; seeding it with {len(undated):,} "
              f"undated urls (none submitted this run)")
    if update_state:
        _writeSeen(state, undated)
    return out


def submit(urls, key, dry):
    host = ORIGIN.split("//", 1)[-1]
    for i in range(0, len(urls), BATCH):
        body = {"host": host, "key": key,
                "keyLocation": f"{ORIGIN}/{key}.txt",
                "urlList": urls[i:i + BATCH]}
        if dry:
            print(f"  would POST {len(body['urlList'])} urls (first {body['urlList'][0]})")
            continue
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                print(f"  POST {len(body['urlList'])} urls -> {resp.status}")
        except urllib.error.HTTPError as exc:
            print(f"  POST {len(body['urlList'])} urls -> HTTP {exc.code}: "
                  f"{exc.read()[:200]!r}")
        except Exception as exc:                          # noqa: BLE001
            print(f"  POST failed: {exc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("XCP_INDEXNOW_KEY", "").strip()
    if not key:
        print("indexnow: XCP_INDEXNOW_KEY is not set; nothing submitted")
        return 0
    urls = collect(a.days, update_state=not a.dry_run)
    print(f"indexnow: {len(urls):,} urls changed in the last {a.days} days")
    if urls:
        submit(urls, key, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
