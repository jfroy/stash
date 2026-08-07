#!/usr/bin/env python3
"""Bulk in-place transcoding plugin for Stash."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-lines,too-many-return-statements,too-many-arguments
# pylint: disable=too-many-positional-arguments,too-many-locals,too-many-branches
# pylint: disable=too-many-statements,broad-exception-caught

import datetime as dt
import hashlib
import json
import os
import shlex
import signal
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from shutil import rmtree

PLUGIN_ID = "bulk_transcode"
CONTAINER_EXTENSIONS = {
    "mp4": ".mp4",
    "mkv": ".mkv",
}
CONTAINER_MUXERS = {
    "mp4": "mp4",
    "mkv": "matroska",
}
CONTAINER_PROBE_FORMATS = {
    "mp4": {"mp4"},
    "mkv": {"matroska"},
}
TARGET_CODECS = {
    "hevc": {"hevc", "h265"},
    "av1": {"av1"},
}
MP4_AUDIO_COPY_CODECS = {"aac", "alac", "mp3", "ac3", "eac3"}
SOURCE_CODEC_FILTERS = {
    "h264": ("LOWER(video_files.video_codec) IN (?, ?)", ["h264", "avc1"]),
    "hevc": ("LOWER(video_files.video_codec) IN (?, ?)", ["hevc", "h265"]),
    "av1": ("LOWER(video_files.video_codec) = ?", ["av1"]),
    "vp9": ("LOWER(video_files.video_codec) = ?", ["vp9"]),
    "mpeg4": ("LOWER(video_files.video_codec) IN (?, ?)", ["mpeg4", "msmpeg4v3"]),
}
COMMON_SOURCE_CODECS = ["h264", "avc1", "hevc", "h265", "av1", "vp9", "mpeg4", "msmpeg4v3"]
RESOLUTION_FILTERS = {"all", "lt1080", "1080p", "4k"}

DEFAULTS = {
    "target": "hevc",
    "hardware_encoding": True,
    "hardware_encoder": "auto",
    "hardware_device": "",
    "hardware_fallback": True,
    "source_codec": "all",
    "source_resolution": "all",
    "scene_ids": "",
    "dry_run": False,
    "skip_if_target": True,
    "keep_backup": False,
    "rename_sidecars": True,
    "calculate_md5": True,
    "max_count": 0,
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "hevc_crf": 28,
    "hevc_preset": "medium",
    "hevc_video_extra_args": "",
    "av1_crf": 32,
    "av1_preset": "8",
    "av1_video_extra_args": "",
    "audio_extra_args": "",
    "target_size_percent": 60,
    "require_smaller_output": True,
    "extra_output_args": "",
    "generated_strategy": "migrate",
    "generated_previews": True,
    "generated_sprites": True,
    "generated_transcodes": True,
    "force_generated_transcodes": False,
}

VIDEO_QUERY = """
mutation VideoFiles($sql: String!, $args: [Any]) {
  querySQL(sql: $sql, args: $args) {
    columns
    rows
  }
}
"""

CONFIG_QUERY = """
query PluginConfig($pluginID: ID!) {
  configuration {
    general {
      generatedPath
      videoFileNamingAlgorithm
      ffmpegPath
      ffprobePath
      transcodeHardwareAcceleration
    }
    plugins(include: [$pluginID])
  }
}
"""

EXEC_MUTATION = """
mutation Exec($sql: String!, $args: [Any]) {
  execSQL(sql: $sql, args: $args) {
    rows_affected
    last_insert_id
  }
}
"""

GENERATE_MUTATION = """
mutation Generate($input: GenerateMetadataInput!) {
  metadataGenerate(input: $input)
}
"""


def _prefix(level):
    return "\x01" + level + "\x02"


def log(level, message):
    print(_prefix(level) + str(message) + "\n", file=sys.stderr, flush=True)


def info(message):
    log("i", message)


def warn(message):
    log("w", message)


def error(message):
    log("e", message)


def debug(message):
    log("d", message)


def progress(value):
    value = max(0.0, min(1.0, float(value)))
    log("p", str(value))


def as_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def as_int(value, default=0):
    if value is None or value == "":
        return default
    return int(float(value))


def as_str(value, default=""):
    if value is None:
        return default
    return str(value)


def normalize_settings(values, general=None):
    general = general or {}
    ret = dict(DEFAULTS)
    ret.update(values)
    ret["target"] = as_str(ret["target"], "hevc").strip().lower()
    ret["hardware_encoding"] = as_bool(
        ret.get("hardware_encoding"), DEFAULTS["hardware_encoding"]
    )
    ret["hardware_encoder"] = as_str(ret.get("hardware_encoder"), "auto").strip().lower() or "auto"
    ret["hardware_device"] = as_str(ret.get("hardware_device"), "").strip()
    ret["hardware_fallback"] = as_bool(
        ret.get("hardware_fallback"), DEFAULTS["hardware_fallback"]
    )
    ret["source_codec"] = as_str(ret.get("source_codec"), "all").strip().lower() or "all"
    ret["source_resolution"] = as_str(ret.get("source_resolution"), "all").strip().lower() or "all"
    ret["scene_ids"] = as_str(ret.get("scene_ids"), "")
    ret["dry_run"] = as_bool(ret.get("dry_run"), DEFAULTS["dry_run"])
    ret["skip_if_target"] = as_bool(ret.get("skip_if_target"), DEFAULTS["skip_if_target"])
    ret["keep_backup"] = as_bool(ret.get("keep_backup"), DEFAULTS["keep_backup"])
    ret["rename_sidecars"] = as_bool(
        ret.get("rename_sidecars"), DEFAULTS["rename_sidecars"]
    )
    ret["calculate_md5"] = as_bool(ret.get("calculate_md5"), DEFAULTS["calculate_md5"])
    ret["max_count"] = as_int(ret.get("max_count"), 0)
    ret["ffmpeg_path"] = (
        as_str(ret.get("ffmpeg_path"), "").strip()
        or as_str(general.get("ffmpegPath"), "").strip()
        or "ffmpeg"
    )
    ret["ffprobe_path"] = (
        as_str(ret.get("ffprobe_path"), "").strip()
        or as_str(general.get("ffprobePath"), "").strip()
        or "ffprobe"
    )
    ret["hevc_crf"] = as_int(ret.get("hevc_crf"), 28)
    ret["hevc_preset"] = as_str(ret.get("hevc_preset"), "medium").strip() or "medium"
    ret["hevc_video_extra_args"] = as_str(ret.get("hevc_video_extra_args"), "")
    ret["av1_crf"] = as_int(ret.get("av1_crf"), 32)
    ret["av1_preset"] = as_str(ret.get("av1_preset"), "8").strip() or "8"
    ret["av1_video_extra_args"] = as_str(ret.get("av1_video_extra_args"), "")
    ret["audio_extra_args"] = as_str(ret.get("audio_extra_args"), "")
    ret["target_size_percent"] = as_int(
        ret.get("target_size_percent"), DEFAULTS["target_size_percent"]
    )
    ret["require_smaller_output"] = as_bool(
        ret.get("require_smaller_output"), DEFAULTS["require_smaller_output"]
    )
    ret["extra_output_args"] = as_str(ret.get("extra_output_args"), "")
    ret["generated_strategy"] = as_str(ret.get("generated_strategy"), "migrate").strip().lower()
    ret["generated_previews"] = as_bool(
        ret.get("generated_previews"), DEFAULTS["generated_previews"]
    )
    ret["generated_sprites"] = as_bool(
        ret.get("generated_sprites"), DEFAULTS["generated_sprites"]
    )
    ret["generated_transcodes"] = as_bool(
        ret.get("generated_transcodes"), DEFAULTS["generated_transcodes"]
    )
    ret["force_generated_transcodes"] = as_bool(
        ret.get("force_generated_transcodes"), DEFAULTS["force_generated_transcodes"]
    )

    if ret["target"] not in {"hevc", "av1"}:
        raise ValueError("target must be 'hevc' or 'av1'")
    if ret["source_codec"] not in set(SOURCE_CODEC_FILTERS) | {"all", "other"}:
        raise ValueError(
            "source_codec must be 'all', 'h264', 'hevc', 'av1', 'vp9', 'mpeg4', or 'other'"
        )
    if ret["source_resolution"] not in RESOLUTION_FILTERS:
        raise ValueError("source_resolution must be 'all', 'lt1080', '1080p', or '4k'")
    if ret["generated_strategy"] not in {"migrate", "regenerate", "none"}:
        raise ValueError("generated_strategy must be 'migrate', 'regenerate', or 'none'")
    if ret["target_size_percent"] < 0 or ret["target_size_percent"] > 95:
        raise ValueError("target_size_percent must be between 0 and 95")

    return ret


class StashClient:
    def __init__(self, connection):
        scheme = connection.get("Scheme") or "http"
        host = connection.get("Host") or "localhost"
        if host in {"", "0.0.0.0", "::"}:
            host = "127.0.0.1"
        port = connection.get("Port")
        netloc = host
        if ":" in host and not host.startswith("["):
            netloc = "[" + host + "]"
        if port:
            netloc = netloc + ":" + str(port)
        self.url = scheme + "://" + netloc + "/graphql"
        self.context = ssl._create_unverified_context() if scheme == "https" else None

        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        cookie = connection.get("SessionCookie")
        if cookie and cookie.get("Value"):
            name = cookie.get("Name") or "session"
            self.headers["Cookie"] = name + "=" + cookie["Value"]

    def call(self, query, variables=None):
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload, headers=self.headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, context=self.context) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GraphQL HTTP error {exc.code}: {body}") from exc

        data = json.loads(body.decode("utf-8"))
        if data.get("errors"):
            raise RuntimeError(f"GraphQL error: {data['errors']}")
        if data.get("error"):
            raise RuntimeError(f"GraphQL error: {data['error']}")
        return data.get("data") or {}

    def query_sql(self, sql, args=None):
        result = self.call(VIDEO_QUERY, {"sql": sql, "args": args or []})
        return result["querySQL"]

    def exec_sql(self, sql, args=None):
        result = self.call(EXEC_MUTATION, {"sql": sql, "args": args or []})
        return result["execSQL"]

    def configuration(self):
        result = self.call(CONFIG_QUERY, {"pluginID": PLUGIN_ID})
        return result.get("configuration", {})

    def plugin_settings(self):
        plugins = self.configuration().get("plugins", {})
        return plugins.get(PLUGIN_ID, {})

    def generate_metadata(self, scene_ids, settings):
        if not scene_ids:
            return None

        input_data = {
            "sceneIDs": [str(scene_id) for scene_id in sorted(scene_ids)],
            "overwrite": True,
            "previews": settings["generated_previews"],
            "sprites": settings["generated_sprites"],
            "transcodes": settings["generated_transcodes"],
            "forceTranscodes": settings["force_generated_transcodes"],
        }
        result = self.call(GENERATE_MUTATION, {"input": input_data})
        return result.get("metadataGenerate")


def parse_scene_ids(value):
    value = value.strip()
    if not value:
        return []
    parts = [p for p in value.replace(";", ",").replace(" ", ",").split(",") if p]
    return [int(p) for p in parts]


def max_dimension_sql():
    width = "COALESCE(video_files.width, 0)"
    height = "COALESCE(video_files.height, 0)"
    return f"(CASE WHEN {width} >= {height} THEN {width} ELSE {height} END)"


def append_source_codec_filter(where, args, settings):
    source_codec = settings["source_codec"]
    if source_codec == "all":
        return
    if source_codec == "other":
        placeholders = ",".join(["?"] * len(COMMON_SOURCE_CODECS))
        where.append(
            "(video_files.video_codec IS NULL OR video_files.video_codec = '' "
            f"OR LOWER(video_files.video_codec) NOT IN ({placeholders}))"
        )
        args.extend(COMMON_SOURCE_CODECS)
        return

    sql, values = SOURCE_CODEC_FILTERS[source_codec]
    where.append(sql)
    args.extend(values)


def append_source_resolution_filter(where, settings):
    source_resolution = settings["source_resolution"]
    if source_resolution == "all":
        return

    max_dim = max_dimension_sql()
    where.append(max_dim + " > 0")
    if source_resolution == "lt1080":
        where.append(max_dim + " < 1920")
    elif source_resolution == "1080p":
        where.append("(" + max_dim + " >= 1920 AND " + max_dim + " < 3840)")
    elif source_resolution == "4k":
        where.append(max_dim + " >= 3840")


def find_video_files(client, settings):
    scene_ids = parse_scene_ids(settings["scene_ids"])
    args = []
    joins = [
        "JOIN folders ON folders.id = files.parent_folder_id",
        "JOIN video_files ON video_files.file_id = files.id",
        "LEFT JOIN scenes_files ON scenes_files.file_id = files.id",
        (
            "LEFT JOIN files_fingerprints AS fp_md5 ON "
            "fp_md5.file_id = files.id AND fp_md5.type = 'md5'"
        ),
        (
            "LEFT JOIN files_fingerprints AS fp_oshash ON "
            "fp_oshash.file_id = files.id AND fp_oshash.type = 'oshash'"
        ),
    ]
    where = ["files.zip_file_id IS NULL"]

    if scene_ids:
        placeholders = ",".join(["?"] * len(scene_ids))
        where.append(f"scenes_files.scene_id IN ({placeholders})")
        args.extend(scene_ids)

    append_source_codec_filter(where, args, settings)
    append_source_resolution_filter(where, settings)

    limit = settings["max_count"]
    limit_clause = ""
    if limit > 0:
        limit_clause = " LIMIT ?"
        args.append(limit)

    joins_sql = "\n".join(joins)
    where_sql = " AND ".join(where)
    sql = f"""
