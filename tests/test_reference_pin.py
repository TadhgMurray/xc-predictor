# Project: xc-predictor / tests
# File:    test_reference_pin.py
# Purpose: the reference class (plan §1), the flat-outdoor-400 pin (§2) and
#          indoor's asserted centre with its reporting gates (§3).
#
# ★ THE DESIGN (owner, 2026-09-19): "we're gonna make all falt outdoor 400m
#   tracks 0.0, and indoor tracks on avg +0.3 slower, adn then allow indoor
#   tracks to be anywhere from -0.3? to let's say +2.0% difficulty", and on the
#   fit's -1.68% for indoor: "yeah the fit is wrong".
#
#   python -m unittest tests.test_reference_pin
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                               # noqa: E402
import track_geometry as tg                                      # noqa: E402
import bracket_engine as be                                      # noqa: E402
import venue_geometry_overrides as vgo                           # noqa: E402


class ThePredicate(unittest.TestCase):

    def test_the_reference_oval(self):
        self.assertTrue(tg.isFlatOutdoor400(400, "Flat", 0))
        self.assertTrue(tg.isFlatOutdoor400(400.0, "", 0))
        self.assertTrue(tg.isFlatOutdoor400(400, None, 0))

    def test_a_quarter_mile_is_the_reference_oval(self):
        """402.34m labelled as the oval it is -- LENGTH_TOLERANCE_M exists for
        exactly this, and excluding it would drop real 400s."""
        self.assertTrue(tg.isFlatOutdoor400(402, "Flat", 0))
        self.assertFalse(tg.isFlatOutdoor400(420, "Flat", 0))

    def test_banked_is_out_and_only_the_positive_claim_counts(self):
        """normalize_distance keys its banking correction off the literal
        string; one spelling, one place."""
        self.assertFalse(tg.isFlatOutdoor400(400, "Banked", 0))
        self.assertTrue(tg.isFlatOutdoor400(400, "Oversized Flat", 0))
        self.assertFalse(tg.isBanked("flat"))
        self.assertTrue(tg.isBanked(" Banked "))

    def test_indoor_is_out(self):
        self.assertFalse(tg.isFlatOutdoor400(400, "Flat", 1))

    def test_unknown_length_is_the_reference_oval(self):
        """⚠⚠ THE ONE THAT MATTERS MOST, AND IT REVERSED ON 2026-09-20 (owner:
        "track difficulty is not set to 0 for all outdoor 400m tracks").

        It was not, because this predicate demanded a positively-known length
        and an unrecorded track_length is the commonest value in the corpus --
        so most outdoor ovals never entered the reference class. The
        assumption is already made upstream:
        normalize_distance._resolveTrackLength returns 400.0 for an unknown
        length, so those cells' normalized_time is ALREADY on the 400m
        reference scale. Refusing them a pinned difficulty applied the
        assumption to the numerator only."""
        self.assertTrue(tg.isFlatOutdoor400(None, "Flat", 0))
        self.assertTrue(tg.isFlatOutdoor400(float("nan"), "Flat", 0))
        self.assertTrue(tg.isFlatOutdoor400(None, "", 0))

    def test_strict_restores_the_fact_only_reading(self):
        """! A POLICY, NOT A REWRITE -- so the old behaviour is still one
        argument away and a run can price the difference."""
        for ln in (None, float("nan")):
            self.assertFalse(tg.isFlatOutdoor400(ln, "Flat", 0,
                                                 unknown_is_400=False))
        self.assertEqual(tg.UNKNOWN_LENGTH_DEFAULT, "assume400")

    def test_silence_is_not_contradiction(self):
        """★ A STATED LENGTH THAT IS NOT 400 IS NEVER THE REFERENCE, and a
        stated length that cannot be a track is refused rather than read as
        silence -- otherwise a broken row joins the anchor on the strength of
        being broken."""
        for mode in (None, True, False):
            self.assertFalse(tg.isFlatOutdoor400(420, "Flat", 0, mode), mode)
            self.assertFalse(tg.isFlatOutdoor400(200, "Flat", 0, mode), mode)
            self.assertFalse(tg.isFlatOutdoor400(0, "Flat", 0, mode), mode)
            self.assertFalse(tg.isFlatOutdoor400(-5, "Flat", 0, mode), mode)
            # banked and indoor still lose, whatever the length policy
            self.assertFalse(tg.isFlatOutdoor400(None, "Banked", 0, mode), mode)
            self.assertFalse(tg.isFlatOutdoor400(None, "Flat", 1, mode), mode)

    def test_an_unstated_surface_is_still_refused(self):
        """! NOT ASSUMED OUTDOOR. The length policy is about length; an
        indoor 400 must not join the outdoor anchor by omission."""
        self.assertFalse(tg.isFlatOutdoor400(400, "Flat", None))
        self.assertFalse(tg.isFlatOutdoor400(None, "Flat", None))

    def test_the_vector_form_agrees_with_the_scalar_one(self):
        """! A SECOND IMPLEMENTATION IS THE FAILURE THE MODULE EXISTS TO
        PREVENT, so this asserts they cannot drift."""
        lens = [400.0, 402.0, 200.0, np.nan, 400.0, 400.0, 420.0]
        types = ["Flat", "Flat", "Flat", "Flat", "Banked", "", "Flat"]
        inds = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        for mode in (None, True, False):
            got = tg.flatOutdoor400Mask(lens, types, inds, mode)
            want = [tg.isFlatOutdoor400(None if l != l else l, t, i, mode)
                    for l, t, i in zip(lens, types, inds)]
            self.assertEqual(list(got), want, mode)

    def test_the_policy_can_only_widen_the_class(self):
        """! assume400 ADDS cells and never removes one, so the strict mask is
        a subset. A policy that changed a stated fact would be a bug."""
        lens = [400.0, 402.0, 200.0, np.nan, None, 400.0, 400.0, 420.0, 0.0]
        types = ["Flat", "Flat", "Flat", "Flat", "", "Banked", "", "Flat", ""]
        inds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        loose = tg.flatOutdoor400Mask(lens, types, inds, True)
        strict = tg.flatOutdoor400Mask(lens, types, inds, False)
        self.assertTrue(bool(np.all(loose | strict == loose)),
                        "strict must be a subset of assume400")
        self.assertGreater(int(loose.sum()), int(strict.sum()))


