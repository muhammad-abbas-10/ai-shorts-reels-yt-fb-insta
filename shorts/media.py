from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .config import Settings, binary


def probe(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size < 1000:
        raise ValueError(f"media file is missing or too small: {path}")
    command = [binary("ffprobe", "FFPROBE_PATH"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(f"ffprobe cannot read {path.name}: {result.stderr.strip()[:300]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid data for {path.name}") from exc


def duration(path: Path) -> float:
    info = probe(path)
    if not any(stream.get("codec_type") == "audio" for stream in info.get("streams", [])):
        raise ValueError(f"audio stream is missing from {path.name}")
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"duration is unavailable for {path.name}") from exc


def validate_video(path: Path, settings: Settings, *, expected_seconds: float | None = None,
                   platforms: tuple[str, ...] = (), caption_path: Path | None = None) -> dict[str, Any]:
    info = probe(path)
    if "mp4" not in str(info.get("format", {}).get("format_name", "")):
        raise ValueError("video container must be MP4")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None or audio is None:
        raise ValueError("video must contain both video and audio streams")
    if video.get("codec_name") != "h264" or audio.get("codec_name") != "aac":
        raise ValueError("video must use H.264 video and AAC audio")
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if width < 360 or height < 640 or abs(width / height - 9 / 16) > 0.02:
        raise ValueError(f"video must be vertical 9:16, at least 360x640; got {width}x{height}")
    if platforms and (width < 540 or height < 960):
        raise ValueError("publishing requires at least 540x960 video")
    seconds = float(info.get("format", {}).get("duration", 0))
    if seconds < 3 or seconds > 180:
        raise ValueError(f"video duration {seconds:.1f}s is outside the supported 3-180s range")
    if expected_seconds is not None and abs(seconds - expected_seconds) > 1.25:
        raise ValueError(f"video duration {seconds:.1f}s differs from narration {expected_seconds:.1f}s")
    if "facebook" in platforms and not 4 <= seconds <= 60.2:
        raise ValueError("Facebook Reel duration must be 4-60 seconds")
    if "instagram" in platforms:
        if path.stat().st_size > 1_000_000_000:
            raise ValueError("Instagram Reel must be 1 GB or smaller")
        if int(audio.get("sample_rate", 48000)) != 48000:
            raise ValueError("Instagram Reel audio must be 48 kHz")
        try:
            frame_rate = float(Fraction(video.get("avg_frame_rate", "30/1")))
        except (ValueError, ZeroDivisionError):
            frame_rate = 0
        if not 23 <= frame_rate <= 60:
            raise ValueError("Instagram Reel frame rate must be 23-60 fps")
    if caption_path is not None and (not caption_path.is_file() or not caption_path.read_text(encoding="utf-8").strip()):
        raise ValueError("captions are missing or empty")
    if path.stat().st_size < 10_000:
        raise ValueError("rendered video is unexpectedly small")
    return {"duration_seconds": seconds, "width": width, "height": height,
            "video_codec": video["codec_name"], "audio_codec": audio["codec_name"],
            "size_bytes": path.stat().st_size, "format": info.get("format", {}).get("format_name")}
