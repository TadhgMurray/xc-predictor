"""The go-live solve's warm start, keyed (2026-11-22).

docs/MOE-WARM-START-2026-11-20.md measured it; this makes it real.

★ WHAT IT DOES. 08_golive saves, beside its solve state, every unknown of
  the solve under a STABLE key -- athlete-season (athlete key, season year,
  sport), cell (the course/era key), race (the unit or the division, and
  the calendar date), curve knot (pool name, knot), distance class (its
  label), meet-importance class (its label), pool (its name), sport group
  (its number) -- in <out>_warm.npz. The next go-live maps those values
  onto its own design by key; anything new starts at the cold value (0).
  Dense ids are rebuilt every night, so a dense index means nothing a
  night later: only the keys carry.

XCP_WARM_START
  theta (DEFAULT) -- pass 1's CG starts from last night's mapped unknowns.
      Nothing else carries: weights, tilt, amplitude, variance components,
      curve prior, day guard and the pass count are tonight's cold ones.
      Every pass still solves its own system to CG_TOL, so the published
      answer is the cold one up to the CG tolerance. The MOE study (1M
      synthetic rows, both designs): no row rating moved by 0.05, a median
      athlete-season move of 0.003-0.0005 pts, the same size as cold 1e-8
      against cold 1e-10. Saved: pass 1 went 37->16 and 35->27 CG
      iterations (7-15% of the solve's CG); production unmeasured.
  full (opt-in, NOT the default) -- ! A MODEL CHANGE, not a speed-up.
      Also carries last night's robust weights, tilt h, amplitude, day-guard
      offset (per row, by result_id), the variance components (per sport
      group), the curve prior (per pool) and the ratings (for the distance
      bands), and runs XCP_WARM_OUTER passes (default 2) instead of
      --outer. Tonight's pass 1 is last night's pass 6, so the board
      depends on the history of nights. The MOE numbers against tonight's
      cold 5 passes (standard design; hard in brackets): athlete-season
      rating max 1.8 pts (1.1), p99.9 0.21 (0.56), median 0.012 (0.009);
      6,277 of 125k athlete-seasons (2,927) and 147k of 1M rows (123k)
      move by >= 0.05 pts; 1,419 of 3,125 courses by >= 0.05%. Against
      the converged point (cold 10 passes) it lands CLOSER than cold 5
      does (median 0.0024 vs 0.014 pts). About 55-85% fewer CG iterations.
      XCP_WARM_COLD_EVERY (default 7): every 7th run of a warm chain is
      forced cold, so carry-over (a down-weight on a row that was wrong
      once, a deleted meet's influence on the variances) cannot build up.
  off / 0 / cold -- always cold.

! ONLY --golive READS OR WRITES IT. Holdouts, --asof replays, the ladder
  and --golive-dry solve cold, so the accuracy tests stay comparable.
! A HARD SAFETY CHECK GOES COLD, AND SAYS WHY: no file, an old file
  without keys, a different model stamp (engine code, the solve's flags,
  the XCP_ switches the solve reads), a different pack layout (its columns
  and dtypes -- the content changes every night by design), or a mapping
  that covers less than XCP_WARM_MIN_COVER (0.90) of tonight's unknowns.
"""
import glob
import hashlib
import json
import os
import re
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
WARM_FORMAT = 1

# run control, not the model: never in the stamp
_RUN_ARGS = frozenset(("golive", "golive_dry", "from_state", "out", "pack",
                       "snapshot", "asof", "holdout", "holdout_only"))
# switches the solve's files read that change speed or logging, not numbers
_PLUMBING_ENV = re.compile(r"^XCP_(WARM_|THREADS$|CG_TRACE$|CG_PARALLEL$|CG_INPLACE$"
                           r"|HOLDOUT_|GOLIVE_|PRED_DIR$)")


