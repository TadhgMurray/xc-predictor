"""The hover's claim about the race-day term matches what the engine did.

Owner, 2026-09-08: "I still see that tf gets the race day tilt, same with
xc, and I'm not certain either are supposed to."

Neither is. The go-live log settles it on every run:

    [joint/live] race-day term, XC: median 1.67 points at 130, OUT OF the rating
    [joint/live] race-day term, TF: median 1.18 points at 130, OUT OF the rating

The term is measured and shown, and applied to nothing. But app.py's
RACE_DAY_SPORTS defaulted to "XC" -- set when the engine's default WAS XC,
and never moved when the engine's default became none for both sports days
later. So the hover told a reader that every XC rating from a slow day was
raised by that amount, which was false about the number underneath it.

⚠ TWO DEFAULTS, ONE FACT. run_joint --race-effect-sports decides what the
  RATING carries; app.RACE_DAY_SPORTS decides what the PAGE says it
  carries. They are in different files, in different languages, and
  nothing connected them. This test is the connection.

    python tests/test_race_day_wording.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def _src(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


APP = _src("racecast", "app.py")
RJ = _src("engine", "run_joint.py")
TPL = _src("racecast", "templates", "_explain.html")


# ---- 1. the two defaults agree ---------------------------------------- #
engine_default = re.search(
    r'ap\.add_argument\("--race-effect-sports",\s*default="([^"]*)"',
    RJ).group(1)
site_default = re.search(
    r'os\.environ\.get\("XCP_RACE_DAY_SPORTS",\s*"([^"]*)"\)', APP).group(1)


def _split(v):
    return tuple(x.strip().upper() for x in v.split(",") if x.strip())


ok(_split(engine_default) == _split(site_default),
   f"the engine rates with --race-effect-sports={engine_default!r} but the "
   f"page describes XCP_RACE_DAY_SPORTS={site_default!r}. The hover would "
   f"claim a tilt the rating does not carry (or hide one it does).")

# and today that means: neither sport
ok(_split(site_default) == (),
   f"expected no sport to carry the day, got {_split(site_default)} -- if "
   f"the engine really is being run with --race-effect-sports now, update "
   f"this test deliberately rather than letting the two drift again")


# ---- 2. the pipeline does not quietly turn it back on ------------------ #
PIPE = _src("deploy", "run_pipeline.sh")
ok("--race-effect-sports" not in PIPE,
   "run_pipeline must not pass --race-effect-sports without this test and "
   "XCP_RACE_DAY_SPORTS moving with it")


# ---- 3. the two branches say the right thing --------------------------- #
# ! THE RACE-DAY ONE, not the first data-blurb in the file -- the macro
#   above it explains the rating scale and also carries one.
blurb = next(b for b in re.findall(r'data-blurb="(.*?)"\n', TPL, re.S)
             if "RACE_DAY_SPORTS" in b)

not_carried = blurb[blurb.index("sport not in RACE_DAY_SPORTS"):
                    blurb.index("{% else %}")]
ok("carry the venue only, not the day" in not_carried,
   "the not-carried branch must say the rating does NOT include the day")
# ⚠ NOT "Track". With neither sport carrying it, this branch renders for XC
#   races too, and calling a cross-country course "Track" is wrong twice.
ok("Track ratings" not in not_carried,
   "the not-carried branch must not hardcode Track: it renders for XC now")

carried = blurb[blurb.index("{% else %}"):]
ok("raised" in carried and "lowered" in carried,
   "the carried branch must still say which way the rating moved")


# ---- 4. the day is still SHOWN either way ------------------------------ #
#   The fix is the claim, not the number: the term is real, measured, and
#   worth seeing even when nothing applies it.
ok("Race Day" in TPL, "the hover must still show the race-day figure")
ok("dv-day" in TPL, "the hover bubble must still render")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