SELECT
  files.id,
  folders.path,
  files.basename,
  files.size,
  video_files.format,
  video_files.video_codec,
  video_files.width,
  video_files.height,
  CAST(fp_md5.fingerprint AS TEXT) AS md5,
  CAST(fp_oshash.fingerprint AS TEXT) AS oshash,
  GROUP_CONCAT(DISTINCT scenes_files.scene_id) AS scene_ids
FROM files
{joins_sql}
WHERE {where_sql}
GROUP BY files.id, folders.path, files.basename, files.size, video_files.format,
  video_files.video_codec, video_files.width, video_files.height,
  fp_md5.fingerprint, fp_oshash.fingerprint
ORDER BY files.id{limit_clause}
"""

    result = client.query_sql(sql, args)
    columns = result["columns"]
    return [dict(zip(columns, row)) for row in result["rows"]]


def target_container_for(source_media):
    return "mkv" if source_media.get("subtitle_streams") else "mp4"


def target_extension(container):
    return CONTAINER_EXTENSIONS[container]


def final_path_for(source_path, container):
    return source_path.with_suffix(target_extension(container))


def already_target(source_media, source_path, settings, container):
    target = settings["target"]
    video_codec = as_str(source_media.get("video_codec"), "").lower()
    suffix = source_path.suffix.lower()
    audio_supported = all(
        audio_codec_supported_by_container(container, as_str(stream.get("codec_name"), ""))
        for stream in source_media.get("audio_streams") or []
    )

    return (
        suffix == target_extension(container)
        and video_codec in TARGET_CODECS[target]
        and audio_supported
    )


def temp_path_for(source_path, container):
    stamp = f"{os.getpid()}-{int(time.time() * 1000)}"
    return source_path.with_name(
        source_path.name + ".stash-transcode-" + stamp + target_extension(container)
    )


def backup_path_for(source_path):
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    base = source_path.with_name(
        source_path.name + ".stash-transcode-backup-" + stamp + "-" + str(os.getpid())
    )
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = source_path.with_name(base.name + "-" + str(index))
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not create a unique backup path for {source_path}")


def available_ffmpeg_encoders(settings):
    if "_available_encoders" in settings:
        return settings["_available_encoders"]

    proc = subprocess.run(
        [settings["ffmpeg_path"], "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    encoders = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    settings["_available_encoders"] = encoders
    return encoders


def target_label(settings):
    return "HEVC" if settings["target"] == "hevc" else "AV1"


def hardware_aliases(target):
    return {
        "nvenc": target + "_nvenc",
        "nvidia": target + "_nvenc",
        "qsv": target + "_qsv",
        "intel": target + "_qsv",
        "vaapi": target + "_vaapi",
        "videotoolbox": target + "_videotoolbox",
        "apple": target + "_videotoolbox",
        "amf": target + "_amf",
        "amd": target + "_amf",
    }


def is_compatible_hardware_encoder(target, encoder):
    hardware_suffixes = ("_nvenc", "_qsv", "_vaapi", "_videotoolbox", "_amf")
    return encoder.startswith(target + "_") and encoder.endswith(hardware_suffixes)


def hardware_encoder_candidates(settings):
    target = settings["target"]
    candidates = [target + "_nvenc", target + "_qsv"]
    if settings["hardware_device"] or Path("/dev/dri/renderD128").exists():
        candidates.append(target + "_vaapi")
    candidates.append(target + "_amf")
    if sys.platform == "darwin":
        candidates.append(target + "_videotoolbox")
    return candidates


def select_hardware_encoder(settings):
    if not settings["hardware_encoding"]:
        return None

    target = settings["target"]
    aliases = hardware_aliases(target)
    requested = settings["hardware_encoder"]
    encoders = available_ffmpeg_encoders(settings)

    if requested != "auto":
        encoder = aliases.get(requested, requested)
        if not is_compatible_hardware_encoder(target, encoder):
            raise RuntimeError(
                f"requested encoder {encoder} is not compatible with target {target}"
            )
        if encoder in encoders:
            return encoder
        if settings["hardware_fallback"]:
            warn(
                f"Requested hardware encoder {encoder} is not available; "
                f"using software {target_label(settings)}"
            )
            return None
        raise RuntimeError(f"requested hardware encoder {encoder} is not available")

    for encoder in hardware_encoder_candidates(settings):
        if encoder in encoders:
            info(f"Using hardware encoder {encoder}")
            return encoder

    if settings["hardware_fallback"]:
        warn(
            f"No supported hardware {target_label(settings)} encoder found; using software encoder"
        )
        return None
    raise RuntimeError(f"no supported hardware {target_label(settings)} encoder found")


def stream_bitrate(stream):
    try:
        return as_int(stream.get("bit_rate"), 0)
    except ValueError:
        return 0


def estimated_audio_output_bitrate(source_media, container):
    total = 0
    for stream in source_media.get("audio_streams") or []:
        codec = as_str(stream.get("codec_name"), "").lower()
        if audio_codec_supported_by_container(container, codec):
            total += stream_bitrate(stream)
        else:
            total += 160_000
    return total


def target_video_bitrate(settings, source_media, container, source_size):
    target_percent = settings["target_size_percent"]
    try:
        duration = float(source_media.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if target_percent <= 0 or source_size is None or source_size <= 0 or duration <= 0:
        return None

    target_size = source_size * (target_percent / 100.0)
    target_total_bitrate = (target_size * 8) / duration
    video_bitrate = target_total_bitrate - estimated_audio_output_bitrate(
        source_media, container
    )
    if video_bitrate <= 0:
        return 50_000
    return max(50_000, int(video_bitrate))


def format_bitrate(bits_per_second):
    return f"{max(1, round(bits_per_second / 1000))}k"


def target_bitrate_args(video_bitrate):
    if not video_bitrate:
        return []
    maxrate = int(video_bitrate * 1.5)
    bufsize = maxrate * 2
    return [
        "-b:v",
        format_bitrate(video_bitrate),
        "-maxrate",
        format_bitrate(maxrate),
        "-bufsize",
        format_bitrate(bufsize),
    ]


def nvenc_rate_control_args(video_bitrate):
    args = target_bitrate_args(video_bitrate)
    if not args:
        return ["-b:v", "0"]
    return ["-rc", "vbr"] + args


def hevc_video_args(settings, encoder, video_bitrate=None):
    quality = str(settings["hevc_crf"])

    if encoder == "hevc_nvenc":
        args = [
            "-c:v",
            encoder,
        ]
        if not video_bitrate:
            args.extend(["-cq:v", quality])
        args.extend(nvenc_rate_control_args(video_bitrate))
        args.extend([
            "-tag:v",
            "hvc1",
        ])
        return args, encoder
    if encoder == "hevc_qsv":
        args = ["-c:v", encoder]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-global_quality", quality])
        args.extend(["-tag:v", "hvc1"])
        return args, encoder
    if encoder == "hevc_vaapi":
        args = [
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            encoder,
        ]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-qp", quality])
        args.extend([
            "-tag:v",
            "hvc1",
        ])
        return args, encoder
    if encoder == "hevc_videotoolbox":
        args = ["-c:v", encoder]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-q:v", quality])
        args.extend(["-tag:v", "hvc1"])
        return args, encoder
    if encoder:
        args = ["-c:v", encoder]
        args.extend(target_bitrate_args(video_bitrate))
        args.extend(["-tag:v", "hvc1"])
        return args, encoder

    args = [
        "-c:v",
        "libx265",
        "-preset",
        settings["hevc_preset"],
    ]
    if video_bitrate:
        args.extend(target_bitrate_args(video_bitrate))
    else:
        args.extend(["-crf", quality])
    args.extend([
        "-tag:v",
        "hvc1",
    ])
    return args, None


def select_av1_software_encoder(settings):
    encoders = available_ffmpeg_encoders(settings)
    for encoder in ["libsvtav1", "libaom-av1", "librav1e"]:
        if encoder in encoders:
            return encoder
    return "libsvtav1"


def av1_video_args(settings, encoder, video_bitrate=None):
    quality = str(settings["av1_crf"])
    preset = settings["av1_preset"]

    if encoder == "av1_nvenc":
        args = [
            "-c:v",
            encoder,
        ]
        if not video_bitrate:
            args.extend(["-cq:v", quality])
        args.extend(nvenc_rate_control_args(video_bitrate))
        return args, encoder
    if encoder == "av1_qsv":
        args = ["-c:v", encoder]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-global_quality", quality])
        return args, encoder
    if encoder == "av1_vaapi":
        args = ["-vf", "format=nv12,hwupload", "-c:v", encoder]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-qp", quality])
        return args, encoder
    if encoder == "av1_amf":
        args = [
            "-c:v",
            encoder,
            "-quality",
            "balanced",
        ]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-qp_i", quality, "-qp_p", quality])
        return args, encoder
    if encoder == "av1_videotoolbox":
        args = ["-c:v", encoder]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-q:v", quality])
        return args, encoder
    if encoder:
        args = ["-c:v", encoder]
        args.extend(target_bitrate_args(video_bitrate))
        return args, encoder

    software_encoder = select_av1_software_encoder(settings)
    if software_encoder == "libaom-av1":
        args = ["-c:v", software_encoder, "-cpu-used", preset]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-crf", quality, "-b:v", "0"])
        return args, None
    if software_encoder == "librav1e":
        args = ["-c:v", software_encoder, "-speed", preset]
        if video_bitrate:
            args.extend(target_bitrate_args(video_bitrate))
        else:
            args.extend(["-qp", quality])
        return args, None
    args = ["-c:v", software_encoder, "-preset", preset]
    if video_bitrate:
        args.extend(target_bitrate_args(video_bitrate))
    else:
        args.extend(["-crf", quality])
    return args, None


def extra_args(value):
    value = value.strip()
    return shlex.split(value) if value else []


def audio_codec_supported_by_container(container, codec):
    if container == "mkv":
        return True
    return codec.lower() in MP4_AUDIO_COPY_CODECS


def audio_args(source_media, container):
    args = []
    for index, stream in enumerate(source_media.get("audio_streams") or []):
        codec = as_str(stream.get("codec_name"), "").lower()
        if audio_codec_supported_by_container(container, codec):
            args.extend([f"-c:a:{index}", "copy"])
        else:
            args.extend([f"-c:a:{index}", "aac", f"-b:a:{index}", "160k"])
    return args


def subtitle_args(container):
    if container != "mkv":
        return []
    return ["-c:s", "copy", "-c:t", "copy"]


def ffmpeg_args(
    source_path,
    output_path,
    settings,
    source_media,
    container,
    force_software=False,
    source_size=None,
):
    args = [
        settings["ffmpeg_path"],
        "-hide_banner",
        "-nostdin",
        "-y",
        "-nostats",
        "-progress",
        "pipe:1",
    ]
    selected_encoder = None

    if not force_software:
        selected_encoder = select_hardware_encoder(settings)
        if selected_encoder and selected_encoder.endswith("_nvenc"):
            args.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
        if selected_encoder and selected_encoder.endswith("_vaapi"):
            device = settings["hardware_device"] or "/dev/dri/renderD128"
            args.extend(["-vaapi_device", device])

    if source_size is None:
        try:
            source_size = source_path.stat().st_size
        except OSError:
            source_size = None
    video_bitrate = target_video_bitrate(settings, source_media, container, source_size)

    args.extend(
        [
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-dn",
            "-map_metadata",
            "0",
        ]
    )
    if container == "mkv":
        args.extend(["-map", "0:s?", "-map", "0:t?"])

    if settings["target"] == "hevc":
        video_args, encoder = hevc_video_args(settings, selected_encoder, video_bitrate)
        selected_encoder = selected_encoder or encoder
        args.extend(video_args)
        args.extend(extra_args(settings["hevc_video_extra_args"]))
    else:
        video_args, encoder = av1_video_args(settings, selected_encoder, video_bitrate)
        selected_encoder = selected_encoder or encoder
        args.extend(video_args)
        args.extend(extra_args(settings["av1_video_extra_args"]))

    args.extend(audio_args(source_media, container))
    args.extend(extra_args(settings["audio_extra_args"]))
    args.extend(subtitle_args(container))
    if container == "mp4":
        args.extend(["-movflags", "+faststart"])

    extra = settings["extra_output_args"].strip()
    if extra:
        args.extend(shlex.split(extra))

    args.extend(["-f", CONTAINER_MUXERS[container]])
    args.append(str(output_path))
    return args, selected_encoder


def format_command(cmd):
    return " ".join(shlex.quote(str(arg)) for arg in cmd)


def format_duration(seconds):
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {seconds:.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {seconds:.1f}s"


def format_file_size(size):
    size = int(size)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def size_savings_percent(before_size, after_size):
    if before_size <= 0:
        return 0.0
    return ((before_size - after_size) / before_size) * 100


def parse_ffmpeg_time(value):
    if not value:
        return None
    try:
        hours, minutes, seconds = value.split(":", 2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None


def ffmpeg_progress_fraction(fields, duration):
    try:
        duration = float(duration or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        return None

    seconds = None
    for key in ("out_time_us", "out_time_ms"):
        value = fields.get(key)
        if value:
            try:
                seconds = float(value) / 1_000_000
                break
            except ValueError:
                pass
    if seconds is None:
        seconds = parse_ffmpeg_time(fields.get("out_time"))
    if seconds is None:
        return None

    return max(0.0, min(1.0, seconds / duration))


def run_ffmpeg_command(cmd, duration=0, progress_callback=None):
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_lines = deque(maxlen=200)

    def collect_stderr():
        try:
            for line in proc.stderr:
                stderr_lines.append(line)
        finally:
            proc.stderr.close()

    stderr_thread = threading.Thread(target=collect_stderr)
    stderr_thread.start()

    fields = {}
    try:
        for raw_line in proc.stdout:
            key, separator, value = raw_line.strip().partition("=")
            if not separator:
                continue
            fields[key] = value
            if key in {"out_time_us", "out_time_ms", "out_time"} and progress_callback:
                fraction = ffmpeg_progress_fraction(fields, duration)
                if fraction is not None:
                    progress_callback(fraction)
            elif key == "progress" and value == "end" and progress_callback:
                progress_callback(1.0)
    finally:
        proc.stdout.close()

    returncode = proc.wait()
    stderr_thread.join()
    if returncode == 0 and progress_callback:
        progress_callback(1.0)

    return returncode, "".join(stderr_lines)[-4000:]


def run_ffmpeg(
    source_path,
    temp_path,
    settings,
    source_media,
    container,
    progress_callback=None,
    cmd=None,
    selected_encoder=None,
):
    if cmd is None:
        cmd, selected_encoder = ffmpeg_args(
            source_path, temp_path, settings, source_media, container
        )
    debug(f"Running: {format_command(cmd)}")
    returncode, stderr = run_ffmpeg_command(
        cmd,
        duration=source_media.get("duration", 0),
        progress_callback=progress_callback,
    )
    if returncode != 0:
        if selected_encoder and settings["hardware_fallback"]:
            warn(
                f"Hardware encoder {selected_encoder} failed for {source_path}; "
                "retrying with software encoder"
            )
            cleanup_temp(temp_path)
            cmd, _ = ffmpeg_args(
                source_path, temp_path, settings, source_media, container, force_software=True
            )
            debug(f"Running fallback: {format_command(cmd)}")
            returncode, stderr = run_ffmpeg_command(
                cmd,
                duration=source_media.get("duration", 0),
                progress_callback=progress_callback,
            )
            if returncode == 0:
                return

        raise RuntimeError(f"ffmpeg failed for {source_path}: {stderr}")


def parse_rate(value):
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            den_f = float(den)
            if den_f == 0:
                return 0.0
            return round(float(num) / den_f, 2)
        except ValueError:
            return 0.0
    try:
        return round(float(value), 2)
    except ValueError:
        return 0.0


def is_rotated(stream):
    rotate = as_int(stream.get("tags", {}).get("rotate"), 0)
    if rotate not in {0, 180, -180}:
        return True
    for side_data in stream.get("side_data_list") or []:
        rotation = as_int(side_data.get("rotation"), 0)
        if abs(rotation) not in {0, 180}:
            return True
    return False


def container_from_probe(format_name, path):
    if format_name == "mov,mp4,m4a,3gp,3g2,mj2":
        return "mp4"
    if format_name == "asf":
        return "wmv"
    if format_name == "matroska,webm":
        suffix = path.suffix.lower()
        return "webm" if suffix == ".webm" else "matroska"
    return format_name or path.suffix.lower().lstrip(".")


def probe_video(path, settings):
    cmd = [
        settings["ffprobe_path"],
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_error",
        str(path),
    ]
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr[-4000:]}")
    data = json.loads(proc.stdout)
    if data.get("error"):
        raise RuntimeError(f"ffprobe error for {path}: {data['error']}")

    streams = data.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    audio_stream = next(iter(audio_streams), None)
    if not video_stream:
        raise RuntimeError(f"ffprobe did not find a video stream in {path}")

    fmt = data.get("format") or {}
    width = as_int(video_stream.get("width"), 0)
    height = as_int(video_stream.get("height"), 0)
    if is_rotated(video_stream):
        width, height = height, width

    try:
        duration = round(float(fmt.get("duration") or 0), 2)
    except ValueError:
        duration = 0.0

    bit_rate = as_int(fmt.get("bit_rate"), as_int(video_stream.get("bit_rate"), 0))

    return {
        "duration": duration,
        "video_codec": video_stream.get("codec_name") or "",
        "format": container_from_probe(fmt.get("format_name") or "", path),
        "audio_codec": (audio_stream or {}).get("codec_name") or "",
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
        "width": width,
        "height": height,
        "frame_rate": parse_rate(video_stream.get("avg_frame_rate")),
        "bit_rate": bit_rate,
    }


def md5_file(path):
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def oshash_file(path):
    size = path.stat().st_size
    if size <= 8:
        raise RuntimeError("cannot calculate oshash for files smaller than 8 bytes")

    chunk_size = 64 * 1024
    if size < chunk_size:
        chunk_size = (size // 8) * 8

    with path.open("rb") as handle:
        head = handle.read(chunk_size)
        handle.seek(-chunk_size, os.SEEK_END)
        tail = handle.read(chunk_size)

    def sum_words(buf):
        total = 0
        for offset in range(0, len(buf), 8):
            total = (total + struct.unpack_from("<Q", buf, offset)[0]) & 0xFFFFFFFFFFFFFFFF
        return total

    value = (sum_words(head) + sum_words(tail) + size) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def sql_timestamp(ts):
    return (
        dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def update_database(client, file_id, final_path, media, fingerprints):
    stat = final_path.stat()
    mod_time = sql_timestamp(stat.st_mtime)
    interactive = final_path.with_suffix(".funscript").exists()

    statements = [
        """