# ! DEFAULT OFF AGAIN (2026-11-24). Re-solving a holdout from its own
#   final answer (the theta warm start's exact mechanism) moved theta by at
#   most 3e-4 but ONE athlete's rating by 40 on scratch -- a jump the CG
#   tolerance cannot explain; something downstream of theta (a guard, a
#   weight, a threshold) flips. Until that is understood, the go-live solves
#   cold. XCP_WARM_START=theta turns it back on.
# ★ EXPLAINED (2026-11-24, same 2.8M-row pack, 692k-row race holdout). The
#   40 was on the 293,345 of 391,047 athlete-season codes with NO training
#   row: their ability is unidentified, CG never moves it, so it keeps its
#   start (now last run's value) plus each pass's recentring -- 3.4e-4 --
#   on a "rating" of ~119,000 (joint_solve.ratingsFromAbility's note).
#   Nothing publishes those. On the 97,702 with rows: max 1.7e-4 pts,
#   0 distance-band flips, identified unknowns within 1.5e-6. The real
#   amplifier is the solve's hard rating bands (DIST_BANDS): a rating
#   within ~1e-3 of 105/120/135 can change band on any CG-scale nudge and
#   move ~0.3 pts (global+arrow vs jacobi: 5 flips, one athlete 0.3 pts).
#   Warm theta's expected flips there are 0.08 a run vs jacobi's 1.8.
def warmMode():
    v = os.environ.get("XCP_WARM_START", "off").strip().lower()
    if v in ("", "0", "off", "cold", "none", "no", "false"):
        return "off"
    if v in ("theta", "1", "on"):
        return "theta"
    if v == "full":
        return "full"
    raise SystemExit(f"[joint] XCP_WARM_START={v!r}: off (default), theta or full")


def minCover():
    return float(os.environ.get("XCP_WARM_MIN_COVER") or 0.9)


def warmOuter():
    return max(1, int(os.environ.get("XCP_WARM_OUTER") or 2))


def coldEvery():
    return max(1, int(os.environ.get("XCP_WARM_COLD_EVERY") or 7))


def warmPath(out_path):
    return os.path.splitext(out_path)[0] + "_warm.npz"


# ------------------------------------------------------------------ #
# STAMPS
# ------------------------------------------------------------------ #

def _norm(x):
    return json.loads(json.dumps(x, sort_keys=True, default=repr))


def modelParts(args):
    """What the solve IS: engine code, the solve's flags, its XCP_ switches.
    Each part is kept, so a mismatch can say which one moved."""
    h = hashlib.sha1()
    for f in sorted(glob.glob(os.path.join(_HERE, "*.py"))):
        h.update(os.path.basename(f).encode())
        with open(f, "rb") as fh:
            h.update(fh.read())
    a = {k: v for k, v in sorted(vars(args).items())
         if not k.startswith("_") and k not in _RUN_ARGS}
    names = set()
    for f in ("joint_solve.py", "run_joint.py"):
        with open(os.path.join(_HERE, f), encoding="utf-8") as fh:
            names |= set(re.findall(r"XCP_[A-Z0-9_]+", fh.read()))
    env = {n: os.environ[n] for n in sorted(names)
           if n in os.environ and not _PLUMBING_ENV.match(n)}
    return _norm({"code": h.hexdigest()[:16], "args": a, "env": env})


def packLayout(cols):
    """The pack's columns and dtypes: its schema, not its rows."""
    return _norm(sorted([k, np.asarray(v).dtype.str, int(np.asarray(v).ndim)]
                        for k, v in cols.items() if isinstance(v, np.ndarray)))


def _stamp(x):
    return hashlib.sha1(json.dumps(x, sort_keys=True).encode()).hexdigest()[:16]


