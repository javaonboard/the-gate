"""Casting, who is in the footage.

Nobody types a cast list. The agent looks at each take, finds the people in it,
crops their faces, and works out whether it has seen them before. The AD only
ever supplies a name, once, and only if they want one.
    """

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from agents import gemini
from PIL import Image

from agents.resilience import retry
from core.coverage import connect

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")
MODEL = os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash")
EMBED_MODEL = os.environ.get("GEMINI_MODEL_EMBED", "multimodalembedding")
EMBED_DIMS = 1408

FACES_DIR = Path(os.environ.get(
    "FACES_DIR",
    Path(__file__).resolve().parents[1] / "api" / "static" / "faces",
))
FACES_DIR.mkdir(parents=True, exist_ok=True)

# multimodalembedding encodes the whole crop, so lighting and background
# weigh as heavily as the face, measured on this footage, two shots of the
# same person land at 0.31 while a man and a woman land at 0.33.
SHORTLIST_DISTANCE = 0.60
SHORTLIST_SIZE = 6

FACE_PAD = 0.35          # crop this much around the detected box

# A hooded or masked character is identified by the costume, not the face, so
# the crop opens up to show it rather than the dark inside of a hood.
COSTUME_PAD = 1.6
MIN_FACE_PX = 48         # anything smaller is background, not a character


PEOPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "How this person looks — hair, clothing, "
                                       "build. Enough to recognise them again.",
                    },
                    "face_box": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Face bounding box as [y0, x0, y1, x1], "
                                       "each 0-1000 relative to the image.",
                    },
                    "prominence": {
                        "type": "string",
                        "enum": ["foreground", "background"],
                        "description": "foreground if they are part of the scene, "
                                       "background if a passer-by or extra.",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["cast", "crew"],
                        "description": "crew if they are working on the film — "
                                       "holding a slate, boom, meter, or "
                                       "adjusting equipment. cast otherwise.",
                    },
                    "face_clear": {
                        "type": "boolean",
                        "description": "true if the face itself is clearly "
                                       "visible. false if it is hooded, masked, "
                                       "in shadow, turned away or lost in haze "
                                       "— even when the person is still "
                                       "identifiable by their costume.",
                    },
                    "identifiable": {
                        "type": "boolean",
                        "description": "true if you could pick this same person "
                                       "out of a different shot — by their "
                                       "face, or by distinctive clothing, mask "
                                       "or costume. false only if there is "
                                       "nothing to tell them apart by.",
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["description", "face_box", "prominence", "role",
                             "identifiable"],
            },
        }
    },
    "required": ["people"],
}

PROMPT = """Find every person whose face is visible in this frame.

For each one give a tight bounding box around the face as [y0, x0, y1, x1],
each value 0-1000 relative to the image.
"""


@dataclass
class Character:
    character_id: str
    name: str
    face_uri: str
    description: str
    embedding: list[float]
    appearances: int


# --- frames and faces -------------------------------------------------------

