#!/usr/bin/env python3
"""Detect silent gaps across one or more videos and write a combined cut/keep edit list to JSON.

Supports multiple --input files (or auto-discovers everything under --input-dir) so
several separately recorded clips can each be silence-cut and then joined into one
final video by cut_by_segments.py, in the given order. Never touches any input file.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi"}


def discover_inputs(input_dir: Path):
    if not input_dir.exists():
        return []
    files = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    # Lexicographic by filename. Use zero-padded numeric prefixes (01_, 02_, ...)
    # if you need a join order other than alphabetical.
    return sorted(files, key=lambda p: p.name)


def get_duration(input_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return float(result.stdout.strip())


def detect_raw_silence(input_path: Path, silence_db: float, min_silence_duration: float, duration: float):
    proc = subprocess.run(
        [
            "ffmpeg", "-i", str(input_path),
            "-vn",
            "-af", f"silencedetect=noise={silence_db}dB:d={min_silence_duration}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log = proc.stderr

    segments = []
    pending_start = None
    for line in log.splitlines():
        start_match = SILENCE_START_RE.search(line)
        end_match = SILENCE_END_RE.search(line)
        if start_match:
            pending_start = max(0.0, float(start_match.group(1)))
        elif end_match and pending_start is not None:
            end = min(duration, float(end_match.group(1)))
            if end > pending_start:
                segments.append({"start": pending_start, "end": end})
            pending_start = None

    # File ended while still silent: ffmpeg doesn't print a trailing silence_end.
    if pending_start is not None and duration > pending_start:
        segments.append({"start": pending_start, "end": duration})

    segments.sort(key=lambda s: s["start"])
    return segments


def apply_padding(silence_segments, padding: float):
    """Shrink each silence segment inward by `padding` on both sides to get the
    region that actually gets cut, leaving padding seconds of near-silence as
    a buffer around speech. Segments too short to survive padding are dropped
    (i.e. left fully intact, nothing is cut there)."""
    cut_segments = []
    for seg in silence_segments:
        cut_start = seg["start"] + padding
        cut_end = seg["end"] - padding
        if cut_end > cut_start:
            cut_segments.append({"start": cut_start, "end": cut_end})
    return cut_segments


def invert_to_keep_segments(cut_segments, duration: float):
    keep_segments = []
    cursor = 0.0
    for seg in cut_segments:
        if seg["start"] > cursor:
            keep_segments.append({"start": cursor, "end": seg["start"]})
        cursor = max(cursor, seg["end"])
    if cursor < duration:
        keep_segments.append({"start": cursor, "end": duration})
    # Drop degenerate zero-length segments from floating point edge cases.
    return [s for s in keep_segments if s["end"] - s["start"] > 0.01]


def process_one(input_path: Path, silence_db: float, min_silence_duration: float, padding: float):
    duration = get_duration(input_path)
    silence_segments = detect_raw_silence(input_path, silence_db, min_silence_duration, duration)
    cut_segments = apply_padding(silence_segments, padding)
    keep_segments = invert_to_keep_segments(cut_segments, duration)
    cut_total = sum(s["end"] - s["start"] for s in cut_segments)
    return {
        "input": str(input_path),
        "duration": duration,
        "silence_segments_raw": silence_segments,
        "cut_segments": cut_segments,
        "keep_segments": keep_segments,
        "summary": {
            "silence_candidates": len(silence_segments),
            "actual_cuts": len(cut_segments),
            "keep_segments": len(keep_segments),
            "estimated_cut_seconds": round(cut_total, 3),
            "estimated_output_seconds": round(duration - cut_total, 3),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Detect silence across one or more videos and build a combined cut/keep edit list as JSON.")
    parser.add_argument("--input", nargs="+", default=None,
                         help="One or more source videos, in the order they should be joined. "
                              "If omitted, auto-discovers every video file directly under --input-dir, sorted by filename.")
    parser.add_argument("--input-dir", default="input", help="Used to auto-discover inputs when --input is omitted.")
    parser.add_argument("--output-json", default="output/cuts.json", help="Where to write the combined edit list.")
    parser.add_argument("--silence-db", type=float, default=-30, help="Noise floor in dB; quieter than this counts as silence.")
    parser.add_argument("--min-silence-duration", type=float, default=0.45, help="Minimum silence length (sec) to count as a cut candidate.")
    parser.add_argument("--padding", type=float, default=0.15, help="Seconds of silence to keep on each side of a cut, as a buffer.")
    args = parser.parse_args()

    if args.input:
        input_paths = [Path(p) for p in args.input]
    else:
        input_paths = discover_inputs(Path(args.input_dir))

    if not input_paths:
        print(f"No input videos found (looked in {args.input_dir}/, extensions {sorted(VIDEO_EXTENSIONS)}).", file=sys.stderr)
        sys.exit(1)

    missing = [p for p in input_paths if not p.exists()]
    if missing:
        print(f"Input(s) not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        sys.exit(1)

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    sources = [process_one(p, args.silence_db, args.min_silence_duration, args.padding) for p in input_paths]

    total_duration = sum(s["duration"] for s in sources)
    total_cut = sum(s["summary"]["estimated_cut_seconds"] for s in sources)

    result = {
        "params": {
            "silence_db": args.silence_db,
            "min_silence_duration": args.min_silence_duration,
            "padding": args.padding,
        },
        "sources": sources,
        "summary": {
            "source_count": len(sources),
            "total_duration": round(total_duration, 3),
            "estimated_cut_seconds": round(total_cut, 3),
            "estimated_output_seconds": round(total_duration - total_cut, 3),
        },
    }

    output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {output_json} — {len(sources)} source file(s), combined output ~"
          f"{result['summary']['estimated_output_seconds']}s (from {round(total_duration, 3)}s total). Join order:")
    for s in sources:
        print(f"  - {s['input']} ({s['summary']['estimated_output_seconds']}s kept)")


if __name__ == "__main__":
    main()
