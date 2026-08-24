# Project: xc-predictor
# File:    model/transformer.py
# Purpose: Defines the network that predicts an athlete's normalized_time
#          from their race history (sequence) + the target race (context).
#          This file ONLY defines the forward pass. PyTorch derives the
#          backward pass (backprop) automatically from it.

import torch
import torch.nn as nn

# ------------------------------------------------------------------ #
# CONSTANTS 
# ------------------------------------------------------------------ #

# ------------------------------------------------------------------ #
# WHAT A [B, S, 256] TENSOR IS  (the shape everything flows through)
# ------------------------------------------------------------------ #
#
# It's a STACK OF GRIDS — one grid per athlete:
#
#     B   = how many athletes in this batch   (the depth of the stack)
#     S   = how many race-slots per athlete    (the rows of one grid)
#     256 = numbers describing one race         (the columns of one row)
#
# Any single value is pinned down by THREE coordinates, [b, s, f]:
#     fix b        -> one athlete's grid     ([S, 256] table)
#     fix b, s     -> one race               (a single 256-long row)
#     fix b, s, f  -> one number
#
# Two consequences this explains:
#   - nn.Linear "acts on the last dimension only": it rewrites each ROW
#     (a race's 256 numbers) and never touches which-athlete (b) or
#     which-race (s). So [B, S, 17] -> [B, S, 256] leaves B and S alone.
#   - pooling sums over dim=1 because dim=1 IS the races axis — summing
#     it collapses each athlete's grid down to a single summary row,
#     turning [B, S, 256] into [B, 256] (one vector per athlete).
#
# (Padding fills unused race-rows with zeros; the mask marks which rows
#  are real so pooling averages only those — see _poolRealRaces.)
# ------------------------------------------------------------------ #

# Width of one race vector coming in from feature_extraction.py.
SEQUENCE_FEATURES = 21

# Width of the target-race context vector (also from feature_extraction).
# 21, not 20: is_forecast was added at index 0. ⚠ THIS MUST MATCH
# _buildContextVector IN feature_extraction.py. A mismatch surfaces as a shape
# error inside the first Linear AFTER the extraction has written gigabytes.
CONTEXT_FEATURES = 21

# ★ VENUE EMBEDDING. course_difficulty stays as the PRIOR -- it is solved from
#   athletes who raced here and elsewhere, which is cross-athlete linkage this
#   model cannot reconstruct from its own loss. The embedding is for the
#   RESIDUAL: whatever about a venue survives after its delta is charged.
#
#   The tilt work measured why one number is not enough -- a course does not
#   slow every runner by the same factor; the effect scales with ability at
#   corr = -0.993. A scalar cannot express that. A vector can.
#
# ⚠ SIZE IT FROM venue_vocab.pkl, NEVER FROM A COUNT AT TRAINING TIME. The
#   vocabulary is written beside the chunks; deriving it again from a different
#   slice would reindex every venue, and the embedding trained for Woodward
#   Park would belong to some other course.
VENUE_EMBED_DIM = 16

# The "thinking width" we project each race into before attention.
# 17 raw numbers is too cramped; 256 gives attention room to work.
EMBED_DIM = 256

# ------------------------------------------------------------------ #
# WHAT ATTENTION DOES  (the heart of the encoder)
# ------------------------------------------------------------------ #
#
# After the input projection, each of the athlete's races is a vector
# of EMBED_DIM (256) numbers. There are S races (e.g. 200). Attention
# rewrites each race's vector as a BLEND of all the races' vectors,
# where each race decides how much weight to give every other race.
#
# Note: 256 is the WIDTH of one race's vector; S is HOW MANY races.
# Attention runs across the S races, not across the 256 numbers.
#
# Step 1 — every race turns its own 256-vector into THREE vectors, via
#          three separate learned weight matrices (W_Q, W_K, W_V):
#            query = what THIS race is looking for
#            key   = what a race advertises about itself
#            value = the content a race contributes if listened to
#          So 200 races -> 200 queries, 200 keys, 200 values.
#
# Step 2 — to update ONE race: dot-product its query with EVERY race's
#          key. Each dot product collapses two vectors into a single
#          match score ("how relevant is that race to me?"). -> S scores.
#
# Step 3 — softmax the S scores into S weights that sum to 1.
#
# Step 4 — the race's new vector = every race's value, each scaled by
#          its weight, summed. The race ends up mostly made of the
#          values of the races it matched.
#
# The weights are computed FROM THE DATA each time (query . key) — they
# are NOT stored in the model. The only learned parts are W_Q/W_K/W_V.
#
# Multi-head: run all of the above 8 times in parallel, each in a
# 32-wide slice (256 / 8), each with its own W_Q/W_K/W_V, then stitch
# the 8 blends back into 256. Each head can specialise (recency,
# distance, season...).
#
# Padding: fake padded races get their scores set to -infinity before
# the softmax, so their weight is 0 and no race blends in padding.
# That is exactly what src_key_padding_mask does.
#
# nn.TransformerEncoderLayer does ALL of this internally; in our code we
# only pass it nhead=8 and d_model=256.
# ------------------------------------------------------------------ #
N_HEADS = 8

