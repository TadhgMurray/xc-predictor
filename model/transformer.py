# Project: xc-predictor
# File:    model/transformer.py
# Purpose: Defines the network that predicts an athlete's normalized_time
#          from their race history (sequence) + the target race (context).
#          This file ONLY defines the forward pass. PyTorch derives the
#          backward pass (backprop) automatically from it.
#
# WHAT CHANGED (2026-09-02), and why each is here rather than in train.py:
#
#   1. THE TARGET IS A LOG RATIO TO THE ATHLETE'S LAST RACE, not absolute
#      seconds. The old head had to learn each athlete's level from scratch
#      through a mean over their history and then output seconds, so a slow
#      runner's 30-second miss and a fast runner's 30-second miss cost the
#      same, and most of the network's capacity went into reproducing the
#      level rather than the change. Predicting ln(target / last race) makes
#      "no change" the zero point, puts every athlete on one multiplicative
#      scale (the engine's own), and makes the printed error a percentage.
#      The baseline is computed HERE, from the sequence tensor, so training
#      and inference cannot disagree about it -- see baselineSeconds.
#
#   2. INPUT STANDARDISATION LIVES IN THE MODEL AS BUFFERS. The sequence
#      vector carries raw seconds, raw days, raw metres, raw altitude and
#      raw GPS in one row, and nothing scaled them. Per-feature mean/std are
#      registered buffers set once from the training set, so they ride in
#      model.pt and inference gets them for free. An uncalibrated model
#      (mean 0, std 1) is the identity, so nothing breaks if they are unset.
#
#   3. THE TARGET RACE ASKS THE HISTORY A QUESTION. The context vector used
#      to be concatenated AFTER the history had been mean-pooled, so the
#      encoder could never favour prior races that resemble the target (same
#      distance, same sport, same course). One cross-attention query built
#      from the context now reads the encoded history; the mean pool is kept
#      beside it so the athlete's overall level is still one step away.
#
#   4. PRE-NORM ENCODER LAYERS (norm_first=True), which train stably without
#      a long warmup, plus one LayerNorm on the way out because pre-norm
#      leaves the residual stream unnormalised.
#
# ⚠ THE SEQUENCE LAYOUT THIS FILE ASSUMES. Rows are chronological with the
#   most recent race LAST, padding after the real rows, and masks True on
#   real rows. feature_extraction builds them that way, collateRagged and
#   racecast/predict.py pad them that way, and _padSequence keeps the LAST
#   max_len rows. Feature 0 of a row is that race's normalized_time in
#   seconds; feature 2 is days before the target.

import torch
import torch.nn as nn

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# Width of one race vector coming in from feature_extraction.py.
SEQUENCE_FEATURES = 21

# Width of the target-race context vector (also from feature_extraction).
# 24: is_forecast at index 0, then 2026-09-15 appended the grade ordinal,
# its known-flag and the race year at 21-23. ⚠ THIS MUST MATCH
# _buildContextVector IN feature_extraction.py. A mismatch surfaces as a shape
# error inside the first Linear AFTER the extraction has written gigabytes --
# which is why tests/test_context_width.py counts the builder's return and
# compares it to both constants without needing a database.
CONTEXT_FEATURES = 24

# ★ WHICH CONTEXT SLOT HOLDS THE RACE YEAR, duplicated here for the same
#   reason CONTEXT_FEATURES is: this module imports nothing but torch, so
#   train.py and racecast/predict.py can read it on a machine with no
#   database and no corrections.py.
#
# ⚠ train.py IMPORTED IT FROM feature_extraction FOR ONE COMMIT AND THAT
#   BROKE THE POD (2026-09-15). feature_extraction pulls in `database`
#   (which wants a password) and `corrections` (51 MB, gitignored, server
#   only), so `import train` died before it read a single chunk -- on the
#   one machine that has the GPU and none of that.
#
# ! tests/test_context_width.py pins this against feature_extraction's copy
#   AND against the builder's actual output, so the duplication cannot rot.
CONTEXT_YEAR_INDEX = 23

