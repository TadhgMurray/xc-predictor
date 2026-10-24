# Project: xc-predictor / engine
# File:    statespace_world.py
# Purpose: a planted world for engine/statespace.py -- high schoolers who
#          get fitter through a season and from one season to the next, a
#          few who go on to college, cross country in the fall, indoor and
#          outdoor track after it, courses, venue-days, divisions with the
#          occasional short course, bad days, a tilt, and a TRUE XC-to-track
#          gap. Every number planted is returned, so a fit can be read
#          against the truth.
#
# ★ WHAT IT IS FOR. Not to prove the engine on real data -- only the pack
#   can do that (scripts/ss_eval.py). It shows (1) the fit recovers what it
#   claims to fit (the gap, the walk, the tilt), and (2) on the same rows, an
#   athlete-season ability with one day per venue scores worse on held-out
#   races than a walk with nested heavy-tailed days, and by how much.
import numpy as np

import statespace as ss

THETA_HS = np.log(18 * 60.0)       # an average HS boy, flat 5000 equivalent
TRUE = dict(mu_xc=0.060, mu_in=0.010, kappa=0.30, sigma=0.015, s_beta=0.015,
            tau=(0.035, 0.006, 0.005), s_w=(0.012, 0.005, 0.005), s_v=0.004,
            bad_p=0.05, bad_mean=0.05, short_p=0.04, long_p=0.02,
            hs_jump=-0.035, hs_jump_sd=0.025, col_jump=-0.015, col_jump_sd=0.015,
            slope_sd=0.05)
# the population's season, log-time against phase (days since 1 August):
# XC fitness gain, a December break, the track build to June
SHAPE = ((0, 0.030), (110, 0.000), (140, 0.012), (200, 0.000), (300, -0.020),
         (366, -0.020))


def _shape(phase):
    xs, ys = zip(*SHAPE)
    return np.interp(phase, xs, ys)