def grab_frame(video: Path, at_seconds: float) -> bytes | None:
    """One frame, as PNG bytes."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at_seconds:.2f}",
         "-i", str(video), "-frames:v", "1", "-f", "image2pipe",
         "-vcodec", "png", "-"],
        capture_output=True,
    )
    return out.stdout or None


def crop_face(frame_png: bytes, box: list[int],
              face_clear: bool = True) -> Image.Image | None:
    """Crop a person from the frame, framed on whatever identifies them.

    For a clear face, tight on the face. For a character in a hood or a mask,
    the face box is the dark inside of the hood, a crop of it is a smudge, and
    tells nobody who they are. What identifies them is the costume, so the crop
    widens to show it.
    """
    image = Image.open(BytesIO(frame_png)).convert("RGB")
    w, h = image.size

    # Gemini returns [y0, x0, y1, x1] on a 0-1000 scale.
    y0, x0, y1, x1 = [v / 1000.0 for v in box[:4]]
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0

    bw, bh = (x1 - x0) * w, (y1 - y0) * h
    if bw < MIN_FACE_PX or bh < MIN_FACE_PX:
        return None

    pad = FACE_PAD if face_clear else COSTUME_PAD
    px, py = bw * pad, bh * pad
    left = max(0, int(x0 * w - px))
    top = max(0, int(y0 * h - py))
    right = min(w, int(x1 * w + px))
    bottom = min(h, int(y1 * h + py))
    if right <= left or bottom <= top:
        return None

    return image.crop((left, top, right, bottom)).resize((160, 160), Image.LANCZOS)


_embedder = None


def _embed_model():
    """multimodalembedding, which only speaks the older Vertex vision API.

    google-genai rejects image-only embed_content for this model, so this is
    the path that actually works. It carries a deprecation warning; if it ever
    stops, faces fall back to a zero vector and every face reads as new.
    """
    global _embedder
    if _embedder is None:
        import vertexai
        from vertexai.vision_models import MultiModalEmbeddingModel

        vertexai.init(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("AGENT_ENGINE_LOCATION", "us-central1"),
        )
        _embedder = MultiModalEmbeddingModel.from_pretrained("multimodalembedding@001")
    return _embedder


def embed_face(client: genai.Client, face: Image.Image) -> list[float]:
    """Embed a face crop so the same person can be recognised later."""
    from vertexai.vision_models import Image as VertexImage

    buffer = BytesIO()
    face.save(buffer, format="PNG")
    try:
        result = _embed_model().get_embeddings(
            image=VertexImage(image_bytes=buffer.getvalue()), dimension=EMBED_DIMS
        )
        return list(result.image_embedding)
    except Exception as exc:
        print(f"  embedding failed ({type(exc).__name__}): face will read as new")
        return [0.0] * EMBED_DIMS


# --- identity ---------------------------------------------------------------

IDENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "same_as": {
            "type": "string",
            "description": "The label of the matching known face, or NEW if this "
                           "is someone not among them.",
        },
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["same_as", "confidence"],
}

IDENTITY_PROMPT = """The first image is a person from a new shot, with how they
were described. The images after it are people already known, each labelled and
described.

Decide whether the first person is one of the labelled people, or someone new.
"""


# Not knowing is not an answer. Returning "new" when the comparison failed to
# run is how one actor became five: every dropped call minted a character.
UNSURE = "?"

# Two crops this close, taken seconds apart in the same take, are the same
# person. Measured on real footage: one actress across four moments of a
# take sat between 0.046 and 0.128; different people were beyond 0.6.
SAME_IN_TAKE = 0.15

# Calling someone new when a known face is this close is the expensive
# mistake, so it gets a second opinion.
DOUBT_DISTANCE = 0.30


def confirm_identity(client: genai.Client, face: Image.Image,
                     candidates: list[tuple[str, Path]],
                     looks_like: str = "",
                     known_as: dict[str, str] | None = None) -> str | None:
    """Show Gemini the new face beside the shortlist and let it decide.

    Returns the matching character, None for genuinely someone new, or UNSURE
    when the comparison could not be made, which the caller must not treat as
    either.
    """
    if not candidates:
        return None

    parts = []
    buffer = BytesIO()
    face.save(buffer, format="PNG")
    parts.append(types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png"))
    if looks_like:
        parts.append(types.Part.from_text(text=f"Described as: {looks_like}"))

    labels = []
    for label, path in candidates:
        if not path.exists():
            continue
        labels.append(label)
        described = (known_as or {}).get(label, "")
        parts.append(types.Part.from_text(
            text=f"Known person: {label}"
                 + (f" — described as: {described}" if described else "")))
        parts.append(types.Part.from_bytes(
            data=path.read_bytes(), mime_type="image/png"))

    if not labels:
        return None

    parts.append(types.Part.from_text(text=IDENTITY_PROMPT))

    try:
        response = retry(
            client.models.generate_content,
            model=MODEL,
            contents=parts,
            config=gemini.config(
                temperature=0,
                response_mime_type="application/json",
                response_schema=IDENTITY_SCHEMA,
            ),
        )
        answer = json.loads(gemini.text_of(response))
    except Exception:
        return UNSURE

    same_as = str(answer.get("same_as", "NEW")).strip()
    if same_as in labels and float(answer.get("confidence", 0)) >= 0.6:
        return same_as
    return None


def described_as(ch, character_ids: list[str]) -> dict[str, str]:
    """How each known person was described when we first saw them.

    A blurred profile and a clear three-quarter face of one actress are a hard
    comparison from pictures alone, and were being called two people. The
    words we already collected settle it: "long wavy brown hair, black tank
    top" twice over is not two women.
    """
    if not character_ids:
        return {}
    rows = ch.query(
        f"SELECT character_id, description FROM {DB}.characters FINAL "
        f"WHERE character_id IN %(ids)s",
        parameters={"ids": tuple(character_ids)},
    ).result_rows
    return {r[0]: r[1] for r in rows}


def _distance(a: list[float], b: list[float]) -> float:
    """Cosine distance, for comparing two crops without a round trip."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 1.0
    return 1.0 - dot / (na * nb)


