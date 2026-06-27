#!/usr/bin/env python3
"""Render a single draft video from a cuts.json edit list (see detect_silence.py) using ffmpeg.

cuts.json lists one or more source videos, each with its own keep_segments; this
script joins them, in the order they appear in cuts.json (i.e. the order
detect_silence.py was given them), into one output file. Refuses to write over any
of the original source files. Re-encodes for frame-accurate cuts, since this is
meant as a review draft, not a final master.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def validate_and_sort_segments(keep_segments, duration=None, label=""):
    """cuts.json may be hand-edited (SKILL.md tells users they can adjust it),
    so don't trust list order or non-overlap — sort by start time and reject
    anything that would play back out of order or double-render a region."""
    for seg in keep_segments:
        if seg["end"] <= seg["start"]:
            raise ValueError(f"{label}: invalid segment, end must be after start: {seg}")
        if duration is not None and (seg["start"] < 0 or seg["end"] > duration + 0.01):
            raise ValueError(f"{label}: segment out of [0, {duration}] range: {seg}")

    ordered = sorted(keep_segments, key=lambda s: s["start"])
    for prev, cur in zip(ordered, ordered[1:]):
        if cur["start"] < prev["end"] - 0.01:
            raise ValueError(f"{label}: overlapping segments, fix before rendering: {prev} and {cur}")
    return ordered


def nvenc_available() -> bool:
    """True if this ffmpeg build can drive an NVIDIA GPU encoder (h264_nvenc)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return "h264_nvenc" in result.stdout
    except FileNotFoundError:
        return False


def video_codec_args(use_nvenc: bool, crf: int, preset: str):
    if use_nvenc:
        # -cq uses the same 0-51 "lower is better" scale as x264's -crf, so the
        # --crf value doubles as the GPU quality target. p5 trades some speed
        # (still far faster than CPU) for quality closer to x264's defaults.
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf)]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def build_filter_complex(sources):
    """Builds one trim+concat graph spanning every source, in source order and then
    segment order within each source — that combined order is the final playback order."""
    parts = []
    labels = []
    for src_idx, source in enumerate(sources):
        for seg_idx, seg in enumerate(source["keep_segments"]):
            v, a = f"v{src_idx}_{seg_idx}", f"a{src_idx}_{seg_idx}"
            parts.append(f"[{src_idx}:v]trim=start={seg['start']}:end={seg['end']},setpts=PTS-STARTPTS[{v}]")
            parts.append(f"[{src_idx}:a]atrim=start={seg['start']}:end={seg['end']},asetpts=PTS-STARTPTS[{a}]")
            labels.append(f"[{v}][{a}]")
    concat = "".join(labels) + f"concat=n={len(labels)}:v=1:a=1[outv][outa]"
    parts.append(concat)
    return ";".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Render output/draft.mp4 by joining every source's keep_segments, in cuts.json order.")
    parser.add_argument("--cuts-json", default="output/cuts.json", help="Edit list produced by detect_silence.py.")
    parser.add_argument("--output", default="output/draft.mp4", help="Draft file to write. Overwritten each run.")
    parser.add_argument("--crf", type=int, default=18, help="Quality (lower = better/larger); used as -crf on CPU or -cq on GPU.")
    parser.add_argument("--preset", default="veryfast", help="x264 preset (CPU encoding only).")
    parser.add_argument("--audio-bitrate", default="192k")
    parser.add_argument(
        "--encoder", default="auto", choices=["auto", "nvenc", "cpu"],
        help="auto: GPU(NVENC)が使えれば自動使用、無ければCPU(libx264) / nvenc: 強制的にGPU / cpu: 強制的にCPU",
    )
    args = parser.parse_args()

    cuts_json_path = Path(args.cuts_json)
    output_path = Path(args.output)

    if not cuts_json_path.exists():
        print(f"Edit list not found: {cuts_json_path}. Run detect_silence.py first.", file=sys.stderr)
        sys.exit(1)

    data = json.loads(cuts_json_path.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    if not sources:
        print("No sources in cuts.json — nothing to render.", file=sys.stderr)
        sys.exit(1)

    input_paths = [Path(s["input"]) for s in sources]

    missing = [p for p in input_paths if not p.exists()]
    if missing:
        print(f"Source(s) not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        sys.exit(1)

    if any(p.resolve() == output_path.resolve() for p in input_paths):
        print("Refusing to write output over one of the original source files.", file=sys.stderr)
        sys.exit(1)

    try:
        for i, source in enumerate(sources):
            source["keep_segments"] = validate_and_sort_segments(
                source.get("keep_segments", []), source.get("duration"),
                label=f"source {i} ({source['input']})",
            )
    except ValueError as e:
        print(f"cuts.json is invalid: {e}", file=sys.stderr)
        sys.exit(1)

    empty = [s["input"] for s in sources if not s["keep_segments"]]
    if empty:
        print(f"Warning: source(s) with no keep_segments are skipped entirely: {empty}", file=sys.stderr)
    sources = [s for s in sources if s["keep_segments"]]
    if not sources:
        print("No keep_segments across any source — nothing to render.", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    filter_complex = build_filter_complex(sources)

    total_segments = sum(len(s["keep_segments"]) for s in sources)
    print(f"Rendering {len(sources)} source file(s), {total_segments} segment(s) total -> {output_path}")
    for s in sources:
        print(f"  - {s['input']}: {len(s['keep_segments'])} segment(s)")

    use_nvenc = args.encoder == "nvenc" or (args.encoder == "auto" and nvenc_available())
    print(f"Encoder: {'h264_nvenc (GPU)' if use_nvenc else 'libx264 (CPU)'}")

    # Pass the filter graph via a script file rather than inline on the command
    # line: with hundreds of segments this string can exceed the OS command-line
    # length limit (hit on Windows around ~300 segments).
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".filter", delete=False, encoding="utf-8"
    ) as f:
        f.write(filter_complex)
        filter_script_path = f.name

    try:
        def render(nvenc: bool):
            cmd = ["ffmpeg", "-y"]
            for source in sources:
                cmd += ["-i", source["input"]]
            cmd += [
                "-filter_complex_script", filter_script_path,
                "-map", "[outv]", "-map", "[outa]",
                *video_codec_args(nvenc, args.crf, args.preset),
                "-c:a", "aac", "-b:a", args.audio_bitrate,
                str(output_path),
            ]
            return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        result = render(use_nvenc)
        if use_nvenc and result.returncode != 0 and args.encoder == "auto":
            print("GPU encode failed, falling back to CPU (libx264):", file=sys.stderr)
            print(result.stderr[-2000:], file=sys.stderr)
            use_nvenc = False
            result = render(False)
    finally:
        Path(filter_script_path).unlink(missing_ok=True)

    if result.returncode != 0:
        print("ffmpeg failed:", file=sys.stderr)
        print(result.stderr[-4000:], file=sys.stderr)
        sys.exit(result.returncode)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
