"""Reports, sent uploads and fix requests mail the admins (owner,
2026-11-03: "can some more things send me emails. Like reports")."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")


def _src(name):
    return open(os.path.join(_ROOT, "racecast", name)).read()


def test_each_queue_mails_the_admins():
    app = _src("app.py")
    rep = app[app.index("def api_report("):app.index("def contact_page(")]
    assert "mailAdmins(" in rep and "log_as=\"report\"" in rep
    assert "remote_ip" not in rep[rep.index("mailAdmins("):]          # no IP in the mail
    up = _src("uploads.py")
    sub = up[up.index("def _submit("):up.index("def upload_discard(")]
    assert "AC.mailAdmins(" in sub and "/account/admin/uploads/" in sub
    fx = _src("fixes.py")
    assert "AC.mailAdmins(" in fx and "/account/admin/fixes" in fx


def test_mail_admins_sends_to_each_admin_off_thread(monkeypatch):
    import accounts as AC
    sent = []
    monkeypatch.setattr(AC, "adminEmails", lambda: {"a@x.com", "b@x.com"})
    monkeypatch.setattr(AC, "sendMail", lambda to, s, t, headers=None, log_as=None: sent.append((to, headers)))
    import threading
    started = []
    real = threading.Thread

    class T(real):
        def start(self):
            started.append(1)
            self.run()
    monkeypatch.setattr(threading, "Thread", T)
    assert AC.mailAdmins("s", "t", reply_to="r@y.com") == 2
    assert started and sorted(s[0] for s in sent) == ["a@x.com", "b@x.com"]
    assert sent[0][1] == {"Reply-To": "r@y.com"}
    monkeypatch.setattr(AC, "adminEmails", lambda: set())
    assert AC.mailAdmins("s", "t") == 0
