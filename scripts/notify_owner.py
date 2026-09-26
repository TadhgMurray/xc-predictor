#!/usr/bin/env python3
"""
notify_owner.py -- email the admins that a pipeline run failed.

    /srv/venv/bin/python scripts/notify_owner.py <logdir> <step> [<step> ...]
    /srv/venv/bin/python scripts/notify_owner.py --test     # send a test
    /srv/venv/bin/python scripts/notify_owner.py --backup-failed

★ WHY (sweep, 2026-09-26). A failed run said so only in its own terminal
  and in logs/<run>/summary.log, so a failure at 3am was found whenever
  someone next looked -- the 10_rankings_finish failures of 09-22 and
  09-24 among them. deploy/run_pipeline.sh calls this from summarise()
  when any step FAILED, which covers both abort paths too.

  To: every address in XCP_ADMIN_EMAILS. Sent with the site's own mail
  setup (accounts.sendMail: XCP_MAIL_PROVIDER, XCP_MAIL_KEY, XCP_MAIL_FROM).
  The mail names each failed step with the last lines of its log, and the
  run's summary.

! NEVER FAILS THE PIPELINE. No mail setup, no admins, a provider error:
  it says so on stdout and exits 0. XCP_NOTIFY=0 turns it off.
"""
import os
import socket
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TAIL = 25


def _tail(path, n=TAIL):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 32768))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return ["(no log)"]


def message(logdir, steps):
    run = os.path.basename(os.path.normpath(logdir))
    host = socket.gethostname()
    subject = (f"[racecast] pipeline {run}: {len(steps)} step"
               f"{'s' if len(steps) != 1 else ''} failed ({', '.join(steps)[:80]})")
    lines = [f"The pipeline run {run} on {host} finished with failed steps.", ""]
    for s in steps:
        lines += [f"== {s} -- last {TAIL} lines of {s}.log ==",
                  *_tail(os.path.join(logdir, s + ".log")), ""]
    lines += ["== summary.log ==", *_tail(os.path.join(logdir, "summary.log"), 60),
              "", f"logs: {logdir}",
              "status: /account/status (or scripts/print_status.py)"]
    return subject, "\n".join(lines) + "\n"


def main(argv):
    if os.environ.get("XCP_NOTIFY", "1") in ("0", "false", "no"):
        print("[notify] XCP_NOTIFY=0 -- not sending")
        return 0
    try:
        import accounts
    except Exception as exc:                            # noqa: BLE001
        print(f"[notify] cannot load the mail code ({type(exc).__name__}: {exc})")
        return 0
    to = sorted(accounts.adminEmails())
    if not to:
        print("[notify] no XCP_ADMIN_EMAILS -- nobody to tell")
        return 0
    if not accounts.mailEnabled():
        print("[notify] no mail provider (XCP_MAIL_PROVIDER / XCP_MAIL_KEY) -- not sent")
        return 0
    if argv[:1] == ["--backup-failed"]:
        subject, text = ("[racecast] the database backup did not run",
                         "deploy/backup_db.sh failed or was skipped (a pipeline "
                         "held the lock for its whole wait).\n\n"
                         "What it said:  journalctl -u xcp-backup -n 50\n"
                         "Run it by hand: sudo bash deploy/backup_db.sh\n")
    elif argv[:1] == ["--test"]:
        subject, text = ("[racecast] test: pipeline alerts reach you",
                         "If you are reading this, a failed pipeline run will "
                         "email you.\n")
    elif len(argv) >= 2:
        subject, text = message(argv[0], argv[1:])
    else:
        print(__doc__)
        return 0
    for addr in to:
        ok = accounts.sendMail(addr, subject, text)
        print(f"[notify] {'sent' if ok else 'FAILED'} to {addr}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
