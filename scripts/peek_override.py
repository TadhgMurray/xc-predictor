# scripts/peek_override.py
# Purpose : print what _DISTANCE_OVERRIDES_XC actually holds for given (meet, div)
#           keys, next to what the tfrrs blob says. If they MATCH, the override
#           is a no-op and 11.1 explains the 176.
import sys
sys.path.insert(0, "engine")
from corrections import _DISTANCE_OVERRIDES_XC

# The six worst from verify_c18.log's [STOP] list.
_KEYS = [(4876, 0), (17453, 0), (10064, 0), (9351, 0), (5300, 0), (8025, 0)]

def _peek(key):
    # Purpose   : one key -> (key, override value or the string 'ABSENT').
    # Note      : .get() with a default distinguishes "key missing" from
    #             "key present, value None" -- two different diagnoses.
    return (key, _DISTANCE_OVERRIDES_XC.get(key, "ABSENT"))

def main():
    print(f"{'key':<14}  override")
    for key in _KEYS:
        k, v = _peek(key)
        print(f"{str(k):<14}  {v}")

if __name__ == "__main__":
    main()