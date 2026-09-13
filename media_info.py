# -*- coding: utf-8 -*-
"""ffprobe で入力ファイルの中身を調べる。

「そのままコピーできるか（＝再エンコードせずに済むか）」を判断するための材料を集める。
ここで取った情報が convert.plan() の入力になる。

単体でも動かせる:

    python media_info.py <ファイル>
"""

from __future__ import annotations

import json
import os
import sys

import binaries


class ProbeError(Exception):
    """情報を取れなかった。メッセージはそのまま利用者に見せる。"""


# 映像が無い＝音声だけのファイル、として扱う拡張子の目安
# （ffprobe が「映像あり」と言っても、ジャケット画像なら映像として扱わない）


def probe(path, timeout=60):
    """ファイルを調べて情報の dict を返す。

    返す形:
        {
          "path", "size", "duration", "format",
          "video": {"codec","width","height","fps","pix_fmt","bit_rate",
                    "rotation","hdr","color_transfer"} または None,
          "audio": {"codec","channels","sample_rate","bit_rate"} または None,
        }
    """
    if not os.path.isfile(path):
        raise ProbeError("ファイルが見つかりません: {}".format(path))
    if not binaries.ffmpeg_ok():
        raise ProbeError("FFmpeg がまだ取得されていません。")

    args = [
        binaries.ffprobe_path(),
        "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    code, out, err = binaries.run_capture(args, timeout=timeout)
    if code != 0 or not out.strip():
        raise ProbeError(_reason(err) or "ファイルの情報を読み取れませんでした。")
    try:
        data = json.loads(out)
    except ValueError:
        raise ProbeError("ffprobe の応答を読み取れませんでした。")

    fmt = data.get("format") or {}
    info = {
        "path": path,
        "size": _int(fmt.get("size")) or _file_size(path),
        "duration": _float(fmt.get("duration")),
        "format": fmt.get("format_name") or "",
        "video": None,
        "audio": None,
    }

    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        if kind == "video" and info["video"] is None:
            if _is_cover_art(stream):
                continue          # mp3 などに埋まったジャケット画像
            info["video"] = _video_of(stream)
        elif kind == "audio" and info["audio"] is None:
            info["audio"] = _audio_of(stream)

    if info["video"] is None and info["audio"] is None:
        raise ProbeError("映像も音声も見つかりませんでした。\n"
                         "動画・音声のファイルを指定してください。")

    if info["duration"] is None and info["video"]:
        info["duration"] = _float((info["video"] or {}).get("stream_duration"))
    return info


def _video_of(stream):
    rotation = _rotation_of(stream)
    width = _int(stream.get("width")) or 0
    height = _int(stream.get("height")) or 0
    transfer = (stream.get("color_transfer") or "").lower()
    return {
        "codec": (stream.get("codec_name") or "").lower(),
        "width": width,
        "height": height,
        "fps": _fps_of(stream),
        "pix_fmt": (stream.get("pix_fmt") or "").lower(),
        "bit_rate": _int(stream.get("bit_rate")),
        "rotation": rotation,
        "color_transfer": transfer,
        # HDR10（PQ）と HLG。SDR に落とさずに再エンコードすると色がくすむ
        "hdr": transfer in ("smpte2084", "arib-std-b67"),
        "stream_duration": stream.get("duration"),
    }


def _audio_of(stream):
    return {
        "codec": (stream.get("codec_name") or "").lower(),
        "channels": _int(stream.get("channels")) or 2,
        "sample_rate": _int(stream.get("sample_rate")),
        "bit_rate": _int(stream.get("bit_rate")),
    }


def _rotation_of(stream):
    """回転の指定を度で返す。スマホで撮った縦動画は 90 / 270 が入っている。"""
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tags = stream.get("tags") or {}
    try:
        return int(round(float(tags.get("rotate", 0)))) % 360
    except (TypeError, ValueError):
        return 0


def _fps_of(stream):
    for key in ("avg_frame_rate", "r_frame_rate"):
        text = stream.get(key) or ""
        if "/" in text:
            num, _, den = text.partition("/")
            try:
                num, den = float(num), float(den)
            except ValueError:
                continue
            if den > 0 and num > 0:
                return num / den
    return None


def _is_cover_art(stream):
    disposition = stream.get("disposition") or {}
    if disposition.get("attached_pic"):
        return True
    # 静止画コーデックが 1 枚だけ入っている場合もジャケット画像とみなす
    return (stream.get("codec_name") or "").lower() in ("mjpeg", "png", "bmp", "gif") \
        and _int(stream.get("nb_frames")) in (None, 1)


def _reason(err):
    """ffprobe のエラー文から、それらしい説明を作る。"""
    low = (err or "").lower()
    if "no such file" in low:
        return "ファイルが見つかりません。"
    if "invalid data found" in low:
        return "このファイルは動画・音声として読み取れませんでした。\n" \
               "壊れているか、対応していない形式の可能性があります。"
    if "permission denied" in low:
        return "ファイルを開けませんでした（アクセス権限）。"
    return (err or "").strip()[:300] or None


# ------------------------------------------------------------------ 小物


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# ------------------------------------------------------------------ 表示用


def display_size(info):
    """回転を反映した「見た目の」幅と高さ。縦動画の判定に使う。"""
    video = info.get("video")
    if not video:
        return (0, 0)
    width, height = video["width"], video["height"]
    if video.get("rotation") in (90, 270):
        return (height, width)
    return (width, height)


def human_size(size):
    if not size:
        return "―"
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return "{:,.1f} {}".format(value, unit) if unit != "B" else "{:,.0f} B".format(value)
        value /= 1024
    return "{:,.1f} GB".format(value)


def human_duration(seconds):
    if not seconds or seconds <= 0:
        return "―"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{}:{:02d}".format(minutes, secs)


def summary(info):
    """一覧に出す 1 行。「1920x1080 h264 / aac  3:24  120.5 MB」の形。"""
    parts = []
    video = info.get("video")
    audio = info.get("audio")
    if video:
        width, height = display_size(info)
        text = "{}x{} {}".format(width, height, video["codec"])
        if video.get("fps"):
            text += " {:.0f}fps".format(video["fps"])
        if video.get("hdr"):
            text += " HDR"
        parts.append(text)
    if audio:
        parts.append(audio["codec"])
    head = " / ".join(parts) if parts else "―"
    return "{}　{}　{}".format(head, human_duration(info.get("duration")),
                              human_size(info.get("size")))


def main(argv):
    if len(argv) < 2:
        print("使い方: python media_info.py <ファイル>")
        return 2
    try:
        info = probe(argv[1])
    except ProbeError as e:
        print("失敗: {}".format(e))
        return 1
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print()
    print(summary(info))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
