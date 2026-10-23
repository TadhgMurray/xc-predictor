# ★ A database that is down or starting up is the site's 503, not a 500
#   (owner, 2026-10-23: 37 "the database system is starting up" reports).
import os
import sys

import psycopg2
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import database as D                                              # noqa: E402


def test_a_pool_that_cannot_open_is_an_operational_error_and_a_runtime_error(monkeypatch):
    def refuse(*a, **k):
        raise psycopg2.OperationalError("FATAL:  the database system is starting up")
    monkeypatch.setattr(D, "_pool", None)
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", refuse)
    with pytest.raises(psycopg2.OperationalError) as e:
        D.initPool()
    assert isinstance(e.value, RuntimeError) and "starting up" in str(e.value)


def test_the_site_answers_it_with_the_503_page():
    src = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert "@app.errorhandler(psycopg2.OperationalError)\ndef _db_blip" in src.replace(
        "@app.errorhandler(psycopg2.InterfaceError)\n", "")
