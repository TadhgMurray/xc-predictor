#!/usr/bin/env python3
"""
diag_sport_gap_ability.py -- is the grass-to-track gap a function of
ABILITY rather than of POOL? (owner, 2026-09-29)

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_sport_gap_ability.py
    /srv/venv/bin/python scripts/diag_sport_gap_ability.py --min-races 2 --keep-shift

Run from the PROJECT ROOT. Read-only.

! A NAME FOR scripts/diag_sport_gap.py --by-ability, NOT A SECOND COPY. The
  measurement, and how to read it, live in that file's header; this only
  adds the flag, so the two can never disagree.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import diag_sport_gap                                          # noqa: E402

if __name__ == "__main__":
    if "--by-ability" not in sys.argv:
        sys.argv.insert(1, "--by-ability")
    sys.exit(diag_sport_gap.main())
