# -*- coding: utf-8 -*-
"""メディアコンバーター (tkinter GUI)

使い方:
    python converter_app.py
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "メディアコンバーター"
APP_VERSION = "1.0"

IS_FROZEN = getattr(sys, "frozen", False)

# exe 化した場合は exe のあるフォルダ、通常実行なら .py のあるフォルダ
APP_DIR = (os.path.dirname(sys.executable) if IS_FROZEN
           else os.path.dirname(os.path.abspath(__file__)))
if not IS_FROZEN and APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _fatal(title, message):
    """起動できないレベルのエラーをダイアログで知らせて終了する。"""
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(message)
    sys.exit(1)


try:
    import binaries
    import convert
    import media_info
except Exception as _e:
    _fatal(
        APP_TITLE,
        "必要なファイルが読み込めませんでした。\n\n"
        + ("・同梱ファイルが壊れている可能性があります。\n"
           "　配布元から入手し直してください。\n\n"
           if IS_FROZEN else
           "・converter_app.py と同じフォルダに\n"
           "　binaries.py / convert.py / media_info.py があるか確認してください。\n\n")
        + "詳細: {}: {}".format(type(_e).__name__, _e),
    )


# ドラッグ＆ドロップ（tkinterdnd2 があれば有効化）
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    ROOT_CLASS = TkinterDnD.Tk
    HAS_DND = True
except Exception:
    ROOT_CLASS = tk.Tk
    DND_FILES = None
    HAS_DND = False


MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm", ".ts", ".m2ts",
    ".mpg", ".mpeg", ".m4v", ".3gp", ".ogv",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff",
}

FILE_TYPES = [
    ("動画・音声", " ".join("*" + e for e in sorted(MEDIA_EXTS))),
    ("すべてのファイル", "*.*"),
]

TARGET_PRESETS = ["8", "10", "25", "50", "100", "200"]

HEIGHTS = [("そのまま", 0), ("1080p まで", 1080), ("720p まで", 720), ("480p まで", 480)]

GIF_WIDTHS = [("320 px", 320), ("480 px", 480), ("640 px", 640)]
GIF_FPS = [("8 fps", 8), ("12 fps", 12), ("15 fps", 15), ("20 fps", 20)]


def _settings_path():
    """設定ファイルの保存先。書き込めない場所に置かれても動くようにする。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "johukku", "media-converter")
    try:
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "settings.json")
    except OSError:
        return os.path.join(APP_DIR, "settings.json")


SETTINGS_PATH = _settings_path()


def default_outdir():
    home = os.path.expanduser("~")
    for name in ("Videos", "Downloads", "Desktop"):
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return home


