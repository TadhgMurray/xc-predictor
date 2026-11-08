"""bench_static_pages.py -- Python (gunicorn behind nginx) vs a static file
(nginx alone, racecast/build_static_pages.py), per URL.

    N=2000 XCP_BENCH_BASE=http://127.0.0.1:80 python scripts/bench_static_pages.py [URL ...]

Needs ab (apache2-utils) and runs on the box itself (it reads the nginx and
gunicorn processes' CPU from /proc).

For each URL: ab at concurrency 1 (latency) and 8 (throughput), and the
server CPU per request read off /proc for the nginx and gunicorn processes
(utime + stime). The Python path is the same nginx with the bypass header,
so the only difference is who answers. Warm: every URL is requested before
timing, so the app answers from its own worker caches, as it does live.
"""
import os
import re
import subprocess
import sys

N = int(os.environ.get("N", "2000"))
BASE = os.environ.get("XCP_BENCH_BASE", "http://127.0.0.1:8089")
GUNICORN = os.environ.get("XCP_BENCH_GUNICORN", "gunicorn.*:8000")
TICK = os.sysconf("SC_CLK_TCK")
URLS = sys.argv[1:] or ["/", "/rankings", "/rankings/xc/hs-girls",
                        "/api/rankings?board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs&limit=50&offset=0&sort=rating",
                        "/api/updates/latest"]


def pids(pattern):
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out]


def cpu(ps):
    t = 0
    for p in ps:
        try:
            f = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
            t += int(f[11]) + int(f[12])
        except OSError:
            pass
    return t / TICK


def ab(url, conc, bypass, gz):
    args = ["ab", "-q", "-n", str(N), "-c", str(conc), "-H", "Host: racecast.co",
            "-H", "X-Forwarded-Proto: https"]
    if gz:
        args += ["-H", "Accept-Encoding: gzip"]
    if bypass:
        args += ["-H", "X-Racecast-Static: 1"]
    args.append(BASE + url)
    procs = pids("nginx: worker") + pids(GUNICORN)
    c0 = cpu(procs)
    out = subprocess.run(args, capture_output=True, text=True).stdout
    c1 = cpu(procs)
    rps = float(re.search(r"Requests per second:\s+([\d.]+)", out).group(1))
    mean = float(re.search(r"Time per request:\s+([\d.]+) \[ms\] \(mean\)", out).group(1))
    p50 = re.search(r"\s50%\s+(\d+)", out).group(1)
    p99 = re.search(r"\s99%\s+(\d+)", out).group(1)
    fails = re.search(r"Failed requests:\s+(\d+)", out).group(1)
    non2xx = re.search(r"Non-2xx responses:\s+(\d+)", out)
    return rps, mean, p50, p99, (c1 - c0) / N * 1e3, fails, non2xx.group(1) if non2xx else "0"


print(f"{'url':<60} {'path':<7} {'c':>2} {'req/s':>8} {'mean ms':>8} {'p50':>4} {'p99':>4} {'cpu ms/req':>10}")
for url in URLS:
    for bypass in (True, False):
        ab(url, 1, bypass, True)              # warm
        for conc in (1, 8):
            rps, mean, p50, p99, c, fails, non2xx = ab(url, conc, bypass, True)
            print(f"{url[:60]:<60} {'python' if bypass else 'static':<7} {conc:>2} {rps:>8.0f} "
                  f"{mean:>8.2f} {p50:>4} {p99:>4} {c:>10.3f}" + (f"  FAILED {fails} non2xx {non2xx}" if fails != "0" or non2xx != "0" else ""))
