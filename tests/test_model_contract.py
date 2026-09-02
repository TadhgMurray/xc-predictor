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
ok("nn.HuberLoss(" in TR, "Huber loss, not MSE")
ok('"scheduler": (scheduler.state_dict()' in body(TR, "_saveCheckpoint")
   and 'scheduler.load_state_dict(ck["scheduler"])' in body(TR, "_loadCheckpoint"),
   "the schedule resumes with the run")
for c in ("HUBER_DELTA", "WEIGHT_DECAY", "WARMUP_STEPS", "LR_FLOOR_FRAC",
          "GRAD_CLIP", "STATS_CHUNKS"):
    ok(re.search(rf"^{c} = ", TR, re.M) is not None, f"{c} is a named constant")

# ---- 5. THE BASELINE IS PRINTED BESIDE THE MODEL. ---- #
ok("last-race" in body(TR, "main") and "pct_base" in body(TR, "main"),
   "every epoch prints the last-race error beside the model's")

# ---- 6. THE SITE INVERTS WITH THE MODEL, AND READS EITHER KEY. ---- #
ok("model.predictSeconds(seqs, masks, ctxs, vens)" in PR,
   "predict.py uses predictSeconds for a log-ratio model")
ok('stats.get("mean", stats.get("target_mean"))' in PR,
   "predict.py reads either spelling of the stats keys")
ok('"mean": float(stats["mean"])' in body(TR, "saveTargetStats")
   and '"target_mean": float(stats["mean"])' in body(TR, "saveTargetStats"),
   "train.py writes both spellings")
ok("predictSeconds" in PC, "predict_check inverts with the model too")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  one target definition, owned by the model ......... OK")
print("  inputs standardised, stats saved with the weights .. OK")
print("  the target race queries the history ................ OK")
print("  AdamW, clipping, warmup/cosine, Huber, resumable ... OK")
print("  last-race baseline printed every epoch ............. OK")
print("  the site inverts with the model .................... OK")
print("\nall model-contract checks passed")