def world(seed=0, n_regions=10, schools=24, per_school=14, n_seasons=5):
    rng = np.random.default_rng(seed)
    T = TRUE
    # ---- people: HS entrants per school per season, some go on to college
    rows = []
    person_base, person_beta = [], []
    persons = []                          # (pid, region, school, first season)
    pid = 0
    for reg in range(n_regions):
        for sch in range(schools):
            for s0 in range(-3, n_seasons):
                for _ in range(per_school // 4 + rng.integers(0, 2)):
                    persons.append((pid, reg, reg * schools + sch, s0))
                    person_base.append(rng.normal(0, 0.08))
                    person_beta.append(rng.normal(0, T["s_beta"]))
                    pid += 1
    n_p = pid
    base = np.array(person_base); beta = np.array(person_beta)
    # season-by-season ability: the seam steps, then a within-season slope
    level = np.zeros((n_p, n_seasons))       # theta at phase 0, per season
    slope = rng.normal(0, T["slope_sd"], (n_p, n_seasons))
    lv = np.zeros((n_p, n_seasons), dtype=np.int64)
    in_school = np.zeros((n_p, n_seasons), dtype=bool)
    college = rng.random(n_p) < 0.0
    for p, reg, sch, s0 in persons:
        th = THETA_HS + 0.05 + base[p]
        go_col = None
        for g in range(8):                    # grades 9..12, then college 1..4
            s = s0 + g
            if g >= 4:
                if go_col is None:
                    go_col = (th < THETA_HS - 0.05) and rng.random() < 0.5
                if not go_col:
                    break
            if 0 <= s < n_seasons:
                level[p, s] = th
                lv[p, s] = 1 if g >= 4 else 0
                in_school[p, s] = True
            th += (rng.normal(T["hs_jump"], T["hs_jump_sd"]) if g < 4
                   else rng.normal(T["col_jump"], T["col_jump_sd"]))
        college[p] = bool(go_col)

    # ---- venues
    xc_course = [[reg * 6 + k for k in range(6)] for reg in range(n_regions)]
    col_course = [reg * 6 + k for reg in range(n_regions) for k in range(2)]
    n_xc = n_regions * 6
    cell_d = {}                                  # cell id -> (group, value)
    def cellId(key, group, sd):
        if key not in cell_d:
            cell_d[key] = (len(cell_d), group, rng.normal(0, sd))
        return cell_d[key][0]

    out = dict(person=[], t=[], season=[], lv=[], phase=[], xc=[], group=[],
               cell=[], day=[], race=[], y=[], truth_theta=[],
               truth_perf=[], bad_race=[])
    day_id = 0; race_id = 0
    school_of = np.array([s for _, _, s, _ in persons])
    region_of = np.array([r for _, r, _, _ in persons])

    def run(members, s, phase, group, ckey, csd, sport_xc, divide=2):
        nonlocal day_id, race_id
        if len(members) < 4:
            return
        cell = cellId(ckey, group, csd)
        g_val = cell_d[ckey][2]
        mu = {ss.GROUP_XC: T["mu_xc"], ss.GROUP_OUT: 0.0, ss.GROUP_IN: T["mu_in"]}[group]
        w = T["s_w"][group] * rng.standard_t(3) / np.sqrt(3.0)
        members = np.array(members)
        th_now = (level[members, s] + _shape(phase)
                  + slope[members, s] * phase / 366.0)
        # divisions: by ability, varsity / JV (or heats)
        order = np.argsort(th_now)
        for chunk in np.array_split(order, divide):
            if chunk.size == 0:
                continue
            v = rng.normal(0, T["s_v"])
            v0 = v
            u = rng.random()
            if u < T["short_p"]:
                v += rng.uniform(-0.12, -0.03)
            elif u < T["short_p"] + T["long_p"]:
                v += rng.uniform(0.03, 0.08)
            for j in chunk:
                p = members[j]
                th = th_now[j] + rng.normal(0, 0.004)
                h = 1.0 + T["kappa"] * (th - THETA_HS)
                e = rng.normal(0, T["sigma"])
                if rng.random() < T["bad_p"]:
                    e += rng.exponential(T["bad_mean"])
                out["person"].append(p); out["t"].append(s * 366.0 + phase)
                out["season"].append(s); out["lv"].append(lv[p, s])
                out["phase"].append(phase); out["xc"].append(1 if sport_xc else 0)
                out["group"].append(group); out["cell"].append(cell)
                out["day"].append(day_id); out["race"].append(race_id)
                out["truth_theta"].append(th)
                # the performance a rating should read: the runner, their
                # grass skill and their own day, with course and day removed
                out["truth_perf"].append(th + beta[p] * sport_xc + e)
                out["bad_race"].append(abs(v - v0) > 0)
                out["y"].append(th + beta[p] * sport_xc + h * (mu + g_val + w + v) + e)
            race_id += 1
        day_id += 1

    for s in range(n_seasons):
        active = np.flatnonzero(in_school[:, s])
        hs = active[lv[active, s] == 0]
        col = active[lv[active, s] == 1]
        xc_hs = hs[rng.random(hs.size) < 0.85]
        tf_hs = hs[rng.random(hs.size) < 0.70]
        in_hs = tf_hs[rng.random(tf_hs.size) < 0.20]
        # HS cross country: Saturdays, two meets a region, schools pick one
        for wk, phase in enumerate(range(25, 106, 7)):
            for reg in range(n_regions):
                here = xc_hs[region_of[xc_hs] == reg]
                go = here[rng.random(here.size) < 0.75]
                pick = rng.integers(0, 2, size=go.size)
                # a school goes together
                pick = (school_of[go] + wk) % 2
                for m in range(2):
                    crs = xc_course[reg][(wk + 3 * m) % 6]
                    run(list(go[pick == m]), s, phase, ss.GROUP_XC, ("xc", crs), T["tau"][0], 1)
                # the occasional visitor from the next region: the bridge
                if wk % 4 == 0:
                    vis = xc_hs[region_of[xc_hs] == (reg + 1) % n_regions]
                    vis = vis[rng.random(vis.size) < 0.1]
                    run(list(vis), s, phase, ss.GROUP_XC, ("xc", xc_course[reg][wk % 6]), T["tau"][0], 1, 1)
        # state final: the faster half, one course
        th_end = level[xc_hs, s] + slope[xc_hs, s] * 115 / 366.0
        fin = xc_hs[th_end < np.median(th_end)]
        run(list(fin), s, 115, ss.GROUP_XC, ("xc", "state"), T["tau"][0], 1, 4)
        # college cross country
        for wk, phase in enumerate(range(30, 110, 14)):
            for reg in range(0, n_regions, 2):
                here = col[(region_of[col] // 2) == reg // 2]
                go = here[rng.random(here.size) < 0.85]
                run(list(go), s, phase, ss.GROUP_XC, ("colxc", col_course[reg + wk % 2]), T["tau"][0], 1, 1)
        # indoor: one facility per two regions, every other week
        for phase in range(150, 211, 14):
            for reg in range(0, n_regions, 2):
                here = np.r_[in_hs, col[rng.random(col.size) < 0.5]]
                here = here[(region_of[here] // 2) == reg // 2]
                go = here[rng.random(here.size) < 0.6]
                run(list(go), s, phase, ss.GROUP_IN, ("in", reg // 2), T["tau"][2], 0, 3)
        # outdoor: weekly, three tracks a region
        for wk, phase in enumerate(range(220, 306, 7)):
            for reg in range(n_regions):
                here = np.r_[tf_hs, col][np.r_[region_of[tf_hs], region_of[col]] == reg]
                go = here[rng.random(here.size) < 0.6]
                pick = (school_of[go] + wk) % 3
                for m in range(3):
                    run(list(go[pick == m]), s, phase, ss.GROUP_OUT,
                        ("out", reg, (wk + m) % 5), T["tau"][1], 0, 3)

    D = {k: np.asarray(v) for k, v in out.items()}
    order = np.lexsort((D["t"], D["person"]))
    D = {k: v[order] for k, v in D.items()}
    # dense person ids
    _, D["person"] = np.unique(D["person"], return_inverse=True)
    D["person"] = D["person"].astype(np.int64)
    for k in ("cell", "day", "race", "season", "lv", "group"):
        D[k] = D[k].astype(np.int64)
    D["t"] = D["t"].astype(np.float64); D["phase"] = D["phase"].astype(np.float64)
    D["starts"] = ss.segments(D["person"])
    return D


def splits(D, seed=1, n_seasons=5):
    """The three held-out designs, as train masks plus the scored rows.
      race     a tenth of the venue-days of the last three seasons, whole
      forward  fit on everything before mid-October of the last season,
               score the rest of its cross country (the championships)
      sport    fit on everything before the last season's outdoor track,
               score that outdoor track (cross country predicts the track)"""
    rng = np.random.default_rng(seed)
    last = n_seasons - 1
    n_day = int(D["day"].max()) + 1
    held_day = rng.random(n_day) < 0.10
    race_out = held_day[D["day"]] & (D["season"] >= last - 2)
    t_fwd = last * 366.0 + 75
    fwd_score = (D["t"] >= t_fwd) & (D["t"] < last * 366.0 + 140)
    t_sp = last * 366.0 + 215
    sp_score = (D["t"] >= t_sp)
    return {
        "race": (~race_out, race_out),
        "forward": (D["t"] < t_fwd, fwd_score),
        "sport": (D["t"] < t_sp, sp_score),
    }


# ------------------------------------------------------------------ #
# THE HARD WORLD: real calendars, tapers, breaks, selection
# ------------------------------------------------------------------ #
#
# ★ WHY A SECOND WORLD. world() has no overlap between the sports, so the
#   XC level is not identified there by construction (statespace.fitGLS
#   says so: the level runs off). The real calendar does overlap, and the
#   overlap is where the gap is learned -- but it is also where every bias
#   lives:
#     December XC    (NXN, Foot Locker, JO) -- only the best, still peaked
#     December indoor -- the first races after a break for most
#     fall track     (a few all-comers / time trials in the XC season)
#     summer track   (June-July), then August XC after a summer off
#     the peak       every season's last race is tapered (-PEAK)
#     the break      a runner who stops 3+ weeks after their last XC race
#                    loses BREAK at three weeks, recovering over ~6 weeks
#   so a naive pair difference is biased by the break and the taper, in
#   opposite directions depending on which way the runner crossed.
HARD = dict(mu_xc=0.060, mu_in=0.010, peak=-0.010, brk=0.025,
            dec_xc_top=0.10, indoor_share=0.30, fall_track=0.03, summer_track=0.12)
# the population's shape, continuous through the sport change
HARD_SHAPE = ((0, 0.030), (110, 0.000), (200, -0.010), (300, -0.025), (366, -0.025))


def _hardShape(phase, shape=None):
    xs, ys = zip(*(shape or HARD_SHAPE))
    return np.interp(phase, xs, ys)


def worldHard(seed=0, n_regions=10, schools=20, per_school=14, n_seasons=5, **over):
    """A world with the real calendar's overlaps and biases. Same row layout
    as world(), plus dist_m. over: any HARD key."""
    rng = np.random.default_rng(seed)
    T = dict(TRUE); T.update(HARD); T.update(over)
    persons = []
    pid = 0
    for reg in range(n_regions):
        for sch in range(schools):
            for s0 in range(-3, n_seasons):
                for _ in range(per_school // 4 + rng.integers(0, 2)):
                    persons.append((pid, reg, reg * schools + sch, s0))
                    pid += 1
    n_p = pid
    base = rng.normal(0, 0.08, n_p)
    beta = rng.normal(0, T["s_beta"], n_p)
    level = np.zeros((n_p, n_seasons)); lv = np.zeros((n_p, n_seasons), dtype=np.int64)
    active = np.zeros((n_p, n_seasons), dtype=bool)
    slope = rng.normal(0, T["slope_sd"], (n_p, n_seasons))
    for p, reg, sch, s0 in persons:
        th = THETA_HS + 0.05 + base[p]
        go = None
        for g in range(8):
            s = s0 + g
            if g >= 4:
                if go is None:
                    go = (th < THETA_HS - 0.05) and rng.random() < 0.5
                if not go:
                    break
            if 0 <= s < n_seasons:
                level[p, s] = th; lv[p, s] = 1 if g >= 4 else 0; active[p, s] = True
            th += (rng.normal(T["hs_jump"], T["hs_jump_sd"]) if g < 4
                   else rng.normal(T["col_jump"], T["col_jump_sd"]))
    region_of = np.array([r for _, r, _, _ in persons])
    school_of = np.array([s for _, _, s, _ in persons])

    # ---- each runner-season's calendar: (phase, kind, meet key) ----------
    # kinds: 0 XC, 1 outdoor, 2 indoor
    plan = {}
    def addRace(p, s, phase, kind, key):
        plan.setdefault((p, s), []).append((phase, kind, key))
    for s in range(n_seasons):
        act = np.flatnonzero(active[:, s])
        hs = act[lv[act, s] == 0]; col = act[lv[act, s] == 1]
        th_mid = level[act, s] + 0.0
        for grp_people, is_col in ((hs, False), (col, True)):
            if grp_people.size == 0:
                continue
            xc = grp_people[rng.random(grp_people.size) < (0.9 if is_col else 0.85)]
            # regular XC: weekly; colleges every other week
            phases = range(20, 106, 14 if is_col else 7)
            for wk, ph in enumerate(phases):
                go = xc[rng.random(xc.size) < 0.75]
                for p in go:
                    m = (school_of[p] + wk) % 2
                    reg = region_of[p] if not is_col else region_of[p] // 2
                    addRace(p, s, ph, 0, ("xc", is_col, reg, wk, m))
            # the championship: everyone's last regular race at a fixed course
            for p in xc:
                addRace(p, s, 110 if not is_col else 113, 0, ("xcchamp", is_col, region_of[p] // 3))
            # December XC: the best tenth (HS), still racing
            if not is_col:
                good = xc[np.argsort(level[xc, s])][: int(T["dec_xc_top"] * xc.size)]
                for p in good:
                    addRace(p, s, 120, 0, ("xcreg", region_of[p] // 5))
                    if rng.random() < 0.5:
                        addRace(p, s, 132, 0, ("xcnat",))
            # fall track: a handful during XC
            ft = xc[rng.random(xc.size) < T["fall_track"]]
            for p in ft:
                addRace(p, s, int(rng.integers(40, 100)), 1, ("falltf", region_of[p]))
            # indoor
            tf = grp_people[rng.random(grp_people.size) < 0.75]
            ind = tf[rng.random(tf.size) < (0.7 if is_col else T["indoor_share"])]
            for p in ind:
                start = int(rng.integers(124, 150))
                for ph in range(start, 216, 14):
                    addRace(p, s, ph, 2, ("in", region_of[p] // 2, ph // 7))
            # outdoor
            for p in tf:
                for wk, ph in enumerate(range(210 + int(rng.integers(0, 14)), 316, 10)):
                    addRace(p, s, ph, 1, ("out", region_of[p], ph // 7, (school_of[p] + wk) % 3))
            # summer track, then (next season) August XC
            su = tf[rng.random(tf.size) < T["summer_track"]]
            for p in su:
                for ph in (330, 345):
                    addRace(p, s, ph, 1, ("summer", region_of[p] // 5, ph))

    # ---- the runner's true fitness on each date --------------------------
    meets = {}
    out = dict(person=[], t=[], season=[], lv=[], phase=[], xc=[], group=[], cell=[],
               day=[], race=[], y=[], truth_theta=[], truth_perf=[], bad_race=[], dist_m=[])
    cells = {}
    def cellOf(key, group, sd):
        if key not in cells:
            cells[key] = (len(cells), rng.normal(0, sd))
        return cells[key]
    rows = []
    for (p, s), races in plan.items():
        races.sort()
        ph = np.array([r[0] for r in races], dtype=float)
        kind = np.array([r[1] for r in races])
        # last race of each sport-season gets the taper; the break follows XC
        last_xc = ph[kind == 0].max() if (kind == 0).any() else None
        th = (level[p, s] + _hardShape(ph, T.get("shape")) + slope[p, s] * ph / 366.0
              + rng.normal(0, 0.004, ph.size))
        for k in (0, 1, 2):
            mk = kind == k
            if mk.sum() >= 4:
                th[np.flatnonzero(mk)[-1]] += T["peak"]
        if last_xc is not None:
            nxt = ph[ph > last_xc]
            if nxt.size and nxt.min() - last_xc >= 21:
                d = ph - last_xc
                dip = np.where(d > 0, T["brk"] * (1 - np.exp(-d / 7.0))
                               * np.exp(-np.maximum(d - 21.0, 0) / 30.0), 0.0)
                th += dip
        for j, (phz, k, key) in enumerate(races):
            rows.append((p, s, phz, k, key, th[j]))
    for p, s, phz, k, key, th in rows:
        group = (ss.GROUP_XC, ss.GROUP_OUT, ss.GROUP_IN)[k]
        csd = T["tau"][group]
        ckey = ("c",) + key[:3] if k == 0 else ("c",) + key[:2]
        # ★ BRIDGE RACES RUN WHERE THE SEASON RUNS (T["bridge_venues"] =
        #   "shared", the default): December XC on a region's regular course,
        #   fall and summer track on the spring tracks -- Van Cortlandt,
        #   Balboa, a high school's own oval. "own" gives each bridge its own
        #   venue, raced at no other time, and then only the venues' prior
        #   can tell the gap from the courses (see the review, §4.4).
        if T.get("bridge_venues", "shared") == "shared":
            if key[0] == "xcreg":
                ckey = ("c", "xc", False, key[1] * 5)
            elif key[0] == "xcnat":
                ckey = ("c", "xc", False, 0)
            elif key[0] == "falltf":
                ckey = ("c", "out", key[1])
            elif key[0] == "summer":
                ckey = ("c", "out", key[1] * 5)
        cid, cval = cellOf(ckey, group, csd)
        mkey = (s, phz) + key
        if mkey not in meets:
            w = T["s_w"][group] * rng.standard_t(3) / np.sqrt(3.0)
            meets[mkey] = [len(meets), w, {}]
        day_id, w, divs = meets[mkey]
        dv = 0 if k != 1 else int(th < THETA_HS)       # heats by ability on the track
        if dv not in divs:
            v = rng.normal(0, T["s_v"]); v0 = v
            u = rng.random()
            if u < T["short_p"]:
                v += rng.uniform(-0.12, -0.03)
            elif u < T["short_p"] + T["long_p"]:
                v += rng.uniform(0.03, 0.08)
            divs[dv] = (len(meets) * 8 + dv, v, abs(v - v0) > 0)
        rid, v, bad = divs[dv]
        mu = {ss.GROUP_XC: T["mu_xc"], ss.GROUP_OUT: 0.0, ss.GROUP_IN: T["mu_in"]}[group]
        h = 1.0 + T["kappa"] * (th - THETA_HS)
        e = rng.normal(0, T["sigma"])
        if rng.random() < T["bad_p"]:
            e += rng.exponential(T["bad_mean"])
        out["person"].append(p); out["t"].append(s * 366.0 + phz); out["season"].append(s)
        out["lv"].append(lv[p, s]); out["phase"].append(phz); out["xc"].append(int(k == 0))
        out["group"].append(group); out["cell"].append(cid); out["day"].append(day_id)
        out["race"].append(rid); out["truth_theta"].append(th)
        out["truth_perf"].append(th + beta[p] * (k == 0) + e); out["bad_race"].append(bad)
        out["dist_m"].append(5000.0 if k == 0 else 1600.0)
        out["y"].append(th + beta[p] * (k == 0) + h * (mu + cval + w + v) + e)
        out.setdefault("truth_course", []).append(cval)
        out.setdefault("truth_day", []).append(w)
        out.setdefault("truth_race", []).append(v)
    D = {k: np.asarray(v) for k, v in out.items()}
    order = np.lexsort((D["t"], D["person"]))
    D = {k: v[order] for k, v in D.items()}
    _, D["person"] = np.unique(D["person"], return_inverse=True)
    for k in ("person", "cell", "day", "race", "season", "lv", "group"):
        D[k] = np.asarray(D[k]).reshape(-1).astype(np.int64)
    for k in ("day", "race"):
        _, D[k] = np.unique(D[k], return_inverse=True)
        D[k] = D[k].reshape(-1).astype(np.int64)
    D["t"] = D["t"].astype(np.float64); D["phase"] = D["phase"].astype(np.float64)
    D["starts"] = ss.segments(D["person"])
    D["truth"] = T
    return D


def naiveGaps(D, window=30):
    """The pair differences the repo measured before (xc_tf_bridge): per
    runner, the mean of every XC row against every indoor / outdoor row of
    the same season within `window` days, log time, track minus XC. Truth
    for a fitness-free world: mu_in - mu_XC and -mu_XC."""
    out = {}
    for name, g in (("indoor-XC", GROUP_IN_), ("outdoor-XC", GROUP_OUT_)):
        diffs = []
        for p in range(D["starts"].size - 1):
            s, e = D["starts"][p], D["starts"][p + 1]
            yy, tt, gg, se = D["y"][s:e], D["t"][s:e], D["group"][s:e], D["season"][s:e]
            ix = np.flatnonzero(gg == ss.GROUP_XC); it = np.flatnonzero(gg == g)
            if ix.size == 0 or it.size == 0:
                continue
            dt = np.abs(tt[it][:, None] - tt[ix][None, :])
            ok = (dt <= window) & (se[it][:, None] == se[ix][None, :])
            if ok.any():
                diffs.append(np.mean((yy[it][:, None] - yy[ix][None, :])[ok]))
        out[name] = (float(np.mean(diffs)) if diffs else float("nan"), len(diffs))
    return out


GROUP_IN_, GROUP_OUT_ = ss.GROUP_IN, ss.GROUP_OUT
