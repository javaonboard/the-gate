"""Identity resolution must not invent people.

One actress came back as three characters in a single take. The logic was
right; what was wrong was what it did when the comparison did not come back.
"""

from PIL import Image

from agents import casting


class DroppedCall:
    """A client whose every request fails, as an overloaded API does."""

    class models:
        @staticmethod
        def generate_content(**_kwargs):
            raise RuntimeError("503 Service Unavailable")


def a_face() -> Image.Image:
    return Image.new("RGB", (160, 160), (90, 90, 110))


def test_failed_comparison_is_not_a_new_person(tmp_path):
    known = tmp_path / "known.png"
    a_face().save(known)

    verdict = casting.confirm_identity(DroppedCall(), a_face(),
                                       [("char_known", known)])

    assert verdict is casting.UNSURE
    assert verdict is not None, "a dropped call must not read as 'someone new'"


def test_nobody_to_compare_against_is_a_new_person(tmp_path):
    """With no candidates at all, new really is the right answer."""
    assert casting.confirm_identity(DroppedCall(), a_face(), []) is None


def test_same_face_twice_in_one_take_is_one_person():
    """Two crops seconds apart do not need a round trip to be recognised."""
    face = [0.4] * 1408
    again = [0.4001] * 1408
    assert casting._distance(face, again) <= casting.SAME_IN_TAKE

    someone_else = [0.4] * 704 + [-0.4] * 704
    assert casting._distance(face, someone_else) > casting.SAME_IN_TAKE


def test_a_shape_in_smoke_is_not_a_character():
    """A person too small or too indistinct to place is recorded as nobody."""
    assert not casting.can_identify(
        {"identifiable": False, "face_box": [400, 400, 500, 500]})
    assert not casting.can_identify(
        {"identifiable": True, "face_box": [400, 400, 410, 410]})
    assert casting.can_identify(
        {"identifiable": True, "face_box": [300, 300, 500, 450]})


def test_the_slate_is_not_in_the_film():
    """Sampling starts after the head of the take, where the slate lives."""
    assert min(casting.moments(36.0)) >= casting.SLATE_SECONDS
    # a short take still gets looked at, four times
    assert len(casting.moments(4.0)) == 4
    assert max(casting.moments(4.0)) <= 4.0


# --- the gate ---------------------------------------------------------------

def _report(**kw):
    """A report with nothing filled in but what the test is about."""
    from core.gate import Coverage, GateReport
    from datetime import datetime
    rows = kw.pop("rows", [])
    summary = kw.pop("summary", {"characters": 1, "required": 1, "have": 1,
                                 "judged": True, "completeness": 1.0,
                                 "missing": [], "exposure_usd": 0})
    return GateReport(scene_id="s", now=datetime(2026, 8, 25, 15, 0),
                      coverage=Coverage(rows=rows, summary=summary),
                      baseline=None, **kw)


def test_no_go_until_the_rules_have_been_checked():
    """A GO says the company can move. Coverage alone cannot say that."""
    report = _report()
    assert report.compliance is None
    assert report.go is False
    assert report.verdict == "NOT CHECKED"


def test_full_coverage_and_a_clean_day_is_a_go():
    from core.union_rules import DayAssessment
    from datetime import datetime
    clean = DayAssessment(call=datetime(2026, 8, 25, 7, 0),
                          wrap=datetime(2026, 8, 25, 17, 0))
    assert _report(compliance=clean).go is True


def test_a_minor_over_hours_stops_the_day():
    """Some rules are cost. This one is law."""
    from core.union_rules import DayAssessment, Violation
    from datetime import datetime
    breached = DayAssessment(
        call=datetime(2026, 8, 25, 7, 0), wrap=datetime(2026, 8, 25, 21, 0),
        violations=[Violation(rule="minor_hours", severity="violation",
                              detail="14h against a 9.5h cap", people=1)])
    report = _report(compliance=breached)
    assert report.go is False
    assert [v.rule for v in report.blocked_by_rule] == ["minor_hours"]


def test_overtime_costs_money_but_does_not_forbid_moving_on():
    """Saying NO-GO on overtime would invent a rule that does not exist."""
    from core.union_rules import DayAssessment, Violation
    from datetime import datetime
    pricey = DayAssessment(
        call=datetime(2026, 8, 25, 7, 0), wrap=datetime(2026, 8, 25, 20, 0),
        violations=[Violation(rule="overtime", severity="warning",
                              detail="13h day", cost_usd=19_386, people=60)])
    report = _report(compliance=pricey)
    assert report.go is True
    assert report.blocked_by_rule == []
    assert report.compliance.total_cost_usd == 19_386


# --- watching a file in windows ---------------------------------------------

def _windows(total):
    """The windows the Editor would watch, for a file this long."""
    from agents import editor
    out, start = [], 0.0
    while total - start >= editor.MIN_WINDOW_SECONDS:
        out.append((start, min(editor.WINDOW_SECONDS, total - start)))
        start += out[-1][1]
    return out


def test_a_file_is_not_cut_into_slivers():
    """A ten-minute clip measured 600.06s and left a 60ms tail.

    That tail was cut out and sent to the model, which rejected it with
    INVALID_ARGUMENT and took the whole ingest down, after the model had
    already watched all ten minutes.
    """
    assert _windows(600.057791) == [(0.0, 300.0), (300.0, 300.0)]
    assert all(length >= 2.0 for _, length in _windows(1336.38))


def test_every_second_of_a_clean_length_is_watched():
    assert sum(length for _, length in _windows(600.0)) == 600.0
    assert sum(length for _, length in _windows(240.0)) == 240.0


# --- the schedule is made of setups -----------------------------------------

def test_a_take_always_has_a_setup_behind_it():
    """A film ingest wrote takes with no setup row.

    The schedule is built from setups, so a day with none looks as though it
    has nothing left to shoot, the odds are meaningless and no missing shot
    can be priced against the time it would cost. It was silent, because the
    takes were all there.
    """
    import re
    from pathlib import Path

    src = Path("agents/intake.py").read_text(encoding="utf-8")
    body = src[src.index("def _run_clips"):]
    insert = body.index('ch.insert("setups"')
    guard = body.rindex("if not setup_hint", 0, insert) if "if not setup_hint" in body[:insert] else -1

    assert guard == -1, (
        "the setups insert is behind `if not setup_hint`, so takes ingested "
        "as part of a film get no camera position on record"
    )
