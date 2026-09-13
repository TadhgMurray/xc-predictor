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
"""
import argparse
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


def collect(days):
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    if not os.path.isdir(SITEMAPS):
        return out
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
            if lastmod is None or lastmod == "" or lastmod >= cutoff:
                out.append(loc)
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
    urls = collect(a.days)
    print(f"indexnow: {len(urls):,} urls changed in the last {a.days} days")
    if urls:
        submit(urls, key, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
