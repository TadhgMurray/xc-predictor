#!/usr/bin/env python3
"""
crawl_report.py -- per day, what the search crawlers and the people got
back from the site, from nginx's access logs.

    sudo /srv/venv/bin/python scripts/crawl_report.py
    sudo /srv/venv/bin/python scripts/crawl_report.py --days 21 --bot bingbot

★ WHY (owner, 2026-09-26: "impressions and visitors crashed about a week
  ago"). Google cuts how much it crawls, and how high it ranks, a site that
  answers it with 5xx errors or slow pages -- and this site had nights when
  every page took 4-20 s (the missing ranking_results index, 2026-09-21),
  503s during table swaps, and a corrupt results_tf page. Search Console's
  Crawl stats shows the same thing from Google's side; this is the server's
  side, per day, so the day the errors started can be put next to the day
  the impressions fell.

Reads /var/log/nginx/access.log* (rotated and .gz included), nginx's
default "combined" format. Behind Cloudflare the address is Cloudflare's
unless deploy/cloudflare_realip.sh is installed; the user agent is passed
through either way, and that is what this counts on. READ-ONLY.
"""
import argparse
import collections
import datetime as dt
import glob
import gzip
import re

LINE = re.compile(r'^\S+ \S+ \S+ \[(\d{2}/\w{3}/\d{4}):[^\]]*\] "(\S+) (\S+)[^"]*" '
                  r'(\d{3}) \S+ "[^"]*" "([^"]*)"')
BOTS = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|preview", re.I)


def lines(pattern):
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", errors="replace") as f:
                yield from f
        except OSError as e:
            print(f"  (skipped {path}: {e})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--logs", default="/var/log/nginx/access.log*")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--bot", default="googlebot",
                    help="user-agent substring for the crawler column (googlebot, bingbot)")
    a = ap.parse_args()
    since = dt.date.today() - dt.timedelta(days=a.days)
    bot = collections.defaultdict(collections.Counter)
    ppl = collections.defaultdict(collections.Counter)
    bot_paths = collections.defaultdict(collections.Counter)
    for ln in lines(a.logs):
        m = LINE.match(ln)
        if not m:
            continue
        day_s, method, path, status, ua = m.groups()
        try:
            day = dt.datetime.strptime(day_s, "%d/%b/%Y").date()
        except ValueError:
            continue
        if day < since:
            continue
        s = int(status)
        cls = "2xx" if s < 300 else "3xx" if s < 400 else str(s) if s in (404, 429) \
            else "4xx" if s < 500 else "5xx"
        if a.bot.lower() in ua.lower():
            bot[day][cls] += 1
            if s >= 500:
                bot_paths[day][path.split("?", 1)[0].split("/")[1] or "/"] += 1
        elif (method == "GET" and not BOTS.search(ua)
              and not path.startswith(("/static/", "/img/", "/api/", "/card/"))):
            ppl[day][cls] += 1           # page views by non-bots (people, mostly)
    days = sorted(set(bot) | set(ppl))
    if not days:
        print(f"no log lines since {since} in {a.logs}")
        return
    print(f"{a.bot} requests, and page views by everything that is not a bot, per day\n")
    print(f"{'day':<12}{a.bot:>10}{'ok':>7}{'5xx':>6}{'404':>6}{'429':>6}{'err%':>6}"
          f"   {'pages':>7}{'5xx':>6}   where {a.bot} got 5xx")
    for d in days:
        b, p = bot[d], ppl[d]
        nb = sum(b.values())
        err = 100.0 * b["5xx"] / nb if nb else 0.0
        where = ", ".join(f"/{k} {v}" for k, v in bot_paths[d].most_common(3))
        flag = "  <--" if err >= 5 else ""
        print(f"{d.isoformat():<12}{nb:>10,}{b['2xx']:>7,}{b['5xx']:>6,}{b['404']:>6,}"
              f"{b['429']:>6,}{err:>5.1f}%   {sum(p.values()):>7,}{p['5xx']:>6,}"
              f"   {where}{flag}")
    print("\n  <-- = 5% or more of the crawler's requests failed that day. Google "
          "slows its crawl\n  and can drop pages after days like that; it "
          "recovers over one to three weeks\n  of clean answers.")


if __name__ == "__main__":
    main()
