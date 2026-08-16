"""Group analysed takes into scenes.

The Vision Agent sees each take alone, so it has no way to reuse wording — the
same canal appears as 'canal street', 'city canal street' and 'canal waterfront'.
This pass shows Gemini every label at once and asks it to merge them into a small
canonical set, then rewrites the analysis with a canonical_location on each take.

    python agents/group_scenes.py
"""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL_PRO", "gemini-3.7-flash")

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "mapping": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "canonical": {"type": "string"},
                },
                "required": ["label", "canonical"],
            },
        }
    },
    "required": ["mapping"],
}

PROMPT = """These are location labels written by someone logging shots from one
film, one shot at a time. Because each shot was described in isolation, the same
physical location has been given many different names.

Merge them into a small canonical set of distinct filming locations. Aim for
roughly 8 to 15 locations for a feature short.

Rules:
- Labels describing the same physical place must map to one canonical name.
- Use a short lowercase canonical name, e.g. 'canal street', 'control room'.
- Interior and exterior of the same building are different locations.
- Do not invent locations that no label refers to.
- Every input label must appear exactly once in the mapping.

Labels:
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", default="../footage/clips/analysis.json")
    args = ap.parse_args()

    path = Path(args.analysis)
    takes = json.loads(path.read_text())
    labels = sorted({t["location_label"] for t in takes})

    client = genai.Client()
    response = client.models.generate_content(
        model=MODEL,
        contents=PROMPT + "\n".join(f"- {lab}" for lab in labels),
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )

    mapping = {m["label"]: m["canonical"] for m in json.loads(response.text)["mapping"]}
    missing = [lab for lab in labels if lab not in mapping]
    if missing:
        print(f"warning: {len(missing)} labels unmapped, keeping originals")
        for lab in missing:
            mapping[lab] = lab

    for t in takes:
        t["canonical_location"] = mapping[t["location_label"]]

    path.write_text(json.dumps(takes, indent=2))

    groups = defaultdict(list)
    for t in takes:
        groups[t["canonical_location"]].append(t)

    print(f"{len(labels)} labels -> {len(groups)} locations\n")
    for loc, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sizes = ", ".join(
            f"{t['clip_name'].split('_')[-1]}:{t['shot_size']}"
            for t in sorted(items, key=lambda x: x["clip_name"])
        )
        people = max(t["subjects_count"] for t in items)
        print(f"{len(items):3d} clips  {loc:28s} up to {people}p")
        print(f"           {sizes}\n")


if __name__ == "__main__":
    main()
