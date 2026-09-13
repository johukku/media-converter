# -*- coding: utf-8 -*-
r"""FFmpeg を自動で取得して共有フォルダに置く。

    %LOCALAPPDATA%\johukku\bin\

配布物に同梱しない理由:
  - FFmpeg は GPL なので、同梱して配ると対応するソースの提供義務が生じる。
    利用者の PC が公式ビルドを直接取得する形なら、こちらは何も再配布しない。
  - 他の johukku 製ツール（メディアダウンローダー・Whisper 字幕作成ツール）と
    同じ場所を使うので、2 本目以降は取得を省ける。

メディアダウンローダーの binaries.py から yt-dlp の分を外したもの。
共有フォルダの決め方と ZIP の展開方法は、あちらと同じにしてある。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

# FFmpeg は yt-dlp 公式ビルドを使う（他の johukku 製ツールと同じものを共有するため）。
# shared 版は DLL が分かれる代わりに、静的版の半分以下で済む。
FFMPEG_URL = ("https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/"
              "ffmpeg-master-latest-win64-gpl-shared.zip")

USER_AGENT = "johukku-media-converter/1.0 (+https://johukku.pages.dev/)"

APPROX_SIZE = {
    "FFmpeg": "約 73 MB（展開後 約 180 MB）",
}

# GUI から起動したときにコンソール窓を出さないためのフラグ
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupError(Exception):
    """部品の取得に失敗した。メッセージはそのまま利用者に見せる。"""


def bin_dir():
    """バイナリの共有置き場。作れなければ例外にせず temp を返す。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "johukku", "bin")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.join(tempfile.gettempdir(), "johukku-bin")
        os.makedirs(folder, exist_ok=True)
    return folder


def ffmpeg_path():
    return os.path.join(bin_dir(), "ffmpeg.exe")


def ffprobe_path():
    return os.path.join(bin_dir(), "ffprobe.exe")


def _usable(path, min_size=64 * 1024):
    """置いてあるだけでなく、途中で切れていないかも軽く見る。"""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > min_size
    except OSError:
        return False


def ffmpeg_ok():
    """shared 版なので、exe だけでなく DLL が揃っているかも見る。"""
    if not (_usable(ffmpeg_path()) and _usable(ffprobe_path())):
        return False
    try:
        names = os.listdir(bin_dir())
    except OSError:
        return False
    return any(n.startswith("avcodec-") and n.endswith(".dll") for n in names)


def missing():
    """まだ無い部品の名前を並べて返す。空リストならすぐ使える。"""
    return [] if ffmpeg_ok() else ["FFmpeg"]


# ---------------------------------------------------------------- 取得


