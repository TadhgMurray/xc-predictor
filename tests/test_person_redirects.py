"""person_redirects.follow: chains are followed, loops stop, none is None."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import person_redirects as P  # noqa: E402


class Cur:
    def __init__(self, table):
        self.t, self.row = table, None

    def execute(self, sql, params):
        n = self.t.get(params[0])
        self.row = (n,) if n is not None else None

    def fetchone(self):
        return self.row


def test_chain_is_followed_to_the_end():
    assert P.follow(Cur({1: 2, 2: 3}), 1) == 3


def test_a_loop_stops():
    assert P.follow(Cur({1: 2, 2: 1}), 1) == 2


def test_no_redirect_is_none():
    assert P.follow(Cur({}), 7) is None
