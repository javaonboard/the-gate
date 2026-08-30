"""Group analysed takes into scenes, over a whole analysis file.

The merge itself lives in agents/locations.py, because the upload path needs
the same judgment as soon as footage lands.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from agents.locations import merge_labels

load_dotenv()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", default="../footage/clips/analysis.json")
    args = ap.parse_args()

    path = Path(args.analysis)
    takes = json.loads(path.read_text())
    labels = sorted({t["location_label"] for t in takes})

    seconds = sum(t.get("duration_seconds", 0) for t in takes)
    mapping = merge_labels(labels, shots=len(takes), minutes=seconds / 60)

    for t in takes:
        label = t["location_label"].strip().lower()
        t["canonical_location"] = mapping.get(label, label)

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
