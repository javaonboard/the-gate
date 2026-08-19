"""Casting — who is in the footage.

Nobody types a cast list. The agent looks at each take, finds the people in it,
crops their faces, and works out whether it has seen them before. The AD only
ever supplies a name, once, and only if they want one.

Matching is by face embedding and cosine distance in ClickHouse. Two takes shot
an hour apart from opposite angles still resolve to the same person, which is
what makes "you're short a close-up on Marcus" possible at all.

    python -m agents.casting --scene prod_now_sc001
    python -m agents.casting --scene prod_now_sc001 --reset
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
from PIL import Image

from core.coverage import connect

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB = os.environ.get("CLICKHOUSE_DATABASE", "the_gate")
MODEL = os.environ.get("GEMINI_MODEL_FLASH", "gemini-3.7-flash")
EMBED_MODEL = os.environ.get("GEMINI_MODEL_EMBED", "multimodalembedding")
EMBED_DIMS = 1408

FACES_DIR = Path(__file__).resolve().parents[1] / "api" / "static" / "faces"
FACES_DIR.mkdir(parents=True, exist_ok=True)

# multimodalembedding encodes the whole crop, so lighting and background weigh
# as heavily as the face — measured on this footage, two shots of the same
# person land at 0.31 while a man and a woman land at 0.33. No threshold
# separates them. So the embedding is used only to shortlist: it narrows eight
# candidates to three, and Gemini looks at the faces and decides.
SHORTLIST_DISTANCE = 0.60
SHORTLIST_SIZE = 6

FACE_PAD = 0.35          # crop this much around the detected box
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
                    "confidence": {"type": "number"},
                },
                "required": ["description", "face_box", "prominence"],
            },
        }
    },
    "required": ["people"],
}

PROMPT = """Find every person whose face is visible in this frame.

For each one give a tight bounding box around the face as [y0, x0, y1, x1],
each value 0-1000 relative to the image.

Describe them by what would let you recognise them in a different shot — hair,
clothing, build. Do not guess names or roles.

Mark someone foreground if they are part of the action, background if they are
a passer-by, a crowd member, or out of focus behind the subject.

If no faces are visible, return an empty list."""


@dataclass
class Character:
    character_id: str
    name: str
    face_uri: str
    description: str
    embedding: list[float]
    appearances: int


def _client() -> genai.Client:
    return genai.Client()


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


def crop_face(frame_png: bytes, box: list[int]) -> Image.Image | None:
    """Crop a face from the frame, with some room around it."""
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

    px, py = bw * FACE_PAD, bh * FACE_PAD
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
        print(f"  embedding failed ({type(exc).__name__}) — face will read as new")
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

IDENTITY_PROMPT = """The first image is a face from a new shot. The images after
it are faces already known, each labelled.

Decide whether the first face is one of the labelled people, or someone new.

Judge by the face — bone structure, features, hairline. Ignore lighting, angle,
expression, focus and background; the same person will look very different
between a wide shot and a close-up on a film set.

Answer with the matching label, or NEW."""


def confirm_identity(client: genai.Client, face: Image.Image,
                     candidates: list[tuple[str, Path]]) -> str | None:
    """Show Gemini the new face beside the shortlist and let it decide."""
    if not candidates:
        return None

    parts = []
    buffer = BytesIO()
    face.save(buffer, format="PNG")
    parts.append(types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png"))

    labels = []
    for label, path in candidates:
        if not path.exists():
            continue
        labels.append(label)
        parts.append(types.Part.from_text(text=f"Known face: {label}"))
        parts.append(types.Part.from_bytes(
            data=path.read_bytes(), mime_type="image/png"))

    if not labels:
        return None

    parts.append(types.Part.from_text(text=IDENTITY_PROMPT))

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=parts,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=IDENTITY_SCHEMA,
            ),
        )
        answer = json.loads(response.text)
    except Exception:
        return None

    same_as = str(answer.get("same_as", "NEW")).strip()
    if same_as in labels and float(answer.get("confidence", 0)) >= 0.6:
        return same_as
    return None


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

def analyse_take(client: genai.Client, ch, production_id: str, scene_id: str,
                 setup_id: str, take_id: str, video: Path, duration: float,
                 known: int) -> list[dict[str, Any]]:
    """Find the people in one take and resolve them to characters."""
    frame = grab_frame(video, max(0.4, duration * 0.4))
    if not frame:
        return []

    response = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=frame, mime_type="image/png"), PROMPT],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=PEOPLE_SCHEMA,
        ),
    )
    people = json.loads(response.text).get("people", [])

    links: list[dict[str, Any]] = []
    for person in people:
        face = crop_face(frame, person.get("face_box", []))
        if face is None:
            continue

        embedding = embed_face(client, face)
        candidates = shortlist(ch, production_id, embedding)
        chosen = confirm_identity(
            client, face,
            [(cid, FACES_DIR / f"{cid}.png") for cid, _ in candidates],
        )

        if chosen:
            character_id = chosen
            distance = dict(candidates).get(chosen, 0.0)
            matched_by = "gemini"
            ch.command(
                f"ALTER TABLE {DB}.characters UPDATE appearances = appearances + 1 "
                f"WHERE production_id = %(p)s AND character_id = %(c)s",
                parameters={"p": production_id, "c": character_id},
            )
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

        box = [v / 1000.0 for v in person.get("face_box", [0, 0, 0, 0])[:4]]
        links.append({
            "character_id": character_id,
            "confidence": float(person.get("confidence", 1.0 - distance)),
            "bbox": box,
            "prominence": person.get("prominence", "foreground"),
            "matched_by": matched_by,
        })

    if links:
        ch.insert(
            "take_characters",
            [[production_id, scene_id, setup_id, take_id, l["character_id"],
              l["confidence"], l["bbox"], l["prominence"], l["matched_by"]]
             for l in links],
            column_names=["production_id", "scene_id", "setup_id", "take_id",
                          "character_id", "confidence", "bbox", "prominence",
                          "matched_by"],
        )

    return links


def cast_scene(scene_id: str, clips_dir: Path, run=None, reset: bool = False
               ) -> dict[str, Any]:
    """Work through every take in a scene and build the cast list."""
    ch = connect()
    client = _client()

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
            continue
        if run:
            run.publish("casting", "working", f"Looking at {take_id}")

        # One bad take must not lose the whole pass — the model and the
        # cluster both drop connections occasionally.
        for attempt in (1, 2):
            try:
                links = analyse_take(client, ch, production_id, scene_id,
                                     setup_id, take_id, video,
                                     float(duration or 3.0), known)
                break
            except Exception as exc:
                if attempt == 2:
                    print(f"{take_id:22s} skipped — {type(exc).__name__}")
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
