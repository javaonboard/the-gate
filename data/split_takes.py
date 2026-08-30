"""Split a film into shots and treat each shot as a take.

Every cut in a finished film is a different camera angle, which is exactly what
the coverage matrix cares about.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

SHOWINFO_PTS = re.compile(r"pts_time:([0-9.]+)")


# How hard a visual change has to be to count as a cut. Edited drama scores
# well above this; raw camera footage, dark scenes and slow dissolves score far
# lower, which is why one fixed number does not work.
DEFAULT_THRESHOLD = 0.12

# If the average shot comes out longer than this, the detector is missing
# cuts and the threshold comes down.
PLAUSIBLE_MEAN_SHOT = 75.0

# Below this a detection is a flash, a flicker or a compression artefact
# rather than a shot.
MIN_SHOT_SECONDS = 1.5

# Nothing is one shot for this long. Anything longer is split anyway, because a
# seventeen-minute "take" is useless to every step downstream.
MAX_SHOT_SECONDS = 150.0


def detect_cuts(path, threshold=DEFAULT_THRESHOLD, start=0.0, end=0.0):
    """Shot-boundary timestamps, in seconds, on the source timeline.

    Detection runs over the whole file with no seeking, trimming the input
    shifts the reported pts_time and makes the offsets wrong.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    times = sorted(set(float(m) for m in SHOWINFO_PTS.findall(proc.stderr)))
    if start:
        times = [t for t in times if t >= start]
    if end:
        times = [t for t in times if t <= end]
    return times


def find_shots(path, duration, threshold=DEFAULT_THRESHOLD, on_step=None):
    """Work out where the shots are, lowering the bar until it looks sane.

A first pass at the usual threshold suits edited footage. When the result
    implies improbably long shots, raw camera takes, a dark scene, gradual
    transitions, the threshold comes down and it tries again.
    """
    cuts: list[float] = []

    for attempt, level in enumerate((threshold, threshold / 2, threshold / 4), 1):
        cuts = detect_cuts(path, level)
        mean_shot = duration / max(1, len(cuts) + 1)
        if on_step:
            on_step(level, len(cuts), mean_shot)
        if mean_shot <= PLAUSIBLE_MEAN_SHOT or attempt == 3:
            break

    # drop detections too close together to be real shots
    kept: list[float] = []
    for t in cuts:
        if not kept or t - kept[-1] >= MIN_SHOT_SECONDS:
            kept.append(t)

    bounds = [0.0] + kept + [duration]

    # last resort: cut anything still enormous into even pieces
    split: list[float] = [0.0]
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        span = b - a
        if span > MAX_SHOT_SECONDS:
            pieces = int(span // MAX_SHOT_SECONDS) + 1
            step = span / pieces
            for k in range(1, pieces):
                split.append(a + step * k)
        split.append(b)

    return sorted(set(split))


def duration_of(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def cut(path, out_path, start, end):
    # -ss before -i seeks fast; -t after -i gives an accurate duration.
    # Using -to before -i is ambiguous and produced wrong clip lengths.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{start:.3f}", "-i", str(path), "-t", f"{end - start:.3f}",
         "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "24", "-c:a", "aac", "-b:a", "96k", str(out_path)],
        check=True,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default="../footage/clips")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--min-seconds", type=float, default=1.5)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0)
    ap.add_argument("--roll", default="A001")
    ap.add_argument("--limit", type=int, default=0, help="stop after N clips")
    args = ap.parse_args()

    src = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("detecting cuts...")
    end = args.end or duration_of(src)
    bounds = find_shots(
        src, end, args.threshold,
        on_step=lambda lvl, n, mean: print(
            f"  threshold {lvl:.3f}: {n} cuts, {mean:.0f}s average shot"),
    )
    if args.start:
        bounds = [b for b in bounds if b >= args.start] or [args.start, end]

    manifest = []
    n = 0
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < args.min_seconds:
            continue
        n += 1
        clip_name = f"{args.roll}_C{n:03d}"
        out_path = outdir / f"{clip_name}.mp4"
        cut(src, out_path, a, b)
        manifest.append({
            "clip_name": clip_name,
            "camera_roll": args.roll,
            "source": src.name,
            "start_s": round(a, 3),
            "end_s": round(b, 3),
            "duration_s": round(b - a, 3),
            "path": str(out_path),
        })
        print(f"{clip_name}  {a:8.2f} -> {b:8.2f}  ({b - a:5.2f}s)")
        if args.limit and n >= args.limit:
            break

    manifest_path = outdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\n{len(manifest)} clips -> {outdir}")
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
