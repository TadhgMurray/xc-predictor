# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Configuration
# File Title: config.py
# Purpose: Database connection settings. Issue #16: credentials come from
#          the ENVIRONMENT now, so this file is safe to ship to any server.
#
# Resolution order for every field:
#   1. XCP_DB_* environment variable (how the server is configured)
#   2. scripts/config_local.py (gitignored; how a dev machine is configured)
#   3. the legacy hardcoded default -- password included FOR ONE TRANSITION
#      ONLY, so tonight's in-flight pipeline cannot break on a mid-run pull.
#      It prints a warning every time it is used and gets REMOVED once the
#      local machine has a config_local.py. Rotate the password after that:
#      it has lived in this file's git history.

import os


def _localPassword():
    try:
        from config_local import DB_PASSWORD
        return DB_PASSWORD
    except ImportError:
        return None


_env_pw = os.environ.get("XCP_DB_PASSWORD")
_pw = _env_pw or _localPassword()
if _pw is None:
    _pw = "Tigger5959!"          # legacy fallback -- see header, remove soon
    print("[config] WARNING: using the legacy hardcoded DB password. Set "
          "XCP_DB_PASSWORD or create scripts/config_local.py with "
          "DB_PASSWORD = \"...\" -- this fallback is scheduled for removal.")

PG_CONFIG = {
    "host":     os.environ.get("XCP_DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("XCP_DB_PORT", "5432")),
    "dbname":   os.environ.get("XCP_DB_NAME", "xc_predictor"),
    "user":     os.environ.get("XCP_DB_USER", "postgres"),
    "password": _pw,
}
