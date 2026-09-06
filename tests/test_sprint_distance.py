"""The sprint boards get a distance (2026-09-06).

The rated parser floors at 600 m, so '60m' answered None and every flat
sprint was dropped before ranking_results -- the sprint PR boards were
empty from the day they were written. The sprint parser answers below the
floor and nowhere else.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from event_parse import (distanceFromEventShort,          # noqa: E402
                         sprintDistanceFromEventShort, _MIN_DISTANCE)


def test_flat_sprints_parse_below_the_rated_floor():
    for name, want in (("60m", 60), ("55 Meters", 55), ("60 Meter Dash", 60),
                       ("100m", 100), ("200 Meter Dash", 200), ("400m", 400),
                       ("Boys 100 Meters", 100), ("Girls 300m", 300)):
        assert sprintDistanceFromEventShort(name)[0] == want, name
        assert distanceFromEventShort(name)[0] is None, name


def test_sprint_parser_refuses_what_is_not_a_flat_sprint():
    for name in ("4x100 Relay", "100mH", "60H", "110m Hurdles", "Shot Put",
                 "Long Jump", "30m", "", None):
        assert sprintDistanceFromEventShort(name)[0] is None, name


def test_sprint_parser_stops_at_the_rated_floor():
    for name in ("600m", "800m", "1600m", "2miles"):
        assert sprintDistanceFromEventShort(name)[0] is None, name
        assert distanceFromEventShort(name)[0] >= _MIN_DISTANCE, name

