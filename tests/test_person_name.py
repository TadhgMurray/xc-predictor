"""scripts/person_name.py: one name key, Python and SQL the same rule
(2026-10-24 survey: the linkers deleted accented letters, never knew Jr)."""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import person_name as P                                          # noqa: E402

CASES = [("José", "Peña"), ("Ánh", "Lê"), ("Zoë", "Ødegård"), ("Mary-Kate", "O'Brien"),
         ("John", "Smith Jr"), ("JOSÉ", "PEÑA"), ("Đỗ", "Văn"), ("  Jake ", "SMITH  "),
         ("Łukasz", "Żmuda"), ("Søren", "Kierkegaard III"), ("Tadhg", "Murray")]
TEXT = ["Peña, José", "Smith Jr., John", "Jake Smith", "O'Neil, Mary-Kate", "Lê, Ánh"]


def test_the_hard_names():
    assert P.personKey("Peña, José") == P.personKey("José Peña") == P.personKey("Jose Pena") == "jose pena"
    assert P.personKey("Ánh Lê") == "anh le"
    assert P.personKey("Zoë Ødegård") == "zoe odegard"
    assert P.personKey("Mary-Kate O'Brien") == "mary kate obrien"
    assert P.personKey("Smith Jr., John") == P.personKey("John Smith Jr.") == "john smith"
    assert P.personKey("Đỗ Văn") == "do van"
    assert P.personKey("Lê") is None                      # one word is not a name
    assert P.personKeyParts("Søren", "Kierkegaard III") == "soren kierkegaard"
    assert len(P.FOLD_FROM) == len(P.FOLD_TO) and "'" not in P.FOLD_FROM


def test_the_sql_is_the_python():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    try:
        for first, last in CASES:
            cur.execute(f"SELECT {P.personKeySql('x.f', 'x.l')} FROM (SELECT %s::text AS f, %s::text AS l) x",
                        (first, last))
            assert cur.fetchone()[0] == P.personKeyParts(first, last), (first, last)
        for name in TEXT:
            cur.execute(f"SELECT {P.personKeyTextSql('x.n')} FROM (SELECT %s::text AS n) x", (name,))
            assert cur.fetchone()[0] == P.personKey(name), name
    finally:
        conn.rollback()
        conn.close()


def test_the_linkers_fold_accents_and_keep_suffixes():
    """the linkers' key (link_freshmen.normName and its SQL twins), 2026-10-26:
    José Peña is Jose Pena; John Smith Jr is NOT John Smith (a father and a son)"""
    sys.path.insert(0, os.path.join(_ROOT, "engine"))
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    import link_freshmen as LF
    assert LF.normName("José Peña") == LF.normName("Jose Pena") == "jose pena"
    assert LF.normName("Peña, José") == "jose pena"
    assert LF.normName("John Smith Jr") == "john smith jr" != LF.normName("John Smith")
    assert LF.normName("Madonna") is None
