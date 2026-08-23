"""Human labels.

Nothing in the interface should require knowing film jargon or reading an id.
Every agent shows as a real crew job, every person shows as a name with their
role underneath, and every technical term carries a plain-English gloss.
"""

from __future__ import annotations

# --- agents, named after the job they would do on a real set ----------------

AGENTS: dict[str, dict[str, str]] = {
    "orchestrator": {
        "name": "The Gate",
        "role": "Production Desk",
        "does": "Runs the crew and puts the call together",
    },
    "vision": {
        "name": "Scripty",
        "role": "Script Supervisor",
        "does": "Watches every take and logs what was captured",
    },
    "script": {
        "name": "Breakdown",
        "role": "Script Breakdown",
        "does": "Reads the scene and lists the shots an editor will need",
    },
    "editor": {
        "name": "Editor",
        "role": "Assistant Editor",
        "does": "Watches the footage and says where each shot begins",
    },
    "casting": {
        "name": "Casting",
        "role": "Casting",
        "does": "Finds who is in each take and keeps track of them",
    },
    "qc": {
        "name": "QC",
        "role": "Quality Control",
        "does": "Looks for what would stop a take being used",
    },
    "historian": {
        "name": "The Book",
        "role": "Production Records",
        "does": "Remembers how long this crew took on past shoots",
    },
    "scout": {
        "name": "Scout",
        "role": "Location Scout",
        "does": "Watches weather, permits and road closures at the location",
    },
    "continuity": {
        "name": "Continuity",
        "role": "Continuity",
        "does": "Checks that shots will cut together",
    },
    "simulator": {
        "name": "The Clock",
        "role": "Schedule",
        "does": "Works out whether the remaining work fits in the day",
    },
    "compliance": {
        "name": "Steward",
        "role": "Union Steward",
        "does": "Checks rest, meals and overtime against the agreements",
    },
    "planner": {
        "name": "1st AD",
        "role": "Assistant Director",
        "does": "Decides what to shoot next and what to let go",
    },
}


def agent_label(key: str) -> dict[str, str]:
    return AGENTS.get(key, {"name": key, "role": "", "does": ""})


# --- people -----------------------------------------------------------------

ROLES = {
    "dp": "Director of Photography",
    "camera": "Camera",
    "grip": "Grip",
    "electric": "Electric",
    "sound": "Sound",
    "art": "Art Department",
    "wardrobe": "Wardrobe",
    "hmu": "Hair and Make-up",
    "ad": "Assistant Director",
    "cast": "Cast",
    "background": "Background",
}


def person_label(person_id: str) -> dict[str, str]:
    """`dp_lind` becomes Lind, Director of Photography."""
    if "_" in person_id:
        prefix, rest = person_id.split("_", 1)
    else:
        prefix, rest = "", person_id
    name = rest.replace("_", " ").strip().title()
    return {"id": person_id, "name": name, "role": ROLES.get(prefix, prefix.title())}


# --- jargon, in plain words -------------------------------------------------

GLOSSARY: dict[str, dict[str, str]] = {
    "turnaround": {
        "short": "rest between wrap and next call",
        "long": "The crew has to get a minimum rest between finishing one day "
                "and starting the next — 10 hours for crew, 12 for cast. Going "
                "into it costs double pay for every hour you take.",
    },
    "meal_penalty": {
        "short": "fine for feeding the crew late",
        "long": "Everyone must eat within six hours of the call time. Every 30 "
                "minutes late, the production pays a penalty to every person on "
                "the crew.",
    },
    "overtime": {
        "short": "extra pay after a long day",
        "long": "Time and a half after eight hours, double after twelve.",
    },
    "setup": {
        "short": "one camera position",
        "long": "Where the camera is placed and how the scene is lit. Moving to "
                "a new setup is the slow part of a shoot day.",
    },
    "take": {
        "short": "one roll of the camera",
        "long": "A single attempt at a shot. One setup usually gets several.",
    },
    "coverage": {
        "short": "the angles needed to cut the scene",
        "long": "An editor needs a wide shot, a close-up of each actor, and "
                "usually over-the-shoulder shots. Miss one and the scene cannot "
                "be assembled properly.",
    },
    "master": {
        "short": "the wide shot of the whole scene",
        "long": "Covers the whole scene in one angle. Everything else cuts into it.",
    },
    "single": {
        "short": "close-up of one actor",
        "long": "A shot with one person in frame, used for their lines and reactions.",
    },
    "ots": {
        "short": "over-the-shoulder shot",
        "long": "Shot past one actor's shoulder onto another. The standard way to "
                "cover a conversation.",
    },
    "insert": {
        "short": "close-up of an object",
        "long": "A detail shot — a letter, a hand, a phone screen. Can usually be "
                "picked up anywhere later.",
    },
    "plate": {
        "short": "clean shot for visual effects",
        "long": "The empty scene with no actors, so effects can be added later. "
                "Missing it means an expensive fix at the effects vendor.",
    },
    "wrap": {
        "short": "end of the shooting day",
        "long": "",
    },
    "call_time": {
        "short": "when the crew starts",
        "long": "",
    },
    "hard_stop": {
        "short": "latest you can finish without breaking the rules",
        "long": "Wrap after this and you go into the crew's rest period, which "
                "costs double pay.",
    },
    "circled": {
        "short": "the take the director preferred",
        "long": "Marked on the report so the editor knows where to start.",
    },
    "pickup": {
        "short": "reshooting something later",
        "long": "Going back for a missing shot after the set is struck. Expensive "
                "— the location, cast and crew all have to be booked again.",
    },
    "shot_size": {
        "short": "how close the camera is",
        "long": "From ELS (very wide) through MS (waist up) to ECU (eyes only).",
    },
}

SHOT_SIZES = {
    "ELS": "extreme wide",
    "LS": "wide",
    "MLS": "medium wide",
    "MS": "medium",
    "MCU": "medium close",
    "CU": "close-up",
    "ECU": "extreme close-up",
}

MOVEMENTS = {
    "static": "locked off",
    "pan": "panning",
    "tilt": "tilting",
    "dolly": "dolly move",
    "handheld": "handheld",
    "crane": "crane move",
    "steadicam": "steadicam",
    "zoom": "zoom",
}


def explain(term: str) -> str:
    return GLOSSARY.get(term, {}).get("short", term.replace("_", " "))
