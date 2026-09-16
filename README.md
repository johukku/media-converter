# メディアコンバーター

手元の動画・音声を変換する Windows 用の GUI ツールです。
実際の処理は [FFmpeg](https://ffmpeg.org/) が行い、**初回起動時に自動で取得します**（同梱していません）。

**[→ ダウンロード（最新版）](https://github.com/johukku/media-converter/releases/latest)**　|　
**[→ 使い方](https://johukku.com/converter/)**

---

## 特徴

- **変換せずに済むなら変換しない** — 入れ物（コンテナ）を詰め替えるだけで済む場合は
  再エンコードしません。数秒で終わり、画質はまったく落ちません
- **Python のインストール不要** — exe をダブルクリックするだけ
- **サイズ指定の圧縮** — 「25 MB に収めたい」と指定できます。2 パスで回すので指定どおりに収まります
- **GIF は 2 段階** — 色を調べてから当てるので、にじんだ GIF になりません
- **GPU 自動判定** — NVENC / QSV / AMF を実際に試してから使い、駄目なら CPU に落とします
- **まとめて処理** — 複数ファイルを追加して連続処理、途中で中止も可能
- **部品を共有** — `Whisper 字幕作成ツール` など他の johukku 製ツールと
  同じ場所に FFmpeg を置くため、2 本目からは取得を省けます

## 動作環境

| | |
|---|---|
| OS | Windows 10 / 11（64bit） |
| 必要なもの | インターネット接続（初回のみ） |
| 空き容量 | 約 300 MB（FFmpeg を置くため） |

## 使い方

1. [Releases](https://github.com/johukku/media-converter/releases/latest) から ZIP を取得して展開
2. `メディアコンバーター.exe` をダブルクリック
3. 初回だけ、部品（FFmpeg）の取得を確認されるので「はい」を押す（数分かかります）

## できること

| 種類 | 内容 |
|---|---|
| MP4 に変換（互換重視） | `.mov` / `.mkv` / `.webm` / `.avi` などを MP4 に。中身がそのまま入れられるならコピーだけ |
| サイズを指定して圧縮 | 目標サイズ（MB）を指定。音声分を差し引いて映像のビットレートを決め、2 パスで書き出す |
| 音声だけ取り出す | mp3 / m4a / wav。元が同じ形式ならそのまま取り出す（無劣化） |
| GIF にする | パレット生成 → 適用の 2 段階。幅と fps を選べる |

共通のオプションとして、解像度の上限、範囲の切り出し（開始・終了）、
画質と速度の選択（画質優先 / バランス / 速度優先）があります。

## 「変換しない」という判断について

同じ H.264 の動画でも、`.mov`・`.mkv`・`.mp4` は入れ物が違うだけで中身は同じことがあります。
このツールは ffprobe で中身を調べ、そのまま MP4 に入れられる場合は
`-c copy`（コピー）で詰め替えます。

- 再エンコードしないので**画質の劣化がゼロ**
- 数百 MB の動画でも**数秒**で終わる

音声だけが MP4 に入れられない形式（Opus など）のときは、
映像はコピーしたまま音声だけを AAC に変換します。

変換前に、画面に「この設定なら: 映像はそのままコピーします（再エンコードなし・無劣化）」
のように、これから何をするかが出ます。

## 画質と速度

| 選択 | 内容 |
|---|---|
| 画質優先（CPU・遅い） | libx264 / CRF 18 / preset slow |
| バランス（おすすめ） | GPU が使えれば NVENC 等（高品質設定）、無ければ libx264 / CRF 20 |
| 速度優先（GPU） | GPU 優先・軽い設定。無ければ libx264 / veryfast |

GPU のエンコーダは「FFmpeg が対応している」だけでは足りず、
ドライバの版が合わないと開けません。実際に 1 コマだけ試してから採用します。

## FFmpeg について

FFmpeg は同梱せず、[yt-dlp のビルド](https://github.com/yt-dlp/FFmpeg-Builds)から自動で取得し、
`%LOCALAPPDATA%\johukku\bin` に置きます。

- FFmpeg は GPL のため、同梱して配布するとソース提供の義務が生じます。
  利用者の PC が公式の配布元から直接取得する形なら、こちらは何も再配布しません
- 他の johukku 製ツールと同じ場所を使うので、すでにあれば取得は省かれます

## 開発

```
python converter_app.py          # GUI
python convert.py <file> mp4     # 変換だけを単体で
python media_info.py <file>      # 中身を調べるだけ
python _build/make_exe.py        # 配布用の exe と ZIP を作る
```

| ファイル | 役割 |
|---|---|
| `converter_app.py` | 画面（tkinter） |
| `convert.py` | 何をするかの判断（plan）と FFmpeg の実行 |
| `media_info.py` | ffprobe で中身を調べる |
| `binaries.py` | FFmpeg の取得と共有フォルダの管理 |

## アンインストール

`アンインストール.bat` を実行してください。
配布物と設定を削除します。他のツールと共有している FFmpeg は、消すかどうかを確認します。

## ライセンス

MIT License（[LICENSE](LICENSE)）

内部で利用しているソフトウェア（同梱はしていません）:

- FFmpeg — GPL v3 / https://ffmpeg.org/

## 関連

- [Whisper 字幕作成ツール](https://github.com/johukku/whisper-subtitle-tool) — 動画から字幕を作る
- [メディアダウンローダー](https://github.com/johukku/media-downloader) — URL から動画・音声を保存する
- [字幕エディター](https://github.com/johukku/subtitle-editor) — 字幕を、音を聞きながら直して、保存するか動画に焼き込む