# ★ TWO MORE CONTEXT POSITIONS WORTH NAMING, because the aggregate error is
#   an average over jobs of wildly different difficulty and the average is
#   the one number that describes neither of them.
#
#   The corpus is 51% real next-race examples, 18% forecast twins and 30%
#   HORIZON twins -- a horizon example asks what somebody runs one to four
#   YEARS from their last race, which is the recruiting projection, and it is
#   nothing like predicting Saturday. Reporting one MAE over the mixture
#   flatters the hard job and libels the easy one.
#
# ! AND NO RE-EXTRACTION IS NEEDED TO SPLIT THEM. days_since_last_race is
#   already in the context vector -- it is what made the horizon class
#   possible without a new feature -- so validation can band on it directly.
CONTEXT_IS_FORECAST_INDEX = 0
CONTEXT_GAP_INDEX = 4          # days_since_last_race, raw days

# Positions inside a sequence row that this file reads by NAME. Mirror
# _buildSequenceVector in feature_extraction.py: index 0 is the prior race's
# normalized_time in seconds, index 2 is days before the target.
SEQ_NORM_TIME = 0
SEQ_DAYS_AGO = 2

# ★ WHAT THE PREDICTION IS ANCHORED ON, AND WHY IT IS NOW A CHOICE.
#
#   forward() predicts a ratio, and predictInterval returns
#   baselineSeconds * exp(mu). So the baseline is not a detail -- it is the
#   whole prediction, with the network supplying a correction on top.
#
# ⚠ AND THE CORRECTION IS SMALL. Measured on a real 152-athlete championship
#   (scripts/diag_model_quality.py, 2026-09-17): exp(mu) has an interquartile
#   range of 0.972-1.023, about one race's noise. So whatever the baseline
#   says, the answer mostly agrees with it.
#
#   The same run scored the ordering three ways against what the day did:
#
#       their last race alone     spearman 0.792
#       THE MODEL                 spearman 0.899
#       season mean rating        spearman 0.904
#
#   That places the fault exactly. The model beats the last race and loses to
#   the season average -- it is anchored on one race and only partly corrected
#   toward the season, when the season is the better answer. A fluke last race
#   is injected straight into the prediction and the network has to spend its
#   small correction undoing it.
#
#   Owner, 2026-09-17: "it has me losing to my teamate who I beat in every
#   single race that season". That is this, exactly.
#
# ★ SO THE BASELINE IS SELECTABLE, AND THE CHOICE RIDES IN THE CHECKPOINT.
#   Both buffers are in the state_dict, so inference cannot use a different
#   rule from the one the weights were trained under -- which would be
#   silently catastrophic rather than loud.
BASELINE_LAST = 0.0      # the most recent race, alone. What shipped.
BASELINE_EWMA = 1.0      # recency-weighted geometric mean of the history.

# Half-life in days for BASELINE_EWMA: a race this long ago counts half as
# much as one today. 60 days weights a whole cross country season with a
# recency tilt, rather than letting the last Saturday decide everything.
BASELINE_HALF_LIFE_DAYS = 60.0


# Purpose:   the weight each prior race gets in an EWMA baseline.
# ★ ONE DEFINITION OF THE RULE, TWO LAYOUTS. The model sees padded [B,S]
#   tensors and train.py's chunks are ragged [R]; both call this for the
#   weights and do their own reduction. tests/test_baseline_rule.py builds
#   the same history in both shapes and asserts the baselines match, because
#   two spellings of a rule is how they come to disagree.
# ! CLAMPED AT ZERO. days_ago is days BEFORE the target and is never
#   negative in a well-formed sequence, but a bad date would otherwise make
#   exp() blow up rather than merely be wrong.
def baselineWeights(days, valid, half_life):
    import math as _math
    lam = _math.log(2.0) / max(float(half_life), 1e-6)
    w = torch.exp(-lam * days.clamp(min=0.0).to(torch.float32))
    return torch.where(valid, w, torch.zeros_like(w))

# ★ VENUE EMBEDDING. course_difficulty stays as the PRIOR -- it is solved from
#   athletes who raced here and elsewhere, which is cross-athlete linkage this
#   model cannot reconstruct from its own loss. The embedding is for the
#   RESIDUAL: whatever about a venue survives after its delta is charged.
#
# ⚠ SIZE IT FROM venue_vocab.pkl, NEVER FROM A COUNT AT TRAINING TIME. The
#   vocabulary is written beside the chunks; deriving it again from a different
#   slice would reindex every venue, and the embedding trained for Woodward
#   Park would belong to some other course.
VENUE_EMBED_DIM = 16

