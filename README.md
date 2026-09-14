# YouTube動画 カット編集Skill

Claude Codeの `video-cut` Skill経由で利用することを基本方針とした、動画の下編集ツールです。
自然言語で依頼し、`input/` に置いた動画の無音検出・カット・複数動画の結合・字幕の下書き作成を進めます。

普段の操作はClaudeへの依頼を想定しています。Pythonの実装は独立しており、動作確認や個別処理のためにCLIからも実行できます。

以下の手順では元動画を保持し、生成物を `output/` に保存します。

## 解決したい課題

YouTube動画の下編集で繰り返す無音カットと字幕の下書き作成を支援します。
毎回コマンドや処理順を組み立てる負担を減らすため、操作手順をSkillにまとめています。
検出結果をClaudeと確認し、必要に応じてカット量や結合順を調整してからドラフトを作る使い方を想定しています。

## 利用実績

約18分の動画では、従来、手作業のカット編集に約40〜60分かかっていました。本ツールでは、GPU搭載のゲーミングPCで自動カット処理と字幕処理が約3分で完了しました（処理後の確認・修正時間は別）。

実際の利用では、追加でカットする箇所も少なく、下編集の負担を軽減できました。また、生成した字幕を編集ソフトで確認しながらカットを調整でき、字幕作成もフォントやサイズの調整を中心に進められました。

上記は開発者自身の利用例です。処理時間や必要な修正量は、素材や実行環境によって異なります。

## 主な機能

- FFmpegによる無音区間の検出
- 編集可能なカットリスト（`cuts.json`）の生成
- 複数動画のカットと結合
- Whisperによる日本語文字起こし
- CSVおよびSRT字幕の出力

## 利用準備

- Claude Code（Skill経由で利用する場合）
- Python 3.10以上
- FFmpeg（`ffmpeg` と `ffprobe`）
- `faster-whisper`（文字起こしを行う場合のみ）

macOSでFFmpegを導入する例：

```bash
brew install ffmpeg
```

プロジェクトのルート（このREADMEのあるディレクトリ）で仮想環境を作り、インストールします。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

文字起こしも利用する場合は、同じ仮想環境で追加依存をインストールします。

```bash
python -m pip install -e '.[transcribe]'
```

初めて使用するWhisperモデルはダウンロードが必要です。既定は日本語・CPU・`small`モデルです。
Skillから呼ぶコマンドもこの仮想環境にインストールされます。Claudeがコマンドを実行する際も、プロジェクトのルートで `source .venv/bin/activate` を実行してから利用してください。

## 基本の使い方：Claude CodeのSkillから利用する

### 1. 動画を配置する

編集したい動画を `input/` に置きます。

```text
input/
├── 01_intro.mp4
└── 02_main.mp4
```

入力順を省略した場合、動画はファイル名順に結合されます。順番が重要なら、ファイル名を `01_`、`02_` のようにゼロ埋めするか、依頼時に結合順を伝えてください。

### 2. Claudeに依頼する

このプロジェクトをClaude Codeで開き、[video-cutの手順](.claude/skills/video-cut/SKILL.md)に沿って処理するよう依頼します。たとえば、最初は次のように検出と確認まで依頼できます。

```text
video-cut Skillを使って、input/の動画の無音を検出して。
プロジェクトの.venvを使い、書き出す前に結合順とカット予定時間を教えて。
```

内容を確認した後の依頼例：

```text
その内容でoutput/draft.mp4を書き出して。
```

動画の順番を指定する例：

```text
video-cut Skillを使って、input/01_intro.mp4、input/02_main.mp4の順に
無音をカットして1本にまとめて。発話の前後には少し余白を残して。
```

字幕も必要な場合：

```text
video-cut Skillを使って、output/draft.mp4を日本語で文字起こしして。
CSVと、DaVinci Resolve向けのSRT字幕をoutput/に作って。
```

### 3. 出力を確認する

- `output/cuts.json`：結合順、残す区間、推定カット時間
- `output/draft.mp4`：無音カット・結合後の確認用動画
- `output/draft.csv` / `output/draft.srt`：依頼した場合に生成する文字起こし・字幕

ドラフトを再生して、語尾や必要な間が切れていないかを確認します。切りすぎていたら「結合順はそのままで、短い無音は残す設定に変えて再検出して」と依頼し、確認後に再度書き出してください。
同じ出力先で再実行すると既存の生成物は上書きされます。

## SkillとPythonの役割分担

処理は「ClaudeがSkillの手順を読む → CLIを実行する → PythonがFFmpegやWhisperを呼ぶ」という流れです。

- `.claude/skills/video-cut/SKILL.md`：環境確認、検出、結果の報告、調整、書き出しの進め方を定義
- `src/video_auto_cut/`：無音検出、区間計算、動画書き出し、文字起こしの実処理を担当
- `pyproject.toml`：依存関係と、CLIから呼び出すPython関数を定義

Skillで操作の流れをまとめ、実処理をPythonに分けることで、区間計算などをClaudeの会話とは独立してテスト・再利用できます。
無音判定は映像の意味をClaudeが判断する処理ではなく、FFmpegによる音量と継続時間の判定です。

- 検出と書き出しを分け、再エンコードの前にJSONで編集内容を確認・調整する
- 発話の前後に余白を残し、語尾などの切りすぎを抑える
- 書き出し前に区間の重複・範囲を検証し、元動画と出力先が同じなら停止する
- 文字起こしを任意の追加機能とし、無音カットにはPython追加パッケージを要求しない