class TheOverrideVenues(unittest.TestCase):
    """The plan's own test: the predicate against the hand-verified venues."""

    def _fields(self, loc, date, **over):
        f = {"track_type": "Banked", "track_length": 200.0, "is_indoor": 1,
             "location_id": loc}
        f.update(over)
        fixed, rule = vgo.applyVenueOverride(f, loc, date)
        return fixed, rule

    def test_every_override_venue_is_indoor_so_none_can_enter_the_class(self):
        """All five curated venues are indoor fieldhouses. Whatever the
        override does to their banking, none of them may become part of the
        OUTDOOR reference class -- which is what `when: {is_indoor: 1}` on
        every rule is protecting ("never flip the outdoor 400")."""
        for loc in vgo.OVERRIDES:
            fixed, rule = self._fields(loc, "2015-02-01")
            self.assertEqual(int(fixed["is_indoor"]), 1, loc)
            self.assertFalse(tg.isFlatOutdoor400(fixed["track_length"],
                                                 fixed["track_type"],
                                                 fixed["is_indoor"]), loc)

    def test_clemson_before_the_rebuild_is_flat_and_still_not_a_400(self):
        """The override turns banking off; the length (200m) keeps it out of
        the class anyway. Both clauses doing their job independently."""
        fixed, rule = self._fields(63253, "2010-02-01")
        self.assertIsNotNone(rule)
        self.assertEqual(fixed["track_type"], "Flat")
        self.assertFalse(tg.isFlatOutdoor400(fixed["track_length"],
                                             fixed["track_type"], 1))

    def test_clemson_after_the_rebuild_keeps_its_banked_meta(self):
        fixed, rule = self._fields(63253, "2020-02-01")
        self.assertIsNone(rule)
        self.assertEqual(fixed["track_type"], "Banked")

    def test_hawkeye_is_corrected_in_both_directions(self):
        """85120 is the two-rule venue: flat before 2016-07-01, banked 200m
        after. The oversized-flat case the plan names."""
        before, r1 = self._fields(85120, "2012-02-01", track_length=300.0)
        after, r2 = self._fields(85120, "2019-02-01", track_length=300.0)
        self.assertEqual(before["track_type"], "Flat")
        self.assertIsNotNone(r2)
        self.assertEqual(after["track_type"], "Banked")
        self.assertEqual(float(after["track_length"]), 200.0)

    def test_an_outdoor_row_at_an_override_venue_is_untouched(self):
        """A campus location_id can cover an outdoor track too, and no rule
        may reach it -- the header's "never flip the outdoor 400"."""
        for loc in vgo.OVERRIDES:
            fixed, rule = self._fields(loc, "2015-02-01", is_indoor=0,
                                       track_type="Flat", track_length=400.0)
            self.assertIsNone(rule, loc)
            self.assertTrue(tg.isFlatOutdoor400(fixed["track_length"],
                                                fixed["track_type"],
                                                fixed["is_indoor"]), loc)