UPDATE files
SET basename = ?, size = ?, mod_time = ?, updated_at = CURRENT_TIMESTAMP
WHERE id = ?
""",
        """
UPDATE video_files
SET duration = ?, video_codec = ?, format = ?, audio_codec = ?,
    width = ?, height = ?, frame_rate = ?, bit_rate = ?, interactive = ?
WHERE file_id = ?
""",
        "DELETE FROM files_fingerprints WHERE file_id = ? AND type IN ('md5', 'oshash')",
    ]
    args = [
        final_path.name,
        stat.st_size,
        mod_time,
        file_id,
        media["duration"],
        media["video_codec"],
        media["format"],
        media["audio_codec"],
        media["width"],
        media["height"],
        media["frame_rate"],
        media["bit_rate"],
        1 if interactive else 0,
        file_id,
        file_id,
    ]

    for fp_type, fingerprint in fingerprints.items():
        statements.append(
            "INSERT INTO files_fingerprints (file_id, type, fingerprint) VALUES (?, ?, ?)"
        )
        args.extend([file_id, fp_type, fingerprint])

    client.exec_sql(";\n".join(statements), args)


def row_scene_ids(row):
    value = as_str(row.get("scene_ids"), "")
    if not value:
        return []
    return [int(v) for v in value.split(",") if v]


def configured_hash_algorithm(general):
    value = as_str(general.get("videoFileNamingAlgorithm"), "oshash").lower()
    return "md5" if value == "md5" else "oshash"


def row_hash(row, general):
    return as_str(row.get(configured_hash_algorithm(general)), "")


def fingerprint_hash(fingerprints, general):
    return fingerprints.get(configured_hash_algorithm(general), "")


def generated_file_pairs(generated_path, old_hash, new_hash, settings):
    generated_path = Path(generated_path)
    pairs = []
    if settings["generated_previews"]:
        pairs.extend(
            [
                (
                    generated_path / "screenshots" / (old_hash + ".mp4"),
                    generated_path / "screenshots" / (new_hash + ".mp4"),
                ),
                (
                    generated_path / "screenshots" / (old_hash + ".webp"),
                    generated_path / "screenshots" / (new_hash + ".webp"),
                ),
            ]
        )
    if settings["generated_sprites"]:
        pairs.extend(
            [
                (
                    generated_path / "vtt" / (old_hash + "_thumbs.vtt"),
                    generated_path / "vtt" / (new_hash + "_thumbs.vtt"),
                ),
                (
                    generated_path / "vtt" / (old_hash + "_sprite.jpg"),
                    generated_path / "vtt" / (new_hash + "_sprite.jpg"),
                ),
            ]
        )
    if settings["generated_transcodes"]:
        pairs.append(
            (
                generated_path / "transcodes" / (old_hash + ".mp4"),
                generated_path / "transcodes" / (new_hash + ".mp4"),
            )
        )
    return pairs


def update_sprite_vtt(vtt_path, old_hash, new_hash):
    if not vtt_path.exists():
        return
    old_name = old_hash + "_sprite.jpg"
    new_name = new_hash + "_sprite.jpg"
    data = vtt_path.read_bytes()
    data = data.replace(old_name.encode("utf-8"), new_name.encode("utf-8"))
    vtt_path.write_bytes(data)


def migrate_generated_files(general, old_hash, new_hash, settings):
    generated_path = as_str(general.get("generatedPath"), "")
    if not generated_path or not old_hash or not new_hash or old_hash == new_hash:
        return

    migrated_vtt = None
    for old_path, new_path in generated_file_pairs(generated_path, old_hash, new_hash, settings):
        if not old_path.exists():
            continue
        if new_path.exists():
            warn(f"Skipping generated migration because target exists: {new_path}")
            continue
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        info(f"Migrated generated file {old_path} -> {new_path}")
        if old_path.name.endswith("_thumbs.vtt"):
            migrated_vtt = new_path

    if migrated_vtt:
        update_sprite_vtt(migrated_vtt, old_hash, new_hash)


def delete_generated_files(general, old_hash, new_hash, settings):
    generated_path = as_str(general.get("generatedPath"), "")
    if not generated_path:
        return

    hashes = {h for h in [old_hash, new_hash] if h}
    for hash_value in hashes:
        for old_path, _ in generated_file_pairs(generated_path, hash_value, hash_value, settings):
            if old_path.is_dir():
                rmtree(old_path)
            elif old_path.exists():
                old_path.unlink()


def matching_sidecars(source_path, final_path):
    if source_path.stem == final_path.stem:
        return []

    known_exts = {".srt", ".vtt", ".ass", ".ssa", ".funscript"}
    ret = []
    old_prefix = source_path.stem + "."
    new_prefix = final_path.stem + "."
    for candidate in source_path.parent.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in known_exts:
            continue
        if candidate.name == source_path.with_suffix(".funscript").name:
            ret.append((candidate, final_path.with_suffix(".funscript")))
        elif candidate.name.startswith(old_prefix):
            ret.append(
                (candidate, candidate.with_name(new_prefix + candidate.name[len(old_prefix) :]))
            )
    return ret


def rename_sidecars(client, file_id, source_path, final_path):
    for old_path, new_path in matching_sidecars(source_path, final_path):
        if old_path == new_path:
            continue
        if new_path.exists():
            warn(f"Skipping sidecar rename because target exists: {new_path}")
            continue
        old_name = old_path.name
        new_name = new_path.name
        os.replace(old_path, new_path)
        if old_path.suffix.lower() in {".srt", ".vtt"}:
            client.exec_sql(
                "UPDATE video_captions SET filename = ? WHERE file_id = ? AND filename = ?",
                [new_name, file_id, old_name],
            )
        info(f"Renamed sidecar {old_name} -> {new_name}")


def swap_files(source_path, temp_path, final_path):
    backup_path = backup_path_for(source_path)
    if final_path != source_path and final_path.exists():
        raise RuntimeError(f"target already exists: {final_path}")

    os.replace(source_path, backup_path)
    try:
        os.replace(temp_path, final_path)
    except Exception:
        os.replace(backup_path, source_path)
        raise
    return backup_path


def restore_after_failure(source_path, final_path, backup_path):
    try:
        if final_path.exists():
            final_path.unlink()
        if backup_path.exists():
            os.replace(backup_path, source_path)
    except Exception as exc:
        error(f"Failed to restore original file {source_path}: {exc}")


def cleanup_temp(path):
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        warn(f"Failed to remove temp file {path}: {exc}")


def process_file(client, row, settings, general, progress_callback=None):
    file_id = int(row["id"])
    source_path = Path(row["path"]) / row["basename"]
    scene_ids = row_scene_ids(row)

    if not source_path.exists():
        raise RuntimeError(f"source file is missing: {source_path}")

    source_media = probe_video(source_path, settings)
    container = target_container_for(source_media)
    final_path = final_path_for(source_path, container)
    temp_path = temp_path_for(source_path, container)

    if settings["skip_if_target"] and already_target(
        source_media, source_path, settings, container
    ):
        info(f"Skipping {source_path}, already matches target {settings['target']}")
        return "skipped", scene_ids

    if final_path != source_path and final_path.exists():
        raise RuntimeError(f"target already exists: {final_path}")

    source_size = source_path.stat().st_size

    if settings["dry_run"]:
        info(f"Would transcode {source_path} -> {final_path}")
        cmd, _ = ffmpeg_args(
            source_path,
            temp_path,
            settings,
            source_media,
            container,
            source_size=source_size,
        )
        info(f"Would run ffmpeg: {format_command(cmd)}")
        return "dry_run", scene_ids

    started_at = time.monotonic()
    cmd, selected_encoder = ffmpeg_args(
        source_path,
        temp_path,
        settings,
        source_media,
        container,
        source_size=source_size,
    )
    info(
        f"Starting transcode {source_path} -> {final_path}; "
        f"ffmpeg: {format_command(cmd)}"
    )
    try:
        run_ffmpeg(
            source_path,
            temp_path,
            settings,
            source_media,
            container,
            progress_callback=progress_callback,
            cmd=cmd,
            selected_encoder=selected_encoder,
        )
        media = probe_video(temp_path, settings)
        if media["format"] not in CONTAINER_PROBE_FORMATS[container]:
            raise RuntimeError(
                f"transcode output container was {media['format']}, expected {container}"
            )
        if media["video_codec"].lower() not in TARGET_CODECS[settings["target"]]:
            raise RuntimeError(
                f"transcode output codec was {media['video_codec']}, "
                f"expected {settings['target']}"
            )
        if (
            container == "mkv"
            and source_media["subtitle_streams"]
            and not media["subtitle_streams"]
        ):
            raise RuntimeError("transcode output did not preserve embedded subtitle tracks")
        post_size = temp_path.stat().st_size
        elapsed = format_duration(time.monotonic() - started_at)
        savings = size_savings_percent(source_size, post_size)
        if settings["require_smaller_output"] and post_size >= source_size:
            info(
                f"Finished transcode {source_path} -> {final_path}: discarded in {elapsed}; "
                f"size {format_file_size(source_size)} -> {format_file_size(post_size)}; "
                f"savings {savings:.1f}%; output is not smaller than original"
            )
            cleanup_temp(temp_path)
            return "skipped", scene_ids

        fingerprints = {"oshash": oshash_file(temp_path)}
        if settings["calculate_md5"]:
            fingerprints["md5"] = md5_file(temp_path)
        info(
            f"Finished transcode {source_path} -> {final_path}: success in {elapsed}; "
            f"size {format_file_size(source_size)} -> {format_file_size(post_size)}; "
            f"savings {savings:.1f}%"
        )
    except Exception as exc:
        elapsed = format_duration(time.monotonic() - started_at)
        info(
            f"Finished transcode {source_path} -> {final_path}: failed in {elapsed}; {exc}"
        )
        cleanup_temp(temp_path)
        raise

    old_hash = row_hash(row, general)
    new_hash = fingerprint_hash(fingerprints, general)

    backup_path = swap_files(source_path, temp_path, final_path)
    try:
        update_database(client, file_id, final_path, media, fingerprints)
    except Exception:
        restore_after_failure(source_path, final_path, backup_path)
        raise

    if settings["generated_strategy"] == "migrate":
        migrate_generated_files(general, old_hash, new_hash, settings)
    elif settings["generated_strategy"] == "regenerate":
        delete_generated_files(general, old_hash, new_hash, settings)

    if settings["rename_sidecars"]:
        try:
            rename_sidecars(client, file_id, source_path, final_path)
        except Exception as exc:
            warn(f"Sidecar rename failed for {final_path}: {exc}")

    if settings["keep_backup"]:
        info(f"Kept backup at {backup_path}")
    else:
        cleanup_temp(backup_path)

    return "transcoded", scene_ids


def run(plugin_input):
    client = StashClient(plugin_input["server_connection"])
    config = client.configuration()
    general = config.get("general") or {}
    settings = dict(DEFAULTS)
    settings.update(plugin_input.get("args") or {})
    settings.update((config.get("plugins") or {}).get(PLUGIN_ID, {}))
    settings = normalize_settings(settings, general)

    if configured_hash_algorithm(general) == "md5" and not settings["calculate_md5"]:
        warn(
            "Stash is configured to use MD5 video file naming; "
            "enabling MD5 calculation for generated file handling"
        )
        settings["calculate_md5"] = True

    files = find_video_files(client, settings)
    total = len(files)
    info(f"Found {total} video file(s) for target {settings['target']}")
    if total == 0:
        progress(1)
        return {"processed": 0, "transcoded": 0, "skipped": 0, "failed": 0}

    counts = {"processed": 0, "transcoded": 0, "skipped": 0, "failed": 0}
    regenerate_scene_ids = set()
    for index, row in enumerate(files, start=1):
        last_file_fraction = 0.0

        def report_file_progress(fraction):
            nonlocal last_file_fraction
            fraction = max(0.0, min(1.0, float(fraction)))
            if fraction < last_file_fraction:
                return
            last_file_fraction = fraction
            progress((index - 1 + fraction) / total)

        try:
            result, scene_ids = process_file(
                client,
                row,
                settings,
                general,
                progress_callback=report_file_progress,
            )
            counts["processed"] += 1
            if result == "transcoded":
                counts["transcoded"] += 1
                if settings["generated_strategy"] == "regenerate":
                    regenerate_scene_ids.update(scene_ids)
            else:
                counts["skipped"] += 1
        except Exception as exc:
            counts["failed"] += 1
            error(f"Failed file id {row.get('id')}: {exc}")
        finally:
            progress(index / total)

    if settings["generated_strategy"] == "regenerate" and regenerate_scene_ids:
        job_id = client.generate_metadata(regenerate_scene_ids, settings)
        info(f"Queued generated file regeneration job {job_id}")
        counts["regenerate_job_id"] = job_id

    info(f"Finished bulk transcode: {counts}")
    return counts


def main():
    output = {}
    try:
        plugin_input = json.loads(sys.stdin.read())
        output["output"] = run(plugin_input)
    except Exception as exc:
        output["error"] = str(exc)
    print(json.dumps(output))


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    main()
