"""Split a film into shots and treat each shot as a take.

Every cut in a finished film is a different camera angle, which is exactly what
the coverage matrix cares about. This detects shot boundaries, cuts each one to
its own file using the camera roll / clip naming a DIT would use, and writes a
manifest for the ingest step.

Clips are downscaled to 720p — Gemini downsamples video anyway, and smaller
files upload far faster.

    python data/split_takes.py --input ../footage/ToS-4k-1920.mov
    python data/split_takes.py --input ../footage/ToS-4k-1920.mov --start 120 --end 420
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

SHOWINFO_PTS = re.compile(r"pts_time:([0-9.]+)")


def detect_cuts(path, threshold, start, end):
    """Return shot-boundary timestamps in seconds, on the source timeline.

    Detection runs over the whole file with no seeking — trimming the input
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
    ap.add_argument("--threshold", type=float, default=0.35)
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
    cuts = detect_cuts(src, args.threshold, args.start, args.end)
    end = args.end or duration_of(src)
    bounds = [args.start] + cuts + [end]

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
