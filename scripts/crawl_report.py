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

LINE = re.compile(r'^(\S+) \S+ \S+ \[(\d{2}/\w{3}/\d{4}):[^\]]*\] "(\S+) (\S+)[^"]*" '
                  r'(\d{3}) \S+ "[^"]*" "([^"]*)"')
BOTS = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|preview", re.I)

# ★ A USER AGENT IS A CLAIM, NOT AN IDENTITY (owner's first run, 2026-09-26:
#   "googlebot"'s top 404s were /.git/HEAD and /wp-config.php -- scanners
#   wearing Google's name). The engines' documented check: the address
#   reverse-resolves to their domain, and that name resolves back to it.
GENUINE = {"googlebot": (".googlebot.com", ".google.com", ".googleusercontent.com"),
           "bingbot": (".search.msn.com",)}
_verified = {}


def genuine(ip, bot):
    """True / False for a crawler we know how to check; None when we don't."""
    suffixes = GENUINE.get(bot.lower())
    if suffixes is None:
        return None
    if ip not in _verified:
        import socket
        ok = False
        try:
            host = socket.gethostbyaddr(ip)[0].lower()
            if host.endswith(suffixes):
                ok = ip in socket.gethostbyname_ex(host)[2] or ":" in ip
        except OSError:
            ok = False
        _verified[ip] = ok
    return _verified[ip]


def prefix(ip):
    """The /16 of an IPv4 address (the /32 of an IPv6 one): a scraper
    rotating addresses usually rotates inside one provider's block."""
    import ipaddress
    try:
        return str(ipaddress.ip_network(f"{ip}/{32 if ':' in ip else 16}", strict=False))
    except ValueError:
        return ip


def lines(pattern, since=None):
    """Every line of every log matching pattern -- skipping a rotated file
    last written before `since`, since nothing in it can be in the window
    (the scraper made these logs gigabytes; reading them all took minutes)."""
    import os
    cutoff = (dt.datetime.combine(since, dt.time()).timestamp() if since else None)
    for path in sorted(glob.glob(pattern)):
        try:
            if cutoff and os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", errors="replace") as f:
                yield from f
        except OSError as e:
            print(f"  (skipped {path}: {e})")


