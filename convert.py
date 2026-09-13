# -*- coding: utf-8 -*-
"""変換の中身。何をするか決める（plan）→ FFmpeg を回す（Job）。

方針は「できるだけ再エンコードしない」。
コンテナだけ違う場合や、音声だけ入れ替えれば済む場合は映像をコピーする。
コピーは一瞬で終わり、しかも画質はまったく落ちない。

再エンコードが要るときは、品質基準（CRF / CQ）で回す。
ビットレート指定にするのは「サイズを指定して圧縮」のときだけ。

GUI とは切り離してあるので、この単体でも動かせる:

    python convert.py <ファイル> mp4
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import binaries
import media_info

NO_WINDOW = binaries.NO_WINDOW


class ConvertError(Exception):
    """変換に失敗した。メッセージはそのまま利用者に見せる。"""


class Cancelled(Exception):
    """利用者が中止した。"""


# ---------------------------------------------------------------- 変換の種類

# (画面の表示名, 内部の名前)。GUI のラジオボタンはこの並び順で作る
TASKS = [
    ("MP4 に変換（互換重視）", "mp4"),
    ("サイズを指定して圧縮", "size"),
    ("音声だけ取り出す", "audio"),
    ("GIF にする", "gif"),
]
TASK_NAMES = {key: label for label, key in TASKS}

QUALITIES = [
    ("画質優先（CPU・遅い）", "best"),
    ("バランス（おすすめ）", "balance"),
    ("速度優先（GPU）", "fast"),
]
QUALITY_NAMES = {key: label for label, key in QUALITIES}

AUDIO_FORMATS = [("mp3", "mp3"), ("m4a（AAC）", "m4a"), ("wav（無圧縮）", "wav")]

# MP4 にそのまま入れられる音声。これ以外は AAC に変換する
# （Opus や Vorbis は MP4 に入れても再生できない機器があるため）。
MP4_SAFE_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}

# MP4 に入れたときに、古い機器や編集ソフトでも再生できる映像
MP4_COMPATIBLE_VIDEO = {"h264"}

# コピーはできるが、機器によっては再生できない映像（そのまま入れるかは利用者の選択）
MP4_PLAYABLE_VIDEO = {"h264", "hevc", "mpeg4", "av1"}

DEFAULT_OPTIONS = {
    "task": "mp4",
    "quality": "balance",
    "max_height": 0,          # 0 なら解像度はそのまま
    "target_mb": 25.0,
    "audio_format": "mp3",
    "gif_width": 480,
    "gif_fps": 12,
    "start": None,            # 秒。None なら先頭から
    "end": None,              # 秒。None なら最後まで
    "prefer_copy": True,      # 変換せずに済むならコピーする
    "tonemap": True,          # HDR を SDR に落とす
    "auto_scale": True,       # サイズ指定のとき、足りなければ解像度も下げる
}


def options_with_defaults(options=None):
    merged = dict(DEFAULT_OPTIONS)
    merged.update(options or {})
    return merged


# ------------------------------------------------------------------ 見立て


def clip_duration(info, options):
    """切り出しを反映した長さ（秒）。分からなければ None。"""
    total = info.get("duration")
    start = options.get("start") or 0.0
    end = options.get("end")
    if end is None:
        if total is None:
            return None
        return max(0.0, total - start)
    return max(0.0, end - start)


def _trimmed(options):
    return bool(options.get("start") or options.get("end"))


def _target_scale(info, options, limit=None):
    """縮小後の (幅, 高さ)。縮めないなら None。

    回転を反映した「見た目の」大きさで判断する。FFmpeg は
    フィルタを通すとき自動で回転を適用するので、この寸法でそのまま指定できる。
    """
    width, height = media_info.display_size(info)
    if not width or not height:
        return None
    limit = limit if limit is not None else int(options.get("max_height") or 0)
    if not limit or height <= limit:
        return None
    new_height = _even(limit)
    new_width = _even(round(width * new_height / float(height)))
    return (max(2, new_width), max(2, new_height))


def _even(value):
    value = int(round(value))
    return value - (value % 2)


def suggested_height(bitrate_kbps, width, height, fps):
    """そのビットレートで無理なく収まる高さを返す。

    低いビットレートで解像度だけ高いと、全体がぼやけてブロックだらけになる。
    1 画素 1 フレームあたり 0.08bit を目安に、収まる高さまで落とす。
    """
    if not (bitrate_kbps and width and height):
        return None
    fps = fps or 30.0
    aspect = float(width) / float(height)
    # bitrate(bps) = h*h*aspect*fps*bpp
    ideal = (bitrate_kbps * 1000.0) / (aspect * fps * 0.08)
    if ideal <= 0:
        return None
    best = int(ideal ** 0.5)
    for step in (2160, 1440, 1080, 720, 540, 480, 360, 240):
        if best >= step:
            return step if step < height else None
    return 240 if height > 240 else None


def _check_range(info, options):
    """範囲の指定が成り立っているか確かめる。

    終了が開始と同じ（または前）だと、FFmpeg には「指定なし」と同じに見えて
    最後まで書き出してしまう。黙って全部変換するより、ここで止める。
    """
    start = float(options.get("start") or 0.0)
    end = options.get("end")
    if end is not None and float(end) <= start:
        raise ConvertError("範囲の「終了」は「開始」より後にしてください。")
    duration = info.get("duration")
    if duration and start >= duration:
        raise ConvertError(
            "範囲の「開始」（{}）が、このファイルの長さ（{}）を超えています。".format(
                media_info.human_duration(start), media_info.human_duration(duration)))


def plan(info, options):
    """何をするかを決める。GUI はこの結果をそのままログに出す。

    返す dict:
        kind        : "remux" / "encode" / "audio" / "gif"
        ext         : 出力の拡張子
        video       : "copy" / "encode" / "none"
        audio       : "copy" / "encode" / "none"
        scale       : (幅, 高さ) または None
        bitrate     : 映像のビットレート（kbps）。品質基準なら None
        two_pass    : 2 パスで回すか
        tonemap     : HDR を SDR に落とすか
        notes       : 利用者に見せる説明（そのままログへ）
        warnings    : 注意（ログに出し、必要なら確認する）
    """
    options = options_with_defaults(options)
    _check_range(info, options)
    task = options["task"]
    video = info.get("video")
    audio = info.get("audio")
    notes = []
    warnings = []

    if task == "audio":
        return _plan_audio(info, options, notes, warnings)
    if not video:
        raise ConvertError("このファイルには映像が入っていません。\n"
                           "「音声だけ取り出す」を選ぶか、動画を指定してください。")
    if task == "gif":
        return _plan_gif(info, options, notes, warnings)
    if task == "size":
        return _plan_size(info, options, notes, warnings)
    return _plan_mp4(info, options, notes, warnings)


def _plan_mp4(info, options, notes, warnings):
    video = info["video"]
    audio = info.get("audio")
    scale = _target_scale(info, options)
    needs_tonemap = bool(video.get("hdr") and options.get("tonemap"))

    can_copy_video = (
        options.get("prefer_copy")
        and scale is None
        and not needs_tonemap
        and video["codec"] in MP4_PLAYABLE_VIDEO
    )

    if can_copy_video:
        if video["codec"] in MP4_COMPATIBLE_VIDEO:
            notes.append("映像はそのままコピーします（再エンコードなし・無劣化）。")
        else:
            notes.append("映像は {} のままコピーします（無劣化）。".format(video["codec"]))
            warnings.append(
                "{} は古い機器や編集ソフトで再生できないことがあります。\n"
                "確実に再生したい場合は「変換せずに済むならコピーする」のチェックを外して"
                "ください（H.264 に変換します）。".format(video["codec"].upper()))
        video_mode = "copy"
    else:
        video_mode = "encode"
        if scale:
            notes.append("解像度を {}x{} に下げます。".format(*scale))
        if needs_tonemap:
            notes.append("HDR を SDR に変換します（色がくすまないようにします）。")
        if not options.get("prefer_copy") and video["codec"] in MP4_COMPATIBLE_VIDEO:
            notes.append("H.264 で入れ直します。")
        elif video["codec"] not in MP4_PLAYABLE_VIDEO:
            notes.append("{} は MP4 に入れられないので H.264 に変換します。".format(
                video["codec"]))

    audio_mode = _audio_plan("mp4", audio, options, notes)

    if video_mode == "copy" and audio_mode in ("copy", "none"):
        notes.append("入れ物を詰め替えるだけなので、数秒で終わります。")
        kind = "remux"
    else:
        kind = "encode"

    if _trimmed(options) and video_mode == "copy":
        warnings.append("無劣化のままカットするため、開始位置が近くのキーフレームまで"
                        "数秒ずれることがあります。")

    return {
        "kind": kind, "ext": ".mp4", "video": video_mode, "audio": audio_mode,
        "scale": scale, "bitrate": None, "two_pass": False,
        "tonemap": needs_tonemap, "notes": notes, "warnings": warnings,
        "suffix": "",
    }


def _plan_size(info, options, notes, warnings):
    video = info["video"]
    audio = info.get("audio")
    duration = clip_duration(info, options)
    target_mb = float(options.get("target_mb") or 25.0)
    target_bytes = target_mb * 1024 * 1024

    if not duration or duration <= 0:
        raise ConvertError("動画の長さが分からないため、サイズを指定した圧縮ができません。")

    if info.get("size") and info["size"] <= target_bytes and not _trimmed(options) \
            and not options.get("max_height"):
        notes.append("元のファイルがすでに {:.0f} MB 以下です。".format(target_mb))
        warnings.append("いま圧縮すると、画質が落ちるだけで小さくなりません。\n"
                        "そのままお使いいただけます。")

    audio_kbps = 0
    if audio:
        audio_kbps = 128 if (audio.get("channels") or 2) > 1 else 96
        # 目標が小さいときは音声を削ってでも映像に回す
        if target_bytes * 8 / duration / 1000.0 < 500:
            audio_kbps = 64

    # 詰め物（コンテナの管理領域）で 3% ほど増えるので、その分を先に引く
    total_kbps = (target_bytes * 8 / duration / 1000.0) * 0.97
    video_kbps = int(total_kbps - audio_kbps)

    if video_kbps < 100:
        raise ConvertError(
            "{:.0f} MB は、この長さ（{}）に対して小さすぎます。\n"
            "目標サイズを大きくするか、範囲を指定して短く切り出してください。".format(
                target_mb, media_info.human_duration(duration)))

    scale = _target_scale(info, options)
    width, height = media_info.display_size(info)
    if scale:
        width, height = scale
    if options.get("auto_scale"):
        limit = suggested_height(video_kbps, width, height, video.get("fps"))
        if limit and limit < height:
            scale = _target_scale(info, options, limit=limit)
            if scale:
                notes.append(
                    "このビットレートでは {}p はぼやけるので、{}p に落とします"
                    "（そのほうがきれいに見えます）。".format(height, limit))

    notes.append("目標 {:.0f} MB → 映像 {:,} kbps{}。".format(
        target_mb, video_kbps,
        " / 音声 {} kbps".format(audio_kbps) if audio_kbps else ""))

    two_pass = options.get("quality") != "fast"
    if two_pass:
        notes.append("2 パスで回すので、指定サイズにきっちり収まります（そのぶん時間は倍）。")
    else:
        warnings.append("速度優先では 1 パスになるため、"
                        "出来上がりが目標より 1 割ほど前後します。")

    return {
        "kind": "encode", "ext": ".mp4", "video": "encode",
        "audio": "encode" if audio else "none",
        "scale": scale, "bitrate": video_kbps, "audio_kbps": audio_kbps,
        "two_pass": two_pass,
        "tonemap": bool(video.get("hdr") and options.get("tonemap")),
        "notes": notes, "warnings": warnings,
        "suffix": "_{:.0f}MB".format(target_mb),
    }


def _plan_audio(info, options, notes, warnings):
    audio = info.get("audio")
    if not audio:
        raise ConvertError("このファイルには音声が入っていません。")

    fmt = options.get("audio_format") or "mp3"
    same = {"mp3": "mp3", "m4a": "aac"}.get(fmt)
    can_copy = (options.get("prefer_copy") and same and audio["codec"] == same
                and not _trimmed(options))

    if can_copy:
        notes.append("音声はそのまま取り出します（再エンコードなし・無劣化）。")
        mode = "copy"
    elif fmt == "wav":
        notes.append("wav（16bit PCM）で書き出します。サイズは大きくなります。")
        mode = "encode"
    else:
        notes.append("{} に変換します（{} から）。".format(fmt, audio["codec"]))
        if audio["codec"] in ("mp3", "aac", "opus", "vorbis"):
            warnings.append("圧縮済みの音声を変換し直すため、音質はわずかに落ちます。")
        mode = "encode"

    return {
        "kind": "audio", "ext": "." + fmt, "video": "none", "audio": mode,
        "scale": None, "bitrate": None, "two_pass": False, "tonemap": False,
        "notes": notes, "warnings": warnings, "suffix": "",
    }


def _plan_gif(info, options, notes, warnings):
    duration = clip_duration(info, options)
    fps = int(options.get("gif_fps") or 12)
    width = int(options.get("gif_width") or 480)

    notes.append("{} 幅・{} fps で、256 色のパレットを作ってから変換します"
                 "（2 段階なので色がにじみません）。".format(width, fps))

    if duration and duration > 30:
        warnings.append(
            "GIF にする範囲が {} あります。\n"
            "GIF は 1 コマずつ静止画を並べる形式なので、"
            "このままだと数百 MB になることがあります。\n"
            "「範囲」で 10 秒程度に切り出すことをおすすめします。".format(
                media_info.human_duration(duration)))

    return {
        "kind": "gif", "ext": ".gif", "video": "encode", "audio": "none",
        "scale": None, "bitrate": None, "two_pass": True, "tonemap": False,
        "gif_fps": fps, "gif_width": width,
        "notes": notes, "warnings": warnings, "suffix": "",
    }


def _audio_plan(container, audio, options, notes):
    if not audio:
        notes.append("音声は入っていません。")
        return "none"
    if options.get("prefer_copy") and audio["codec"] in MP4_SAFE_AUDIO:
        notes.append("音声（{}）もそのままコピーします。".format(audio["codec"]))
        return "copy"
    if audio["codec"] in MP4_SAFE_AUDIO:
        notes.append("音声も AAC で入れ直します。")
    else:
        notes.append("音声は {} なので AAC に変換します（MP4 に入れるため）。".format(
            audio["codec"]))
    return "encode"


# ------------------------------------------------------------ エンコーダ選び

# GPU のエンコーダ。上から順に試し、実際に 1 コマ焼けたものを使う
HW_ENCODERS = {
    "balance": [
        ("h264_nvenc", ["-preset", "p7", "-tune", "hq", "-rc", "vbr",
                        "-cq", "21", "-b:v", "0", "-spatial-aq", "1"]),
        ("h264_qsv", ["-preset", "slow", "-global_quality", "21"]),
        ("h264_amf", ["-quality", "quality", "-rc", "cqp",
                      "-qp_i", "21", "-qp_p", "21"]),
    ],
    "fast": [
        ("h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "25", "-b:v", "0"]),
        ("h264_qsv", ["-preset", "medium", "-global_quality", "25"]),
        ("h264_amf", ["-quality", "balanced", "-rc", "cqp",
                      "-qp_i", "25", "-qp_p", "25"]),
    ],
}

# CPU（libx264）。画質優先は遅いぶんきれい
SW_ENCODERS = {
    "best": ("libx264", ["-preset", "slow", "-crf", "18"]),
    "balance": ("libx264", ["-preset", "medium", "-crf", "20"]),
    "fast": ("libx264", ["-preset", "veryfast", "-crf", "23"]),
}

_encoder_cache = {}


def pick_encoder(quality="balance"):
    """使うエンコーダを (名前, 追加引数) で返す。

    GPU のエンコーダは「FFmpeg が対応している」だけでは足りない。
    ドライバが古いと開く段階で失敗するので、1 コマだけ試して確かめる。
    画質優先のときは GPU を使わない（同じサイズなら libx264 のほうがきれい）。
    """
    quality = quality if quality in SW_ENCODERS else "balance"
    if quality == "best":
        return SW_ENCODERS["best"]

    key = (os.path.abspath(binaries.ffmpeg_path()), quality)
    if key in _encoder_cache:
        return _encoder_cache[key]

    chosen = SW_ENCODERS[quality]
    for name, args in HW_ENCODERS.get(quality, []):
        if _encoder_works(name):
            chosen = (name, args)
            break
    _encoder_cache[key] = chosen
    return chosen


def _encoder_works(name):
    cmd = [binaries.ffmpeg_path(), "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "color=c=black:s=128x128:r=10:d=0.2",
           "-c:v", name, "-frames:v", "1", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30,
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return proc.returncode == 0
    except Exception:
        return False


def bitrate_args(encoder, kbps, pass_no=None, passlog=None, quality="balance"):
    """サイズ指定のときの引数。libx264 は 2 パス、GPU は 1 パス。"""
    args = ["-b:v", "{}k".format(kbps)]
    if encoder == "libx264":
        preset = "slow" if quality == "best" else "medium"
        args += ["-preset", preset]
        if pass_no:
            args += ["-pass", str(pass_no), "-passlogfile", passlog]
        return args
    # GPU は 2 パスの精度が出ないので、上限と貯め幅を決めて 1 パスで回す
    args += ["-maxrate", "{}k".format(int(kbps * 1.5)),
             "-bufsize", "{}k".format(int(kbps * 2))]
    if encoder == "h264_nvenc":
        args += ["-preset", "p6", "-rc", "vbr"]
    elif encoder == "h264_qsv":
        args += ["-preset", "medium"]
    return args


# ------------------------------------------------------------------ 組み立て


def _filters(job_plan, tonemap_ok):
    """映像フィルタの並びを組み立てる。空なら None。"""
    chain = []
    if job_plan.get("tonemap") and tonemap_ok:
        # 一度リニアに戻してから SDR の明るさに畳む。これを通さずに
        # HDR を再エンコードすると、全体が灰色っぽくくすむ
        chain.append("zscale=t=linear:npl=100")
        chain.append("tonemap=tonemap=hable:desat=0")
        chain.append("zscale=t=bt709:m=bt709:r=tv")
    if job_plan.get("scale"):
        chain.append("scale={}:{}:flags=lanczos".format(*job_plan["scale"]))
    if job_plan["video"] == "encode":
        chain.append("format=yuv420p")
    return ",".join(chain) if chain else None


def _trim_args(options, before_input=True):
    start = options.get("start")
    end = options.get("end")
    args = []
    if before_input:
        if start:
            args += ["-ss", "{:.3f}".format(float(start))]
    else:
        if end is not None:
            length = float(end) - float(start or 0.0)
            if length > 0:
                args += ["-t", "{:.3f}".format(length)]
    return args


def build_commands(info, job_plan, options, out_path, encoder, workdir):
    """実行する FFmpeg のコマンドを順番に並べて返す。

    GIF とサイズ指定の 2 パスがあるので、戻り値は必ずリスト。
    各要素は (説明, コマンド, 進捗の割り当て) の組。
    """
    options = options_with_defaults(options)
    ffmpeg = binaries.ffmpeg_path()
    src = os.path.abspath(info["path"])
    dst = os.path.abspath(out_path)
    head = [ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-nostats", "-progress", "pipe:1"]
    name, enc_args = encoder

    if job_plan["kind"] == "gif":
        return _gif_commands(head, src, dst, job_plan, options, workdir)

    cmd = list(head)
    cmd += _trim_args(options, before_input=True)
    cmd += ["-i", src]
    cmd += _trim_args(options, before_input=False)

    if job_plan["kind"] == "audio":
        cmd += ["-map", "0:a:0", "-vn", "-dn", "-sn"]
        cmd += _audio_args(job_plan, info, options)
        cmd += ["-map_metadata", "0", dst]
        return [("変換", cmd, (0.0, 1.0))]

    cmd += ["-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]

    if job_plan["video"] == "copy":
        cmd += ["-c:v", "copy"]
        # コピーで切り出すと、先頭のタイムスタンプが負になることがある
        if _trimmed(options):
            cmd += ["-avoid_negative_ts", "make_zero"]
    else:
        filters = _filters(job_plan, binaries.has_filter("zscale"))
        if filters:
            cmd += ["-vf", filters]
        cmd += ["-c:v", name]
        if job_plan.get("bitrate"):
            cmd += bitrate_args(name, job_plan["bitrate"], quality=options["quality"])
        else:
            cmd += enc_args

    cmd += _audio_args(job_plan, info, options)
    cmd += ["-map_metadata", "0", "-movflags", "+faststart", dst]

    if not (job_plan.get("two_pass") and job_plan.get("bitrate") and name == "libx264"):
        return [("変換", cmd, (0.0, 1.0))]

    # libx264 の 2 パス。1 回目は解析だけなので出力を捨てる
    passlog = os.path.join(workdir, "x264")
    first = list(head)
    first += _trim_args(options, before_input=True)
    first += ["-i", src]
    first += _trim_args(options, before_input=False)
    first += ["-map", "0:v:0", "-an", "-sn", "-dn"]
    filters = _filters(job_plan, binaries.has_filter("zscale"))
    if filters:
        first += ["-vf", filters]
    first += ["-c:v", name]
    first += bitrate_args(name, job_plan["bitrate"], 1, passlog, options["quality"])
    first += ["-f", "null", os.devnull]

    second = []
    for item in cmd:
        second.append(item)
    # 2 回目は同じコマンドに -pass 2 を足すだけ（-b:v などは既に入っている）
    index = second.index("-c:v")
    second[index + 2:index + 2] = ["-pass", "2", "-passlogfile", passlog]

    return [("1 パス目（解析）", first, (0.0, 0.5)),
            ("2 パス目（書き出し）", second, (0.5, 1.0))]


def _gif_commands(head, src, dst, job_plan, options, workdir):
    """パレットを作ってから当てる 2 段階。1 段階で作ると色が汚くなる。"""
    fps = job_plan.get("gif_fps", 12)
    width = job_plan.get("gif_width", 480)
    palette = os.path.join(workdir, "palette.png")
    common = "fps={},scale={}:-1:flags=lanczos".format(fps, width)

    first = list(head)
    first += _trim_args(options, before_input=True)
    first += ["-i", src]
    first += _trim_args(options, before_input=False)
    first += ["-vf", common + ",palettegen=stats_mode=diff", "-y", palette]

    second = list(head)
    second += _trim_args(options, before_input=True)
    second += ["-i", src]
    second += _trim_args(options, before_input=False)
    second += ["-i", palette,
               "-lavfi", common + " [x];[x][1:v] paletteuse=dither=sierra2_4a:"
                                  "diff_mode=rectangle",
               "-loop", "0", dst]

    return [("色を調べています", first, (0.0, 0.25)),
            ("GIF を書き出しています", second, (0.25, 1.0))]


def _audio_args(job_plan, info, options):
    mode = job_plan["audio"]
    if mode == "none":
        return ["-an"]
    if mode == "copy":
        return ["-c:a", "copy"]

    ext = job_plan["ext"]
    audio = info.get("audio") or {}
    if ext == ".wav":
        return ["-c:a", "pcm_s16le"]
    if ext == ".mp3":
        # -q:a 0 は可変ビットレートの最高音質（およそ 245 kbps）
        return ["-c:a", "libmp3lame", "-q:a", "0"]
    if ext == ".m4a":
        return ["-c:a", "aac", "-b:a", "256k"]

    kbps = job_plan.get("audio_kbps")
    if not kbps:
        kbps = 192 if (audio.get("channels") or 2) <= 2 else 384
    return ["-c:a", "aac", "-b:a", "{}k".format(kbps)]


# ------------------------------------------------------------------ 出力先


def output_path(info, job_plan, outdir):
    """出力先を決める。元のファイルを上書きしないようにする。"""
    base = os.path.splitext(os.path.basename(info["path"]))[0]
    ext = job_plan["ext"]
    suffix = job_plan.get("suffix") or ""
    candidate = os.path.join(outdir, base + suffix + ext)

    if os.path.abspath(candidate) == os.path.abspath(info["path"]):
        candidate = os.path.join(outdir, base + "_変換" + ext)

    if not os.path.exists(candidate):
        return candidate

    stem = os.path.splitext(os.path.basename(candidate))[0]
    for index in range(2, 1000):
        candidate = os.path.join(outdir, "{}_{}{}".format(stem, index, ext))
        if not os.path.exists(candidate):
            return candidate
    raise ConvertError("出力先に同じ名前のファイルが多すぎます。")


# ------------------------------------------------------------------ 実行


class Job:
    """1 ファイルの変換。中止できるように process を持つ。"""

    def __init__(self, info, options, outdir, job_plan=None):
        self.info = info
        self.options = options_with_defaults(options)
        self.outdir = outdir
        self.plan = job_plan or plan(info, self.options)
        self.out_path = output_path(info, self.plan, outdir)
        self.proc = None
        self.cancelled = False
        self.encoder = None

    def cancel(self):
        self.cancelled = True
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            pass

    def run(self, on_progress=None, log=None):
        """最後まで走らせて出力パスを返す。中止したときは Cancelled。"""
        if not binaries.ffmpeg_ok():
            raise ConvertError("FFmpeg が見つかりません。")

        duration = clip_duration(self.info, self.options)
        workdir = tempfile.mkdtemp(prefix="johukku-convert-")
        try:
            attempts = [self._encoder_for(self.plan)]
            # GPU で始めて失敗したら CPU でやり直す（ドライバ側の都合で落ちることがある）
            if attempts[0][0] != "libx264" and self.plan["video"] == "encode":
                attempts.append(SW_ENCODERS.get(self.options["quality"],
                                                SW_ENCODERS["balance"]))

            last_error = ""
            for index, encoder in enumerate(attempts):
                if index > 0:
                    if log:
                        log("GPU でのエンコードに失敗したので CPU でやり直します。")
                    self._discard()
                self.encoder = encoder
                if log and self.plan["video"] == "encode"                         and self.plan["kind"] != "gif":
                    log("エンコーダ: {}".format(encoder[0]))

                commands = build_commands(self.info, self.plan, self.options,
                                          self.out_path, encoder, workdir)
                ok, last_error = self._run_all(commands, duration, on_progress, log)
                if ok:
                    return self.out_path

            self._discard()
            raise ConvertError("変換に失敗しました。\n{}".format(
                _friendly(last_error) or last_error))
        finally:
            _rmtree(workdir)

    def _encoder_for(self, job_plan):
        if job_plan["video"] != "encode" or job_plan["kind"] == "gif":
            return ("libx264", [])      # 使わないが、形を揃えておく
        return pick_encoder(self.options["quality"])

    def _run_all(self, commands, duration, on_progress, log):
        for label, cmd, (low, high) in commands:
            if self.cancelled:
                raise Cancelled()
            if log and len(commands) > 1:
                log("　{}".format(label))

            def report(ratio, low=low, high=high):
                if on_progress:
                    on_progress(low + (high - low) * ratio, label)

            ok, error = self._run_one(cmd, duration, report)
            if not ok:
                return False, error
        if on_progress:
            on_progress(1.0, "")
        return True, ""

    def _run_one(self, cmd, duration, report):
        err_path = os.path.join(tempfile.gettempdir(),
                                "johukku-convert-{}.log".format(os.getpid()))
        with open(err_path, "wb") as err:
            try:
                self.proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=err, creationflags=NO_WINDOW)
            except OSError as e:
                return False, "FFmpeg を起動できませんでした: {}".format(e)

            try:
                for raw in self.proc.stdout:
                    if self.cancelled:
                        _stop(self.proc)
                        raise Cancelled()
                    if not (duration and duration > 0):
                        continue
                    seconds = _parse_out_time(raw.decode("utf-8", "replace"))
                    if seconds is not None:
                        report(max(0.0, min(1.0, seconds / duration)))
            except Cancelled:
                self._discard()
                raise
            finally:
                try:
                    self.proc.stdout.close()
                except Exception:
                    pass
                self.proc.wait()

        code = self.proc.returncode
        text = _tail(err_path)
        _remove(err_path)
        if code == 0:
            report(1.0)
            return True, ""
        return False, text

    def _discard(self):
        """途中まで書けた出力は残さない。"""
        _remove(self.out_path)


def _parse_out_time(line):
    """-progress の出力から現在位置（秒）を取り出す。

    out_time_ms という名前なのに中身はマイクロ秒、という FFmpeg 側の
    古い癖があるので、どちらもマイクロ秒として扱う。
    """
    line = line.strip()
    for key in ("out_time_us=", "out_time_ms="):
        if line.startswith(key):
            try:
                return int(line[len(key):]) / 1000000.0
            except ValueError:
                return None
    return None


def _stop(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _tail(path, limit=600):
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace").strip()
        return text[-limit:] if text else "（FFmpeg からの詳細はありません）"
    except OSError:
        return "（FFmpeg からの詳細はありません）"


def _remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _rmtree(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# よくある失敗と、利用者に見せる言い換え
HINTS = (
    (("no space left", "disk full"),
     "保存先の空き容量が足りません。"),
    (("permission denied", "access is denied", "winerror 5"),
     "保存先に書き込めませんでした。\n"
     "別のフォルダを指定するか、ファイルが他のソフトで開かれていないか確認してください。"),
    (("invalid data found", "moov atom not found"),
     "元のファイルが壊れているようです。"),
    (("unknown encoder", "encoder not found"),
     "この FFmpeg には必要なエンコーダが入っていませんでした。\n"
     "「部品を入れ直す」で取得し直してください。"),
    (("no such filter", "unknown filter"),
     "この FFmpeg には必要なフィルタが入っていませんでした。\n"
     "「部品を入れ直す」で取得し直してください。"),
    (("does not contain any stream",),
     "変換できる映像・音声が入っていませんでした。"),
    (("could not open encoder", "cannot load", "openencodesessionex",
      "no capable devices"),
     "GPU のエンコーダを開けませんでした。\n"
     "「画質優先（CPU）」を選ぶと変換できます。"),
)


def _friendly(text):
    low = (text or "").lower()
    for keys, message in HINTS:
        if any(k in low for k in keys):
            return message + "\n\n詳細: " + (text or "").strip()[-300:]
    return None


def hint_for(text):
    low = (text or "").lower()
    for keys, message in HINTS:
        if any(k in low for k in keys):
            return message
    return None


def main(argv):
    """開発用の簡易実行: python convert.py <ファイル> [mp4|size|audio|gif]"""
    if len(argv) < 2:
        print(main.__doc__)
        return 2
    if binaries.missing():
        print("FFmpeg を取得します...")
        binaries.ensure_ffmpeg(lambda d, t, n: None)

    info = media_info.probe(argv[1])
    print(media_info.summary(info))

    options = dict(DEFAULT_OPTIONS)
    options["task"] = argv[2] if len(argv) > 2 else "mp4"
    job_plan = plan(info, options)
    for note in job_plan["notes"]:
        print("・" + note)
    for warning in job_plan["warnings"]:
        print("! " + warning)

    job = Job(info, options, os.path.dirname(os.path.abspath(argv[1])), job_plan)
    print("出力: {}".format(job.out_path))

    def show(ratio, label):
        print("\r  {:5.1f}%  {}".format(ratio * 100, label), end="")

    try:
        out = job.run(on_progress=show, log=lambda t: print("\n" + t))
    except Cancelled:
        print("\n中止しました。")
        return 1
    except (ConvertError, media_info.ProbeError) as e:
        print("\n失敗: {}".format(e))
        return 1
    print("\n完成: {}  ({})".format(out, media_info.human_size(os.path.getsize(out))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
