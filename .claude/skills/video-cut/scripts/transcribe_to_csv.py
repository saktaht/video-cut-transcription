#!/usr/bin/env python3
"""動画/音声ファイルを文字起こしし、開始秒・終了秒・テキストを対応させたCSV/SRTを出力する。

事前準備:
    pip3 install faster-whisper

使い方:
    python3 .claude/skills/video-cut/scripts/transcribe_to_csv.py output/draft.mp4
    python3 .claude/skills/video-cut/scripts/transcribe_to_csv.py output/draft.mp4 --granularity word --model medium
    python3 .claude/skills/video-cut/scripts/transcribe_to_csv.py output/draft.mp4 --format srt
"""

import argparse
import csv
import os
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _make_cuda_libs_discoverable():
    """ctranslate2's GPU backend needs cuBLAS/cuDNN (and their own runtime deps,
    e.g. nvJitLink) at runtime. Rather than requiring a system-wide CUDA Toolkit
    install, pick up the DLLs/SOs bundled in whichever `nvidia-*-cu12` pip
    packages are installed (cublas, cudnn, nvjitlink, ...). No-op if none are."""
    try:
        import nvidia
    except ImportError:
        return
    lib_dirs = [
        Path(pkg_dir) / subdir
        for ns_dir in nvidia.__path__
        for pkg_dir in Path(ns_dir).iterdir()
        for subdir in ("bin", "lib")
    ]
    lib_dirs = [d for d in lib_dirs if d.is_dir()]
    if sys.platform == "win32":
        for d in lib_dirs:
            os.add_dll_directory(str(d))
        # ctranslate2's C++ delay-loading of cuBLAS/cuDNN goes through plain
        # LoadLibrary calls, which only consult PATH, not add_dll_directory()
        # (that mechanism is only honored by Python's own import/ctypes loads).
        os.environ["PATH"] = os.pathsep.join([str(d) for d in lib_dirs] + [os.environ.get("PATH", "")])
    else:
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(
            [str(d) for d in lib_dirs] + [os.environ.get("LD_LIBRARY_PATH", "")]
        )


_make_cuda_libs_discoverable()


def detect_device() -> str:
    """Use the GPU when ctranslate2 can see one, otherwise fall back to CPU."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="文字起こし対象のファイル（例: output/draft.mp4）")
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="出力ファイルパス（拡張子は --format に応じて付け替える。省略時は入力ファイルと同じ場所・同じ名前）",
    )
    parser.add_argument(
        "--format", choices=["csv", "srt", "both"], default="csv",
        help="出力形式。csv: 秒数+テキストのCSV（既定） / srt: DaVinci Resolveに字幕として読み込めるSRT / both: 両方",
    )
    parser.add_argument(
        "--model", default="small",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Whisperモデルサイズ（既定: small。精度を上げたい場合は medium / large-v3）",
    )
    parser.add_argument(
        "--language", default="ja",
        help="文字起こし言語コード（既定: ja。自動検出させたい場合は auto）",
    )
    parser.add_argument(
        "--granularity", choices=["segment", "word"], default="segment",
        help="segment: フレーズ単位で秒数と対応（既定） / word: 単語単位でより細かく秒数と対応",
    )
    parser.add_argument(
        "--device", default="auto", choices=["auto", "cpu", "cuda"],
        help="推論デバイス（既定: auto。GPUが使えれば自動でcuda、無ければcpu）",
    )
    return parser


def transcribe(video_path: Path, model_size: str, language: str, device: str, granularity: str):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device=device, compute_type="int8" if device == "cpu" else "float16")
    lang = None if language == "auto" else language
    segments, info = model.transcribe(
        str(video_path),
        language=lang,
        word_timestamps=(granularity == "word"),
        vad_filter=True,
    )

    rows = []
    for segment in segments:
        if granularity == "word" and segment.words:
            for word in segment.words:
                text = word.word.strip()
                if text:
                    rows.append((round(word.start, 2), round(word.end, 2), text))
        else:
            text = segment.text.strip()
            if text:
                rows.append((round(segment.start, 2), round(segment.end, 2), text))
    return rows, info


def write_csv(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["start_sec", "end_sec", "text"])
        writer.writerows(rows)


def format_srt_timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(rows, start=1):
            f.write(f"{i}\n")
            f.write(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n")
            f.write(f"{text}\n\n")


def main():
    args = build_parser().parse_args()
    if not args.video.exists():
        sys.exit(f"ファイルが見つかりません: {args.video}")

    base_output = args.output or args.video

    device = detect_device() if args.device == "auto" else args.device
    print(f"文字起こし中... ({args.video} / model={args.model} / granularity={args.granularity} / device={device})")
    rows, info = transcribe(args.video, args.model, args.language, device, args.granularity)
    print(f"検出言語: {info.language} (確度 {info.language_probability:.2f})")

    if args.format in ("csv", "both"):
        csv_path = base_output.with_suffix(".csv")
        write_csv(rows, csv_path)
        print(f"{len(rows)} 行を書き出しました: {csv_path}")

    if args.format in ("srt", "both"):
        srt_path = base_output.with_suffix(".srt")
        write_srt(rows, srt_path)
        print(f"{len(rows)} 件の字幕を書き出しました: {srt_path}")


if __name__ == "__main__":
    main()