# The "thinking width" we project each race into before attention.
EMBED_DIM = 256

# Attention runs across the S races, not across the 256 numbers. Multi-head:
# 8 heads of 32 each, so heads can specialise (recency, distance, season...).
N_HEADS = 8

# How many encoder layers we stack. Each refines the one below it.
N_LAYERS = 4

# The feedforward expansion inside each encoder layer: 256 -> 1024 -> 256.
FF_DIM = EMBED_DIM * 4

# Fraction of activations randomly zeroed during training (regularisation).
# Automatically disabled when you call model.eval().
DROPOUT = 0.1

# A std below this is a constant feature (a flag that never varied in the
# sample); it is left unscaled rather than divided by ~0.
_STD_FLOOR = 1e-6

# The smallest seconds value a target or a baseline may take inside a log.
_MIN_SECONDS = 1.0

# Bounds on the variance head's log-variance, in z units. See forwardDist.
LOGVAR_MIN, LOGVAR_MAX = -7.0, 3.0

# ★ AND THE TARGET IS CLAMPED TOO, WHICH IT WAS NOT (2026-09-16, the first
#   full-corpus run went NaN in its second epoch).
#
#   The loss is Gaussian NLL on the z-scored log ratio, so it is QUADRATIC
#   in z. The corpus has corrupt times in it -- this database has a result
#   dated 2223 -- and _MIN_SECONDS lets a bad row through as one second: a
#   1s time against a 1000s baseline is ln(1/1000) = -6.9, and at the
#   fitted std of 0.10 that is z = -69. One such example costs 0.5 * 69**2
#   = 2,370, which on its own moved a 1024-batch mean by +2.3; the observed
#   epoch mean was 3.90 against 0.073 on a clean 200-chunk sample.
#
#   The gradient is worse than the loss. d(loss)/d(mu) is (mu - z)/var, so
#   with var at its exp(-7) floor that single row asks for a step of ~76,000.
#   Gradient clipping bounds the NORM but not the DIRECTION: the whole
#   clipped step then points wherever one corrupt row wanted. A few of those
#   and the weights are gone.
#
# ! CLAMPED, NOT DROPPED. An athlete really can run far outside their form
#   -- a fall, a first race back, a DNF walked in -- and "much slower than
#   last time" is a true thing to learn. 8 sigma is a race 2.2x their last,
#   past which the row is telling us about the scraper rather than the
#   runner. Clamping keeps the example and its direction and takes away only
#   the magnitude that makes it a bomb; dropping would also throw away the
#   batch slot and quietly bias the tails.
#
# ⚠ THE SAME CLAMP MUST HOLD AT INFERENCE, which is why it lives here on
#   the model and not in the training loop. A model trained on clamped
#   targets predicts in clamped units.
TARGET_Z_CLAMP = 8.0


# ------------------------------------------------------------------ #
# THE MODEL
# ------------------------------------------------------------------ #

