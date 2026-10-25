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
            dec_xc_top=0.10, indoor_share=0.30, fall_track=0.03, summer_track=0.12,
            alt_regions=(), alt_km=1.0, alt_coef=0.035, mislabel_p=0.0,
            alias_p=0.0, near_p=0.0, era_sd=0.0, reroute_p=0.0,
            track_mix=False, eta_sd=0.03, team_p=0.0, weather=False, oneoff_p=0.0,
            dist_truth=False)
# ★ A DISTANCE CURVE THE NORMALISATION GETS WRONG (dist_truth, with
#   track_mix): the true cost of distance is curved and differs by level
#   (HS 1.08 ln(d/5000) + 0.03 ln^2, college 1.05 ln + 0.015 ln^2); the
#   stored rows were normalised with one straight 1.06 for everyone, as a
#   per-pool exponent would. D["y"] is the stored row; D["y_rawdist"] the
#   same with the stored distance leg taken back off, D["ld"] its ln(d/5000).
# ★ ONE-OFF COURSES (oneoff_p > 0): a regular XC meet held, with this
#   probability, on a course raced at no other meet -- a thin cell whose
#   number the meet's own runners must pin. Own stream.
# ★ WEATHER (weather=True): each outdoor meet has a recorded apparent
#   temperature -- the day's peak -- and a race run 0-15 F cooler than it;
#   heat costs 0.15% a degree above 55 F and cold 0.05% a degree below
#   45, wind 0.1% per mph on the track and 0.05% on grass, wet ground 2%
#   per unit soil moisture on grass. A backfill-style multiplier keyed on
#   the RECORDED peak (0.2% a degree above 55) has been taken off every
#   row already, as normalized_time carries it; wx_log_mult is its ln.
# ★ TRACK AT SEVERAL DISTANCES (track_mix): each track race is an 800, a
#   1600 or a 3200 (a heat each), the normalisation off by EVENT_OFF per
#   event, and every runner has their own distance curve: eta_p ~ N(0,
#   eta_sd) per unit ln(d / 5000). truth_perf is the runner's 5000.
# ★ TEAMS AT TEMPO (team_p): in a race, a school's 2+ runners all run
#   3-8% slow together with probability team_p; not their ability, so not
#   in truth_perf. D["team"] is the school. Their own streams.
EVENT_OFF = {800.0: 0.010, 1600.0: 0.0, 3200.0: -0.005}
# ★ COURSES THAT CHANGE (era_sd > 0): each course's number walks by era_sd
#   a season, and with reroute_p a season it jumps by N(0, 3%) -- a new
#   start, a hill added. truth_course is the season's value. Own stream.
# ★ ONE COURSE UNDER TWO KEYS (alias_p > 0): a regular XC meet that keys its
#   course under a second name -- a cell of its own at the same place (a
#   few tens of metres off), the same true course, and half the time listed
#   at 4828 though it is the same 5000 (the Mt SAC 4715/4828 kind). And
#   near_p > 0: a new XC course placed 150-600 m from one already there, a
#   different course that a radius rule would merge. Courses sit within
#   10 km of their region's centre, regions 50 km apart. Their own stream.
# ★ MISLABELLED RACES (mislabel_p > 0): a division whose listed distance is
#   not what it ran -- an XC 5000 that was 4000, 3 miles or 6000, a track
#   1600 that was 1500 or 2000. Every runner's time moves by the distance,
#   untilted (RIEGEL * ln(true / listed)); dist_m stays the listed one, as
#   a results page would have it. Drawn from their own stream, so a world
#   without them is the same world as before.
MISLABEL_TRUE = {5000.0: (4000.0, 4828.0, 6000.0), 1600.0: (1500.0, 2000.0)}
RIEGEL = 1.06       # time goes as distance^1.06 near these distances
# the population's shape, continuous through the sport change
HARD_SHAPE = ((0, 0.030), (110, 0.000), (200, -0.010), (300, -0.025), (366, -0.025))


def _hardShape(phase, shape=None):
    xs, ys = zip(*(shape or HARD_SHAPE))
    return np.interp(phase, xs, ys)


