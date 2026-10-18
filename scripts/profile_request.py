#!/usr/bin/env python3
"""
profile_request.py -- one site request under cProfile, exactly as the site
serves it (Flask's test client). READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/profile_request.py \
        "/api/predict/field?meet_id=268246&sport=XC&when=thisyear&pick=predicted&date=2026-10-17"
    /srv/venv/bin/python scripts/profile_request.py "<url>" --top 50 --sort tottime

★ WHY (owner, 2026-10-18: /api/predict/field and /team take ~33 s; "caching
  isn't the play, there must be some other way to speed up"). The slow-request
  notice names the slow SQL (about 12 s of it); the other ~21 s is Python, and
  only a profile says which function. Prints the request's status and time,
  the top functions by cumulative and by own time, and writes the raw profile
  to /tmp/profile_request.prof (snakeviz / pstats can read it).
"""
import argparse
import cProfile
import io
import os
import pstats
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine", "model"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.chdir(_ROOT)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="path and query string, e.g. /api/predict/field?meet_id=...")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--sort", default="cumulative", choices=("cumulative", "tottime"))
    ap.add_argument("--warm", action="store_true",
                    help="run the request once first, so imports and model loading "
                         "are not in the profile")
    a = ap.parse_args()
    os.environ.setdefault("XCP_DB_QUIET", "1")
    t0 = time.time()
    import app as app_mod
    client = app_mod.app.test_client()
    print(f"[profile] app imported in {time.time() - t0:.1f}s", flush=True)
    if a.warm:
        t1 = time.time()
        client.get(a.url)
        print(f"[profile] warm-up request {time.time() - t1:.1f}s", flush=True)
    prof = cProfile.Profile()
    t1 = time.time()
    prof.enable()
    resp = client.get(a.url)
    prof.disable()
    el = time.time() - t1
    print(f"[profile] {resp.status_code} in {el:.1f}s, {len(resp.data):,} bytes\n", flush=True)
    prof.dump_stats("/tmp/profile_request.prof")
    for key in (a.sort, "tottime" if a.sort == "cumulative" else "cumulative"):
        s = io.StringIO()
        st = pstats.Stats(prof, stream=s)
        st.strip_dirs().sort_stats(key).print_stats(a.top)
        print(f"===== top {a.top} by {key} =====")
        print("\n".join(l for l in s.getvalue().splitlines()
                        if l.strip() and not l.startswith("   Ordered by")))
        print()
    print("[profile] raw profile: /tmp/profile_request.prof")


if __name__ == "__main__":
    main()
