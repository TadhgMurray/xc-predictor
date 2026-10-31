"""The athletic.net launcher says who it is (2026-10-31): racecast's token in
the user agent, no webdriver mask, no proxy list, no stealth plugin."""
import ast
import os

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "launcher.py")


def _source():
    with open(SRC, encoding="utf-8") as f:
        return f.read()


def test_user_agent_carries_racecast_token():
    tree = ast.parse(_source())
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "AGENT_TOKEN" for t in node.targets):
            exec(compile(ast.Module([node], []), SRC, "exec"), ns)
        if isinstance(node, ast.FunctionDef) and node.name == "honestUserAgent":
            exec(compile(ast.Module([node], []), SRC, "exec"), ns)
    ua = ns["honestUserAgent"]("141.0.7390.54", system="Linux")
    assert "X11; Linux x86_64" in ua and "Chrome/141.0.7390.54" in ua
    assert ua.endswith("racecast-results/1.0 (+https://racecast.co/about)")


def test_no_disguise_left():
    s = _source()
    assert "playwright_stealth" not in s
    assert "navigator, 'webdriver'" not in s
    assert "PROXIES" not in s
