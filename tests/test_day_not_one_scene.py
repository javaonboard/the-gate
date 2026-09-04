"""The gate answers about the day, so it has to have looked at the day.

Every number on the screen said "the day" — the verdict, the coverage bar, the
odds of making it, what is worth grabbing before the company moves. Underneath,
all four came from `build_report(client, scene_id)`, which matrixed one scene
and simulated one scene's setups. Whichever scene the gate happened to run on
was being reported as the whole day.

Nothing failed. The percentages were real percentages of a real thing; it was
just a different thing from the one the label claimed, and the "worth grabbing"
list never changed when you opened another scene, because it had never been
about the scene you opened.

Forty-six tests passed through all of it, so here is the one that would not.
"""

from __future__ import annotations

import pytest

from core import character_coverage as cc


class FakeClient:
    """Enough ClickHouse to answer `scenes_of` and `people_seen`."""

    def __init__(self, scenes: list[tuple[str, str]], seen: dict[str, int]):
        self._scenes = scenes
        self._seen = seen
        self.asked_about: list[str] = []

    def query(self, sql: str, parameters: dict | None = None):
        params = parameters or {}
        if "FROM the_gate.scenes" in sql or ".scenes " in sql:
            rows = [(sid, place.replace(" ", "_")) for sid, place in self._scenes]
        elif "max(length(subjects))" in sql:
            scene_id = params.get("s", "")
            self.asked_about.append(scene_id)
            rows = [(self._seen.get(scene_id, 0),)]
        else:
            rows = []
        return type("Result", (), {"result_rows": rows})()


def person(name: str, **bands: bool):
    """One character, required in the bands named, holding none of them."""
    return cc.CharacterRow(
        character_id=f"char_{name}", name=name, face_uri="", appearances=1,
        cells={b: cc.Cell(band=b, required=bands.get(b, False),
                          recover_cost_usd=1000 if bands.get(b) else 0)
               for b in cc.BANDS},
    )


@pytest.fixture
def two_scenes(monkeypatch):
    """A day of two scenes, each short of a different amount."""
    scenes = [("sc001", "hallway"), ("sc002", "stairwell")]
    rows = {
        "sc001": [person("A", wide=True, close_up=True)],
        "sc002": [person("B", wide=True), person("C", wide=True)],
    }
    monkeypatch.setattr(cc, "matrix",
                        lambda client, sid, rate=None: rows[sid])
    monkeypatch.setattr(
        cc, "scene_shots",
        lambda client, sid, rate=None: cc.SceneRow(cells={
            s: cc.Cell(band=s, required=False) for s in cc.SCENE_SHOTS
        }),
    )
    return FakeClient(scenes, seen={"sc001": 1, "sc002": 2}), rows


def test_the_day_is_every_scene_added_up(two_scenes):
    client, rows = two_scenes
    _, day = cc.day(client, "prod_now")

    apiece = [cc.summarise(rows[sid], None) for sid in ("sc001", "sc002")]

    assert day["required"] == sum(s["required"] for s in apiece)
    assert day["have"] == sum(s["have"] for s in apiece)
    assert day["exposure_usd"] == sum(s["exposure_usd"] for s in apiece)
    assert day["characters"] == 3, "three people work today, across two scenes"


def test_the_day_is_more_than_any_one_scene_in_it(two_scenes):
    """The shape of the bug: one scene's answer standing in for the day."""
    client, rows = two_scenes
    _, day = cc.day(client, "prod_now")

    for sid in ("sc001", "sc002"):
        alone = cc.summarise(rows[sid], None)
        assert day["required"] > alone["required"], (
            f"{sid} alone was being reported as the whole day"
        )


def test_a_missing_shot_says_which_scene_to_go_back_to(two_scenes):
    """Day-wide, "you are short a wide" is useless without a place."""
    client, _ = two_scenes
    _, day = cc.day(client, "prod_now")

    assert day["missing"], "two scenes short of shots and nothing listed"
    for gap in day["missing"]:
        assert gap["scene_id"], f"{gap['name']} is short of something, nowhere"
        assert gap["place"], f"{gap['name']} is short of something, nowhere named"

    assert {g["scene_id"] for g in day["missing"]} == {"sc001", "sc002"}


def test_every_scene_is_asked_about(two_scenes):
    """A scene skipped is coverage silently counted as covered."""
    client, _ = two_scenes
    cc.day(client, "prod_now")
    assert client.asked_about == ["sc001", "sc002"]
