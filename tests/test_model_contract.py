"""The model's training contract, checked without torch.

Same shape as test_train_runtime.py: no torch in the session that writes
this, so nothing here runs a forward pass. It holds the properties that
decide whether a paid run learns the right thing and whether the site can
invert what it learned.

    python tests/test_model_contract.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


TR = read("model", "train.py")
TF = read("model", "transformer.py")
PR = read("racecast", "predict.py")
PC = read("model", "predict_check.py")

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def body(src, name):
    i = src.index(f"def {name}(")
    j = src.find("\n    def ", i + 1)
    k = src.find("\ndef ", i + 1)
    ends = [x for x in (j, k) if x != -1]
    return src[i:min(ends)] if ends else src[i:]


# ---- 1. ONE DEFINITION OF THE TARGET, AND IT LIVES IN THE MODEL. ---- #
ok("def baselineSeconds(" in TF and "def targetZ(" in TF
   and "def predictSeconds(" in TF,
   "the model defines the baseline, the training target and the inversion")
ok("model.targetZ(sequences, masks, targets)" in body(TR, "_trainOneEpoch")
   and "model.targetZ(sequences, masks, targets)" in body(TR, "_validateOneEpoch"),
   "training and validation both take the target from the model, not from a "
   "hand-rolled z-score")
ok("targetZ" in TF and "* self.target_std" in body(TF, "predictSeconds")
   and "torch.exp(" in body(TF, "predictSeconds"),
   "predictSeconds inverts exactly what targetZ built: exp(z*std + mean) "
   "times the baseline")
ok("SEQ_NORM_TIME" in body(TF, "baselineSeconds")
   and "SEQ_NORM_TIME" in body(TR, "_chunkBaselines"),
   "both baseline readers use the named feature index, not a literal 0")

# ---- 2. THE INPUTS ARE STANDARDISED, AND THE STATS RIDE IN model.pt. ---- #
for buf in ("seq_mean", "seq_std", "ctx_mean", "ctx_std",
            "target_mean", "target_std", "fallback_seconds"):
    ok(f'register_buffer("{buf}"' in TF,
       f"{buf} is a registered buffer, so it is saved with the weights")
ok("- self.seq_mean) / self.seq_std" in TF
   and "- self.ctx_mean) / self.ctx_std" in TF,
   "forward standardises both inputs before the first Linear")
ok("model.setFeatureStats(" in TR and "model.setTargetStats(" in TR,
   "main stamps the training stats into the model")
ok("_trainSideMask(" in body(TR, "main"),
   "stats are computed on the TRAIN side only")

# ---- 3. THE TARGET RACE QUERIES THE HISTORY. ---- #
ok("nn.MultiheadAttention(" in TF and "self.context_query" in TF,
   "one attention query built from the context reads the encoded history")
ok("key_padding_mask=~masks" in body(TF, "_askHistory"),
   "and padding is masked out of that query")
ok("norm_first=True" in TF, "pre-norm encoder layers")

# ---- 4. OPTIMISER HYGIENE. ---- #
ok("torch.optim.AdamW(" in TR and "weight_decay=WEIGHT_DECAY" in TR,
   "AdamW with decoupled weight decay")
ok("clip_grad_norm_(model.parameters(), GRAD_CLIP)" in body(TR, "_trainOneEpoch"),
   "gradients are clipped between backward and step")
ok("scheduler.step()" in body(TR, "_trainOneEpoch"),
   "the LR schedule advances once per optimizer step")
ok("nn.GaussianNLLLoss(" in TR and "model.forwardDist(" in body(TR, "_trainOneEpoch"),
   "Gaussian NLL with the learned variance, not MSE")
ok("def forwardDist(" in TF and "def predictInterval(" in TF
   and "nn.Linear(64, 2)" in TF, "the head predicts a mean and a log-variance")
ok("clamp(min=LOGVAR_MIN, max=LOGVAR_MAX)" in body(TF, "forwardDist"),
   "the log-variance is clamped")
ok("inside_1s" in body(TR, "_validateOneEpoch") and "sigma_pct" in body(TR, "main"),
   "calibration is printed every epoch")
ok("model.predictInterval(" in PR and '"sigma_pct"' in PR,
   "predict.py returns the band")
ok('"scheduler": (scheduler.state_dict()' in body(TR, "_saveCheckpoint")
   and 'scheduler.load_state_dict(ck["scheduler"])' in body(TR, "_loadCheckpoint"),
   "the schedule resumes with the run")
for c in ("VAR_EPS", "WEIGHT_DECAY", "WARMUP_STEPS", "LR_FLOOR_FRAC",
          "GRAD_CLIP", "STATS_CHUNKS"):
    ok(re.search(rf"^{c} = ", TR, re.M) is not None, f"{c} is a named constant")

# ---- 5. THE BASELINE IS PRINTED BESIDE THE MODEL. ---- #
ok("last-race" in body(TR, "main") and "pct_base" in body(TR, "main"),
   "every epoch prints the last-race error beside the model's")

# ---- 6. THE SITE INVERTS WITH THE MODEL, AND READS EITHER KEY. ---- #
ok("predictInterval" in PR,
   "predict.py inverts through the model for a log-ratio model")
ok('stats.get("mean", stats.get("target_mean"))' in PR,
   "predict.py reads either spelling of the stats keys")
ok('"mean": float(stats["mean"])' in body(TR, "saveTargetStats")
   and '"target_mean": float(stats["mean"])' in body(TR, "saveTargetStats"),
   "train.py writes both spellings")
ok("predictInterval" in PC, "predict_check inverts with the model too")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  one target definition, owned by the model ......... OK")
print("  inputs standardised, stats saved with the weights .. OK")
print("  the target race queries the history ................ OK")
print("  AdamW, clipping, warmup/cosine, NLL+variance, resumable OK")
print("  last-race baseline printed every epoch ............. OK")
print("  the site inverts with the model .................... OK")
print("\nall model-contract checks passed")


def test_a_corrupt_time_cannot_end_a_run():
    """★ THE FIRST FULL-CORPUS RUN WENT NaN IN ITS SECOND EPOCH (2026-09-16).

    The loss is Gaussian NLL on the z-scored log ratio, so it is QUADRATIC
    in z, and the corpus contains scraped times that are not times -- a 1s
    finish, a 14-hour one, a zero. At the fitted std of 0.10 a 1s time
    against a 1000s baseline is z = -69, one example costs millions at the
    variance floor, and the gradient it asks for swamps the clipped step so
    completely that the whole update points wherever that row wanted.

    ⚠ A 200-CHUNK SAMPLE DID NOT CONTAIN ONE, which is why calibration was
      clean and the real run was not. This is the guard, not the sample.
    """
    import torch
    from transformer import XCPredictor, TARGET_Z_CLAMP, SEQ_NORM_TIME

    m = XCPredictor(n_venues=8)
    m.setTargetStats(-0.0050, 0.1003, 900.0)      # the real corpus's numbers

    seq = torch.zeros(4, 3, 21)
    seq[:, :, SEQ_NORM_TIME] = 1000.0
    masks = torch.ones(4, 3, dtype=torch.bool)
    targets = torch.tensor([1010.0, 1.0, 50000.0, 0.0])

    z = m.targetZ(seq, masks, targets)
    assert torch.isfinite(z).all(), z
    assert z.abs().max() <= TARGET_Z_CLAMP + 1e-6, z

    # ! AND THE ORDINARY TARGET IS UNTOUCHED. A clamp that moved real races
    #   would be trading one silent error for another; 8 sigma is a race
    #   2.2x the athlete's last, which is the scraper talking, not a runner.
    raw = (m.logRatio(seq, masks, targets) - m.target_mean) / m.target_std
    assert torch.allclose(raw[0], z[0]), (raw[0], z[0])
    assert abs(float(raw[1])) > 60, "the 1s time should be far outside"


def test_the_clamp_lives_on_the_model_so_inference_shares_it():
    """A model trained on clamped targets predicts in clamped units, so the
    clamp cannot live in the training loop -- it has to be the same code
    path racecast/predict.py reaches through."""
    import inspect
    from transformer import XCPredictor
    src = inspect.getsource(XCPredictor.targetZ)
    assert "clamp" in src, src
