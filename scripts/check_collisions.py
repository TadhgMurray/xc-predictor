import glob
import importlib.util
from importlib.machinery import SourceFileLoader


def load(path, name):
    # SourceFileLoader is explicit about "this is Python source", which
    # spec_from_file_location cannot infer from a .bak-<timestamp> suffix.
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


new = load("engine/corrections.py", "_new")._RESULT_OVERRIDE_XC
bak = sorted(glob.glob("engine/corrections.py.bak-*"))[-1]
old = load(bak, "_old")._RESULT_OVERRIDE_XC
ours = load("scripts/result_override_gender_xc.py",
            "_g")._RESULT_OVERRIDE_ADDITIONS

collided = [r for r in ours if r in old]
changed = {r: (old[r], ours[r]) for r in collided if old[r] != ours[r]}

print(f"backup: {bak}")
print(f"{len(ours)} pins | {len(ours) - len(collided)} new "
      f"| {len(collided)} collided | {len(changed)} CHANGED A VALUE")
for r, (o, n) in list(changed.items())[:20]:
    print(f"  {r}: {o} -> {n}")