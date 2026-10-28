"""scripts/diag_name_key.py: the dry run of the one name key, on a scratch
Postgres (XCP_TWIN_TEST_DSN). Each pair kind planted once."""
import contextlib
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

_FIXTURE = """
CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, first_name text,
                            last_name text, school text, gender text);
CREATE TEMP TABLE results (result_id bigint, person_id bigint, meet_id bigint,
                           div_id bigint, source text, date text, grade text);
INSERT INTO athletes VALUES
 (1,1,'José','Peña','Lincoln HS','M'), (2,2,'Jose','Pena','Lincoln HS','M'),
 (3,3,'John','Smith Jr','Lincoln HS','M'), (4,4,'John','Smith','Lincoln HS','M'),
 (5,5,'Zoë','Ng','Union HS','F'), (6,6,'Zoe','Ng','Union HS','F'), (7,7,'Zoé','Ng','Union HS','F'),
 (8,8,'Ánh','Lê','Union HS','F'), (9,9,'Anh','Le','Union HS','F'),
 (10,10,'René','Dupont','Union HS','M'), (11,11,'Rene','Dupont','Union HS','M'),
 (12,12,'José','Peña','Other HS','M');
INSERT INTO results VALUES (1,8,100,1,'anet','2025-09-10','10'), (2,9,100,1,'anet','2025-09-10','10'),
 (3,10,101,1,'anet','2025-09-12','9'), (4,11,102,1,'anet','2025-10-12','12');
"""


def test_each_kind_of_pair(monkeypatch, capsys):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.cursor().execute(_FIXTURE)
    db = types.ModuleType("database")

    @contextlib.contextmanager
    def getConn():
        yield conn
    db.getConn = getConn
    monkeypatch.setitem(sys.modules, "database", db)
    import diag_name_key as D
    monkeypatch.setattr(sys, "argv", ["diag_name_key.py"])
    D.main()
    out = capsys.readouterr().out
    conn.close()
    for kind, n in (("safe", 2), ("crowded", 2), ("overlap", 1), ("same race", 1)):
        assert f"    {kind:<10} {n:>8,}" in out, (kind, out)
    assert "'José Peña'" in out and "'John Smith Jr'" in out
