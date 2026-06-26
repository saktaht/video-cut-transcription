---
name: video-cut
description: Detect silent gaps across one or more videos in input/ with ffmpeg and join the kept parts into one tempo-cut draft at output/draft.mp4, plus an editable cuts.json edit list. Also generates a timestamped transcript CSV (start_sec, end_sec, text) from a video using faster-whisper. Use when the user asks to remove silence/dead air, auto-cut footage for pacing, combine multiple clips (e.g. separate match/take recordings) into one edited video, generate a draft edit before finishing in DaVinci Resolve, or transcribe a video to CSV with timestamps. Never modifies any original source file.
---

# video-cut

## いつ使うか
- 「無音をカットして」「テンポよく詰めて」「仮編集を作って」と言われたとき
- `input/` 以下の**1本または複数本**の動画から、無音カット済みで1本に結合された確認用動画（`output/draft.mp4`）を作りたいとき（例：試合ごと・テイクごとに分かれた動画を1本のYouTube動画にまとめる）
- 最終調整はDaVinci Resolveなど別ツールで行う前提で、下処理（無音探索・カットリスト作成・結合）だけを高速にやりたいとき

## 絶対に守ること
- 元動画（`input/` 以下のすべてのファイル）は**絶対に上書き・変更しない**。書き込み先は常に `output/` 以下。
- `cut_by_segments.py` は `cuts.json` 内のどの元動画パスも出力パスと一致しないかを起動時にチェックして、一致したらエラーで停止する。
- 切りすぎ・やりすぎない。デフォルトは安全寄り（無音0.45秒以上のみカット、前後0.15秒は残す）。迷ったらカット量を減らす方向に倒す。

## 結合順について
- `output/draft.mp4` には、`cuts.json` の `sources` 配列に並んでいる順番で各動画が結合される。
- `--input` を省略すると、`input/` 直下の動画ファイルを**ファイル名のアルファベット順**で自動検出する。`match2.mp4` と `match10.mp4` のような番号付きファイルは文字列順だと意図しない順序になりやすいので、`01_match1.mp4` `02_match2.mp4` のようにゼロ埋めした連番にするか、後述の通り `--input` で明示的に順番を指定する。
- 結合順を明示したい場合は、検出ステップで `--input` に望む順番でファイルを列挙する（下記コマンド例参照）。

## 手順

1. **環境確認**
   ```bash
   which ffmpeg ffprobe
   ```
   どちらか無ければ、ここで止めてユーザーに `brew install ffmpeg` 等を案内する。

2. **無音検出 → カットリスト作成（複数動画対応）**

   `input/` 内の動画をファイル名順で自動結合する場合:
   ```bash
   python3 .claude/skills/video-cut/scripts/detect_silence.py \
     --output-json output/cuts.json \
     --silence-db -30 \
     --min-silence-duration 0.45 \
     --padding 0.15
   ```

   結合順を明示したい場合（このファイル列挙順がそのまま最終動画の順番になる）:
   ```bash
   python3 .claude/skills/video-cut/scripts/detect_silence.py \
     --input input/match1.mp4 input/match2.mp4 input/match3.mp4 \
     --output-json output/cuts.json \
     --silence-db -30 \
     --min-silence-duration 0.45 \
     --padding 0.15
   ```
   - `output/cuts.json` には動画ごとに `sources[i]` として、検出された無音区間（`silence_segments_raw`）、実際にカットする区間（`cut_segments`）、残す区間（`keep_segments`）が入る。トップレベルの `summary` に全体の推定カット秒数・出力秒数が出る。
   - 実行後、`summary` と各 `sources[i].summary` を読んでユーザーに「何本の動画を」「何秒中何秒カットする予定か」を必ず報告する。カット量が動画全体の30%を超えるなど明らかに過剰なら、`--min-silence-duration` を上げる／`--silence-db` を下げる（しきい値を厳しくする）よう提案してから次に進む。

3. **必要なら `output/cuts.json` をユーザーと一緒に確認・調整**
   - 各 `sources[i].keep_segments` を手動で編集してから次のステップに進んでも良い（このJSONは人間が読み書きできる形にしてある）。
   - `sources` 配列内の要素の並び順を入れ替えれば結合順も変わる。

4. **ドラフト動画を書き出す**
   ```bash
   python3 .claude/skills/video-cut/scripts/cut_by_segments.py \
     --cuts-json output/cuts.json \
     --output output/draft.mp4
   ```
   - 入力ファイルは `cuts.json` の `sources` から読むので、このコマンドに `--input` は不要。
   - フレーム精度のカットのため再エンコード（libx264 + aac）する。実行ごとに `output/draft.mp4` は上書きされる（これは意図した動作）。
   - 完了したらファイルサイズと長さをユーザーに報告する（`ffprobe -show_entries format=duration output/draft.mp4` 等）。

5. **完了報告**
   - 何本の動画を結合したか、何秒 → 何秒になったか
   - カット件数
   - 次の選択肢（DaVinciへ持っていく／パラメータを変えて再生成／文字起こしCSVを生成する）を提示する

6. **（任意）文字起こしCSVを生成する**
   - 初回のみ依存パッケージを入れる: `pip3 install faster-whisper`
   ```bash
   python3 .claude/skills/video-cut/scripts/transcribe_to_csv.py output/draft.mp4
   ```
   - `output/draft.csv` に `start_sec,end_sec,text` の列で、秒数とその区間の発話テキストを対応させて出力する（既定はフレーズ単位、`--granularity word` で単語単位）。
   - 字幕の下書きや、カット見直しのための文字起こし参照に使う。モデルは既定 `small`。精度を上げたい場合は `--model medium` や `large-v3` を指定（その分実行時間は伸びる）。

## 失敗時の確認項目
- `ffmpeg`/`ffprobe` が `PATH` にあるか（`which ffmpeg ffprobe`）
- 入力動画が実際に存在するか、パスが正しいか
- 自動検出（`--input` 省略時）で意図しないファイルや順番になっていないか → `output/cuts.json` の `sources` の並びを確認し、必要なら `--input` で明示的に再指定する
- `detect_silence.py` が `silence_segments_raw: []` を返した場合 → `--silence-db` が低すぎる（厳しすぎる）か、動画が常に無音閾値以上の音量で鳴っている可能性。`-30dB` を `-25dB` 寄りに緩めて再試行する
- カットが多すぎ/少なすぎる場合 → `--min-silence-duration` と `--padding` を調整して `detect_silence.py` だけ再実行（`cut_by_segments.py` は再実行しなくてよい）
- `cut_by_segments.py` が ffmpeg エラーで落ちる場合 → 元動画の音声トラックが存在するか確認（無音動画や音声トラックなしのファイルだと `atrim`/`concat` が失敗する）
- `cut_by_segments.py` が `cuts.json is invalid: ...` で落ちる場合 → どの `source` のどの区間が原因か、エラーメッセージに出ている内容を修正する（重なり・`end <= start` など。並び順自体は自動でソートされるので心配しなくてよい）
- `cut_by_segments.py` が `no keep_segments are skipped entirely` と警告する場合 → その動画全体が無音判定されている。`--silence-db` か `--min-silence-duration` を見直す

## ファイル構成
```
.claude/skills/video-cut/
  SKILL.md
  scripts/
    detect_silence.py     # 1本以上の動画の無音検出 → output/cuts.json を生成
    cut_by_segments.py    # output/cuts.json の sources を結合順に並べ、output/draft.mp4 を生成
    transcribe_to_csv.py  # 動画/音声を文字起こしし、秒数とテキストを対応させたCSVを生成（任意・faster-whisper要）
```