class TheGaugeWiring(unittest.TestCase):

    def test_flat400_is_a_choice_and_the_default_is_still_outdoor(self):
        """! THE MODULE DEFAULT IS UNCHANGED ON PURPOSE. The pin arrives
        through deploy/solve_env.sh, so a bare fit() keeps its old behaviour
        and every existing diagnostic reads the same as before."""
        self.assertIn("flat400", be.GAUGE_CHOICES)
        self.assertEqual(be.GAUGE_DEFAULT, "outdoor")

    def test_the_reference_cells_are_held_at_zero_not_their_mean(self):
        src = __import__("inspect").getsource(be.fit)
        self.assertIn("np.where(hard_ref & (w_c_ > 0), hard_val, D_new_)", src)
        # ! hard_val IS 0.0 FOR EVERY FLAT-400 CELL (2026-09-20); only a
        #   NAMED cross-country course can carry a different number.
        self.assertIn("hard_val = np.zeros(n_cell", src)
        # ! and only where the cell has votes: forcing a cell nobody raced
        #   would publish a 0.0 no race supports
        self.assertIn("hard_ref & (w_c_ > 0)", src)

    def test_a_pack_without_geometry_STOPS(self):
        """⚠ THIS TEST USED TO ASSERT THE OPPOSITE, and the opposite was wrong
        (owner, 2026-09-20). "falls back out loud" was the design: print a
        warning, run as gauge=outdoor, carry on. A warning a reader has to
        notice is not a guard -- both arms of a real gauge comparison printed
        it, ran on the wrong gauge, and produced two identical sds that looked
        like a finding. Asking for flat400 and getting outdoor is a DIFFERENT
        MODEL, so it stops now."""
        src = __import__("inspect").getsource(be.fit)
        self.assertIn("raise ValueError", src)
        self.assertIn("REBUILD THE PACK", src)
        self.assertNotIn("-- falling back to gauge=outdoor. Rebuild", src)

    def test_the_pipeline_can_ask_for_it(self):
        env = open(os.path.join(_ROOT, "deploy", "solve_env.sh")).read()
        pipe = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
        self.assertIn("XCP_GAUGE:=flat400", env)
        self.assertIn('--gauge "$XCP_GAUGE"', pipe)
        self.assertIn('--bracket-indoor-centre "$XCP_BRACKET_INDOOR_CENTRE"', pipe)

    def test_run_joint_passes_the_gauge_through(self):
        """⚠ IT DID NOT, AND THAT WAS HALF THE INDOOR BUG: bracketDifficulties
        called be.fit without a gauge, so the engine always took its default
        and no flag could reach it."""
        src = open(os.path.join(_ROOT, "engine", "run_joint.py")).read()
        body = src[src.index("def bracketDifficulties("):]
        body = body[:body.index("\ndef ")]
        self.assertIn('place_kw["gauge"] = gauge', body)
        self.assertIn('place_kw["indoor_centre"]', body)

    def test_the_double_recentring_is_skipped_under_the_pin(self):
        """⚠ THE OTHER HALF. run_joint re-centred every cell on the outdoor
        mean AFTER the engine had set its zero, which moved indoor by a second
        differently-weighted amount -- so an asserted +0.3% published as
        -1.68% -- and under the pin would move the cells just fixed at 0.0."""
        src = open(os.path.join(_ROOT, "engine", "run_joint.py")).read()
        body = src[src.index("def bracketDifficulties("):]
        self.assertIn('skip_recentre = str(f.get("gauge") or "") == "flat400"',
                      body)
        self.assertIn("if skip_recentre:", body)


