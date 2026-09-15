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

# Positions inside a sequence row that this file reads by NAME. Mirror
# _buildSequenceVector in feature_extraction.py: index 0 is the prior race's
# normalized_time in seconds, index 2 is days before the target.
SEQ_NORM_TIME = 0
SEQ_DAYS_AGO = 2

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
        n_real = masks.sum(dim=1).clamp(min=1)                   # [B]
        idx = (n_real - 1).view(-1, 1, 1).expand(-1, 1,
                                                  sequences.shape[2])
        last_row = sequences.gather(1, idx).squeeze(1)           # [B, F]
        base = last_row[:, SEQ_NORM_TIME].to(torch.float32)
        return torch.where(base > 0, base,
                           self.fallback_seconds.expand_as(base))

    def logRatio(self, sequences, masks, targets) -> torch.Tensor:
        """ln(target / baseline), in natural-log units. [B]"""
        base = self.baselineSeconds(sequences, masks)
        t = targets.to(torch.float32).clamp(min=_MIN_SECONDS)
        return torch.log(t / base.clamp(min=_MIN_SECONDS))

    def targetZ(self, sequences, masks, targets) -> torch.Tensor:
        """What forward() is trained to output for these targets. [B]"""
        return (self.logRatio(sequences, masks, targets)
                - self.target_mean) / self.target_std

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