def shortlist(ch, production_id: str, embedding: list[float]
              ) -> list[tuple[str, float]]:
    """The few known faces worth comparing against."""
    if not embedding or not any(embedding):
        return []
    # Keep this shape. The vector similarity index only kicks in for a plain
    # ORDER BY cosineDistance(...) LIMIT n, and any extra predicate touching the
    # embedding column makes ClickHouse drop it from the block entirely.
    rows = ch.query(
        f"""
        SELECT character_id, cosineDistance(embedding, %(e)s) AS d
        FROM {DB}.characters
        WHERE production_id = %(p)s
        ORDER BY d ASC LIMIT {SHORTLIST_SIZE}
        """,
        parameters={"e": embedding, "p": production_id},
    ).result_rows

    return [
        (r[0], float(r[1])) for r in rows
        if r[1] == r[1] and float(r[1]) <= SHORTLIST_DISTANCE
    ]


def next_name(existing: int) -> str:
    """Placeholder names until the AD types a real one."""
    return f"Character {chr(65 + existing)}" if existing < 26 else f"Character {existing + 1}"


# --- the pass ---------------------------------------------------------------

# One frame is a poor sample of a take. Someone can be facing camera for
# two seconds of a ninety-second shot and away for the rest, measured on
# real footage, a face visible at 5s was gone by 30s.
SLATE_SECONDS = 3.0
FRAME_MARKS = (0.15, 0.4, 0.65, 0.9)

# A face smaller than this across the frame cannot identify anyone, the crop
# comes out as a smudge. Roughly a head at the far end of a corridor.
MIN_FACE_HEIGHT = 45      # of 1000


def big_enough(box: list[int]) -> bool:
    if len(box) < 4:
        return False
    y0, x0, y1, x1 = box[:4]
    return (y1 - y0) >= MIN_FACE_HEIGHT and (x1 - x0) >= MIN_FACE_HEIGHT * 0.6


def moments(duration: float) -> list[float]:
    """When to look, given how long the take runs.

    A short take still gets looked at four times; the slate window just shrinks
    with it rather than swallowing the whole clip.
    """
    start = min(SLATE_SECONDS, duration * 0.25)
    span = max(duration - start - 0.3, 0.5)
    return [start + span * f for f in FRAME_MARKS]


def people_in_take(client: genai.Client, video: Path, duration: float
                   ) -> list[tuple[bytes, dict[str, Any]]]:
    """Everyone who appears in the take, from several moments in it.

Keeping only the single frame with the most faces meant a take's cast was
    whoever happened to share one instant.
   """
    found: list[tuple[bytes, dict[str, Any]]] = []

    for at in moments(duration):
        frame = grab_frame(video, max(0.3, at))
        if not frame:
            continue
        try:
            response = retry(
                client.models.generate_content,
                model=MODEL,
                contents=[types.Part.from_bytes(data=frame, mime_type="image/png"),
                          PROMPT],
                config=gemini.config(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=PEOPLE_SCHEMA,
                ),
            )
            people = json.loads(gemini.text_of(response)).get("people", [])
        except Exception:
            continue

        for person in people:
            # The crew are not in the film. A 2nd AC holding the slate is the
            # clearest face in the take and the least relevant.
            if person.get("role") == "crew":
                continue
            found.append((frame, person))

    return found


def can_identify(person: dict[str, Any]) -> bool:
    """Whether this person could be picked out of another shot.

Not the same as a clear face. A character in a hazmat hood or a helmet has
    no visible face and is still one particular character, the costume is what
    identifies them, and a script supervisor tracks them by it perfectly well.
    """
    return (bool(person.get("identifiable", True))
            and big_enough(person.get("face_box", [])))


