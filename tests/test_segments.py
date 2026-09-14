"""Run with: python -m unittest discover -s tests -v."""

import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_auto_cut.detect_silence import apply_padding, invert_to_keep_segments
from video_auto_cut.render_video import main as render_main, validate_and_sort_segments
from video_auto_cut.transcribe import format_srt_timestamp, write_csv, write_srt


class SegmentTests(unittest.TestCase):
    def test_padding_preserves_speech_margin(self):
        cuts = apply_padding([{"start": 2.0, "end": 4.0}], 0.15)
        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts[0]["start"], 2.15)
        self.assertAlmostEqual(cuts[0]["end"], 3.85)

    def test_short_silence_is_not_cut(self):
        self.assertEqual(apply_padding([{"start": 1, "end": 1.5}], 0.25), [])

    def test_zero_padding_keeps_entire_cut(self):
        silence = [{"start": 1, "end": 2}]
        self.assertEqual(apply_padding(silence, 0), silence)

    def test_no_silence_keeps_whole_video(self):
        self.assertEqual(invert_to_keep_segments([], 5), [{"start": 0, "end": 5}])

    def test_full_cut_leaves_no_segments(self):
        self.assertEqual(invert_to_keep_segments([{"start": 0, "end": 5}], 5), [])

    def test_start_middle_and_end_cuts(self):
        cuts = [{"start": 0, "end": 1}, {"start": 3, "end": 4}, {"start": 8, "end": 10}]
        self.assertEqual(invert_to_keep_segments(cuts, 10),
                         [{"start": 1, "end": 3}, {"start": 4, "end": 8}])

    def test_sort_does_not_reorder_original_list(self):
        segments = [{"start": 4, "end": 5}, {"start": 0, "end": 2}]
        self.assertEqual(validate_and_sort_segments(segments, 5),
                         [{"start": 0, "end": 2}, {"start": 4, "end": 5}])
        self.assertEqual(segments[0]["start"], 4)

    def test_adjacent_segments_are_allowed(self):
        segments = [{"start": 0, "end": 1}, {"start": 1, "end": 2}]
        self.assertEqual(validate_and_sort_segments(segments, 2), segments)

    def test_invalid_intervals_are_rejected(self):
        for segment in [{"start": 2, "end": 2}, {"start": 3, "end": 2},
                        {"start": -1, "end": 2}, {"start": 0, "end": 6}]:
            with self.subTest(segment=segment), self.assertRaises(ValueError):
                validate_and_sort_segments([segment], duration=5)

    def test_overlapping_segments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlapping"):
            validate_and_sort_segments([{"start": 0, "end": 3}, {"start": 2, "end": 4}], 5)

    def test_example_totals_match_kept_segments(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "cuts.example.json"
        data = json.loads(example.read_text(encoding="utf-8"))
        kept_total = 0
        for source in data["sources"]:
            kept = validate_and_sort_segments(source["keep_segments"], source["duration"])
            self.assertEqual(invert_to_keep_segments(source["cut_segments"], source["duration"]), kept)
            kept_seconds = sum(seg["end"] - seg["start"] for seg in kept)
            self.assertAlmostEqual(kept_seconds, source["summary"]["estimated_output_seconds"])
            kept_total += kept_seconds
        self.assertAlmostEqual(kept_total, data["summary"]["estimated_output_seconds"])


class RenderSafetyTests(unittest.TestCase):
    def test_source_overwrite_is_rejected_before_ffmpeg(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "original.mp4"
            source.write_bytes(b"original source placeholder")
            cuts = Path(temp) / "cuts.json"
            cuts.write_text(json.dumps({"sources": [{"input": str(source), "duration": 1,
                              "keep_segments": [{"start": 0, "end": 1}]}]}), encoding="utf-8")
            with patch("sys.argv", ["video-auto-cut-render", "--cuts-json", str(cuts),
                                    "--output", str(source)]), \
                 patch("video_auto_cut.render_video.subprocess.run") as run, \
                 contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                render_main()
            self.assertEqual(raised.exception.code, 1)
            run.assert_not_called()
            self.assertEqual(source.read_bytes(), b"original source placeholder")


class SubtitleTests(unittest.TestCase):
    def test_timestamp_rollover(self):
        self.assertEqual(format_srt_timestamp(59.9996), "00:01:00,000")
        self.assertEqual(format_srt_timestamp(3661.125), "01:01:01,125")

    def test_csv_preserves_japanese_commas_and_newlines(self):
        rows = [(0, 1.25, "こんにちは,世界\n次の行")]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "draft.csv"
            write_csv(rows, path)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(list(csv.reader(stream)),
                                 [["start_sec", "end_sec", "text"], ["0", "1.25", rows[0][2]]])

    def test_srt_contains_number_timing_and_text(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "draft.srt"
            write_srt([(0, 1.25, "こんにちは")], path)
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "1\n00:00:00,000 --> 00:00:01,250\nこんにちは\n\n")


if __name__ == "__main__":
    unittest.main()
