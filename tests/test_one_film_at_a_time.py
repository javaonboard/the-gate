"""Asking twice must not take the film in twice.

A drag that also registers as a click, an impatient second drop, a retried
request — any of these posts the same file again while the first pass is still
running. Each pass generates its own camera roll and cuts the same video from
scratch, so one forty-five second clip arrived in the scene as two takes,
U13F_C001 and U206_C001, identical but for the roll.

Nothing downstream can tell those apart afterwards. They are two genuine takes
of the same length in the same setup, which is exactly what a second take of a
shot looks like.
"""

from __future__ import annotations

from api.events import Bus


def test_a_run_in_flight_is_found():
    bus = Bus()
    run = bus.start("ws_one")
    assert bus.active("ws_one") is run


def test_a_finished_run_is_not_in_the_way():
    bus = Bus()
    run = bus.start("ws_one")
    run.finish()
    assert bus.active("ws_one") is None, (
        "a finished run still counted as in flight, so the day could never "
        "take another film"
    )


def test_another_day_is_not_blocked():
    bus = Bus()
    bus.start("ws_one")
    assert bus.active("ws_two") is None


def test_the_newest_run_is_the_one_returned():
    bus = Bus()
    first = bus.start("ws_one")
    first.finish()
    second = bus.start("ws_one")
    assert bus.active("ws_one") is second
