"""The account page lists who you follow (owner, 2026-11-08: "make the
account pages list the follows"), and unfollowing there comes back there."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_the_account_page_lists_the_follows():
    t = read("racecast", "templates", "account.html")
    assert 'id="following"' in t and "{% for f in follows %}" in t
    assert 'name="back" value="account"' in t and "/account/me#alerts" in t
    a = read("racecast", "accounts.py")
    body = a[a.index("def account_page"):a.index("def _back")]
    assert "followsFor" in body and "follows=follows" in body


def test_unfollowing_from_the_account_page_returns_there():
    f = read("racecast", "follows.py")
    body = f[f.index("def account_follow_remove"):f.index("def account_alerts")]
    assert '(request.form.get("back") or "") == "account"' in body
    assert 'redirect("/account?' in body
