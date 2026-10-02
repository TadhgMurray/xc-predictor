#!/usr/bin/env python3
"""
weather_card.py -- what the weather correction in force actually does, per
sport and distance. Read-only; reads the artifact, not the database.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/weather_card.py            # both sports
    /srv/venv/bin/python scripts/weather_card.py --sport TF

★ WHY (owner, 2026-10-02: "what about track where there's just no possible
  race day term? We need weather to actually be able to work"). On track the
  weather correction is the only thing between a hot, windy or wet meet and
  the ratings, so the question is what it does today: how many percent a
  90F afternoon is worth at 1500 m against 10,000 m, what 5 m/s of wind and
  5 mm of rain cost. Each line is the correction relative to the venue's
  normal for that fortnight (heat colder than the curve's optimum moves
  nothing). Also says whether the artifact on disk is the one the corpus was
  normalised with (weather_applied_<sport>.pkl).
"""
import argparse
import os
import pickle
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DISTS = {"XC": (4000.0, 5000.0, 8000.0, 10000.0),
         "TF": (800.0, 1500.0, 3200.0, 5000.0, 10000.0)}
TEMPS_F = (40, 55, 65, 75, 85, 95)


def _load(path):
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        return None


def card(sport):
    import fit_weather_correction as fw
    path = os.path.join(_ROOT, fw.ARTIFACT_TMPL.format(sport=sport))
    art = _load(path)
    print(f"\n==== {sport}: {os.path.relpath(path, _ROOT)}")
    if art is None:
        print("  NO ARTIFACT: this sport is not weather-corrected at all")
        return
    applied = _load(os.path.join(_ROOT, "engine", "data", f"weather_applied_{sport}.pkl"))
    if applied is None:
        print("  (no record of which artifact the corpus was normalised with)")
    else:
        same = (applied.get("betas") == art.get("betas")
                and applied.get("splines") == art.get("splines"))
        print("  the corpus was normalised with THIS artifact" if same else
              "  ⚠ the corpus was normalised with a DIFFERENT artifact: the "
              "ratings carry that one until the backfill re-runs")
    dists = DISTS[sport]
    tsp = art["splines"].get("apparent_temp")
    if tsp is not None:
        opt = tsp.get("optimum")
        print(f"  heat (apparent temperature, peak for TF), against {tsp['ref']:.0f}C"
              + (f"; nothing below {opt:.0f}C ({opt * 9 / 5 + 32:.0f}F) counts" if opt is not None else ""))
        print("      " + "".join(f"{int(d):>9}m" for d in dists))
        for tf in TEMPS_F:
            tc = (tf - 32) * 5 / 9
            x = max(tc, opt) if opt is not None else tc
            ref = max(tsp["ref"], opt) if opt is not None else tsp["ref"]
            cells = "".join(f"{fw._curveDistPct(tsp, x, d) - fw._curveDistPct(tsp, ref, d):>+9.2f}%"
                            for d in dists)
            print(f"    {tf:>3}F {cells}")
    ssp = art["splines"].get("soil")
    if ssp is not None and sport == "XC":
        print("  mud (soil moisture, x the course's own sensitivity s_c in [0, 1])")
        for sv in (0.2, 0.3, 0.4, 0.5):
            cells = "".join(f"{fw._curveDistPct(ssp, sv, d):>+9.2f}%" for d in dists)
            print(f"    {sv:.2f} {cells}")
        sm = art.get("soil_sensitivity") or {}
        if sm:
            s = sorted(v.get("s", 1.0) for v in sm.values())
            print(f"    s_c over {len(s):,} courses: median {s[len(s) // 2]:.2f}, "
                  f"{sum(1 for v in s if v >= 0.999):,} at the cap of 1")
    betas, dist_betas = art.get("betas", {}), art.get("dist_betas", {})
    dist_ref = art.get("dist_ref", 5000.0)
    for f in art.get("linear_features", ()):
        step = fw._STEP.get(f, 1.0)
        cells = "".join(
            f"{(pow(2.718281828, (betas[f] + dist_betas.get(f, 0.0) * (d / dist_ref - 1)) * step) - 1) * 100:>+9.2f}%"
            for d in dists)
        print(f"  {fw._UNIT.get(f, f):<22}{cells}")
    print(f"  venue normals: {len(art.get('venue_norms') or {}):,} "
          f"(a venue's usual weather is in its course difficulty; this corrects "
          f"the day's departure from it)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", choices=("XC", "TF"), default=None)
    a = ap.parse_args()
    for sport in ([a.sport] if a.sport else ["XC", "TF"]):
        card(sport)


if __name__ == "__main__":
    main()
