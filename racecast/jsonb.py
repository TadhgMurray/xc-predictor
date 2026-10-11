"""jsonb.py -- a value bound for a jsonb column, the way this database takes it.

    from jsonb import J
    cur.execute("INSERT ... VALUES (%s)", (J(payload),))

★ UTF-8, NOT \\u ESCAPES. The server encoding is SQL_ASCII (database.py,
  _Utf8Json, 2026-09-18). psycopg2's Json writes ensure_ascii=True, and jsonb
  REJECTS a \\uXXXX above ASCII under SQL_ASCII -- "unsupported Unicode escape
  sequence" -- so one athlete named Ordoñez or one school called St. Mary's
  with a curly apostrophe failed the whole write (2026-10-11: 23 meet
  forecasts). The raw bytes go through and round-trip.
! AND NO NUL: a school\\x00state key is refused by jsonb outright.
"""
import json

import psycopg2.extras


def clean(obj):
    """The same shape with every NUL removed from strings and keys."""
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, tuple):
        return [clean(v) for v in obj]
    if isinstance(obj, dict):
        return {clean(k) if isinstance(k, str) else k: clean(v) for k, v in obj.items()}
    return obj


def dumps(obj):
    return json.dumps(obj, default=str, ensure_ascii=False)


def J(obj):
    # psycopg2.extras.Json looked up at call time, its dumps set on the
    # instance: a test that stands Json in with a recorder still records
    j = psycopg2.extras.Json(clean(obj))
    try:
        j.dumps = dumps
    except AttributeError:
        pass
    return j