# How many encoder layers we stack. Each refines the one below it.
N_LAYERS = 4

# The feedforward expansion inside each encoder layer: 256 -> 1024 -> 256.
# 4x the embedding dim, per the original transformer paper.
FF_DIM = EMBED_DIM * 4  # = 1024

# Fraction of activations randomly zeroed during training (regularisation).
# Automatically disabled when you call model.eval().
DROPOUT = 0.1

# ------------------------------------------------------------------ #
# XCPredictor Model Class 
# ------------------------------------------------------------------ #

class XCPredictor(nn.Module):
    # Inheriting nn.Module is what makes PyTorch track our weights,
    # move the model to GPU, toggle dropout, and save/load — for free.

    def __init__(self, n_venues: int = 1):
        # Runs nn.Module's own setup first. Mandatory before we register
        # any layers, or PyTorch won't see our weights.
        super().__init__()

        # Build the three parts of the model.
        self.input_projection = self._buildInputProjection()
        self.encoder          = self._buildEncoder()
        # padding_idx=0 pins the UNKNOWN_VENUE row at zero and keeps it there:
        # no gradient flows to it, so the thin-venue bucket cannot drift into
        # meaning something. It is "no information", and it stays that way.
        self.venue_embedding  = nn.Embedding(n_venues, VENUE_EMBED_DIM,
                                             padding_idx=0)
        self.head             = self._buildHead()

    # _buildInputProjection
    # Purpose: the 17 -> 256 "dimension up" step. One Linear layer.
    # Arguments: None
    # Output: an nn.Linear that rewrites each race's 17 numbers as 256.
    def _buildInputProjection(self) -> nn.Linear:
        # nn.Linear(in, out) holds a learned [out, in] weight matrix
        # + a [out] bias, and acts on the LAST dimension only. So it
        # turns [B, S, 17] into [B, S, 256] and leaves B and S alone.
        # This is the neuron machine that has 17 numbers in and makes it
        # into a 256 output layer. 
        # Builds a weight matrix 265 x 17, a bias vector 256. This is
        # just the calculator to go from 17->256, and we're trying to
        # optimize thsoe weights and biases.
        return nn.Linear(SEQUENCE_FEATURES, EMBED_DIM)
    

    # _buildEncoder
    # Purpose: the attention stack — the part that lets each race look
    #          at every other race. This is the "transformer ×4" box.
    # Arguments: None.
    # Output: an nn.TransformerEncoder of N_LAYERS identical layers.
    def _buildEncoder(self) -> nn.TransformerEncoder:
        
        # One encoder layer = (multi-head self-attention) + (the
        # 256->1024->ReLU->256 feedforward block), each wrapped in a
        # residual-add + LayerNorm. PyTorch builds all of that from
        # these arguments — we don't hand-write attention.
        layer = nn.TransformerEncoderLayer(
            d_model=EMBED_DIM,        # width attention operates in (256)
            nhead=N_HEADS,            # 8 parallel attention heads
            dim_feedforward=FF_DIM,   # the 1024 expansion width
            dropout=DROPOUT,
            activation="relu",        # the nonlinearity inside the FFN
            batch_first=True,         # our tensors are [B, S, D], not [S, B, D]
        )

        # Stack N_LAYERS (4) identical-shape encoder layers — each holds
        # its OWN weights: the attention matrices W_Q/W_K/W_V/W_O (each
        # 256x256) and the feed-forward W1 (256->1024) / W2 (1024->256),
        # plus two LayerNorms. Data flows through them in order: layer 1's
        # output is layer 2's input, and so on. LayeNorms normalize the
        # data into mean 0 std 1.
        #
        # So W_Q is the Query matrix, W_K the Key matrix, W_V the Value matrix, W_O 
        # the Output matrix. The "W" is just "weights," 
        # the letter is which of the four it builds.
        #
        # Tying it back to what each does: W_Q builds a race's query (what it's looking for), 
        # W_K its key (what it advertises), W_V its value (what it contributes), 
        # and W_O mixes the heads' blended outputs back together at the end.
        #
        # Returns ONE object bundling all 4 layers' matrices (~3.1M weights)
        # with the rule for running data through them. It holds NO race
        # data yet — forward() is where data actually flows through it.
        return nn.TransformerEncoder(layer, num_layers=N_LAYERS)
    
    # _buildHead
    # Purpose: the "dimension down" step. Takes the 273-wide combined
    #          vector (256 pooled history + 17 context) down to 1 number.
    # Output:  an nn.Sequential of Linear -> ReLU -> Linear.
    def _buildHead(self) -> nn.Sequential:

        # nn.Sequential just chains layers: data flows top to bottom.
        # The ReLU between the two Linears is the activation — without
        # it, 273->64->1 would collapse into a single 273->1 Linear.
        # Each step summarizes the info down into lower dimensions.
        # ReLU is needed as an interim because otherwise it's
        # just two matrix multiplications downwards w/o any changing
        # of the data, which is the same as one matrix multiplication.
        # This is necessary because one step is a linear relationship,
        # which isn't true because some factors depend on others in
        # different ways (curves). An intermediate step helps show that.
        # Returns an object holding the list of the three steps (273->64->1)
        # and to run them sequentially.
        return nn.Sequential(
            # 256 pooled history + 21 context + 16 venue = 293.
            nn.Linear(EMBED_DIM + CONTEXT_FEATURES + VENUE_EMBED_DIM, 64),
            nn.ReLU(),                                    # the nonlinearity
            nn.Linear(64, 1),                             # 64 -> 1
        )
    
    # forward
    # Purpose: The full forward pass — turns a batch of race histories +
    #          target-race context into one predicted normalized_time each.
    #          PyTorch calls this automatically when you do model(seq, mask, ctx).
    #          Each line is exactly one box from the shape-flow diagram.
    # Arguments:
    #           sequences: [B, S, 17] padded race-history feature vectors
    #           masks:     [B, S]     bool — True = real race, False = padding
    #           context:   [B, 17]    target-race feature vector
    # Output:   [B] — one predicted (z-scored) normalized_time per example
    def forward(self, sequences: torch.Tensor, masks: torch.Tensor,
                context: torch.Tensor,
                venues: torch.Tensor = None) -> torch.Tensor:

        x        = self._project(sequences)         # [B,S,21]  -> [B,S,256]
        x        = self._encode(x, masks)           # [B,S,256] -> [B,S,256]
        pooled   = self._poolRealRaces(x, masks)    # [B,S,256] -> [B,256]

        # venues defaults to None so an existing caller still runs -- it then
        # gets the padding row, which is zeros, and behaves exactly as before.
        if venues is None:
            venues = torch.zeros(pooled.shape[0], dtype=torch.long,
                                 device=pooled.device)
        venue_vec = self.venue_embedding(venues)    #           -> [B,16]

        combined = torch.cat([pooled, context, venue_vec], dim=1)   # -> [B,292]
        return self._predict(combined)              # [B,292]   -> [B]
    
    # _project
    # Purpose: The "dimension up" box. Rewrites each race's 17 raw numbers as
    #          a 256-vector so attention has room to work.
    # Arguments: sequences: [B, S, 17]
    # Output:   [B, S, 256]
    def _project(self, sequences: torch.Tensor) -> torch.Tensor:

        # nn.Linear acts on the LAST dimension only, so B and S are untouched —
        # only the 17 becomes 256.
        return self.input_projection(sequences)
    
    # _encode
    # Purpose: The attention stack. Each race looks at every other race and is
    #          rewritten as a weighted blend of them.
    # Arguments:
    #           x:     [B, S, 256] projected races
    #           masks: [B, S]      bool — True = real race, False = padding
    # Output:   [B, S, 256] — same shape, but each race now informed by the others
    def _encode(self, x: torch.Tensor, masks: torch.Tensor) -> torch.Tensor:

        # PyTorch's mask convention is the OPPOSITE of ours: it wants
        # True = "ignore this position." Ours is True = real. So flip with ~
        # (bitwise NOT). This is what stops any race from attending to padding.
        padding_mask = ~masks  # [B, S], now True = padding = ignore
        return self.encoder(x, src_key_padding_mask=padding_mask)
    
    # Purpose: The "mean pool" box. Collapse the variable number of races into
    #          ONE 256-vector per athlete by averaging — but averaging ONLY the
    #          real races, never the zero padding rows.
    #          The encoder hands back [B, S, 256] — still one vector per race. 
    #          But you predict one number per athlete, so first you 
    #          need one vector per athlete. So athlete dimension stays (B), but
    #          S (races) dimension is collapsed.
    # Arguments:
    #           x:     [B, S, 256] encoded races
    #           masks: [B, S]      bool — True = real race, False = padding
    # Output:   [B, 256] — one summary vector per athlete
    def _poolRealRaces(self, x: torch.Tensor, masks: torch.Tensor) -> torch.Tensor:
        # unsqueeze(-1) adds a trailing size-1 axis: [B,S] -> [B,S,1], so the
        # mask lines up with x's [B,S,256] and broadcasts across all 256 numbers.
        # .float() turns True/False into 1.0/0.0 so we can multiply with it.
        #             races (rows)                mask     after unsqueeze(-1)
        # athlete 0:   [[10, 20],               [1, 1, 0]      [[1],
        #               [30, 40],                               [1],
        #               [ 0,  0]]   ← padding                   [0]]
        # athlete 1:   [[ 6,  6],               [1, 0, 0]      [[1],
        #               [ 0,  0],   ← padding                   [0],
        #               [ 0,  0]]   ← padding                   [0]]
        real_mask = masks.unsqueeze(-1).float()     # [B, S, 1]

        # x * real_mask zeroes every padding row (real rows pass through).
        # .sum(dim=1) adds up across the S races            -> [B, 256]
        # real_mask.sum(dim=1) counts how many real races   -> [B, 1]
        # Dividing = mean over REAL races only: padding adds nothing on top
        # and isn't counted on the bottom so it doesn't drag the average
        # towards 0.
        #
        # x * real_mask → athlete 0 keeps [[10,20],[30,40],[0,0]]; 
        # athlete 1 keeps [[6,6],[0,0],[0,0]].
        #
        # .sum(dim=1) (sum down the races axis) → [[40, 60], [6, 6]], 
        # shape [2, 2] = [B, features].
        #
        # counts = real_mask.sum(dim=1) → [[2], [1]] — 
        # athlete 0 had 2 real races, athlete 1 had 1.
        #
        # divide the sum over all races by counts → [[20, 30], [6, 6]].
        summed = (x * real_mask).sum(dim=1)         # [B, 256]
        counts = real_mask.sum(dim=1)               # [B, 1]  (always >= 1)
        return summed / counts                      # [B, 256]
    
    # _combineWithContext
    # Purpose: The "concat" box. Glue the athlete summary (256, from history)
    #          onto the target-race context (17) so the head sees both WHO the
    #          athlete is and WHAT race we're asking about.
    # Arguments:
    #           pooled:  [B, 256] athlete summary
    #           context: [B, 17]  target-race features
    # Output:   [B, 273]
    def _combineWithContext(self, pooled: torch.Tensor,
                            context: torch.Tensor) -> torch.Tensor:
        # torch.cat joins tensors end-to-end along one axis. dim=1 is the
        # feature axis, so 256 + 17 line up into one 273-wide vector.
        # (dim=0 is the batch B — we are NOT stacking more athletes.)
        return torch.cat([pooled, context], dim=1)
    
    # _predict
    # Purpose: The "head" box. Taper 273 -> 64 -> 1 down to a single number.
    # Arguments: combined: [B, 273]
    # Output:   [B] — one prediction per athlete
    def _predict(self, combined: torch.Tensor) -> torch.Tensor:
        # self.head outputs [B, 1] — one number, but still wrapped in a
        # length-1 axis. squeeze(-1) drops that trailing axis -> [B], matching
        # the targets tensor so the loss in train.py can compare them directly.
        return self.head(combined).squeeze(-1)