def _why(old, new):
    if old.get("code") != new.get("code"):
        yield "engine code changed"
    for part in ("args", "env"):
        o, n = old.get(part) or {}, new.get(part) or {}
        diff = sorted(k for k in set(o) | set(n) if o.get(k) != n.get(k))
        if diff:
            yield f"{part} changed: " + ", ".join(
                f"{k} {o.get(k, '-')!s:.40} -> {n.get(k, '-')!s:.40}" for k in diff[:6]) + (
                f" (+{len(diff) - 6} more)" if len(diff) > 6 else "")


# ------------------------------------------------------------------ #
# KEYS
# ------------------------------------------------------------------ #

def _joined(arr):
    """One string per athlete key, whatever its tuple width."""
    a = np.asarray(arr)
    if a.ndim == 1:
        return a.astype(str)
    s = a[:, 0].astype(str)
    for j in range(1, a.shape[1]):
        s = np.char.add(np.char.add(s, "\x1f"), a[:, j].astype(str))
    return s


def _venue(k):
    """run_joint.venueOfCell's venue name of one cell key."""
    head, tag, dist = str(k).rpartition(":d")
    return head if tag and dist.isdigit() else str(k)


def designKeys(D, cols, race_all, race_by, split_sport, pool_names, abs_day):
    """The stable key of every unknown of D. race_all: run_joint.raceCodesFor
    over every pack row; abs_day: the calendar day per pack row (days are
    'before the pack', so they move a day a night). None when the keys do
    not reproduce the design's own counts (then the run is cold)."""
    ath = np.asarray(cols["athlete"], dtype=np.int64)
    key = ath * 10000 + np.asarray(cols["year"], dtype=np.int64)
    split = bool(split_sport and "sport" in cols)
    if split:
        key = key * 2 + (np.asarray(cols["sport"]).astype(np.int64) != 0)
    u = np.unique(key)
    del key
    if u.size != D.n_ath:
        return None
    sport = u % 2 if split else np.zeros(u.size, dtype=np.int64)
    rest = u // 2 if split else u
    k = {"ath_names": _joined(cols["athlete_keys"]),
         "as_raw": rest // 10000, "as_year": rest % 10000, "as_sport": sport,
         "cell_keys": np.asarray([str(c) for c in D.course_keys])}
    if k["cell_keys"].size != D.n_cell:
        return None
    # one representative pack row per race: every row of a race shares its key
    race_all = np.asarray(race_all, dtype=np.int64)
    n_race = int(race_all.max()) + 1 if race_all.size else 0
    if n_race != D.n_race:
        return None
    rep = np.zeros(n_race, dtype=np.int64)
    rep[race_all] = np.arange(race_all.size, dtype=np.int64)
    course = np.asarray(cols["course"], dtype=np.int64)[rep]
    base = [str(c) for c in cols["course_keys"]]
    units = np.asarray([_venue(c) for c in base] if race_by == "venue" else base)
    kind = np.zeros(n_race, dtype=np.int64)
    a = course.copy()
    b = np.zeros(n_race, dtype=np.int64)
    if race_by == "division" and "meet_id" in cols and "div_id" in cols:
        meet = np.asarray(cols["meet_id"], dtype=np.int64)[rep]
        div = np.asarray(cols["div_id"], dtype=np.int64)[rep]
        has = (meet >= 0) & (div >= 0)
        kind = has.astype(np.int64)
        a = np.where(has, meet, course)
        b = np.where(has, div, 0)
    k.update(race_units=units, race_kind=kind, race_a=a, race_b=b,
             race_day=np.asarray(abs_day, dtype=np.int64)[rep],
             race_neg_last=np.array([1 if race_by == "venue" else 0], dtype=np.int64))
    k["pool_names"] = np.asarray([str(p) for p in pool_names])
    k["dist_labels"] = np.asarray([str(x) for x in (getattr(D, "dist_labels", None) or [])])
    k["imp_labels"] = np.asarray([str(x) for x in (getattr(D, "imp_labels", None) or [])])
    fg = getattr(D, "free_grid", None)
    k["c_pool"] = (np.zeros(0, dtype=np.int64) if fg is None
                   else np.asarray(fg, dtype=np.int64) // D.n_knot)
    k["c_knot"] = (np.zeros(0, dtype=np.int64) if fg is None
                   else np.asarray(fg, dtype=np.int64) % D.n_knot)
    return k


def _layout(D):
    return {n: int(getattr(D, n, 0) or 0) for n in (
        "o_a", "o_d", "o_u", "o_mu", "o_beta", "o_c", "o_r", "o_e", "o_g",
        "o_q", "o_k", "o_imp", "o_ind", "n_total", "n_ath", "n_cell", "n_race",
        "n_mu", "n_beta", "n_c", "n_r", "n_e", "n_g", "n_q", "n_k", "n_imp",
        "n_ind", "n_group", "n_pool", "n_knot")}


def _shared(old, new):
    """Both string lists as ids in one vocabulary."""
    old = np.asarray(old).astype(str)
    new = np.asarray(new).astype(str)
    if old.size + new.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    _, inv = np.unique(np.concatenate([old, new]), return_inverse=True)
    inv = inv.reshape(-1).astype(np.int64)
    return inv[:old.size], inv[old.size:]


def matchTuples(old, new):
    """Per new tuple, the position of the equal old tuple, or -1. old and
    new are lists of int64 columns; old tuples are unique."""
    n0 = np.asarray(old[0]).size
    n1 = np.asarray(new[0]).size
    if n0 == 0 or n1 == 0:
        return np.full(n1, -1, dtype=np.int64)
    cat = [np.concatenate([np.asarray(o, dtype=np.int64), np.asarray(n, dtype=np.int64)])
           for o, n in zip(old, new)]
    order = np.lexsort(cat[::-1])
    start = np.zeros(order.size, dtype=bool)
    start[0] = True
    for c in cat:
        s = c[order]
        start[1:] |= s[1:] != s[:-1]
    gid = np.empty(order.size, dtype=np.int64)
    gid[order] = np.cumsum(start) - 1
    owner = np.full(int(gid.max()) + 1, -1, dtype=np.int64)
    owner[gid[:n0]] = np.arange(n0, dtype=np.int64)
    return owner[gid[n0:]]


def _byName(old, new):
    o, n = _shared(old, new)
    return matchTuples([o], [n])


def mapTheta(old_k, old_lay, theta_old, new_k, new_lay):
    """Last night's theta on tonight's indexing: (theta, covered, idx_as)."""
    n_total = new_lay["n_total"]
    th = np.zeros(n_total)
    cov = np.zeros(n_total, dtype=bool)
    o_ids, n_ids = _shared(old_k["ath_names"], new_k["ath_names"])
    idx_as = matchTuples(
        [o_ids[old_k["as_raw"]], old_k["as_year"], old_k["as_sport"]],
        [n_ids[new_k["as_raw"]], new_k["as_year"], new_k["as_sport"]])
    idx_cell = _byName(old_k["cell_keys"], new_k["cell_keys"])
    uo, un = _shared(old_k["race_units"], new_k["race_units"])

    def raceCols(k, uid):
        # a (cell, day) race: the cell's unit name, by id in the shared
        # vocabulary. ! A ROW WITHOUT A CELL (-1): under --race-key venue
        #   venueOfCell's array is indexed with it, i.e. the LAST cell's
        #   venue, so the same here; under cell it is its own unit (-1)
        ra = np.asarray(k["race_a"], dtype=np.int64)
        if not uid.size:
            return [k["race_kind"], ra, k["race_b"], k["race_day"]]
        neg_last = bool(np.asarray(k["race_neg_last"]).ravel()[0])
        safe = np.where(ra < 0, -1 if neg_last else 0, np.minimum(ra, uid.size - 1))
        unit = np.where((ra < 0) & (not neg_last), -1, uid[safe])
        a = np.where(k["race_kind"] == 0, unit, ra)
        return [k["race_kind"], a, k["race_b"], k["race_day"]]
    idx_race = matchTuples(raceCols(old_k, uo), raceCols(new_k, un))
    idx_pool = _byName(old_k["pool_names"], new_k["pool_names"])
    po, pn = _shared(old_k["pool_names"], new_k["pool_names"])
    idx_c = matchTuples(
        [po[old_k["c_pool"]] if old_k["c_pool"].size else old_k["c_pool"], old_k["c_knot"]],
        [pn[new_k["c_pool"]] if new_k["c_pool"].size else new_k["c_pool"], new_k["c_knot"]])
    grp_new = np.arange(1, new_lay["n_group"], dtype=np.int64)
    grp_old = np.arange(1, old_lay["n_group"], dtype=np.int64)
    blocks = (                      # (block, offset key, size key, new -> old)
        ("a", "o_a", "n_ath", idx_as),
        ("d", "o_d", "n_cell", idx_cell),
        ("u", "o_u", "n_race", idx_race),
        ("mu", "o_mu", "n_mu", matchTuples([grp_old], [grp_new])),
        ("beta", "o_beta", "n_beta", idx_as),
        ("c", "o_c", "n_c", idx_c),
        ("r", "o_r", "n_r", idx_pool),
        ("e", "o_e", "n_e", _byName(old_k["dist_labels"], new_k["dist_labels"])),
        ("g", "o_g", "n_g", idx_as),
        ("q", "o_q", "n_q", idx_as),
        ("k", "o_k", "n_k", matchTuples([np.arange(old_lay["n_k"])],
                                        [np.arange(new_lay["n_k"])])),
        ("imp", "o_imp", "n_imp", _byName(old_k["imp_labels"], new_k["imp_labels"])),
        ("ind", "o_ind", "n_ind", idx_pool),
    )
    for _name, o, n, idx in blocks:
        size, old_size = new_lay[n], old_lay[n]
        if not size or not old_size or idx.size != size:
            continue
        hit = (idx >= 0) & (idx < old_size)
        pos = np.flatnonzero(hit)
        th[new_lay[o] + pos] = theta_old[old_lay[o] + idx[pos]]
        cov[new_lay[o] + pos] = True
    return th, cov, idx_as


# ------------------------------------------------------------------ #
# LOAD, CHECK, MAP -- and SAVE
# ------------------------------------------------------------------ #

def prepare(path, mode, D, keys, parts, layout_pack, y=None, result_id=None,
            athlete_pool=None, log=print):
    """(warm dict for D.warm_start or None, n_outer override or None, info).
    Never raises: anything odd is a cold run with the reason logged."""
    info = {"mode": "cold", "chain": 0, "last_cold_p1": None, "last_cold_total": None}
    try:
        return _prepare(path, mode, D, keys, parts, layout_pack, y, result_id,
                        athlete_pool, log, info)
    except Exception as exc:                                     # noqa: BLE001
        log(f"[joint] warm start: COLD -- the saved state could not be mapped "
            f"({type(exc).__name__}: {exc})")
        return None, None, info


def _prepare(path, mode, D, keys, parts, layout_pack, y, result_id,
             athlete_pool, log, info):
    def cold(why):
        log(f"[joint] warm start: COLD -- {why}")
        return None, None, info
    if mode == "off":
        return cold("XCP_WARM_START=off")
    if keys is None:
        return cold("tonight's keys do not reproduce the design's counts")
    if not os.path.exists(path):
        return cold(f"no saved state yet ({os.path.basename(path)})")
    t0 = time.time()
    with np.load(path, allow_pickle=False) as z:
        if "__meta__" not in z.files or "ath_names" not in z.files:
            return cold("the saved state has no keys (an older file)")
        meta = json.loads(str(z["__meta__"][0]))
        if meta.get("format") != WARM_FORMAT:
            return cold(f"the saved state is format {meta.get('format')}, not {WARM_FORMAT}")
        info["last_cold_p1"] = meta.get("last_cold_p1")
        info["last_cold_total"] = meta.get("last_cold_total")
        if _stamp(meta.get("model") or {}) != _stamp(parts):
            return cold("the model stamp differs: "
                        + "; ".join(_why(meta.get("model") or {}, parts)))
        if _stamp(meta.get("pack_layout")) != _stamp(layout_pack):
            o = {c[0]: c[1:] for c in meta.get("pack_layout") or []}
            n = {c[0]: c[1:] for c in layout_pack}
            diff = sorted(c for c in set(o) | set(n) if o.get(c) != n.get(c))
            return cold("the pack's layout differs: " + ", ".join(diff[:8]))
        chain = int(meta.get("chain") or 0)
        if mode == "full" and chain + 1 >= coldEvery():
            return cold(f"XCP_WARM_COLD_EVERY={coldEvery()}: {chain} warm runs since "
                        f"the last cold one, so this one is cold")
        old_k = {k: z[k] for k in z.files if k != "__meta__"}
    th, cov, idx_as = mapTheta(old_k, meta["layout"], old_k["theta"], keys, _layout(D))
    share = float(cov.mean()) if cov.size else 0.0
    by = (f"{int((idx_as >= 0).sum()):,} of {idx_as.size:,} athlete-seasons, "
          f"{share:.2%} of {cov.size:,} unknowns")
    if share < minCover():
        return cold(f"the keys cover {by}, under XCP_WARM_MIN_COVER={minCover():g}")
    warm = {"theta": th, "mode": "theta"}
    n_outer = None
    if mode == "full":
        if "result_id" not in old_k or result_id is None:
            log("[joint] warm start: full needs last night's per-row state "
                "(saved only under XCP_WARM_START=full) -- theta tonight")
        else:
            warm.update(_fullState(old_k, meta, D, keys, idx_as, result_id))
            warm["mode"] = "full"
            n_outer = warmOuter()
    info["mode"] = warm["mode"]
    info["chain"] = chain + 1 if warm["mode"] == "full" else 0
    log(f"[joint] warm start: {warm['mode'].upper()} from {meta.get('saved', '?')} -- "
        f"keys cover {by}; new ones start cold (0); mapped in {time.time() - t0:.0f}s"
        + (f"; {n_outer} passes (XCP_WARM_OUTER), warm run {info['chain']} of "
           f"{coldEvery() - 1} before a cold one" if n_outer else ""))
    return warm, n_outer, info


def _fullState(old_k, meta, D, keys, idx_as, result_id):
    """The per-row and per-group state a full warm start carries; anything
    new takes the cold value (w = h = amp = 1, no day-guard offset)."""
    rid_old = old_k["result_id"]
    order = np.argsort(rid_old, kind="stable")
    rs = rid_old[order]
    rid = np.asarray(result_id, dtype=np.int64)
    pos = np.minimum(np.searchsorted(rs, rid), max(rs.size - 1, 0))
    hit = rs[pos] == rid if rs.size else np.zeros(rid.size, dtype=bool)
    src = order[pos]

    def rows(name, cold_value):
        v = np.full(rid.size, cold_value)
        if name in old_k:
            v[hit] = old_k[name][src[hit]].astype(np.float64)
        return v
    out = {"w": rows("weights", 1.0), "h": rows("h", 1.0), "amp": rows("amp", 1.0),
           "div_off": rows("div_off", 0.0) if "div_off" in old_k else None}
    n_group = int(meta["layout"]["n_group"])
    if n_group == D.n_group:
        out["tau2"] = old_k["tau2"].astype(np.float64)
        out["sigma_u2"] = old_k["sigma_u2"].astype(np.float64)
    else:
        out["tau2"] = np.full(D.n_group, 0.05)
        out["sigma_u2"] = np.full(D.n_group, 0.01)
    out["sigma2"] = float(meta.get("sigma2") or 1.0)
    if "curve_sd" in old_k and keys["pool_names"].size:
        idx = _byName(old_k["pool_names"], keys["pool_names"])
        if (idx >= 0).all():
            out["curve_sd2"] = old_k["curve_sd"][idx].astype(np.float64) ** 2
    if "rating" in old_k:
        r = np.full(D.n_ath, 100.0)               # a new athlete-season: the pool mean
        m = idx_as >= 0
        r[m] = old_k["rating"][idx_as[m]]
        out["rating"] = r
    return out


def save(path, out, D, keys, parts, layout_pack, mode, info, cg_iters,
         result_id=None, log=print):
    """Write tonight's keyed state, atomically (a temp file, then os.replace).
    Never raises: a failed save is a cold run tomorrow, not a failed go-live."""
    try:
        if keys is None:
            log("[joint] warm state NOT saved: the keys do not reproduce the design")
            return False
        p1 = int(cg_iters[0]) if cg_iters else None
        total = int(sum(cg_iters)) if cg_iters else None
        cold = info.get("mode") == "cold"
        meta = {"format": WARM_FORMAT, "model": parts, "pack_layout": layout_pack,
                "layout": _layout(D), "mode": info.get("mode"),
                "chain": int(info.get("chain") or 0),
                "cg_iters": [int(i) for i in (cg_iters or [])],
                "last_cold_p1": p1 if cold else info.get("last_cold_p1"),
                "last_cold_total": total if cold else info.get("last_cold_total"),
                "sigma2": float(out["sigma2"]),
                "saved": time.strftime("%Y-%m-%d %H:%M")}
        arrays = dict(keys)
        arrays["theta"] = np.asarray(out["theta"], dtype=np.float64)
        arrays["tau2"] = np.asarray(out["tau2"], dtype=np.float64)
        arrays["sigma_u2"] = np.asarray(out["sigma_u2"], dtype=np.float64)
        if out.get("rating") is not None:
            arrays["rating"] = np.asarray(out["rating"], dtype=np.float64)
        if out.get("curve_sd") is not None:
            arrays["curve_sd"] = np.asarray(out["curve_sd"], dtype=np.float64)
        # ! PER ROW ONLY UNDER full: ~60M rows of four arrays is ~1.5 GB a
        #   night that theta never reads
        if warmMode() == "full" and result_id is not None:
            arrays["result_id"] = np.asarray(result_id, dtype=np.int64)
            for k in ("weights", "h", "amp"):
                arrays[k] = np.asarray(out[k], dtype=np.float32)
            if out.get("race_day_div_offset") is not None:
                arrays["div_off"] = np.asarray(out["race_day_div_offset"], dtype=np.float32)
        tmp = path + ".tmp"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "wb") as fh:
            np.savez(fh, __meta__=np.array([json.dumps(meta)]), **arrays)
        os.replace(tmp, path)
        log(f"[joint] warm state -> {path} ({info.get('mode')} run)")
        return True
    except Exception as exc:                                     # noqa: BLE001
        log(f"[joint] warm state NOT saved ({type(exc).__name__}: {exc}); "
            f"tomorrow solves cold")
        return False


def reportIters(info, cg_iters, log=print):
    """Pass 1's CG count tonight against the last cold run's: the saving."""
    if not cg_iters:
        return
    p1, total = int(cg_iters[0]), int(sum(cg_iters))
    c1, ct = info.get("last_cold_p1"), info.get("last_cold_total")
    mode = info.get("mode", "cold")
    if mode == "cold" or not c1:
        log(f"[joint] warm start: {mode} run, pass 1 CG {p1} iters, all passes {total}")
        return
    log(f"[joint] warm start ({mode}): pass 1 CG {p1} iters vs {c1} on the last cold "
        f"run ({100.0 * (p1 - c1) / c1:+.0f}%); all passes {total} vs {ct} "
        f"({100.0 * (total - ct) / max(ct, 1):+.0f}%)")
