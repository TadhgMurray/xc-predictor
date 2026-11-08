# Project: xc-predictor / tests
# File:    test_replica_scripts.py
# Purpose: the second-server scripts (deploy/replica, docs/REPLICA-PLAN.md)
#          parse, stop on the first error, never open Postgres to the
#          internet, and keep the standby's replay delay above the site's
#          statement timeout. No server needed.
import os
import re
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "deploy", "replica")


def _scripts():
    return sorted(os.path.join(_DIR, f) for f in os.listdir(_DIR) if f.endswith(".sh"))


def _read(p):
    return open(p, encoding="utf-8").read()


def test_every_script_parses_and_stops_on_errors():
    assert len(_scripts()) >= 10
    for p in _scripts():
        r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
        assert r.returncode == 0, f"{p}: {r.stderr}"
        if not p.endswith("common.sh"):
            assert "set -euo pipefail" in _read(p), p
            assert '. "$(dirname "$0")/common.sh"' in _read(p), p


def test_postgres_is_reachable_only_through_the_tunnel():
    for p in _scripts():
        s = _read(p)
        for m in re.finditer(r"ufw allow [^\n]*", s):
            line = m.group(0)
            if "5432" in line or "RC_PG_PORT" in line:
                assert "in on" in line, f"{p}: {line}"
    conf = _read(os.path.join(_DIR, "common.sh"))
    assert "listen_addresses = 'localhost,${RC_SELF_WG}'" in conf
    assert "listen_addresses = '*'" not in conf


def test_replay_waits_out_a_site_query_rather_than_killing_it():
    """A replayed swap conflicts with the site's queries on the standby. The
    standby waits up to max_standby_streaming_delay before cancelling them;
    the site's own statement_timeout is shorter, so no site query is cut."""
    conf = _read(os.path.join(_DIR, "common.sh"))
    delay = int(re.search(r"max_standby_streaming_delay = (\d+)s", conf).group(1))
    unit = _read(os.path.join(_ROOT, "deploy", "server_setup.sh"))
    timeout_ms = int(re.search(r"XCP_DB_STATEMENT_TIMEOUT_MS=(\d+)", unit).group(1))
    assert delay * 1000 > timeout_ms
    assert "hot_standby_feedback = on" in conf


def test_no_password_on_a_command_line():
    """ps shows every user the arguments of every process."""
    for p in _scripts():
        s = _read(p)
        assert not re.search(r"-v\s+pw=", s), p
        assert not re.search(r"PASSWORD '\$\{?RC_REPL_PASSWORD", s), p


def test_the_site_role_file_is_read_after_the_main_env_file():
    """systemd lets EnvironmentFile= beat Environment=, so the role must be a
    second EnvironmentFile (read later), not Environment= lines."""
    s = _read(os.path.join(_DIR, "site_role.sh"))
    assert "EnvironmentFile=-%s" in s
    assert "XCP_DB_READ_DSN=host=127.0.0.1" in s