## 補足：CLIから個別に実行する

動作確認や手動調整に使うための手順です。以下は仮想環境を有効にした状態で、プロジェクトのルートから実行してください。

### 1. 無音を検出する

`input/` 直下の対応動画（`.mp4`、`.mov`、`.mkv`、`.m4v`、`.avi`）をファイル名順で処理する場合：

```bash
video-auto-cut-detect \
  --output-json output/cuts.json
```

動画と結合順を明示する場合：

```bash
video-auto-cut-detect \
  --input input/01_intro.mp4 input/02_main.mp4 \
  --output-json output/cuts.json
```

デフォルトでは、音量が `-30 dB` 未満の状態が `0.45秒` 以上続く箇所を無音候補とし、発話の前後に `0.15秒` の余白を残します。

### 2. カット内容を確認する

生成された `output/cuts.json` の次の項目を確認します。

- `summary`: 全動画の長さと推定カット時間
- `sources`: 入力動画と結合順
- `sources[i].cut_segments`: 削除する区間
- `sources[i].keep_segments`: 残す区間

[examples/cuts.example.json](examples/cuts.example.json) に2本の動画を結合するサンプルがあります。パスと時間は架空で、対応する動画は同梱していません。JSON内の相対パスはコマンド実行時のディレクトリを基準に解決します。

手動調整では `sources[i].keep_segments` を編集してください。書き出しはこの区間を使用し、`cut_segments` や `summary` は自動更新しません。

切りすぎている場合は、いきなりドラフトを書き出さず、検出条件を厳しくして再実行してください。

```bash
video-auto-cut-detect \
  --output-json output/cuts.json \
  --silence-db -35 \
  --min-silence-duration 0.7 \
  --padding 0.2
```

検出条件を変えても、既存のドラフト動画は更新されません。確認後に次の書き出しを再実行します。動画や順番を `--input` で指定した場合は、再検出時にも同じ指定を付けてください。

### 3. ドラフト動画を生成する

```bash
video-auto-cut-render \
  --cuts-json output/cuts.json \
  --output output/draft.mp4
```

`output/draft.mp4` は実行するたびに上書きされます。入力動画は変更されません。

### 4. 文字起こしや字幕を作る（任意）

CSVを生成：

```bash
video-auto-cut-transcribe \
  output/draft.mp4
```

CSVとSRTを両方生成：

```bash
video-auto-cut-transcribe \
  output/draft.mp4 \
  --format both
```

文字起こしの精度を上げたい場合は `--model medium` または `--model large-v3` を指定できますが、処理時間と必要なメモリは増えます。

### CLIの主な調整項目

最初の3項目は `video-auto-cut-detect`、残り2項目は `video-auto-cut-render` のオプションです。各コマンドの `--help` で詳細を確認できます。

| オプション | 初期値 | 内容 |
| --- | ---: | --- |
| `--silence-db` | `-30` | この音量より小さい部分を無音として扱う |
| `--min-silence-duration` | `0.45` | カット候補にする無音の最短秒数 |
| `--padding` | `0.15` | カットの前後に残す余白（秒） |
| `--crf` | `18` | ドラフトの画質。小さいほど高画質・大容量 |
| `--preset` | `veryfast` | エンコード速度と圧縮効率のバランス |

## ディレクトリ構成

```text
カット編集/                   ← プロジェクト全体
├── README.md                 ← 目的・導入方法・使い方
├── pyproject.toml            ← Pythonの対応バージョン・依存・コマンド設定
├── .gitignore                ← Gitに含めないファイルの指定
├── src/                      ← アプリ本体のコード
│   └── video_auto_cut/       ← Pythonから読み込むパッケージ
│       ├── __init__.py       ← パッケージであることを示す
│       ├── detect_silence.py ← 無音を検出し、カットリストを作る
│       ├── render_video.py   ← カットリストに従って動画を書き出す
│       └── transcribe.py     ← 音声を文字起こしする
├── tests/
│   └── test_segments.py      ← 区間計算や上書き防止などのテスト
├── examples/
│   └── cuts.example.json     ← カットリストの見本
├── input/                    ← 自分が用意した元動画
│   └── .gitignore
├── output/                   ← 処理で生成した動画・JSON・字幕
│   └── .gitignore
└── .claude/skills/video-cut/
    └── SKILL.md              ← Claude Codeでの基本操作を定義する手順
```

`input/` と `output/` はディレクトリ維持用の `.gitignore` だけをGitで管理し、動画や生成物はコミットしない構成です。

## テスト

パッケージをインストールした後、標準ライブラリの `unittest` で実行します。

```bash
python -m unittest discover -s tests -v
```

余白処理、残す区間の計算、不正区間の拒否、元動画への上書き防止、サンプルJSONの整合性、CSV/SRT出力を検証します。
これらのテストにはFFmpegやWhisperモデルは不要です。実際の動画処理や音声認識の品質検証は別途必要です。

## 注意事項

- ドラフト生成はフレーム精度で切るため、映像をH.264、音声をAACで再エンコードします。
- 複数動画の解像度などを自動で統一する処理はありません。結合する素材の形式を揃えてください。
- 音声トラックがない動画は、現在のカット処理ではFFmpegエラーになる可能性があります。
- 無音判定は内容を理解しているわけではありません。短い間や演出上必要な無音まで削ることがあるため、`cuts.json` とドラフトの確認は必須です。
- 最終的なカット、字幕調整、音量調整はDaVinci Resolveなどの編集ソフトで行うことを想定しています。
