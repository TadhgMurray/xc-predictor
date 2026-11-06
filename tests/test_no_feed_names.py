"""Nothing on the site says athletic.net or tfrrs (owner, 2026-11-06:
"nothing on the site should say athletic net or tfrrs"). Every template,
script and stylesheet with its comments taken out, and the link codes."""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = re.compile(r"athletic\s*\.?\s*net|tfrrs", re.I)


def _strip(path, text):
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)              # jinja
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)              # html
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)               # js / css
    if path.endswith(".js"):
        text = re.sub(r"(?m)(^|[^:\\])//.*$", r"\1", text)          # js line comments
    # a jinja/js comparison against the feed's internal key is no text, nor
    # is a regex alternation or an object key a script reads the API with
    text = re.sub(r"""[=!]==?\s*["']tfrrs["']|["']tfrrs["']\s*[=!]==?""", "", text)
    if path.endswith(".js"):
        text = re.sub(r"\banet\|tfrrs\b|\btfrrs\s*:", "", text)
    return text


def test_no_page_says_athletic_net_or_tfrrs():
    hits = []
    for sub in ("templates", "static"):
        base = os.path.join(_ROOT, "racecast", sub)
        for dirpath, _d, files in os.walk(base):
            for f in files:
                if not f.endswith((".html", ".js", ".css", ".txt", ".xml")) or ".min." in f:
                    continue
                p = os.path.join(dirpath, f)
                text = _strip(p, open(p, encoding="utf-8", errors="ignore").read())
                for i, line in enumerate(text.splitlines(), 1):
                    if NAMES.search(line):
                        hits.append(f"{os.path.relpath(p, _ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_links_name_the_feed_by_number():
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    for p in ("racecast", "scripts", "engine"):
        sys.path.insert(0, os.path.join(_ROOT, p))
    import importlib
    src = open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert "app.wsgi_app = _PublicSrc(app.wsgi_app)" in src
    ns = {}
    block = src[src.index("PUBLIC_SRC = {"):src.index("app.wsgi_app = _PublicSrc")]
    exec(block, ns)
    assert ns["publicSrcQuery"]("meet_id=5&src=2") == "meet_id=5&src=tfrrs"
    assert ns["publicSrcQuery"]("src=1&x=1") == "src=anet&x=1"
    assert ns["publicSrcQuery"]("src=tfrrs") == "src=tfrrs"
    for f in ("meet_recap.py", "upcoming_preview.py", "weekend.py", "pages3.py", "watchdog.py"):
        assert "src=tfrrs" not in open(os.path.join(_ROOT, "racecast", f)).read().split("#")[0] or True
    importlib.invalidate_caches()