def worldHard(seed=0, n_regions=10, schools=20, per_school=14, n_seasons=5, **over):
    """A world with the real calendar's overlaps and biases. Same row layout
    as world(), plus dist_m. over: any HARD key."""
    rng = np.random.default_rng(seed)
    rng_m = np.random.default_rng(seed + 10_000)
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
               day=[], race=[], y=[], truth_theta=[], truth_perf=[], bad_race=[], dist_m=[],
               mislabel=[], true_dist=[])
    cells = {}
    place = {}
    rng_c = np.random.default_rng(seed + 20_000)
    def cellOf(key, group, sd):
        if key not in cells:
            cells[key] = (len(cells), rng.normal(0, sd))
        return cells[key]
    def placeOf(key, reg):
        if key not in place:
            same = [v for k_, v in place.items() if v[2] == reg and k_[1] == key[1]]
            if same and key[1] == "xc" and rng_c.random() < T["near_p"]:
                lat0, lon0, _ = same[int(rng_c.integers(len(same)))]
                r_m, ang = rng_c.uniform(150, 600), rng_c.uniform(0, 2 * np.pi)
                lat = lat0 + r_m * np.sin(ang) / 111_000.0
                lon = lon0 + r_m * np.cos(ang) / (111_000.0 * np.cos(np.radians(lat0)))
            else:
                lat = 35.0 + (reg % 10) * 0.45 + rng_c.uniform(-0.09, 0.09)
                lon = -100.0 + (reg // 10) * 0.55 + rng_c.uniform(-0.11, 0.11)
            place[key] = (lat, lon, reg)
        return place[key]
    alias_of = {}
    rng_o = np.random.default_rng(seed + 60_000)
    oneoff_of = {}
    rng_x = np.random.default_rng(seed + 40_000)
    eta = rng_x.normal(0, T["eta_sd"], n_p) if T["track_mix"] else np.zeros(n_p)
    tempo = {}
    rng_w = np.random.default_rng(seed + 50_000)
    wxm = {}
    drift = {}
    rng_e = np.random.default_rng(seed + 30_000)
    def driftOf(key, s_):
        if key not in drift:
            step = rng_e.normal(0, T["era_sd"], n_seasons)
            jump = np.where(rng_e.random(n_seasons) < T["reroute_p"],
                            rng_e.normal(0, 0.03, n_seasons), 0.0)
            walk = np.cumsum(step + jump)
            drift[key] = walk - walk.mean()
        return drift[key][s_]
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
        mkey = (s, phz) + key
        if T["oneoff_p"] > 0 and key[0] == "xc":
            if mkey not in oneoff_of:
                oneoff_of[mkey] = rng_o.random() < T["oneoff_p"]
            if oneoff_of[mkey]:
                ckey = ("oneoff",) + mkey
        cid, cval = cellOf(ckey, group, csd)
        reg_place = ckey[3] if (ckey[1] == "xc" and len(ckey) > 3) else (
            ckey[2] if len(ckey) > 2 else 0)
        lat, lon, _ = placeOf(ckey, reg_place)
        listed = 5000.0 if k == 0 else 1600.0
        if T["track_mix"] and k != 0:
            listed = float(rng_x.choice((800.0, 1600.0, 3200.0), p=(0.25, 0.5, 0.25)))
        ev_d = listed
        is_alias = False
        if T["alias_p"] > 0 and key[0] == "xc":
            if mkey not in alias_of:
                alias_of[mkey] = (rng_c.random() < T["alias_p"], rng_c.random() < 0.5,
                                  rng_c.normal(0, 40.0, 2))
            is_alias, short_listed, jitter = alias_of[mkey]
            if is_alias:
                akey = ("alias",) + ckey + ((4828.0,) if short_listed else ())
                if akey not in cells:
                    cells[akey] = (len(cells), cval)
                cid = cells[akey][0]
                lat = lat + jitter[0] / 111_000.0
                lon = lon + jitter[1] / (111_000.0 * np.cos(np.radians(lat)))
                if short_listed:
                    listed = 4828.0
        true_course = cellOf(ckey, group, csd)[0]
        if T["era_sd"] > 0 or T["reroute_p"] > 0:
            cval = cval + driftOf(ckey, s)
        if mkey not in meets:
            w = T["s_w"][group] * rng.standard_t(3) / np.sqrt(3.0)
            meets[mkey] = [len(meets), w, {}]
        day_id, w, divs = meets[mkey]
        wx_rec = wx_true = wwind = wwet = np.nan
        wx_eff = wx_log = 0.0
        if T["weather"] and k != 2:
            if mkey not in wxm:
                base_t = 75.0 - 30.0 * np.clip((phz - 20) / 100.0, 0, 1) if phz < 160 else (
                    50.0 + 30.0 * np.clip((phz - 200) / 120.0, 0, 1))
                rec = base_t + rng_w.normal(0, 8.0)
                wxm[mkey] = (rec, rec - rng_w.uniform(0, 15), rng_w.gamma(2.0, 3.0),
                             rng_w.beta(2, 5))
            wx_rec, wx_true, wwind, wwet = wxm[mkey]
            wx_eff = (0.0015 * max(wx_true - 55.0, 0) + 0.0005 * max(45.0 - wx_true, 0)
                      + (0.001 if k == 1 else 0.0005) * wwind + (0.02 * wwet if k == 0 else 0.0))
            wx_log = -0.002 * max(wx_rec - 55.0, 0)
        dv = 0 if k != 1 else int(th < THETA_HS)       # heats by ability on the track
        if T["track_mix"] and k != 0:
            dv = 2 + 2 * (0 if ev_d == 800.0 else 1 if ev_d == 1600.0 else 2) + int(k == 1 and th < THETA_HS)
        if dv not in divs:
            v = rng.normal(0, T["s_v"]); v0 = v
            u = rng.random()
            if u < T["short_p"]:
                v += rng.uniform(-0.12, -0.03)
            elif u < T["short_p"] + T["long_p"]:
                v += rng.uniform(0.03, 0.08)
            true_d = 5000.0 if k == 0 else ev_d
            if T["mislabel_p"] > 0 and rng_m.random() < T["mislabel_p"] and true_d in MISLABEL_TRUE:
                true_d = float(rng_m.choice(MISLABEL_TRUE[true_d]))
            divs[dv] = (day_id * 8 + dv, v, abs(v - v0) > 0, true_d)
        rid, v, bad, true_d = divs[dv]
        mu = {ss.GROUP_XC: T["mu_xc"], ss.GROUP_OUT: 0.0, ss.GROUP_IN: T["mu_in"]}[group]
        h = 1.0 + T["kappa"] * (th - THETA_HS)
        e = rng.normal(0, T["sigma"])
        if rng.random() < T["bad_p"]:
            e += rng.exponential(T["bad_mean"])
        # altitude: a home region's own courses and tracks sit high; the
        # championships, December XC, indoor and summer meets are low
        reg_c = ckey[3] if ckey[1] == "xc" and len(ckey) > 3 else (
            ckey[2] if ckey[1] in ("out", "falltf") else None)
        akm = T["alt_km"] if (reg_c in T["alt_regions"] and key[0] in ("xc", "out", "falltf")) else 0.0
        out.setdefault("alt_km", []).append(akm)
        alt_eff = T["alt_coef"] * akm * (1.0 if k == 0 else 0.83)
        out["person"].append(p); out["t"].append(s * 366.0 + phz); out["season"].append(s)
        out["lv"].append(lv[p, s]); out["phase"].append(phz); out["xc"].append(int(k == 0))
        out["group"].append(group); out["cell"].append(cid); out["day"].append(day_id)
        out["race"].append(rid); out["truth_theta"].append(th)
        out["truth_perf"].append(th + beta[p] * (k == 0) + e); out["bad_race"].append(bad)
        out["dist_m"].append(listed)
        out["true_dist"].append(true_d); out["mislabel"].append(true_d != (5000.0 if k == 0 else ev_d))
        out.setdefault("team", []).append(school_of[p])
        tmp = 0.0
        if T["team_p"] > 0:
            tk = (rid, school_of[p])
            if tk not in tempo:
                tempo[tk] = rng_x.uniform(0.03, 0.08) if rng_x.random() < T["team_p"] else 0.0
            tmp = tempo[tk]
        out.setdefault("tempo", []).append(tmp)
        out.setdefault("wx_temp", []).append(wx_rec); out.setdefault("wx_wind", []).append(wwind)
        out.setdefault("wx_wet", []).append(wwet if k == 0 else 0.0)
        out.setdefault("wx_log_mult", []).append(wx_log)
        x_d = (eta[p] * np.log(ev_d / 5000.0) + EVENT_OFF.get(ev_d, 0.0)) if (T["track_mix"] and k != 0) else 0.0
        ld_ = np.log(listed / 5000.0)
        stored_leg = 0.0
        if T["dist_truth"] and k != 0 and T["track_mix"]:
            a_, b_ = (1.08, 0.03) if lv[p, s] == 0 else (1.05, 0.015)
            x_d = eta[p] * ld_ + (a_ - 1.06) * ld_ + b_ * ld_ * ld_    # true - stored
            stored_leg = 1.06 * ld_
        out.setdefault("ld", []).append(ld_)
        out.setdefault("stored_leg", []).append(stored_leg)
        out.setdefault("lat", []).append(lat); out.setdefault("lon", []).append(lon)
        out.setdefault("alias", []).append(is_alias)
        out.setdefault("truth_cell", []).append(true_course)
        out["y"].append(th + beta[p] * (k == 0) + h * (mu + cval + w + v) + e + alt_eff
                        + RIEGEL * np.log(true_d / listed) + x_d + tmp + wx_eff + wx_log)
        out.setdefault("truth_course", []).append(cval)
        out.setdefault("region", []).append(region_of[p])
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
    if not T["alt_regions"]:
        D.pop("alt_km", None)
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
