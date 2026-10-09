"""tf_points.eventFamily: one record-list name per event, however the meet
labelled it (owner, 2026-10-09, a school PR page listing "s 100 Hurdles"
beside "100m Hurdles" and "High Jump" four ways)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "racecast"))
from tf_points import eventFamily, familyLabel                  # noqa: E402


def test_spellings_fold_to_one_event():
    same = [("60 M Hurdles", "60m Hurdles", "s 60 Hurdles", "60 M Hurdles Invitational"),
            ("3ksteeple", "Men's 3000 Steeplechase", "s 3000 Steeplechase"),
            ("High Jump", "High Jump Championship", "High Jump University", "Girls High Jump"),
            ("Pole Vault Division 1", "Pole Vault Invite", "s Pole Vault"),
            ("Javelin", "Javelin Throw"), ("Weight Throw", "Weight Throw Invite")]
    for group in same:
        assert len({eventFamily(e) for e in group}) == 1, group


def test_labels():
    assert familyLabel(eventFamily("s 100 Hurdles")) == "100m Hurdles"
    assert familyLabel(eventFamily("2ksteeple")) == "2000m Steeplechase"
    assert familyLabel(eventFamily("Indoor Pentathlon INVITE")) == "Pentathlon"
    assert familyLabel(eventFamily("Weight Throw")) == "Weight Throw"
    assert eventFamily("100m Hurdles") != eventFamily("110m Hurdles")