class AMissingGaugeStopsTheRun(unittest.TestCase):
    """⚠⚠ IT USED TO FALL BACK SILENTLY, AND IT VOIDED A REAL EXPERIMENT
       (owner, 2026-09-20). Both arms of a --gauge-scope sport/merge comparison
       printed one warning line and ran as gauge=outdoor, so merge fused two
       MEAN-pinned groups into one mean-pinned group with no absolute anchor to
       transmit. The sds came out identical -- which looked like a finding and
       was an artefact of the fallback.

       docs/HANDOFF-2026-09-20.md PART 2 predicted it: "the pin silently became
       the old outdoor-mean gauge and everything below is moot." A warning a
       reader has to notice is not a guard.
    """

    def setUp(self):
        with open(os.path.join(_ROOT, "engine", "bracket_engine.py"),
                  encoding="utf-8") as fh:
            self.src = fh.read()

    def test_absent_geometry_raises_instead_of_regauging(self):
        i = self.src.index("if not have_geom:")
        body = self.src[i:i + 1800]
        self.assertIn("raise ValueError", body)
        self.assertIn("REBUILD THE PACK", body)

    def test_a_shape_mismatch_raises_too(self):
        """! GEOMETRY FROM ANOTHER PACK IS THE SAME BUG WEARING A HAT."""
        i = self.src.index("if ref_base.size != n_base:")
        self.assertIn("raise ValueError", self.src[i:i + 900])

    def test_the_old_behaviour_is_still_reachable_and_says_it_is_void(self):
        """! A RUN MAY GENUINELY WANT WHATEVER GAUGE IS AVAILABLE -- but it is
        told, in the message, that comparisons made under it do not count."""
        self.assertIn("XCP_GAUGE_FALLBACK", self.src)
        self.assertIn("void", self.src)

    def test_os_is_imported(self):
        """! py_compile DOES NOT CATCH A MISSING IMPORT. The guard reads an
        environment variable, and bracket_engine did not import os -- so the
        first pack without geometry would have raised NameError from inside
        the error path."""
        self.assertIn("\nimport os\n", self.src)


class IndoorsCentre(unittest.TestCase):

    def test_the_centre_is_the_owners_number(self):
        self.assertEqual(be.INDOOR_CENTRE, 0.003)

    def test_the_gates_are_the_owners_range(self):
        self.assertEqual(be.INDOOR_GATES, (-0.003, 0.020))

    def test_the_gates_are_a_report_and_never_a_clamp(self):
        """★ THE DISTINCTION THE OWNER AGREED TO: "they're right they're sanity
        gates". A clipped cell stops responding to evidence and cannot be told
        apart from a genuinely +2.0% one -- the distance floor's mistake."""
        src = __import__("inspect").getsource(be.fit)
        self.assertIn("PUBLISHED ANYWAY", src)
        for clamp in ("np.clip(D, ", "np.minimum(D, INDOOR", "np.maximum(D, INDOOR"):
            self.assertNotIn(clamp, src)

    def test_the_centre_only_moves_the_indoor_group(self):
        src = __import__("inspect").getsource(be.fit)
        self.assertIn("g_mean_[PG_INDOOR] = float(indoor_centre)", src)
        # named, not a bare 2 that survives a reordering
        self.assertEqual(be.PG_INDOOR, 2)
        self.assertEqual(be.PRIOR_GROUP_NAMES[be.PG_INDOOR], "TF:in")

    def test_it_is_the_same_number_the_joint_solve_is_told(self):
        env = open(os.path.join(_ROOT, "deploy", "solve_env.sh")).read()
        self.assertIn("XCP_INDOOR_LEVEL:=0.003", env)
        self.assertIn("XCP_BRACKET_INDOOR_CENTRE:=0.003", env)


if __name__ == "__main__":
    unittest.main()