def _download(url, dest, on_progress=None, label=""):
    """url を dest に保存する。途中経過は on_progress(受信バイト, 全体バイト, 名前)。

    書きかけを残さないよう、同じフォルダに .part で受けてから差し替える。
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = res.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total, label)
    except urllib.error.HTTPError as e:
        _remove(tmp)
        raise SetupError(
            "{} の取得に失敗しました（HTTP {}）。\n"
            "時間をおいて試すか、ネットワークの設定を確認してください。".format(label, e.code))
    except urllib.error.URLError as e:
        _remove(tmp)
        raise SetupError(
            "{} の取得に失敗しました。\n"
            "インターネットに接続できていないか、\n"
            "ウイルス対策ソフトや社内プロキシに遮断された可能性があります。\n\n"
            "詳細: {}".format(label, e.reason))
    except OSError as e:
        _remove(tmp)
        raise SetupError("{} の保存に失敗しました。\n\n詳細: {}".format(label, e))

    try:
        os.replace(tmp, dest)
    except OSError as e:
        _remove(tmp)
        raise SetupError(
            "{} を置き換えられませんでした。\n"
            "ツールを二重に起動していないか確認してください。\n\n詳細: {}".format(label, e))


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def ensure_ffmpeg(on_progress=None):
    """ffmpeg.exe / ffprobe.exe が無ければ ZIP を取得して取り出す。"""
    if ffmpeg_ok():
        return ffmpeg_path()

    tmpdir = tempfile.mkdtemp(prefix="johukku-ffmpeg-")
    zip_path = os.path.join(tmpdir, "ffmpeg.zip")
    try:
        _download(FFMPEG_URL, zip_path, on_progress, "FFmpeg")
        if on_progress:
            on_progress(-1, -1, "FFmpeg を展開しています")
        _extract_ffmpeg(zip_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return ffmpeg_path()


def ensure_all(on_progress=None):
    ensure_ffmpeg(on_progress)


def _extract_ffmpeg(zip_path):
    """ZIP の bin/ の中身（exe と DLL）を平らに置く。

    shared 版なので exe だけ取り出しても動かない。DLL も同じ場所に要る。
    """
    target = bin_dir()
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = []
            for name in z.namelist():
                parts = name.replace("\\", "/").split("/")
                if len(parts) >= 2 and parts[-2] == "bin" and parts[-1]:
                    members.append((name, parts[-1]))
            names = {base for _, base in members}
            if not {"ffmpeg.exe", "ffprobe.exe"} <= names:
                raise SetupError(
                    "FFmpeg の ZIP に想定したファイルが入っていませんでした。\n"
                    "配布元の構成が変わった可能性があります。")
            for member, base in members:
                dest = os.path.join(target, base)
                tmp = dest + ".part"
                with z.open(member) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                os.replace(tmp, dest)
    except zipfile.BadZipFile:
        raise SetupError(
            "FFmpeg のダウンロードが途中で壊れていました。\n"
            "もう一度お試しください。")
    except OSError as e:
        raise SetupError("FFmpeg の展開に失敗しました。\n\n詳細: {}".format(e))


# ---------------------------------------------------------------- 実行


def run_capture(args, timeout=None):
    """コンソール窓を出さずに実行し、(終了コード, 標準出力, 標準エラー) を返す。"""
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)

    def decode(raw):
        return (raw or b"").decode("utf-8", "replace")

    return p.returncode, decode(p.stdout), decode(p.stderr)


def ffmpeg_version():
    """FFmpeg のバージョン文字列。取れなければ None。"""
    if not _usable(ffmpeg_path()):
        return None
    code, out, err = run_capture([ffmpeg_path(), "-hide_banner", "-version"], timeout=30)
    if code != 0:
        return None
    head = (out or err).splitlines()
    if not head:
        return None
    # "ffmpeg version N-xxxxx-gxxxxxxx Copyright ..." の 3 語目までを見せる
    words = head[0].split()
    return words[2] if len(words) > 2 else head[0]


_filters_cache = {}


def has_filter(name):
    """FFmpeg のビルドにそのフィルタが入っているか。

    HDR のトーンマップ（zscale）のように、ビルドによって
    入っていないものがあるので、使う前に確かめる。
    """
    key = os.path.abspath(ffmpeg_path())
    table = _filters_cache.get(key)
    if table is None:
        code, out, err = run_capture([ffmpeg_path(), "-hide_banner", "-filters"],
                                     timeout=30)
        table = set()
        if code == 0:
            for line in (out or "").splitlines():
                parts = line.split()
                # " T.. scale  V->V  Scale the input video size." の 2 語目
                if len(parts) >= 2 and not line.startswith("Filters:"):
                    table.add(parts[1])
        _filters_cache[key] = table
    return name in table


if __name__ == "__main__":
    # 動作確認用: python binaries.py で取得まで一通り試せる
    def _show(done, total, label):
        if done < 0:
            print("\n  {}".format(label))
        elif total > 0:
            print("\r  {} {:5.1f}%".format(label, done * 100.0 / total), end="")
        else:
            print("\r  {} {:,} バイト".format(label, done), end="")

    print("置き場所: {}".format(bin_dir()))
    print("不足: {}".format("、".join(missing()) or "なし"))
    try:
        ensure_ffmpeg(_show)
    except SetupError as e:
        print("\n失敗: {}".format(e))
        sys.exit(1)
    print("\nFFmpeg {}".format(ffmpeg_version()))
    print("zscale フィルタ: {}".format("あり" if has_filter("zscale") else "なし"))
