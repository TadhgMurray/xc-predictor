# Project: xc-predictor / tests
# File:    test_jsonb_encoding.py
# Purpose: an accented name does not roll back a whole meet's save.
#
# ★ THE SYMPTOM (owner, 2026-09-18): "[!] Save failed for TF meet 629415:
#   unsupported Unicode escape sequence ... Unicode escape value could not be
#   translated to the server's encoding SQL_ASCII", on an athlete named
#   Sadé. The meet's entire save -- every athlete, every result -- was rolled
#   back because one name had an accent in it.
#
# ⚠ THE CAUSE IS A DEFAULT. psycopg2.extras.Json calls json.dumps with
#   ensure_ascii=True, which escapes every non-ASCII character as \uXXXX, and
#   jsonb REFUSES a \uXXXX above ASCII when the server encoding is SQL_ASCII
#   -- there is no encoding to translate the codepoint into. TEXT columns
#   were never affected, because only jsonb parses \u escapes.
import io
import os
import re
import ast
import json
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every module that writes a jsonb column.
_WRITERS = (
    "scripts/database.py",
    "tfrrs/driver/run_tfrrs.py",
    "tfrrs/scraper/save_tfrrs.py",
    "racecast/build_course_boards.py",
)


def _read(rel):
    with io.open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class NobodyUsesTheRawAdapter(unittest.TestCase):

    def test_no_module_calls_psycopg2_extras_json(self):
        for rel in _WRITERS:
            with self.subTest(module=rel):
                self.assertNotIn("psycopg2.extras.Json(", _read(rel),
                                 "use _Utf8Json: extras.Json escapes "
                                 "non-ASCII and SQL_ASCII rejects it")

    def test_they_all_use_the_shared_one(self):
        for rel in _WRITERS:
            with self.subTest(module=rel):
                self.assertIn("_Utf8Json(", _read(rel))

    # ! ONE DEFINITION. A second copy is a second default to get wrong.
    def test_it_is_defined_once_and_imported_elsewhere(self):
        defs = [rel for rel in _WRITERS
                if "class _Utf8Json(" in _read(rel)]
        self.assertEqual(defs, ["scripts/database.py"])
        for rel in _WRITERS[1:]:
            self.assertIn("_Utf8Json", _read(rel).split("\n\n")[0]
                          + _read(rel))


class TheAdapterSendsTheCharacter(unittest.TestCase):
    """What the class does, without needing psycopg2 installed."""

    def setUp(self):
        src = _read("scripts/database.py")
        i = src.index("class _Utf8Json(")
        self.body = src[i:src.index("\ndef ", i)]

    def test_it_turns_ensure_ascii_off(self):
        self.assertIn("ensure_ascii=False", self.body)

    def test_it_overrides_dumps(self):
        self.assertIn("def dumps(self", self.body)

    # The behaviour the override buys, asserted on json itself so this test
    # fails if the standard library ever changes under us.
    def test_escaped_form_is_what_postgres_rejected(self):
        obj = {"Name": "Sadé"}
        self.assertIn("\\u00e9", json.dumps(obj))
        self.assertNotIn("\\u00e9", json.dumps(obj, ensure_ascii=False))

    def test_the_name_survives_the_round_trip(self):
        obj = {"Name": "Sadé", "Team": "Saint-André"}
        self.assertEqual(
            json.loads(json.dumps(obj, ensure_ascii=False)), obj)


if __name__ == "__main__":
    unittest.main(verbosity=2)
