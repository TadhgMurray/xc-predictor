#!/usr/bin/env python3
"""
traffic_report.py -- who is hitting the site, from nginx's own access log.

    sudo /srv/venv/bin/python scripts/traffic_report.py                 # last 200k requests
    sudo /srv/venv/bin/python scripts/traffic_report.py --lines 1000000
    sudo /srv/venv/bin/python scripts/traffic_report.py --log /var/log/nginx/access.log.1

★ WHY (owner, 2026-09-30: "the events tab is pretty useless bcs there's so
  many crawlers"). Cloudflare's Events lists requests one by one; the
  question is WHO, in totals. This reads the origin's log -- every request
  that got past Cloudflare, which is exactly the traffic that costs the
  server -- and groups it: by kind of client, by user agent, by address
  (with the owner's network from reverse DNS), by section of the site, and
  over time. deploy/cloudflare_realip.sh makes $remote_addr the visitor's
  real address rather than Cloudflare's.

Read-only. Expects nginx's default "combined" log format.
"""
import argparse
import collections
import gzip
import os
import re
import socket
import subprocess
import sys
from datetime import datetime

LINE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) \S+ '
                  r'"[^"]*" "([^"]*)"')

# ★ KINDS, BY WHAT THE CLIENT SAYS IT IS. A scraper can claim to be Chrome,
#   so "browser" here is only "did not say otherwise"; the address section
#   below is what catches a fake browser (hundreds of pages a minute from
#   one cloud machine).
KINDS = [
    ("search engine", r"googlebot|bingbot|duckduckbot|yandex|baiduspider|applebot|"
                      r"google-inspectiontool|storebot-google|adsbot"),
    ("AI training crawler", r"gptbot|claudebot|anthropic-ai|ccbot|bytespider|"
                            r"meta-externalagent|amazonbot|google-extended|"
                            r"cohere|diffbot|omgili|imagesiftbot|petalbot"),
    ("AI assistant (on a person's request)", r"chatgpt-user|perplexity|claude-user|"
                                             r"oai-searchbot|claude-searchbot"),
    ("SEO crawler", r"ahrefs|semrush|mj12bot|dotbot|dataforseo|blexbot|"
                    r"serpstat|seokicks|barkrowler|megaindex"),
    ("script / library", r"python|requests|curl|wget|go-http|java/|okhttp|"
                         r"node-fetch|axios|scrapy|httpx|aiohttp|libwww|headless"),
    ("social preview", r"facebookexternalhit|twitterbot|slackbot|discordbot|"
                       r"whatsapp|telegrambot|linkedinbot|iframely"),
    ("monitor", r"uptime|pingdom|statuscake|cloudflare-healthchecks|betteruptime"),
    ("other bot", r"bot|crawl|spider|scan"),
]
KIND_RX = [(k, re.compile(p, re.I)) for k, p in KINDS]


def kindOf(ua):
    if not ua or ua == "-":
        return "no user agent"
    for k, rx in KIND_RX:
        if rx.search(ua):
            return k
    return "browser (claimed)"


def section(path):
    p = path.split("?", 1)[0]
    parts = [x for x in p.split("/") if x]
    if not parts:
        return "/"
    head = parts[0]
    if head in ("api", "search") and len(parts) > 1:
        return f"/{head}/{parts[1]}"
    return f"/{head}"


def readTail(path, n):
    opener = gzip.open if path.endswith(".gz") else open
    if path.endswith(".gz"):
        with opener(path, "rt", errors="replace") as f:
            return collections.deque(f, maxlen=n)
    out = subprocess.run(["tail", "-n", str(n), path], capture_output=True, text=True,
                         errors="replace")
    return out.stdout.splitlines()


def rdns(ip, cache={}):
    if ip not in cache:
        try:
            socket.setdefaulttimeout(2)
            cache[ip] = socket.gethostbyaddr(ip)[0]
        except (OSError, UnicodeError):
            cache[ip] = "-"
    return cache[ip]


