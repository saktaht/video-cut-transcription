#!/usr/bin/env python3
"""動画/音声ファイルを文字起こしし、開始秒・終了秒・テキストを対応させたCSVを出力する。

事前準備:
    pip3 install faster-whisper

使い方:
    python3 .claude/skills/video-cut/scripts/transcribe_to_csv.py output/draft.mp4
    python3 .claude/skills/video-cut/scripts/transcribe_to_csv.py output/draft.mp4 --granularity word --model medium
"""

import argparse
import csv
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="文字起こし対象のファイル（例: output/draft.mp4）")
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="出力CSVパス（省略時は入力ファイルと同じ場所に <ファイル名>.csv）",
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
        "--device", default="cpu", choices=["cpu", "cuda"],
        help="推論デバイス（既定: cpu）",
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


def main():
    args = build_parser().parse_args()
    if not args.video.exists():
        sys.exit(f"ファイルが見つかりません: {args.video}")

    output_path = args.output or args.video.with_suffix(".csv")

    print(f"文字起こし中... ({args.video} / model={args.model} / granularity={args.granularity})")
    rows, info = transcribe(args.video, args.model, args.language, args.device, args.granularity)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["start_sec", "end_sec", "text"])
        writer.writerows(rows)

    print(f"検出言語: {info.language} (確度 {info.language_probability:.2f})")
    print(f"{len(rows)} 行を書き出しました: {output_path}")


if __name__ == "__main__":
    main()