def analyse_take(client: genai.Client, ch, production_id: str, scene_id: str,
                 setup_id: str, take_id: str, video: Path, duration: float,
                 known: int, sightings: list | None = None
                 ) -> list[dict[str, Any]]:
    """Find the people in one take and resolve them to characters.

Two halves with very different rules, which is why `sightings` can be
    handed in already done:
    """
    if sightings is None:
        sightings = people_in_take(client, video, duration or 3.0)
    if not sightings:
        return []

    # One person seen at three moments is one character, not three. Keeping
    # them by resolved id collapses the repeats, and keeps the first sighting's
    # box so the face shown is one that was actually detected.
    links: dict[str, dict[str, Any]] = {}
    resolved: list[tuple[str, list[float]]] = []
    for frame, person in sightings:
        face = crop_face(frame, person.get("face_box", []),
                         face_clear=bool(person.get("face_clear", True)))
        if face is None:
            continue

        embedding = embed_face(client, face)

        # Someone already resolved a moment ago in this same take does not need
        # resolving again.
        near = [cid for cid, emb in resolved
                if _distance(embedding, emb) <= SAME_IN_TAKE]
        if near:
            if near[0] in links and person.get("prominence") == "foreground":
                links[near[0]]["prominence"] = "foreground"
            continue

        candidates = shortlist(ch, production_id, embedding)
        described = described_as(ch, [cid for cid, _ in candidates])
        faces = [(cid, FACES_DIR / f"{cid}.png") for cid, _ in candidates]
        looks_like = person.get("description", "")

        chosen = confirm_identity(client, face, faces, looks_like, described)

        # Asked once and told "new", with a known face sitting right there , 
        # ask again before inventing someone.
        if chosen is None and candidates and candidates[0][1] <= DOUBT_DISTANCE:
            chosen = confirm_identity(client, face, faces, looks_like, described)

        if chosen is UNSURE:
            # We could not tell who this is. Recording nothing loses one
            # sighting; guessing "new" invents a cast member who then shows as
            # missing every shot in the scene.
            continue

        if chosen:
            character_id = chosen
            distance = dict(candidates).get(chosen, 0.0)
            matched_by = "gemini"
            # counted once per take, however many moments they were seen in
            if character_id not in links:
                ch.command(
                    f"ALTER TABLE {DB}.characters "
                    f"UPDATE appearances = appearances + 1 "
                    f"WHERE production_id = %(p)s AND character_id = %(c)s",
                    parameters={"p": production_id, "c": character_id},
                )
        elif not can_identify(person):
            # Someone is there and we cannot say who. Better to record nobody
            # than to invent a cast member, an unknown face leaves the scene
            # looking uncovered, which is the safe way to be wrong.
            continue
        else:
            character_id = f"char_{uuid.uuid4().hex[:8]}"
            distance = 0.0
            matched_by = "new"
            filename = f"{character_id}.png"
            face.save(FACES_DIR / filename)
            ch.insert(
                "characters",
                [[production_id, character_id, next_name(known), f"/faces/{filename}",
                  embedding, person.get("description", ""), take_id, 1,
                  datetime.now()]],
                column_names=["production_id", "character_id", "name", "face_uri",
                              "embedding", "description", "first_take_id",
                              "appearances", "created_at"],
            )
            known += 1

        resolved.append((character_id, embedding))

        box = [v / 1000.0 for v in person.get("face_box", [0, 0, 0, 0])[:4]]
        seen = links.get(character_id)
        if seen is None:
            links[character_id] = {
                "character_id": character_id,
                "confidence": float(person.get("confidence", 1.0 - distance)),
                "bbox": box,
                "prominence": person.get("prominence", "foreground"),
                "matched_by": matched_by,
            }
        elif person.get("prominence") == "foreground":
            # foreground in any moment of the take is foreground in the take
            seen["prominence"] = "foreground"

    if links:
        ch.insert(
            "take_characters",
            [[production_id, scene_id, setup_id, take_id, l["character_id"],
              l["confidence"], l["bbox"], l["prominence"], l["matched_by"]]
             for l in links.values()],
            column_names=["production_id", "scene_id", "setup_id", "take_id",
                          "character_id", "confidence", "bbox", "prominence",
                          "matched_by"],
        )

    return list(links.values())


