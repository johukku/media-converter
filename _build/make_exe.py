# -*- coding: utf-8 -*-
r"""PyInstaller で単体 exe 版（onedir）をビルドする（開発者用）

    python _build\make_exe.py

このツールは標準ライブラリと tkinter しか使わないので、出来上がりは 30MB 前後です。
FFmpeg は同梱せず、利用者の PC が初回起動時に取得します。

ドラッグ＆ドロップ（tkinterdnd2）は入っていれば取り込みます。
無い場合もビルドは通りますが、その exe では D&D が使えません。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BUILD_DIR)

EXE_NAME = "MediaConverter"           # ビルド中は ASCII 名（後で日本語名に変更）
FINAL_EXE = "メディアコンバーター.exe"
WORK = os.path.join(BUILD_DIR, "pyinstaller")
DIST = os.path.join(BUILD_DIR, "dist")


def get_version():
    with open(os.path.join(ROOT, "converter_app.py"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0"


ARGS = [
    "--noconfirm",
    "--clean",
    "--onedir",       # onefile は毎回の展開でウイルス対策ソフトに引っかかりやすい
    "--windowed",     # 進捗はアプリ内のログ欄に出すので、コンソールは要らない
    "--name", EXE_NAME,
    "--distpath", DIST,
    "--workpath", os.path.join(WORK, "build"),
    "--specpath", WORK,

    # 同じフォルダの自作モジュール
    "--hidden-import", "binaries",
    "--hidden-import", "convert",
    "--hidden-import", "media_info",

    # 使わない重いものを巻き込まないように明示的に外す
    "--exclude-module", "numpy",
    "--exclude-module", "torch",
    "--exclude-module", "PIL",
    "--exclude-module", "matplotlib",
    "--exclude-module", "test",
    "--exclude-module", "unittest",
]


def main():
    version = get_version()
    print("=" * 60)
    print(" exe ビルド開始   version {}".format(version))
    print("=" * 60)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller が入っていません。インストールします...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    args = list(ARGS)
    try:
        import tkinterdnd2  # noqa: F401

        # tkdnd の DLL と Tcl スクリプトを丸ごと持っていく
        args += ["--collect-all", "tkinterdnd2"]
        print("ドラッグ＆ドロップ: 取り込みます（tkinterdnd2）")
    except ImportError:
        print("!! tkinterdnd2 が無いので、ドラッグ＆ドロップ無しでビルドします。")
        print("   使えるようにするには: pip install tkinterdnd2")

    os.makedirs(DIST, exist_ok=True)
    cmd = [sys.executable, "-m", "PyInstaller"] + args + [
        os.path.join(ROOT, "converter_app.py")
    ]
    print(" ".join(cmd))
    print()
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print("\n!! ビルドに失敗しました (exit {})".format(rc))
        return rc

    out_dir = os.path.join(DIST, EXE_NAME)

    # exe を日本語名にリネーム（onedir なので _internal の探索には影響しない）
    src_exe = os.path.join(out_dir, EXE_NAME + ".exe")
    dst_exe = os.path.join(out_dir, FINAL_EXE)
    if os.path.exists(src_exe):
        if os.path.exists(dst_exe):
            os.remove(dst_exe)
        os.rename(src_exe, dst_exe)

    readme = os.path.join(BUILD_DIR, "readme_exe.txt")
    if os.path.exists(readme):
        shutil.copy(readme, os.path.join(out_dir, "はじめにお読みください.txt"))

    # アンインストーラ（johukku 製ツール共通の方式）と、README で案内している MIT のライセンス文
    for name in ("アンインストール.bat", "uninstall.ps1", "LICENSE"):
        shutil.copy(os.path.join(ROOT, name), os.path.join(out_dir, name))

    problems = check_uninstaller(out_dir)
    if problems:
        print("\n!! アンインストーラと配布物が食い違っています:")
        for problem in problems:
            print("   -", problem)
        return 1

    zip_path = _make_zip(out_dir, version)

    size = _dir_size(out_dir)
    print()
    print("=" * 60)
    print("完成: {}".format(out_dir))
    print("サイズ: {:,.1f} MB".format(size / (1024 * 1024)))
    print("配布用 ZIP: {}  ({:,.1f} MB)".format(
        zip_path, os.path.getsize(zip_path) / (1024 * 1024)))
    print("=" * 60)
    print()
    print("受け取った人は「{}」をダブルクリックするだけです。".format(FINAL_EXE))
    print("FFmpeg は初回起動時に自動で取得されます。")
    return 0


def check_uninstaller(out_dir):
    """アンインストーラが配布物と食い違っていないか確かめ、問題の一覧を返す。

    uninstall.ps1 は「配布物として知っているもの」しか消さない。
    出来上がったフォルダに一覧に無いものがあると、それが残ってフォルダが消えない。
    文字コードも、ずれると日本語が化けたり bat が途中で止まったりするので見ておく。
    """
    problems = []

    with open(os.path.join(ROOT, "uninstall.ps1"), "rb") as f:
        raw = f.read()
    if not raw.startswith(b"\xef\xbb\xbf"):
        problems.append("uninstall.ps1 に BOM がありません（PowerShell 5.1 が日本語を読み違えます）")
    text = raw.decode("utf-8-sig")
    known = set()
    for var in ("$KnownFiles", "$KnownDirs"):
        start = text.find(var + " = @(")
        end = text.find("\n)", start)
        block = text[start:end] if 0 <= start < end else ""
        known |= {line.strip().split("'")[1] for line in block.splitlines()
                  if line.strip().startswith("'")}
    for name in sorted(os.listdir(out_dir)):
        if name not in known:
            problems.append("uninstall.ps1 の $KnownFiles / $KnownDirs に {} がありません".format(name))

    with open(os.path.join(ROOT, "アンインストール.bat"), "rb") as f:
        bat = f.read()
    if any(b > 127 for b in bat):
        problems.append("アンインストール.bat は ASCII だけで書いてください")
    if bat.count(b"\n") != bat.count(b"\r\n"):
        problems.append("アンインストール.bat の改行を CRLF にしてください")

    return problems


def _make_zip(out_dir, version):
    """配布用 ZIP を作る。ファイル名は ASCII（環境によって文字化けするため）。"""
    zip_path = os.path.join(DIST, "MediaConverter-v{}-win64.zip".format(version))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.basename(out_dir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(out_dir):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.join(base, os.path.relpath(full, out_dir))
                z.write(full, rel)
    return zip_path


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


if __name__ == "__main__":
    sys.exit(main())