def parse_time(text):
    """"90" / "1:30" / "0:01:30.5" を秒に直す。空欄なら None。"""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(text)
    total = 0.0
    for part in parts:
        part = part.strip()
        if part and not part.replace(".", "", 1).isdigit():
            raise ValueError(text)
        total = total * 60 + (float(part) if part else 0.0)
    return total


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.worker = None
        self.job = None
        self.busy = False
        self.stop_all = False
        self.files = []            # {"path", "info", "error"}
        self.settings = self._load_settings()
        self.started_at = 0.0

        s = self.settings
        self.var_outdir = tk.StringVar(value=s.get("outdir", default_outdir()))
        self.var_same_dir = tk.BooleanVar(value=s.get("same_dir", True))
        self.var_task = tk.StringVar(value=s.get("task", "mp4"))
        self.var_quality = tk.StringVar(value=s.get("quality", "balance"))
        self.var_maxh = tk.IntVar(value=int(s.get("max_height", 0)))
        self.var_target = tk.StringVar(value=str(s.get("target_mb", "25")))
        self.var_afmt = tk.StringVar(value=s.get("audio_format", "mp3"))
        self.var_gifw = tk.IntVar(value=int(s.get("gif_width", 480)))
        self.var_giffps = tk.IntVar(value=int(s.get("gif_fps", 12)))
        self.var_copy = tk.BooleanVar(value=s.get("prefer_copy", True))
        self.var_autoscale = tk.BooleanVar(value=s.get("auto_scale", True))
        self.var_start = tk.StringVar(value="")
        self.var_end = tk.StringVar(value="")
        self.var_status = tk.StringVar(value="準備中...")
        self.var_parts = tk.StringVar(value="")
        self.var_preview = tk.StringVar(value="ファイルを追加すると、ここに変換の内容が出ます。")

        self.root.title("{} v{}".format(APP_TITLE, APP_VERSION))
        self.root.geometry("{}x{}".format(*self._initial_size()))
        self.root.minsize(760, 660)

        self._build_ui()
        self._update_states()
        self._watch_options()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._pump)
        self.root.after(200, self._first_run)

    def _initial_size(self):
        """保存された窓の大きさ。画面からはみ出すなら、収まるように縮める。"""
        try:
            width = int(self.settings.get("width", 860))
            height = int(self.settings.get("height", 820))
        except (TypeError, ValueError):
            width, height = 860, 820
        width = min(width, self.root.winfo_screenwidth() - 40)
        height = min(height, self.root.winfo_screenheight() - 80)
        return max(width, 760), max(height, 660)

    # ------------------------------------------------------------ 画面

    def _build_ui(self):
        pad = {"padx": 8, "pady": 5}
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # --- 下段（いちばん最初に置く）---
        # pack は窓の高さが足りないとき、後から置いたものから削っていく。
        # ここを最後に置くと、押してほしいボタンが真っ先に消える。
        bottom = ttk.Frame(outer)
        bottom.pack(side="bottom", fill="x", **pad)
        ttk.Label(bottom, textvariable=self.var_parts, foreground="#666").pack(side="left")
        self.btn_reinstall = ttk.Button(bottom, text="部品を入れ直す", width=14,
                                        command=self.on_reinstall)
        self.btn_reinstall.pack(side="right")

        # --- 1. ファイル ---
        f_files = ttk.LabelFrame(outer, text="1. 変換するファイル")
        f_files.pack(fill="both", expand=True, **pad)

        body = ttk.Frame(f_files)
        body.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        wrap = ttk.Frame(body)
        wrap.pack(side="left", fill="both", expand=True)
        self.listbox = tk.Listbox(wrap, height=6, selectmode="extended",
                                  exportselection=False, activestyle="none")
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        xsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.listbox.xview)
        self.listbox.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._refresh_preview())

        if HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self.on_drop)

        btns = ttk.Frame(body)
        btns.pack(side="left", fill="y", padx=(8, 0))
        self.btn_add = ttk.Button(btns, text="追加...", width=12, command=self.on_add)
        self.btn_add.pack(fill="x", pady=2)
        self.btn_add_folder = ttk.Button(btns, text="フォルダ追加", width=12,
                                         command=self.on_add_folder)
        self.btn_add_folder.pack(fill="x", pady=2)
        ttk.Button(btns, text="選択を削除", width=12,
                   command=self.on_remove).pack(fill="x", pady=2)
        ttk.Button(btns, text="クリア", width=12,
                   command=self.on_clear).pack(fill="x", pady=2)

        hint = ("ここにファイルをドラッグ＆ドロップできます" if HAS_DND
                else "「追加...」または「フォルダ追加」でファイルを選んでください")
        ttk.Label(f_files, text=hint, foreground="#666").pack(anchor="w", padx=10,
                                                              pady=(0, 8))

        # --- 2. 変換の種類 ---
        f_task = ttk.LabelFrame(outer, text="2. 何をするか")
        f_task.pack(fill="x", **pad)

        row = ttk.Frame(f_task)
        row.pack(fill="x", padx=8, pady=(8, 2))
        for label, key in convert.TASKS:
            ttk.Radiobutton(row, text=label, value=key, variable=self.var_task,
                            command=self._on_task_change).pack(side="left", padx=(0, 14))

        # 種類ごとの細かい指定。選ばれたものだけ見せる
        self.detail = ttk.Frame(f_task)
        self.detail.pack(fill="x", padx=8, pady=(2, 8))
        self.panels = {
            "mp4": self._panel_mp4(self.detail),
            "size": self._panel_size(self.detail),
            "audio": self._panel_audio(self.detail),
            "gif": self._panel_gif(self.detail),
        }

        # --- 3. 保存先・範囲・画質 ---
        f_opt = ttk.LabelFrame(outer, text="3. 保存先とオプション")
        f_opt.pack(fill="x", **pad)

        out_row = ttk.Frame(f_opt)
        out_row.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Checkbutton(out_row, text="元のファイルと同じ場所", variable=self.var_same_dir,
                        command=self._update_states).pack(side="left")
        self.entry_outdir = ttk.Entry(out_row, textvariable=self.var_outdir)
        self.entry_outdir.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.btn_browse = ttk.Button(out_row, text="参照...", width=9,
                                     command=self.on_browse)
        self.btn_browse.pack(side="left", padx=(6, 0))
        ttk.Button(out_row, text="開く", width=7,
                   command=self.on_open_outdir).pack(side="left", padx=(6, 0))

        range_row = ttk.Frame(f_opt)
        range_row.pack(fill="x", padx=8, pady=(2, 4))
        ttk.Label(range_row, text="範囲（任意）　開始").pack(side="left")
        ttk.Entry(range_row, textvariable=self.var_start, width=10).pack(side="left",
                                                                        padx=(4, 0))
        ttk.Label(range_row, text="終了").pack(side="left", padx=(10, 0))
        ttk.Entry(range_row, textvariable=self.var_end, width=10).pack(side="left",
                                                                      padx=(4, 0))
        ttk.Label(range_row, text="※ 1:30 のように分:秒で。空欄なら全体",
                  foreground="#666").pack(side="left", padx=(10, 0))

        q_row = ttk.Frame(f_opt)
        q_row.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(q_row, text="画質と速度:").pack(side="left")
        for label, key in convert.QUALITIES:
            ttk.Radiobutton(q_row, text=label, value=key, variable=self.var_quality,
                            command=self._refresh_preview).pack(side="left", padx=(10, 0))

        # --- 見立て ---
        f_plan = ttk.Frame(outer)
        f_plan.pack(fill="x", **pad)
        self.lbl_preview = ttk.Label(f_plan, textvariable=self.var_preview,
                                     foreground="#1a5c1a", justify="left", wraplength=780)
        self.lbl_preview.pack(anchor="w")

        # --- 実行 ---
        run_row = ttk.Frame(outer)
        run_row.pack(fill="x", **pad)
        self.btn_start = ttk.Button(run_row, text="変換する", width=16,
                                    command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(run_row, text="中止", width=10,
                                     command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(run_row, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        ttk.Label(outer, textvariable=self.var_status).pack(anchor="w", padx=8)

        # --- ログ ---
        f_log = ttk.LabelFrame(outer, text="ログ")
        f_log.pack(fill="both", expand=True, **pad)
        log_wrap = ttk.Frame(f_log)
        log_wrap.pack(fill="both", expand=True, padx=8, pady=8)
        self.txt_log = tk.Text(log_wrap, height=6, wrap="none", state="disabled")
        lsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=lsb.set)
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        lsb.grid(row=0, column=1, sticky="ns")
        log_wrap.rowconfigure(0, weight=1)
        log_wrap.columnconfigure(0, weight=1)

        self._on_task_change()

    def _panel_mp4(self, parent):
        frame = ttk.Frame(parent)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="解像度:").pack(side="left")
        for label, value in HEIGHTS:
            ttk.Radiobutton(row, text=label, value=value, variable=self.var_maxh,
                            command=self._refresh_preview).pack(side="left", padx=(8, 0))
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(row2, text="変換せずに済むならコピーする（無劣化・数秒で終わる）",
                        variable=self.var_copy,
                        command=self._refresh_preview).pack(side="left")
        return frame

    def _panel_size(self, parent):
        frame = ttk.Frame(parent)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="目標サイズ:").pack(side="left")
        combo = ttk.Combobox(row, textvariable=self.var_target, width=6,
                             values=TARGET_PRESETS)
        combo.pack(side="left", padx=(6, 4))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        ttk.Label(row, text="MB").pack(side="left")
        ttk.Label(row, text="※ メール添付なら 25、チャットに貼るなら 8〜10 が目安",
                  foreground="#666").pack(side="left", padx=(10, 0))
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(row2, text="必要なら解像度も自動で下げる（そのほうがきれいに見えます）",
                        variable=self.var_autoscale,
                        command=self._refresh_preview).pack(side="left")
        return frame

    def _panel_audio(self, parent):
        frame = ttk.Frame(parent)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="形式:").pack(side="left")
        for label, value in convert.AUDIO_FORMATS:
            ttk.Radiobutton(row, text=label, value=value, variable=self.var_afmt,
                            command=self._refresh_preview).pack(side="left", padx=(8, 0))
        ttk.Label(row, text="※ 元が同じ形式なら、そのまま取り出します（無劣化）",
                  foreground="#666").pack(side="left", padx=(10, 0))
        return frame

    def _panel_gif(self, parent):
        frame = ttk.Frame(parent)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="幅:").pack(side="left")
        for label, value in GIF_WIDTHS:
            ttk.Radiobutton(row, text=label, value=value, variable=self.var_gifw,
                            command=self._refresh_preview).pack(side="left", padx=(8, 0))
        ttk.Label(row, text="なめらかさ:").pack(side="left", padx=(16, 0))
        for label, value in GIF_FPS:
            ttk.Radiobutton(row, text=label, value=value, variable=self.var_giffps,
                            command=self._refresh_preview).pack(side="left", padx=(8, 0))
        ttk.Label(frame, text="※ GIF は 1 コマずつ静止画を並べる形式です。"
                              "上の「範囲」で 10 秒ほどに切り出すことをおすすめします。",
                  foreground="#666").pack(anchor="w", pady=(4, 0))
        return frame

    def _watch_options(self):
        """入力欄をいじったら見立てを作り直す。"""
        for var in (self.var_start, self.var_end, self.var_target):
            var.trace_add("write", lambda *_a: self._refresh_preview())

    # ------------------------------------------------------------ 起動時

    def _first_run(self):
        self._refresh_parts()
        if binaries.missing():
            ok = messagebox.askyesno(
                APP_TITLE,
                "動作に必要な部品（FFmpeg）がまだありません。\n"
                "いま取得しますか？（初回だけです）\n\n"
                "　・FFmpeg（{}）\n\n"
                "保存先: {}\n\n"
                "※ 他の johukku 製ツールと共有するので、\n"
                "　 すでに入っていれば取得は省かれます。".format(
                    binaries.APPROX_SIZE["FFmpeg"], binaries.bin_dir()))
            if ok:
                self._start_setup()
            else:
                self.var_status.set("部品が未取得です。変換のときに改めて確認します。")
        else:
            self.var_status.set("ファイルを追加して「変換する」を押してください。")

    def _refresh_parts(self):
        version = binaries.ffmpeg_version()
        self.var_parts.set("FFmpeg {}".format(version or "未取得"))

    # ------------------------------------------------------------ ファイル

    def on_add(self):
        paths = filedialog.askopenfilenames(title="変換するファイルを選ぶ",
                                            filetypes=FILE_TYPES)
        if paths:
            self._add_paths(paths)

    def on_add_folder(self):
        folder = filedialog.askdirectory(title="フォルダを選ぶ")
        if not folder:
            return
        found = []
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in MEDIA_EXTS:
                found.append(path)
        if not found:
            messagebox.showinfo(APP_TITLE, "そのフォルダに動画・音声が見つかりませんでした。")
            return
        self._add_paths(found)

    def on_drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        self._add_paths(paths)

    def _add_paths(self, paths):
        known = {item["path"] for item in self.files}
        added = []
        for path in paths:
            path = os.path.normpath(str(path).strip('"'))
            if os.path.isdir(path):
                continue
            if not os.path.isfile(path) or path in known:
                continue
            known.add(path)
            item = {"path": path, "info": None, "error": None}
            self.files.append(item)
            added.append(item)
            self.listbox.insert("end", "{}　（調べています...）".format(
                os.path.basename(path)))
        if not added:
            return
        self._update_states()
        # ffprobe は 1 件あたり一瞬だが、まとめて入れられると待たされるので裏で回す
        threading.Thread(target=self._probe_all, args=(added,), daemon=True).start()

    def _probe_all(self, items):
        for item in items:
            if not binaries.ffmpeg_ok():
                item["error"] = "FFmpeg がまだありません"
            else:
                try:
                    item["info"] = media_info.probe(item["path"])
                except media_info.ProbeError as e:
                    item["error"] = str(e).splitlines()[0]
                except Exception as e:
                    item["error"] = "{}: {}".format(type(e).__name__, e)
            self.queue.put(("probed", item))

    def _redraw_item(self, item):
        try:
            index = self.files.index(item)
        except ValueError:
            return
        name = os.path.basename(item["path"])
        if item["info"]:
            text = "{}　{}".format(name, media_info.summary(item["info"]))
        else:
            text = "{}　（{}）".format(name, item["error"] or "調べています...")
        self.listbox.delete(index)
        self.listbox.insert(index, text)
        self._refresh_preview()

    def on_remove(self):
        for index in sorted(self.listbox.curselection(), reverse=True):
            self.listbox.delete(index)
            del self.files[index]
        self._update_states()
        self._refresh_preview()

    def on_clear(self):
        self.listbox.delete(0, "end")
        self.files.clear()
        self._update_states()
        self._refresh_preview()

    # ------------------------------------------------------------ 保存先

    def on_browse(self):
        path = filedialog.askdirectory(initialdir=self.var_outdir.get() or default_outdir())
        if path:
            self.var_outdir.set(os.path.normpath(path))
            self.var_same_dir.set(False)
            self._update_states()

    def on_open_outdir(self):
        path = self._outdir_for(self.files[0]["path"]) if (
            self.var_same_dir.get() and self.files) else self.var_outdir.get()
        if not os.path.isdir(path):
            messagebox.showinfo(APP_TITLE, "保存先フォルダが見つかりません。")
            return
        try:
            os.startfile(path)
        except OSError as e:
            messagebox.showerror(APP_TITLE, "フォルダを開けませんでした。\n\n{}".format(e))

    def _outdir_for(self, path, same_dir=None, outdir=None):
        same_dir = self.var_same_dir.get() if same_dir is None else same_dir
        if same_dir:
            return os.path.dirname(os.path.abspath(path))
        return (self.var_outdir.get() if outdir is None else outdir).strip()

    # ------------------------------------------------------------ 見立て

    def _options(self, quiet=True):
        """いまの画面の設定を convert に渡す形にまとめる。"""
        try:
            start = parse_time(self.var_start.get())
            end = parse_time(self.var_end.get())
        except ValueError:
            if not quiet:
                raise
            start = end = None
        try:
            target = float(self.var_target.get())
        except ValueError:
            if not quiet:
                raise
            target = 25.0
        return {
            "task": self.var_task.get(),
            "quality": self.var_quality.get(),
            "max_height": int(self.var_maxh.get() or 0),
            "target_mb": target,
            "audio_format": self.var_afmt.get(),
            "gif_width": int(self.var_gifw.get() or 480),
            "gif_fps": int(self.var_giffps.get() or 12),
            "start": start,
            "end": end,
            "prefer_copy": bool(self.var_copy.get()),
            "auto_scale": bool(self.var_autoscale.get()),
            "tonemap": True,
        }

    def _preview_target(self):
        """見立てを作る対象。選ばれていれば その 1 件、無ければ先頭。"""
        selection = self.listbox.curselection()
        if selection and selection[0] < len(self.files):
            return self.files[selection[0]]
        return self.files[0] if self.files else None

    def _refresh_preview(self):
        item = self._preview_target()
        if item is None:
            self.var_preview.set("ファイルを追加すると、ここに変換の内容が出ます。")
            return
        if not item["info"]:
            self.var_preview.set("{}　{}".format(
                os.path.basename(item["path"]), item["error"] or "調べています..."))
            return

        try:
            job_plan = convert.plan(item["info"], self._options())
        except convert.ConvertError as e:
            self.var_preview.set("！ " + str(e).replace("\n", " "))
            return

        lines = ["この設定なら: " + "　".join(job_plan["notes"])]
        for warning in job_plan["warnings"]:
            lines.append("！ " + warning.replace("\n", " "))
        if len(self.files) > 1:
            lines.append("（{} 件を上から順に処理します。ファイルごとに内容は変わります）".format(
                len(self.files)))
        self.var_preview.set("\n".join(lines))

    # ------------------------------------------------------------ 操作

    def on_start(self):
        if self._busy():
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "変換するファイルを追加してください。")
            return

        try:
            options = self._options(quiet=False)
        except ValueError:
            messagebox.showwarning(
                APP_TITLE,
                "「範囲」または「目標サイズ」の書き方が正しくありません。\n\n"
                "範囲は 1:30（1 分 30 秒）のように、\n"
                "目標サイズは 25 のように半角数字で入れてください。")
            return

        if options["start"] is not None and options["end"] is not None \
                and options["end"] <= options["start"]:
            messagebox.showwarning(APP_TITLE, "範囲の「終了」は「開始」より後にしてください。")
            return
        if options["task"] == "size" and options["target_mb"] <= 0:
            messagebox.showwarning(APP_TITLE, "目標サイズには 1 以上の数字を入れてください。")
            return

        if not self.var_same_dir.get():
            outdir = self.var_outdir.get().strip()
            if not os.path.isdir(outdir):
                messagebox.showwarning(APP_TITLE, "保存先フォルダが見つかりません。")
                return

        if binaries.missing():
            if not messagebox.askyesno(
                    APP_TITLE, "先に FFmpeg を取得します。よろしいですか？"):
                return
            self._start_setup(then_convert=options)
            return

        self._start_convert(options)

    def on_cancel(self):
        self.stop_all = True
        if self.job:
            self.job.cancel()
        self.var_status.set("中止しています...")

    def on_reinstall(self):
        if self._busy():
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "FFmpeg を取得し直します。\n"
                "（{}）\n\n"
                "※ 他の johukku 製ツールとも共有しているファイルです。\n\n"
                "よろしいですか？".format(binaries.bin_dir())):
            return
        for path in (binaries.ffmpeg_path(), binaries.ffprobe_path()):
            try:
                os.remove(path)
            except OSError:
                pass
        self._start_setup()

    def on_close(self):
        if self._busy():
            if not messagebox.askyesno(APP_TITLE, "処理中です。終了しますか？"):
                return
            self.stop_all = True
            if self.job:
                self.job.cancel()
        self._save_settings()
        self.root.destroy()

    # ------------------------------------------------------------ 実行

    def _busy(self):
        return self.busy or bool(self.worker and self.worker.is_alive())

    def _run_in_thread(self, func):
        self.worker = threading.Thread(target=self._guard, args=(func,), daemon=True)
        self.worker.start()

    def _guard(self, func):
        """作業スレッドの例外をログに落として、ボタンを戻す。"""
        try:
            func()
        except binaries.SetupError as e:
            self.queue.put(("log", str(e)))
            self.queue.put(("done", False, str(e)))
        except Exception:
            self.queue.put(("log", traceback.format_exc()))
            self.queue.put(("done", False, "予期しないエラーが起きました。ログを確認してください。"))

    def _start_setup(self, then_convert=None):
        self._set_busy(True, cancellable=False)
        self.var_status.set("FFmpeg を取得しています...")
        self._log("── 部品の取得 ──")
        self._log("保存先: {}".format(binaries.bin_dir()))
        self._run_in_thread(lambda: self._work_setup(then_convert))

    def _work_setup(self, then_convert):
        def on_progress(done, total, label):
            if done < 0:
                self.queue.put(("status", label))
            elif total > 0:
                self.queue.put(("prog", done * 100.0 / total,
                                "{} を取得中".format(label), ""))
            else:
                self.queue.put(("prog", None, "{} を取得中".format(label), ""))

        binaries.ensure_ffmpeg(on_progress)
        self.queue.put(("log", "部品の取得が完了しました。"))
        self.queue.put(("parts", None))
        if then_convert:
            self.queue.put(("chain", then_convert))
        else:
            self.queue.put(("done", True, "部品の準備ができました。"))

    def _start_convert(self, options):
        self.stop_all = False
        self._set_busy(True, cancellable=True)
        self._save_settings()
        self.started_at = time.time()
        # tk の変数は作業スレッドから読まない。ここで確定させて持ち回る
        where = (bool(self.var_same_dir.get()), self.var_outdir.get().strip())
        self._log("── 変換開始（{} / {}）──".format(
            convert.TASK_NAMES.get(options["task"], options["task"]),
            convert.QUALITY_NAMES.get(options["quality"], options["quality"])))
        self._run_in_thread(lambda: self._work_convert(options, where))

    def _work_convert(self, options, where):
        total = len(self.files)
        done = 0
        failed = []
        skipped = []

        for index, item in enumerate(list(self.files)):
            if self.stop_all:
                break
            head = "（{}/{}）".format(index + 1, total) if total > 1 else ""
            name = os.path.basename(item["path"])
            self.queue.put(("log", "{} {}".format(head, name).strip()))

            info = item["info"]
            if info is None:
                try:
                    info = media_info.probe(item["path"])
                except media_info.ProbeError as e:
                    self.queue.put(("log", "　読み取れませんでした: {}".format(e)))
                    failed.append(name)
                    continue

            try:
                job_plan = convert.plan(info, options)
            except convert.ConvertError as e:
                self.queue.put(("log", "　{}".format(e)))
                skipped.append(name)
                continue

            for note in job_plan["notes"]:
                self.queue.put(("log", "　・{}".format(note)))

            outdir = self._outdir_for(item["path"], where[0], where[1])
            try:
                self.job = convert.Job(info, options, outdir, job_plan)
            except convert.ConvertError as e:
                self.queue.put(("log", "　{}".format(e)))
                failed.append(name)
                continue

            def on_progress(ratio, label, index=index):
                overall = (index + ratio) / float(total)
                self.queue.put(("prog", overall * 100.0,
                                "{}変換中".format(head), self._eta(overall)))

            try:
                out = self.job.run(on_progress=on_progress,
                                   log=lambda t: self.queue.put(("log", "　" + t)))
            except convert.Cancelled:
                break
            except convert.ConvertError as e:
                self.queue.put(("log", "　失敗: {}".format(e)))
                failed.append(name)
                continue

            done += 1
            self.queue.put(("log", "　保存: {}　({})".format(
                out, media_info.human_size(os.path.getsize(out)))))
            if options["task"] == "size":
                actual = os.path.getsize(out) / (1024.0 * 1024.0)
                if actual > options["target_mb"] * 1.05:
                    self.queue.put((
                        "log",
                        "　！ 目標 {:.0f} MB に対して {:.1f} MB になりました。"
                        "「画質優先」か「バランス」なら 2 パスできっちり収まります。".format(
                            options["target_mb"], actual)))

        self.job = None
        parts = ["完了 {} 件".format(done)]
        if failed:
            parts.append("失敗 {} 件".format(len(failed)))
        if skipped:
            parts.append("できないもの {} 件".format(len(skipped)))
        summary = "、".join(parts) + "。"
        if self.stop_all:
            self.queue.put(("done", False, "中止しました。（{}）".format(summary)))
        else:
            self.queue.put(("done", not (failed or skipped), summary))

    def _eta(self, ratio):
        """全体の進み具合から残り時間を見積もる。"""
        if ratio <= 0.02:
            return ""
        elapsed = time.time() - self.started_at
        remain = elapsed / ratio - elapsed
        if remain < 1:
            return ""
        return "残り {}".format(media_info.human_duration(remain))

    # ------------------------------------------------------------ 表示の更新

    def _pump(self):
        """作業スレッドからの連絡をまとめて画面に反映する。"""
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1])
                elif kind == "status":
                    self.var_status.set(msg[1])
                elif kind == "probed":
                    self._redraw_item(msg[1])
                elif kind == "prog":
                    percent, label, tail = msg[1], msg[2], msg[3]
                    if percent is None:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(30)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                        self.progress["value"] = percent
                    text = label
                    if percent is not None:
                        text += "  {:.1f}%".format(percent)
                    if tail.strip():
                        text += "　{}".format(tail)
                    self.var_status.set(text)
                elif kind == "parts":
                    self._refresh_parts()
                elif kind == "chain":
                    self._refresh_parts()
                    self._start_convert(msg[1])
                elif kind == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress["value"] = 0
                    self.var_status.set(msg[2])
                    self._log(msg[2])
                    self._refresh_parts()
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _log(self, text):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _set_busy(self, busy, cancellable=True):
        self.busy = bool(busy)
        state = "disabled" if busy else "normal"
        for widget in (self.btn_start, self.btn_add, self.btn_add_folder,
                       self.btn_browse, self.btn_reinstall):
            widget.configure(state=state)
        self.btn_cancel.configure(state="normal" if (busy and cancellable) else "disabled")
        self._update_states()

    def _update_states(self):
        """保存先の欄は「元と同じ場所」のときだけ触れないようにする。"""
        same = self.var_same_dir.get()
        state = "disabled" if same else "normal"
        self.entry_outdir.configure(state=state)
        self.btn_browse.configure(state="disabled" if self.busy else "normal")

    def _on_task_change(self):
        for key, panel in self.panels.items():
            panel.pack_forget()
        panel = self.panels.get(self.var_task.get())
        if panel is not None:
            panel.pack(fill="x")
        self._refresh_preview()

    # ------------------------------------------------------------ 設定

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        self.settings.update({
            "outdir": self.var_outdir.get(),
            "same_dir": self.var_same_dir.get(),
            "task": self.var_task.get(),
            "quality": self.var_quality.get(),
            "max_height": int(self.var_maxh.get() or 0),
            "target_mb": self.var_target.get(),
            "audio_format": self.var_afmt.get(),
            "gif_width": int(self.var_gifw.get() or 480),
            "gif_fps": int(self.var_giffps.get() or 12),
            "prefer_copy": self.var_copy.get(),
            "auto_scale": self.var_autoscale.get(),
            "width": self.root.winfo_width(),
            "height": self.root.winfo_height(),
        })
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


def main():
    root = ROOT_CLASS()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
