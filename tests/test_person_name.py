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


def test_nicknames_are_one_first_name():
    """2026-11-02: Christopher Hovet and Chris Hovet, Tufts, never joined."""
    import person_name as P
    assert P.sameFirstName("christopher", "chris")
    assert P.sameFirstName("steve", "stephen") and P.sameFirstName("steve", "steven")
    assert not P.sameFirstName("steven", "stephen")
    assert not P.sameFirstName("liam", "william")
    assert ("chris", "christopher") in P.nicknamePairs()


def test_the_ascii_fast_path_is_the_full_translate():
    """2026-11-19: _keySql calls translate() only on a name with a non-ASCII
    character. The key must be the plain translate() key on every name --
    ASCII, folded letters, letters outside the map, combining marks, edge
    code points -- in the UTF8 and in the SQL_ASCII (production) encoding."""
    import random
    dsn = os.environ.get("XCP_TEST_PG_DSN") or os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TEST_PG_DSN to a scratch Postgres")
    import psycopg2

    def plain(expr, keep_suffix):          # _keySql before 2026-11-19
        folded = f"lower(translate({expr}, '{P.FOLD_FROM}', '{P.FOLD_TO}'))"
        cleaned = (f"btrim(regexp_replace(regexp_replace(replace({folded}, '-', ' '), "
                   f"'[^a-z ]+', '', 'g'), '\\s+', ' ', 'g'))")
        return cleaned if keep_suffix else f"regexp_replace({cleaned}, ' ({'|'.join(P.SUFFIXES)})$', '')"
    rnd = random.Random(11)
    alpha = list("abcXYZ -',.\t\x7f\x01") + list(P.FOLD_FROM) + ["中", "й", "́", "€", "ǅ", "\x80"]
    names = [None, "", " ", "Jake Smith", "Smith Jr., John"] + [f"{a} {b}" for a, b in CASES] + TEXT
    names += ["".join(rnd.choice(alpha) for _ in range(rnd.randint(0, 12))) for _ in range(3000)]
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    try:
        for db, enc in (("pn_fast_utf8", "UTF8"), ("pn_fast_ascii", "SQL_ASCII")):
            admin.cursor().execute(f"DROP DATABASE IF EXISTS {db}")
            admin.cursor().execute(f"CREATE DATABASE {db} ENCODING '{enc}' TEMPLATE template0 "
                                   f"LC_COLLATE 'C' LC_CTYPE 'C'")
            conn = psycopg2.connect(dsn, dbname=db)
            conn.set_client_encoding("UTF8")
            try:
                cur = conn.cursor()
                cur.execute("CREATE TEMP TABLE n (f text, l text)")
                cur.executemany("INSERT INTO n VALUES (%s, %s)",
                                [(x, names[-i - 1]) for i, x in enumerate(names)])
                two = "concat_ws(' ', NULLIF(btrim(x.f), ''), NULLIF(btrim(x.l), ''))"
                for ks in (False, True):
                    cur.execute(f"SELECT count(*) FILTER (WHERE ({P.personKeySql('x.f', 'x.l', ks)}) "
                                f"IS DISTINCT FROM ({plain(two, ks)})), "
                                f"count(*) FILTER (WHERE x.f ~ '[^\\x01-\\x7f]') FROM n x")
                    diff, wide = cur.fetchone()
                    assert diff == 0 and wide > 1000, (enc, ks, diff, wide)
            finally:
                conn.close()
                admin.cursor().execute(f"DROP DATABASE IF EXISTS {db}")
    finally:
        admin.close()