def cast_scene(scene_id: str, clips_dir: Path, run=None, reset: bool = False
               ) -> dict[str, Any]:
    """Work through every take in a scene and build the cast list."""
    ch = connect()
    client = gemini.client()

    production_id = ch.query(
        f"SELECT production_id FROM {DB}.scenes WHERE scene_id = %(s)s LIMIT 1",
        parameters={"s": scene_id},
    ).result_rows
    production_id = production_id[0][0] if production_id else "prod_now"

    if reset:
        for table in ("characters", "take_characters"):
            ch.command(
                f"ALTER TABLE {DB}.{table} DELETE WHERE production_id = %(p)s",
                parameters={"p": production_id},
            )

    takes = ch.query(
        f"""
        SELECT take_id, setup_id, duration_s, proxy_uri
        FROM {DB}.takes WHERE scene_id = %(s)s ORDER BY setup_id, take_no
        """,
        parameters={"s": scene_id},
    ).result_rows

    known = ch.query(
        f"SELECT count() FROM {DB}.characters WHERE production_id = %(p)s",
        parameters={"p": production_id},
    ).result_rows[0][0]

    seen = 0
    for take_id, setup_id, duration, _ in takes:
        video = clips_dir / f"{take_id}.mp4"
        if not video.exists():
            found = next((clips_dir.parent / "uploads").rglob(f"{take_id}.mp4"), None)
            if found is None:
                continue
            video = found
        if run:
            run.publish("casting", "working", f"Looking at {take_id}")

        # One bad take must not lose the whole pass, the model and the
        # cluster both drop connections occasionally.
        for attempt in (1, 2):
            try:
                links = analyse_take(client, ch, production_id, scene_id,
                                     setup_id, take_id, video,
                                     float(duration or 3.0), known)
                break
            except Exception as exc:
                if attempt == 2:
                    print(f"{take_id:22s} skipped: {type(exc).__name__}")
                    links = []
                else:
                    time.sleep(2)

        known += sum(1 for l in links if l["matched_by"] == "new")
        seen += 1
        print(f"{take_id:22s} {len(links)} face(s)"
              f"  {' '.join(l['matched_by'][0] for l in links)}", flush=True)

    cast = ch.query(
        f"""
        SELECT character_id, name, face_uri, appearances, description
        FROM {DB}.characters WHERE production_id = %(p)s
        ORDER BY appearances DESC
        """,
        parameters={"p": production_id},
    ).result_rows

    return {
        "scene_id": scene_id,
        "takes_seen": seen,
        "cast": [
            {"character_id": r[0], "name": r[1], "face_uri": r[2],
             "appearances": r[3], "description": r[4]}
            for r in cast
        ],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="prod_now_sc001")
    ap.add_argument("--all", action="store_true",
                    help="every scene in the production")
    ap.add_argument("--clips", default="../footage/clips")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    ch = connect()

    if args.all:
        scenes = [r[0] for r in ch.query(
            f"SELECT scene_id FROM {DB}.scenes "
            f"WHERE production_id = 'prod_now' ORDER BY scene_id"
        ).result_rows]
        for i, scene in enumerate(scenes):
            print(f"\n=== {scene} ===", flush=True)
            # wipe only on the first scene, or each pass erases the last
            cast_scene(scene, Path(args.clips),
                       reset=args.reset and i == 0)

        cast = ch.query(
            f"SELECT name, appearances, description "
            f"FROM {DB}.characters FINAL "
            f"WHERE production_id = 'prod_now' ORDER BY appearances DESC"
        ).result_rows
        print(f"\nacross {len(scenes)} scenes, {len(cast)} people:")
        for name, seen, description in cast:
            print(f"  {name:14s} {seen:3d} appearances  "
                  f"{description[:58]}")
    else:
        result = cast_scene(args.scene, Path(args.clips), reset=args.reset)
        print(f"\n{len(result['cast'])} people:")
        for c in result["cast"]:
            print(f"  {c['name']:14s} {c['appearances']:3d} appearances "
                  f" {c['description'][:58]}")
