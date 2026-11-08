"""Every template is compiled once in the gunicorn master and shared by the
workers (app.precompileTemplates, called from deploy/gunicorn.conf.py's
when_ready before gc.freeze and the fork). It must change nothing a page
shows: a precompiled template renders the same bytes as one compiled fresh,
the cache holds every template (an evicted one would be compiled again),
and the master compiles them before the heap is frozen.

    python -m pytest -q tests/test_template_precompile.py
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine"):
    _p = os.path.join(_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

flask = pytest.importorskip("flask")
jinja2 = pytest.importorskip("jinja2")


@pytest.fixture(scope="module")
def A():
    import app
    return app


def test_every_template_loads_and_the_cache_holds_them(A):
    names = A.app.jinja_env.list_templates()
    assert len(names) > 50
    assert A.precompileTemplates() == len(names)
    cache = A.app.jinja_env.cache
    assert cache.capacity >= len(names) + 50, "raise cache_size: templates would be evicted"
    for n in names:
        assert A.app.jinja_env.get_template(n) is A.app.jinja_env.get_template(n)


def test_a_precompiled_template_renders_what_a_fresh_compile_does(A):
    A.precompileTemplates()
    fresh = A.app.jinja_env.overlay(cache_size=0)
    names = A.app.jinja_env.list_templates()
    same = 0
    with A.app.test_request_context("/"):
        for env in (A.app.jinja_env, fresh):
            env.undefined = jinja2.ChainableUndefined
        try:
            for n in names:
                try:
                    a = A.app.jinja_env.get_template(n).render()
                except Exception as exc:                # noqa: BLE001
                    a = ("raised", type(exc).__name__)
                try:
                    b = fresh.get_template(n).render()
                except Exception as exc:                # noqa: BLE001
                    b = ("raised", type(exc).__name__)
                assert a == b, n
                same += isinstance(a, str)
        finally:
            A.app.jinja_env.undefined = jinja2.Undefined
    assert same > 40


def test_the_master_compiles_them_before_freezing():
    src = open(os.path.join(_ROOT, "deploy", "gunicorn.conf.py"), encoding="utf-8").read()
    body = src[src.index("def when_ready"):src.index("def _startWarmer")]
    assert "precompileTemplates()" in body
    assert body.index("precompileTemplates()") < body.index("gc.freeze()")
    assert body.index("precompileTemplates()") < body.index("database.closePool()")