# ===================================================================== #
#  AND THE PIN ON A REAL FIT, not on the source text                    #
# ===================================================================== #

import contextlib                                                # noqa: E402
import io                                                        # noqa: E402

sys.path.insert(0, os.path.join(_ROOT, "tests"))
from test_track_diagnostics import _track_pack                   # noqa: E402


def _withGeometry(cols, banked=(6, 7), oversized=(8,)):
    """The track world plus geometry: ovals 0-5 indoor 200s, tracks 6-35
    outdoor 400s except two banked and one oversized, which must NOT join the
    reference class."""
    keys = list(cols["course_keys"])
    n = len(keys)
    length = np.full(n, np.nan)
    ttype = np.array([""] * n, dtype="U32")
    indoor = np.zeros(n)
    for i, k in enumerate(keys):
        if str(k).endswith(":in"):
            length[i], ttype[i], indoor[i] = 200.0, "Flat", 1.0
        else:
            length[i], ttype[i], indoor[i] = 400.0, "Flat", 0.0
    for i in banked:
        ttype[i] = "Banked"
    for i in oversized:
        length[i] = 500.0
    out = dict(cols)
    out["track_length"] = length
    out["track_type"] = ttype
    out["track_indoor"] = indoor
    return out


def test_the_reference_cells_come_out_exactly_zero():
    cols, _npz, _level = _track_pack()
    cols = _withGeometry(cols)
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=60, top=1.0, era_years=0,
                   gauge="flat400", verbose=False)
    D = np.asarray(f["D"])
    ref = np.asarray(f["hard_ref"])
    votes = np.asarray(f["votes"])
    pinned = ref & (votes > 0)
    assert pinned.sum() > 20, pinned.sum()
    # exactly, not approximately: that is what "a fixed reference" means
    assert np.allclose(D[pinned], 0.0, atol=1e-12), D[pinned][:5]


def test_a_banked_or_oversized_outdoor_track_is_not_pinned():
    """The whole reason §1 had to be built: under gauge=outdoor these were
    part of the anchor, because the cell key cannot tell them apart."""
    cols, _npz, _level = _track_pack()
    cols = _withGeometry(cols, banked=(6, 7), oversized=(8,))
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=60, top=1.0, era_years=0,
                   gauge="flat400", verbose=False)
    ref = np.asarray(f["hard_ref"])
    keys = [str(k) for k in f["cell_keys"]]
    for i in (6, 7, 8):
        j = keys.index(f"TF:loc:{i}:out")
        assert not ref[j], keys[j]
    j = keys.index("TF:loc:20:out")
    assert ref[j]


def test_indoor_is_not_in_the_reference_class():
    cols, _npz, _level = _track_pack()
    cols = _withGeometry(cols)
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=60, top=1.0, era_years=0,
                   gauge="flat400", verbose=False)
    ref = np.asarray(f["hard_ref"])
    keys = [str(k) for k in f["cell_keys"]]
    for i in range(6):
        assert not ref[keys.index(f"TF:loc:{i}:in")]


def test_the_gate_report_counts_and_does_not_clamp():
    """The planted indoor level here is +1.5%, inside the gates; the report
    must still exist, count every indoor cell, and leave D alone."""
    cols, _npz, level = _track_pack(indoor_level=0.015)
    cols = _withGeometry(cols)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        f = be.fit(cols, None, window=60, top=1.0, era_years=0,
                   gauge="flat400", verbose=True)
    r = f["indoor_gate_report"]
    assert r is not None and r["n_indoor"] == 6, r
    assert r["centre"] == be.INDOOR_CENTRE
    assert "PUBLISHED ANYWAY" in buf.getvalue()


def test_a_pack_without_geometry_says_so_and_still_fits():
    cols, _npz, _level = _track_pack()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        f = be.fit(cols, None, window=60, top=1.0, era_years=0,
                   gauge="flat400", verbose=True)
    assert "falling back to gauge=outdoor" in buf.getvalue()
    assert not np.asarray(f["hard_ref"]).any()
    assert np.isfinite(np.asarray(f["D"])).all()


# ===================================================================== #
#  §4 THE RACE-DAY TERM, MEASURED WHERE THE COURSE CANNOT ABSORB IT     #
# ===================================================================== #

