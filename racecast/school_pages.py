"""
school_pages.py -- a big school's page, precomputed (build_school_pages.py).

★ WHY (2026-11-12, the 72 h slow log): /school/:name was the costliest page
  left -- ~2,700 s in 72 h, p90 560 ms -- and what remained after the shared
  copies (school.sharedSchool) was the FIRST view of a school, mostly a
  crawler on a school nobody had opened lately, its rows read from disk.
  The pipeline now computes the history half of the page (header, year bar,
  current season, all-time best, top athletes) for the big programmes, and
  the page reads it by key.

★ EXACT, OR NOT SERVED. A row is the value of the page's own function
  (school.py), called with the page's own arguments, stored as tagged JSON
  that must come back identical (types, key order, a Capped list's flags)
  or is not stored. It is served only while
    - the boards it was read from are the ones in service (athlete_season's
      and ranking_results' oids, which a rebuild changes),
    - school.py / school_identity.py / capped.py are the code that built it
      (CODE), and
    - it is younger than MAX_AGE_S.
  Anything else -- no row, another build, other code, a missing table -- is
  a miss, and the page computes as it always did.
"""
import datetime
import decimal
import hashlib
import json
import os
import time

TABLE = "school_page"
MAX_AGE_S = float(os.environ.get("XCP_SCHOOL_PAGE_MAX_AGE_H") or 30) * 3600.0

_HERE = os.path.dirname(os.path.abspath(__file__))


def _codeHash():
    h = hashlib.sha1()
    for name in ("school.py", "school_identity.py", "capped.py", "school_pages.py"):
        try:
            with open(os.path.join(_HERE, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:16]


CODE = _codeHash()


def keyOf(fn_name, args, kw):
    """The row key of one call: the same identity sharedSchool shares under,
    without its stamps."""
    text = repr((fn_name, tuple(args), sorted(kw.items())))
    return hashlib.sha1(text.encode("utf-8")).hexdigest(), text


# ------------------------------------------------------------------ #
#  exact JSON
# ------------------------------------------------------------------ #
_TAG = "__sp__"


def _enc(v):
    from capped import Capped
    if v is None or type(v) in (bool, str, int):
        return v
    if type(v) is float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("a non-finite number")
        return v
    if type(v) is dict:
        if _TAG not in v and all(type(k) is str for k in v):
            return {k: _enc(x) for k, x in v.items()}
        return {_TAG: "dict", "v": [[_enc(k), _enc(x)] for k, x in v.items()]}
    if type(v) is list:
        return [_enc(x) for x in v]
    if type(v) is Capped:
        extra = {k: _enc(x) for k, x in v.__dict__.items()}
        return {_TAG: "capped", "v": [_enc(x) for x in v], "a": extra}
    if type(v) is tuple:
        return {_TAG: "tuple", "v": [_enc(x) for x in v]}
    if type(v) is datetime.datetime and v.tzinfo is None:
        return {_TAG: "datetime", "v": v.isoformat()}
    if type(v) is datetime.date:
        return {_TAG: "date", "v": v.isoformat()}
    if type(v) is decimal.Decimal:
        return {_TAG: "decimal", "v": str(v)}
    raise TypeError(f"cannot store a {type(v).__name__}")


def _dec(d):
    t = d.get(_TAG)
    if t is None or "v" not in d:
        return d
    v = d["v"]
    if t == "dict":
        return {k: x for k, x in v}
    if t == "capped":
        from capped import Capped
        out = Capped(v)
        out.__dict__.update(d.get("a") or {})
        return out
    if t == "tuple":
        return tuple(v)
    if t == "datetime":
        return datetime.datetime.fromisoformat(v)
    if t == "date":
        return datetime.date.fromisoformat(v)
    if t == "decimal":
        return decimal.Decimal(v)
    return d


def payloadFrom(text):
    return json.loads(text, object_hook=_dec)


def sameValue(a, b):
    """Equal AND of the same types, dict keys in the same order, a Capped's
    attributes too: what the page renders from `a` it renders from `b`."""
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return list(a) == list(b) and all(sameValue(a[k], b[k]) for k in a)
    if type(a) in (list, tuple) or isinstance(a, list):
        if getattr(a, "__dict__", {}) != getattr(b, "__dict__", {}):
            return False
        return len(a) == len(b) and all(sameValue(x, y) for x, y in zip(a, b))
    return a == b


def payloadText(value):
    """`value` as stored text; raises when it would not come back exactly."""
    text = json.dumps(_enc(value), ensure_ascii=True, allow_nan=False,
                      separators=(",", ":"))
    if not sameValue(payloadFrom(text), value):
        raise ValueError("the payload does not round-trip")
    return text


# ------------------------------------------------------------------ #
#  the page's read
# ------------------------------------------------------------------ #
_NO_TABLE = {"until": 0.0}


def storedValue(cur, fn_name, args, kw, oids, now=time.time):
    """The stored value of fn(cur, *args, **kw) when it may be served (see
    the module docstring), else None. One primary-key read under a
    savepoint; never raises."""
    t = now()
    if t < _NO_TABLE["until"]:
        return None
    key, _text = keyOf(fn_name, args, kw)
    try:
        with cur.connection.cursor() as c:
            c.execute("SAVEPOINT school_page")
            try:
                c.execute(f"""SELECT payload, oids, code,
                                     extract(epoch FROM built_at) AS at
                              FROM {TABLE} WHERE key = %s""", (key,))
                row = c.fetchone()
                c.execute("RELEASE SAVEPOINT school_page")
            except Exception as exc:                    # noqa: BLE001
                c.execute("ROLLBACK TO SAVEPOINT school_page")
                if getattr(exc, "pgcode", None) == "42P01":   # undefined_table
                    _NO_TABLE["until"] = t + 300.0
                return None
    except Exception:                                   # noqa: BLE001
        return None
    if not row:
        return None
    payload, row_oids, code, at = row
    if code != CODE or row_oids != oidText(oids) or at is None:
        return None
    if not (t - MAX_AGE_S <= float(at) <= t + 300.0):
        return None
    try:
        return payloadFrom(payload)
    except Exception:                                   # noqa: BLE001
        return None


def oidText(oids):
    return ",".join(str(o) for o in oids)