class XCPredictor(nn.Module):
    """History encoder + context query + head. Output is a z-scored log
    ratio; predictSeconds turns it back into a time.

    forward(sequences, masks, context, venues) -> [B]  z-scored ln(t/base)
    predictSeconds(...)                        -> [B]  seconds
    targetZ(sequences, masks, targets)         -> [B]  what forward is trained
                                                       to produce
    """

    def __init__(self, n_venues: int = 1):
        super().__init__()

        self.input_projection = nn.Linear(SEQUENCE_FEATURES, EMBED_DIM)

        layer = nn.TransformerEncoderLayer(
            d_model=EMBED_DIM, nhead=N_HEADS, dim_feedforward=FF_DIM,
            dropout=DROPOUT, activation="relu", batch_first=True,
            norm_first=True)
        # enable_nested_tensor=False: the nested fast path is unavailable
        # with norm_first anyway, and asking for it only prints a warning.
        self.encoder = nn.TransformerEncoder(layer, num_layers=N_LAYERS,
                                             enable_nested_tensor=False)
        self.encoder_norm = nn.LayerNorm(EMBED_DIM)

        # padding_idx=0 pins the UNKNOWN_VENUE row at zero and keeps it there:
        # no gradient flows to it, so the thin-venue bucket cannot drift into
        # meaning something. It is "no information", and it stays that way.
        self.venue_embedding = nn.Embedding(n_venues, VENUE_EMBED_DIM,
                                            padding_idx=0)

        # The question the target race asks of the history: one query vector
        # made from the context, attending over the encoded races.
        self.context_query = nn.Linear(CONTEXT_FEATURES, EMBED_DIM)
        self.pool_attention = nn.MultiheadAttention(
            EMBED_DIM, N_HEADS, dropout=DROPOUT, batch_first=True)

        # ★ TWO OUTPUTS: the predicted z-scored log ratio and its LOG
        #   VARIANCE. Trained under Gaussian negative log-likelihood, the
        #   second output learns where the first one's errors are large --
        #   two prior races against forty, a comeback after a long gap -- so
        #   every prediction comes with its own band. forward() returns only
        #   the mean, so every existing caller is unchanged; forwardDist()
        #   returns both.
        self.head = nn.Sequential(
            nn.Linear(2 * EMBED_DIM + CONTEXT_FEATURES + VENUE_EMBED_DIM, 64),
            nn.ReLU(),
            nn.Linear(64, 2),
        )

        # ★ CALIBRATION BUFFERS. Part of the state_dict, so model.pt carries
        #   them and inference applies exactly what training applied. The
        #   defaults are the identity, so an unset model still runs.
        self.register_buffer("seq_mean", torch.zeros(SEQUENCE_FEATURES))
        self.register_buffer("seq_std", torch.ones(SEQUENCE_FEATURES))
        self.register_buffer("ctx_mean", torch.zeros(CONTEXT_FEATURES))
        self.register_buffer("ctx_std", torch.ones(CONTEXT_FEATURES))
        self.register_buffer("target_mean", torch.zeros(()))
        self.register_buffer("target_std", torch.ones(()))
        # For an example with no usable last race (an empty history row):
        # the mean raw target of the training set, in seconds.
        self.register_buffer("fallback_seconds", torch.full((), 1000.0))
        # ★ THE BASELINE RULE TRAVELS WITH THE WEIGHTS. See BASELINE_LAST.
        #   Defaulting to LAST means a checkpoint trained before this existed
        #   loads and behaves exactly as it always did.
        self.register_buffer("baseline_mode", torch.full((), BASELINE_LAST))
        self.register_buffer("baseline_half_life",
                             torch.full((), BASELINE_HALF_LIFE_DAYS))

    # ---- calibration ----------------------------------------------- #

    def setFeatureStats(self, seq_mean, seq_std, ctx_mean, ctx_std):
        """Per-feature mean/std from the TRAINING set. Constant features
        (std under _STD_FLOOR) are left unscaled."""
        seq_std = torch.where(torch.as_tensor(seq_std) > _STD_FLOOR,
                              torch.as_tensor(seq_std), torch.ones_like(
                                  torch.as_tensor(seq_std)))
        ctx_std = torch.where(torch.as_tensor(ctx_std) > _STD_FLOOR,
                              torch.as_tensor(ctx_std), torch.ones_like(
                                  torch.as_tensor(ctx_std)))
        self.seq_mean.copy_(torch.as_tensor(seq_mean, dtype=torch.float32))
        self.seq_std.copy_(seq_std.to(torch.float32))
        self.ctx_mean.copy_(torch.as_tensor(ctx_mean, dtype=torch.float32))
        self.ctx_std.copy_(ctx_std.to(torch.float32))

    def setBaselineRule(self, mode: float,
                        half_life: float = BASELINE_HALF_LIFE_DAYS):
        """Pick what predictions are anchored on. See BASELINE_LAST.

        ⚠ CALL THIS BEFORE computeStats. The target is ln(target/baseline),
          so target_mean and target_std are properties OF THE RULE -- stats
          measured under one baseline and used under another un-z-score the
          network's output against the wrong centre.
        """
        self.baseline_mode.fill_(float(mode))
        self.baseline_half_life.fill_(max(float(half_life), 1e-6))

    def setTargetStats(self, mean: float, std: float,
                       fallback_seconds: float = None):
        """Mean/std of ln(target / baseline) over the TRAINING set."""
        self.target_mean.fill_(float(mean))
        self.target_std.fill_(max(float(std), _STD_FLOOR))
        if fallback_seconds is not None and fallback_seconds > 0:
            self.fallback_seconds.fill_(float(fallback_seconds))

    # ---- the baseline ----------------------------------------------- #

    def baselineSeconds(self, sequences: torch.Tensor,
                        masks: torch.Tensor) -> torch.Tensor:
        """The athlete's most recent visible race, in seconds. [B]

        ★ THE LAST REAL ROW, because rows are chronological with padding
          after them (see the layout note in the header). A forecast twin
          has its most recent races hidden by extraction, so its last real
          row is the most recent race the model is ALLOWED to see -- which
          is exactly the baseline inference has.

        ! AN EMPTY HISTORY is one all-zero row with mask True (collateRagged's
          NaN guard). Its seconds read 0, and the fallback takes over.
        """
        if float(self.baseline_mode) == BASELINE_EWMA:
            base = self._ewmaBaseline(sequences, masks)
        else:
            n_real = masks.sum(dim=1).clamp(min=1)               # [B]
            idx = (n_real - 1).view(-1, 1, 1).expand(-1, 1,
                                                      sequences.shape[2])
            last_row = sequences.gather(1, idx).squeeze(1)       # [B, F]
            base = last_row[:, SEQ_NORM_TIME].to(torch.float32)
        return torch.where(base > 0, base,
                           self.fallback_seconds.expand_as(base))

    def _ewmaBaseline(self, sequences: torch.Tensor,
                      masks: torch.Tensor) -> torch.Tensor:
        """Recency-weighted GEOMETRIC mean of the visible history. [B]

        ★ GEOMETRIC, NOT ARITHMETIC, because the target is a LOG ratio. The
          natural centre of ln(t) is the geometric mean, so this makes the
          thing the network predicts a deviation from the middle of its own
          distribution rather than from an arbitrary point beside it.

        ! A ROW WITH NO TIME IS NOT A ZERO-SECOND RACE. Padding is zeros and
          _orZero writes 0.0 for a missing normalized_time, so `t > 0` is
          part of what makes a row real -- the mask alone is not enough.
        """
        t = sequences[:, :, SEQ_NORM_TIME].to(torch.float32)     # [B,S]
        d = sequences[:, :, SEQ_DAYS_AGO].to(torch.float32)      # [B,S]
        valid = masks.to(torch.bool) & (t > 0)
        w = baselineWeights(d, valid, float(self.baseline_half_life))
        den = w.sum(dim=1)
        num = (w * torch.log(t.clamp(min=_MIN_SECONDS))).sum(dim=1)
        out = torch.exp(num / den.clamp(min=1e-12))
        # no usable row at all -> 0, and baselineSeconds swaps in the fallback
        return torch.where(den > 0, out, torch.zeros_like(out))

    def logRatio(self, sequences, masks, targets) -> torch.Tensor:
        """ln(target / baseline), in natural-log units. [B]"""
        base = self.baselineSeconds(sequences, masks)
        t = targets.to(torch.float32).clamp(min=_MIN_SECONDS)
        return torch.log(t / base.clamp(min=_MIN_SECONDS))

    def targetZ(self, sequences, masks, targets) -> torch.Tensor:
        """What forward() is trained to output for these targets. [B]

        Clamped to +-TARGET_Z_CLAMP -- see the constant for why an
        unclamped one took the first full-corpus run to NaN.
        """
        z = ((self.logRatio(sequences, masks, targets)
              - self.target_mean) / self.target_std)
        return z.clamp(min=-TARGET_Z_CLAMP, max=TARGET_Z_CLAMP)

    # ---- the forward pass ------------------------------------------ #

    def _forwardRaw(self, sequences: torch.Tensor, masks: torch.Tensor,
                    context: torch.Tensor,
                    venues: torch.Tensor = None) -> torch.Tensor:
        x = (sequences.to(torch.float32) - self.seq_mean) / self.seq_std
        ctx = (context.to(torch.float32) - self.ctx_mean) / self.ctx_std

        x = self.input_projection(x)                     # [B,S,21] -> [B,S,256]
        x = self._encode(x, masks)                        # [B,S,256]

        attended = self._askHistory(x, ctx, masks)        # [B,256]
        pooled = self._poolRealRaces(x, masks)            # [B,256]

        if venues is None:
            venues = torch.zeros(pooled.shape[0], dtype=torch.long,
                                 device=pooled.device)
        venue_vec = self.venue_embedding(venues)          # [B,16]

        combined = torch.cat([attended, pooled, ctx, venue_vec], dim=1)
        return self.head(combined)                         # [B,2]

    def forwardDist(self, sequences, masks, context, venues=None):
        """(mu, logvar), both [B], in z units of the log ratio.

        ⚠ logvar IS CLAMPED. Unbounded, the variance head can run to -inf on
          an easy example (infinite confidence, infinite gradient) or to +inf
          to make a hard example cost nothing. [-7, 3] in z units spans a
          sigma from 0.03 to 4.5 standard deviations, which is every case
          that means anything.
        """
        out = self._heads(sequences, masks, context, venues)
        mu = out[:, 0]
        logvar = out[:, 1].clamp(min=LOGVAR_MIN, max=LOGVAR_MAX)
        return mu, logvar

    def forward(self, sequences: torch.Tensor, masks: torch.Tensor,
                context: torch.Tensor,
                venues: torch.Tensor = None) -> torch.Tensor:
        """The mean prediction, z-scored log ratio. [B]"""
        return self._heads(sequences, masks, context, venues)[:, 0]

    def sigmaLog(self, sequences, masks, context, venues=None):
        """Predicted one-sigma spread of ln(time), per example. [B]

        In natural-log units, so 0.02 means "about 2% of the time". Undo the
        z-scoring: the head's variance is in z units of the log ratio.
        """
        _mu, logvar = self.forwardDist(sequences, masks, context, venues)
        return torch.exp(0.5 * logvar.to(torch.float32)) * self.target_std

    def predictSeconds(self, sequences, masks, context,
                       venues=None) -> torch.Tensor:
        """The prediction as a normalised time in seconds. [B]"""
        z = self.forward(sequences, masks, context, venues)
        base = self.baselineSeconds(sequences, masks)
        return base * torch.exp(z.to(torch.float32) * self.target_std
                                + self.target_mean)

    def predictInterval(self, sequences, masks, context, venues=None,
                        z_score: float = 1.0):
        """(seconds, lo, hi, sigma_log): the prediction with its band.

        lo/hi are seconds at -/+ z_score sigma in LOG time, so the band is
        multiplicative and never crosses zero. z_score 1.0 is a 68% band
        under the model's own Gaussian; 1.645 is 90%.
        """
        mu, logvar = self.forwardDist(sequences, masks, context, venues)
        base = self.baselineSeconds(sequences, masks)
        mu = mu.to(torch.float32) * self.target_std + self.target_mean
        sig = torch.exp(0.5 * logvar.to(torch.float32)) * self.target_std
        secs = base * torch.exp(mu)
        return (secs, base * torch.exp(mu - z_score * sig),
                base * torch.exp(mu + z_score * sig), sig)

    # ---- pieces ------------------------------------------------------ #

    def _heads(self, sequences, masks, context, venues=None):
        """The raw [B,2] head output; forward/forwardDist read it."""
        return self._forwardRaw(sequences, masks, context, venues)

    def _encode(self, x: torch.Tensor, masks: torch.Tensor) -> torch.Tensor:
        padding_mask = ~masks                    # True = padding = ignore
        x = self.encoder(x, src_key_padding_mask=padding_mask)
        return self.encoder_norm(x)

    def _askHistory(self, x, ctx, masks) -> torch.Tensor:
        """One query from the target race, attending over the history."""
        q = self.context_query(ctx).unsqueeze(1)          # [B,1,256]
        out, _ = self.pool_attention(q, x, x, key_padding_mask=~masks,
                                     need_weights=False)
        return out.squeeze(1)                             # [B,256]

    def _poolRealRaces(self, x: torch.Tensor,
                       masks: torch.Tensor) -> torch.Tensor:
        real_mask = masks.unsqueeze(-1).to(x.dtype)       # [B,S,1]
        summed = (x * real_mask).sum(dim=1)               # [B,256]
        counts = real_mask.sum(dim=1).clamp(min=1.0)      # [B,1]
        return summed / counts