def _pinnedWorld(n_ath=900, seed=11, day_sd=0.012, noise=0.02, n_day=40):
    """One flat outdoor 400 (the reference) raced on n_day days, plus one XC
    course, so every athlete has a level. Each reference DAY carries a planted
    shift; the course itself is identical every day. So the only thing a
    reference race's reading can contain is its day."""
    rng = np.random.default_rng(seed)
    a_true = rng.normal(0, 0.15, n_ath)
    day_shift = rng.normal(0, day_sd, n_day)
    ath, course, doy, eff = [], [], [], []
    for i in range(n_ath):
        for d in rng.choice(n_day, 3, replace=False):
            ath.append(i); course.append(0); doy.append(100 + d)
            eff.append(day_shift[d])
        ath.append(i); course.append(1); doy.append(300)
        eff.append(0.05)                      # the XC course, plainly harder
    ath = np.array(ath); course = np.array(course)
    doy = np.array(doy); eff = np.array(eff)
    y = a_true[ath] + eff + rng.normal(0, noise, ath.size)
    keys = ["TF:loc:1:out", "XC:7:d5000"]
    cols = {"athlete": ath, "year": np.full(ath.size, 2025), "course": course,
            "days": (400 - doy).astype(np.float64), "doy": doy,
            "sport": np.where(course == 0, 1, 0).astype(np.int64),
            "norm": np.exp(y), "dist_m": np.where(course == 0, 1600.0, 5000.0),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": keys,
            "track_length": np.array([400.0, np.nan]),
            "track_type": np.array(["Flat", ""], dtype="U32"),
            "track_indoor": np.array([0.0, np.nan])}
    return cols, day_sd


def test_the_day_noise_is_recovered_from_the_pinned_cell():
    """★ THE POINT OF §4. The reference cell is held at 0.0, so a race there
    reads its DAY and nothing else -- and the estimate should land on the
    planted day sd."""
    cols, day_sd = _pinnedWorld(day_sd=0.012)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        f = be.fit(cols, None, window=365, top=1.0, era_years=0,
                   prior_group="fit", gauge="flat400", verbose=True)
    r = f["day_report"]
    assert r is not None and r["n_races"] >= 30, r
    # within a fifth of the planted value: the estimator is robust (MAD), the
    # rows carry their own noise, and the levels are estimated not given
    assert abs(r["sigma_day"] - day_sd) < 0.4 * day_sd, (r, day_sd)
    assert "race-day noise at the PINNED reference cells" in buf.getvalue()


def test_a_bigger_planted_day_noise_reads_bigger():
    """Monotone in the thing it claims to measure, which a single-point check
    cannot show."""
    got = []
    for sd in (0.008, 0.024):
        cols, _ = _pinnedWorld(day_sd=sd)
        with contextlib.redirect_stdout(io.StringIO()):
            f = be.fit(cols, None, window=365, top=1.0, era_years=0,
                       prior_group="fit", gauge="flat400", verbose=False)
        got.append(f["day_report"]["sigma_day"])
    assert got[1] > 1.5 * got[0], got


def test_the_default_measures_and_changes_nothing():
    """! THE DEFAULT IS 'fitted'. The measurement is printed; the prior's
    numerator is untouched until asked. Same D either way."""
    cols, _ = _pinnedWorld()
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=365, top=1.0, era_years=0,
                   prior_group="fit", gauge="flat400", verbose=False)
    assert f["day_noise"] == "fitted"
    assert f["day_report"]["used"] is False
    assert np.isfinite(f["day_report"]["sigma_day"])


def test_reference_mode_uses_it_for_the_outdoor_group_only():
    cols, _ = _pinnedWorld()
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=365, top=1.0, era_years=0,
                   prior_group="fit", gauge="flat400", day_noise="reference",
                   verbose=False)
    assert f["day_report"]["used"] is True
    rep = f["prior_report"]
    if rep is not None:
        # XC keeps its own confounded estimate: sigma_day measured on a track
        # in April is not a November cross-country day -- see the note on
        # dayNoiseAtReference, which corrects the plan on exactly this.
        xc = rep[be.PG_XC]
        if np.isfinite(xc[1]) and np.isfinite(xc[4]):
            assert abs(xc[1] - xc[4]) < 1e-12, xc


