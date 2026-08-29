# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Configuration
# File Title: config.py
# Purpose: Database connection settings. Credentials come from the ENVIRONMENT
#          or from a gitignored local file, so this file is safe to ship to
#          any server.
#
# Resolution order, for EVERY field:
#   1. XCP_DB_* environment variable   -- how the SERVER is configured
#                                         (/etc/xc-predictor.env, read by the
#                                         systemd unit's EnvironmentFile)
#   2. scripts/config_local.py         -- gitignored; how a DEV MACHINE is
#   3. the shipped default             -- 127.0.0.1:5432/xc_predictor as
#                                         postgres, and NO password
#
# ★ ENV WINS OVER THE FILE, AND THE ORDER IS NOT ARBITRARY.
#   /etc/xc-predictor.env is production truth. A config_local.py that reached
#   the server by an over-broad scp or a stray commit must not be able to
#   silently repoint the live site at somebody's workstation.
#
# ★ AND EVERY FIELD NOW READS THE SAME WAY, WHICH IT DID NOT BEFORE.
#   config_local.py used to carry DB_PASSWORD *alone* -- host, port, name and
#   user came from env vars or the defaults and nothing else. So pointing a
#   local checkout at the server meant exporting XCP_DB_* by hand in every
#   shell, and forgetting one of them left you quietly on the local corpus
#   while believing otherwise. That is the whole reason this file changed.
#   See deploy/db_tunnel.ps1 for the other half.
#
# ⚠ THERE IS NO PASSWORD FALLBACK ANY MORE, AND ITS ABSENCE IS THE POINT.
#   The default that used to live here is in this file's git history, and the
#   server's password was rotated away from it at setup
#   (docs/HANDOFF-2026-08-27.md §3.1), so it could not work again in any
#   case. But while it existed, a misconfigured tool CONNECTED SOMEWHERE
#   instead of failing -- and with a tunnel on 5433 beside a local cluster on
#   5432, the two corpora differ by one digit. drop_old.py, wipe_overrides.py
#   and every --write in the tree take whatever connection they are handed
#   without asking which one it is, and none of those is undoable. A hard
#   stop is the cheap half of the guard; the banner below is the other half.

import os

# ! IMPORTED ONCE, HERE, RATHER THAN INSIDE EACH LOOKUP. A missing
#   config_local.py is the normal case on the server and must cost nothing;
#   an ImportError raised from INSIDE the file (a typo in it, say) would
#   otherwise be swallowed five separate times and look like "no local
#   config" instead of the syntax error it is.
try:
    import config_local as _local
except ModuleNotFoundError:
    _local = None

# Where each field actually came from, for the banner. Not part of the API.
_SOURCE = {}


# _setting
# Purpose: resolve one connection field through the order in the header.
# Arguments: name -- the bare field name ('HOST'), read as XCP_DB_<name> from
#                    the environment and DB_<name> from config_local;
#            default -- the shipped value, or None for "no default";
#            cast -- applied to whatever wins, so a port from either source
#                    arrives as an int.
# Output: the resolved value, or None when nothing supplied one.
def _setting(name, default=None, cast=str):
    value = os.environ.get(f"XCP_DB_{name}")
    source = "env"
    if value is None and _local is not None:
        value = getattr(_local, f"DB_{name}", None)
        source = "config_local"
    if value is None:
        value, source = default, "default"
    _SOURCE[name] = source
    return None if value is None else cast(value)


PG_CONFIG = {
    "host":     _setting("HOST", "127.0.0.1"),
    "port":     _setting("PORT", 5432, int),
    "dbname":   _setting("NAME", "xc_predictor"),
    "user":     _setting("USER", "postgres"),
    "password": _setting("PASSWORD"),
    # ★ ASKED FOR EXPLICITLY, BECAUSE THE DEFAULT IS THE DATABASE'S OWN
    #   ENCODING AND THAT IS NOT ALWAYS UTF8.
    #
    #   The server's cluster was built by server_setup.sh's bare `createdb`,
    #   which inherits whatever initdb chose -- and initdb on a bare Ubuntu
    #   box with no LANG set chooses SQL_ASCII. psycopg2 then maps SQL_ASCII
    #   to Python's `ascii` codec, so the FIRST accented character in the
    #   corpus raises
    #
    #       UnicodeDecodeError: 'ascii' codec can't decode byte 0xc2
    #
    #   out of cur.fetchall() -- 0xc2 being the lead byte of a UTF-8 pair.
    #   The bytes are fine; only the decoder was wrong.
    #
    # ! AND SAYING UTF8 IS SAFE AGAINST EITHER SERVER ENCODING. Against a
    #   UTF8 database this is what already happens and the line is a no-op.
    #   Against a SQL_ASCII one, SQL_ASCII performs no conversion in either
    #   direction, so the stored UTF-8 bytes arrive intact and are now
    #   decoded as what they actually are.
    #
    # ⚠ THIS IS A DECODER FIX, NOT AN ENCODING FIX. A SQL_ASCII database
    #   still cannot validate, collate or upper-case non-ASCII text
    #   correctly. The real repair is a dump and restore into a UTF8
    #   database, and server_setup.sh should be creating one:
    #       createdb --encoding=UTF8 --locale=C.UTF-8 --template=template0
    "client_encoding": "UTF8",
}

if not PG_CONFIG["password"]:
    raise RuntimeError(
        "no database password.\n"
        "  On a server:      set XCP_DB_PASSWORD (see /etc/xc-predictor.env).\n"
        "  On this machine:  create scripts/config_local.py (gitignored) with\n"
        "                        DB_PASSWORD = \"...\"\n"
        "                    and, to reach the server through the tunnel,\n"
        "                        DB_HOST = \"127.0.0.1\"\n"
        "                        DB_PORT = 5433\n"
        "  There is deliberately no fallback -- see this file's header.")


# ★ THE BANNER IS A GUARD, NOT A COURTESY. Two corpora are now reachable from
#   one checkout and they differ by a single digit in the port. Every
#   destructive tool in the tree takes its connection silently, so the only
#   moment anything can tell you which database you are about to rewrite is
#   this line. It names the host it resolved AND where that came from,
#   because "127.0.0.1" alone cannot distinguish the local cluster from the
#   far end of an ssh tunnel.
#
# ! NEVER THE PASSWORD. This prints into terminals, CI logs and journalctl.
#
# ! XCP_DB_QUIET SILENCES IT for a caller that parses stdout. gunicorn boots
#   eight workers and each prints once; that is eight lines in the journal at
#   restart, which is a fair price for knowing what the site is serving from.
if not os.environ.get("XCP_DB_QUIET"):
    print(f"[config] {PG_CONFIG['user']}@{PG_CONFIG['host']}:"
          f"{PG_CONFIG['port']}/{PG_CONFIG['dbname']}"
          f"  (host from {_SOURCE['HOST']})")
