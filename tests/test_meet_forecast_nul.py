"""meet_forecast.saveRace: no NUL reaches jsonb (2026-10-11: 23 forecasts
failed 'unsupported Unicode escape sequence' on a school\\x00state key)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: F401,E402


class _Cur:
    def __init__(self):
        self.params = None

    def execute(self, sql, params=None):
        self.params = params
        self.rowcount = 1

    def fetchone(self):
        return (1,)


def test_team_keys_with_a_nul_are_stored_clean():
    import json
    import meet_forecast as MF
    cur = _Cur()
    pred = {"runners": [{"school": "Williams\x00MA", "name": "A"}],
            "teams": [{"team": "Williams\x00MA", "score": 30}], "n_field": 1}
    MF.saveRace(cur, {"source": "anet", "meet_id": 1, "date": "2099-10-10", "name": "M"},
                {"div_id": 2, "label": "V"}, pred, "2026-10-11")
    for k in ("runners", "teams"):
        dumped = json.dumps(cur.params[k].adapted)
        assert "\\u0000" not in dumped