def detail(a, bot_404, bot_crawlfiles, ppl_ip, ppl_ip_err, ppl_ip_ua, ppl_ua, fakes):
    """What the crawler could not find, whether it could read robots.txt
    and the sitemaps, and who the non-bot traffic is."""
    print(f"\n{a.bot}: robots.txt and sitemap fetches, by answer")
    for (path, cls), n in sorted(bot_crawlfiles.items()):
        print(f"  {n:>7,}  {cls:<4} {path}")
    if not bot_crawlfiles:
        print("  none -- it has not fetched robots.txt or a sitemap in the window")
    print(f"\n{a.bot}: the {a.top} URLs it asked for most that were NOT FOUND (404)")
    for path, n in bot_404.most_common(a.top):
        print(f"  {n:>7,}  {path[:110]}")
    kinds = collections.Counter()
    samples = collections.defaultdict(list)
    for p, n in bot_404.most_common():
        k = p.split("?", 1)[0].split("/")[1] or "/"
        kinds[k] += n
        if len(samples[k]) < 4:
            samples[k].append(p)
    if kinds:
        print("  by section, with examples:")
        for k, v in kinds.most_common(8):
            print(f"  {v:>7,}  /{k}")
            for p in samples[k]:
                print(f"             {p[:110]}")
    if fakes:
        print(f"\n  requests that CLAIMED to be {a.bot} from addresses that are not "
              f"its: {sum(fakes.values()):,}\n  (left out of everything above; "
              "the biggest: "
              + ", ".join(f"{ip} {n:,}" for ip, n in fakes.most_common(5)) + ")")
    total = sum(ppl_ip.values())
    print(f"\nnon-bot page views, last {a.recent} days: {total:,}. The {a.top} biggest "
          "addresses:")
    print("  (a Cloudflare address here means deploy/cloudflare_realip.sh is not "
          "installed, and\n   every visitor is hiding behind the edge that "
          "carried them)")
    for ip, n in ppl_ip.most_common(a.top):
        print(f"  {n:>9,}  {100.0 * n / max(total, 1):5.1f}%  5xx {ppl_ip_err[ip]:>7,}  "
              f"{ip:<40} {ppl_ip_ua.get(ip, '')[:60]}")
    blocks = collections.Counter()
    ips_in = collections.defaultdict(set)
    for ip, n in ppl_ip.items():
        blocks[prefix(ip)] += n
        ips_in[prefix(ip)].add(ip)
    print(f"\n  the same traffic by address block (a scraper rotating addresses "
          f"shows up here):")
    for b, n in blocks.most_common(a.top):
        print(f"  {n:>9,}  {100.0 * n / max(total, 1):5.1f}%  {b:<22} "
              f"{len(ips_in[b]):>6,} addresses")
    rotating = sorted(b for b, n in blocks.items()
                      if n >= a.rotator_min and len(ips_in[b]) >= 0.8 * n)
    if rotating:
        share = sum(blocks[b] for b in rotating)
        print(f"\n  {len(rotating)} blocks use a NEW ADDRESS FOR ALMOST EVERY REQUEST "
              f"({share:,} page views, {100.0 * share / max(total, 1):.0f}% of the "
              "total).\n  People do not do that; a rotating proxy pool does. "
              "Cloudflare rule expression, to paste as is:\n")
        print("  (ip.src in {" + " ".join(rotating) + "} and not cf.client.bot)")
    print(f"\n  the {min(a.top, 8)} commonest user agents among them:")
    for ua, n in ppl_ua.most_common(min(a.top, 8)):
        print(f"  {n:>9,}  {100.0 * n / max(total, 1):5.1f}%  {ua[:100]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--logs", default="/var/log/nginx/access.log*")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--recent", type=int, default=3,
                    help="days of non-bot traffic to break down by address and agent")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--rotator-min", type=int, default=5000,
                    help="page views a block needs before it is listed as rotating")
    ap.add_argument("--no-verify", action="store_true",
                    help="trust the user agent (skip the reverse-DNS check)")
    ap.add_argument("--bot", default="googlebot",
                    help="user-agent substring for the crawler column (googlebot, bingbot)")
    a = ap.parse_args()
    since = dt.date.today() - dt.timedelta(days=a.days)
    bot = collections.defaultdict(collections.Counter)
    ppl = collections.defaultdict(collections.Counter)
    bot_paths = collections.defaultdict(collections.Counter)
    bot_404 = collections.Counter()
    bot_crawlfiles = collections.Counter()
    recent = dt.date.today() - dt.timedelta(days=a.recent)
    ppl_ip = collections.Counter()
    ppl_ip_err = collections.Counter()
    ppl_ip_ua = {}
    ppl_ua = collections.Counter()
    fakes = collections.Counter()
    import socket
    socket.setdefaulttimeout(3)
    for ln in lines(a.logs, since):
        m = LINE.match(ln)
        if not m:
            continue
        ip, day_s, method, path, status, ua = m.groups()
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
            if not a.no_verify and genuine(ip, a.bot) is False:
                fakes[ip] += 1
                continue
            bot[day][cls] += 1
            if s >= 500:
                bot_paths[day][path.split("?", 1)[0].split("/")[1] or "/"] += 1
            if s == 404:
                bot_404[path] += 1
            if "sitemap" in path or path == "/robots.txt":
                bot_crawlfiles[(path.split("?", 1)[0] if "sitemap" not in path
                                else re.sub(r"sitemap-[^/]*", "sitemap-*", path), cls)] += 1
        elif (method == "GET" and not BOTS.search(ua)
              and not path.startswith(("/static/", "/img/", "/api/", "/card/"))):
            ppl[day][cls] += 1           # page views by non-bots (people, mostly)
            if day >= recent:
                ppl_ip[ip] += 1
                if s >= 500:
                    ppl_ip_err[ip] += 1
                ppl_ip_ua.setdefault(ip, ua)
                ppl_ua[ua] += 1
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
    detail(a, bot_404, bot_crawlfiles, ppl_ip, ppl_ip_err, ppl_ip_ua, ppl_ua, fakes)
    print("\n  <-- = 5% or more of the crawler's requests failed that day. Google "
          "slows its crawl\n  and can drop pages after days like that; it "
          "recovers over one to three weeks\n  of clean answers.")


if __name__ == "__main__":
    main()