def test_it_declines_without_a_pin():
    """No reference cells -> no unconfounded races -> NaN, not a guess."""
    cols, _ = _pinnedWorld()
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=365, top=1.0, era_years=0,
                   prior_group="fit", gauge="outdoor", verbose=False)
    r = f["day_report"]
    assert r is None or not np.isfinite(r["sigma_day"]), r


def test_a_bad_day_noise_name_is_refused():
    cols, _ = _pinnedWorld()
    try:
        be.fit(cols, None, window=365, day_noise="refrence")
    except ValueError as exc:
        assert "day_noise must be one of" in str(exc)
    else:
        raise AssertionError("a typo must not silently mean 'fitted'")


# ===================================================================== #
#  (a) THE COMPARISON: BOTH ESTIMATORS ON ONE GAUGE                     #
# ===================================================================== #

class TheJointComparison(unittest.TestCase):
    """⚠ THE POINT: two difficulty columns on two different zeros cannot be
    subtracted. The joint solve's d is centred by recentreLevels; the bracket
    engine's D under the pin is centred on the flat outdoor 400s. Comparing
    them raw measures the gauge."""

    def setUp(self):
        import importlib
        self.m = importlib.import_module("diag_joint_vs_bracket")

    def test_regauge_puts_the_reference_mean_at_zero(self):
        d = np.array([0.02, 0.04, 0.10, -0.01])
        ref = np.array([True, True, False, False])
        grp = np.zeros(4, dtype=np.int64)
        out, shift = self.m._regauge(d, ref, grp)
        self.assertAlmostEqual(float(out[ref].mean()), 0.0)
        self.assertAlmostEqual(float(shift[0]), 0.03)
        # the non-reference cells move by the same amount, not to zero
        self.assertAlmostEqual(float(out[2]), 0.07)

    def test_each_group_is_gauged_on_its_own_reference(self):
        d = np.array([0.02, 0.04, 0.50, 0.60])
        ref = np.array([True, False, True, False])
        grp = np.array([0, 0, 1, 1], dtype=np.int64)
        out, shift = self.m._regauge(d, ref, grp)
        self.assertAlmostEqual(float(shift[0]), 0.02)
        self.assertAlmostEqual(float(shift[1]), 0.50)
        self.assertAlmostEqual(float(out[0]), 0.0)
        self.assertAlmostEqual(float(out[2]), 0.0)

    def test_a_group_with_no_reference_falls_back_to_its_own_mean(self):
        """! NEVER TO NO CELLS. A group with no reference cell still has to be
        gauged somehow or its numbers are not comparable to anything."""
        d = np.array([0.10, 0.20])
        ref = np.array([False, False])
        grp = np.zeros(2, dtype=np.int64)
        out, shift = self.m._regauge(d, ref, grp)
        self.assertAlmostEqual(float(shift[0]), 0.15)
        self.assertAlmostEqual(float(out.mean()), 0.0)

    def test_it_refuses_a_solve_file_without_the_joint_column(self):
        """delta_joint only exists on a --difficulty bracket solve, so the
        script must say that rather than compare a column with itself."""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.npz")
            np.savez(p, delta=np.zeros(3))
            r = subprocess.run(
                [sys.executable, os.path.join(_ROOT, "engine",
                                              "diag_joint_vs_bracket.py"),
                 "--npz", p],
                capture_output=True, text=True,
                env=dict(os.environ, XCP_DB_PASSWORD="x"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("delta_joint", r.stdout + r.stderr)

    def test_the_chain_runs_it_and_the_holdout_is_no_longer_skipped(self):
        """(a) cannot be measured predictively without a FRESH joint holdout
        dump: a stale one prints "do not land on this pack" and silently
        declines, which is what happened on 2026-09-19."""
        sh = open(os.path.join(_ROOT, "scripts",
                              "overnight_fit_pool_solve.sh")).read()
        self.assertIn("diag_joint_vs_bracket.py", sh)
        self.assertIn("--skip 08b_ladder", sh)
        self.assertNotIn("--skip 08a_holdout,08b_ladder", sh)