def pct(a, b):
    return f"{100.0 * a / b:5.1f}%" if b else "   - "


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="/var/log/nginx/access.log")
    ap.add_argument("--lines", type=int, default=200_000)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    if not os.path.exists(a.log):
        sys.exit(f"no log at {a.log} (pass --log)")

    rows = []
    for ln in readTail(a.log, a.lines):
        m = LINE.match(ln)
        if m:
            ip, ts, method, path, status, ua = m.groups()
            rows.append((ip, ts, path, int(status), ua))
    if not rows:
        sys.exit("no lines in the combined format -- is this nginx's access log?")
    n = len(rows)

    def when(ts):
        try:
            return datetime.strptime(ts.split()[0], "%d/%b/%Y:%H:%M:%S")
        except ValueError:
            return None
    t0, t1 = when(rows[0][1]), when(rows[-1][1])
    mins = max((t1 - t0).total_seconds() / 60.0, 1.0) if t0 and t1 else None
    print(f"\n{n:,} requests, {rows[0][1]} .. {rows[-1][1]}"
          + (f"  ({n / mins:,.0f} per minute on average)" if mins else ""))

    # --- 1. by kind -------------------------------------------------------- #
    kinds = collections.Counter(kindOf(r[4]) for r in rows)
    print("\n1. BY KIND OF CLIENT (what it says it is)")
    for k, c in kinds.most_common():
        print(f"   {c:>10,}  {pct(c, n)}  {k}")

    # --- 2. by user agent -------------------------------------------------- #
    uas = collections.Counter(r[4] for r in rows)
    print(f"\n2. TOP {a.top} USER AGENTS")
    for ua, c in uas.most_common(a.top):
        print(f"   {c:>10,}  {pct(c, n)}  [{kindOf(ua)}]  {ua[:110]}")

    # --- 3. by address ----------------------------------------------------- #
    by_ip = collections.Counter(r[0] for r in rows)
    ip_ua = collections.defaultdict(collections.Counter)
    ip_sec = collections.defaultdict(collections.Counter)
    for ip, _ts, path, _st, ua in rows:
        ip_ua[ip][ua] += 1
        ip_sec[ip][section(path)] += 1
    top_ips = by_ip.most_common(a.top)
    print(f"\n3. TOP {a.top} ADDRESSES ({len(by_ip):,} distinct; the top {a.top} made "
          f"{pct(sum(c for _i, c in top_ips), n)} of all requests)")
    print("   reverse DNS names the network: *.amazonaws.com, *.googleusercontent.com,"
          " *.azure, hetzner, ovh... = a cloud machine, not a person")
    for ip, c in top_ips:
        ua = ip_ua[ip].most_common(1)[0][0]
        secs = ", ".join(f"{s} {v:,}" for s, v in ip_sec[ip].most_common(3))
        rate = f"{c / mins:6.1f}/min" if mins else ""
        print(f"   {c:>9,} {rate}  {ip:<39} {rdns(ip)[:45]}")
        # ! a search-engine name from a network that is not the engine's own
        #   is a scraper borrowing a name the rules wave through
        host = rdns(ip)
        fake = (kindOf(ua) == "search engine"
                and not re.search(r"(googlebot|google|search\.msn|applebot|yandex|"
                                  r"baidu|duckduckgo)\.(com|net|ru)$", host))
        print(f"              [{kindOf(ua)}] {ua[:80]}"
              + ("   <- FAKE: not the engine's network" if fake else ""))
        print(f"              {secs}")

    # --- 4. by network (reverse-DNS domain) over the busiest addresses ------ #
    nets = collections.Counter()
    for ip, c in by_ip.most_common(300):
        host = rdns(ip)
        dom = ".".join(host.split(".")[-2:]) if host != "-" else "(no reverse DNS)"
        nets[dom] += c
    print("\n4. NETWORKS, from the 300 busiest addresses' reverse DNS")
    for d, c in nets.most_common(a.top):
        print(f"   {c:>10,}  {pct(c, n)}  {d}")

    # --- 5. by section ----------------------------------------------------- #
    secs = collections.Counter(section(r[2]) for r in rows)
    print(f"\n5. WHAT THEY ASK FOR (top {a.top} sections)")
    for s, c in secs.most_common(a.top):
        print(f"   {c:>10,}  {pct(c, n)}  {s}")

    # --- 6. status ---------------------------------------------------------- #
    st = collections.Counter(r[3] // 100 for r in rows)
    print("\n6. STATUS: " + "  ".join(f"{k}xx {pct(v, n)}" for k, v in sorted(st.items()))
          + "   (many 404s = someone guessing URLs; 429 = the nginx limits working)")

    # --- 7. over time -------------------------------------------------------- #
    per_hour = collections.Counter()
    for r in rows:
        t = when(r[1])
        if t:
            per_hour[t.strftime("%m-%d %H:00")] += 1
    if per_hour:
        peak = max(per_hour.values())
        print("\n7. PER HOUR (server time)")
        for h in sorted(per_hour):
            c = per_hour[h]
            print(f"   {h}  {c:>8,}  {'#' * max(1, round(40 * c / peak))}")
    print()


if __name__ == "__main__":
    main()
