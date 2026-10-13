from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import BinaryIO

from .config import Settings
from .media import probe


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}


def store_video_upload(source: BinaryIO, filename: str, settings: Settings,
                       max_bytes: int = 500_000_000) -> Path:
    """Keep a local upload by content hash so a saved job can be recovered."""
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise ValueError("upload an MP4, MOV, MKV, or WebM video")
    directory = settings.work_dir / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    partial = directory / f"{uuid.uuid4().hex}.partial"
    digest = hashlib.sha256()
    size = 0
    try:
        source.seek(0)
        with partial.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"uploaded video exceeds the {max_bytes // 1_000_000} MB limit")
                output.write(chunk)
                digest.update(chunk)
        if size < 1000:
            raise ValueError("uploaded video is empty or too small")
        details = probe(partial)
        if not any(stream.get("codec_type") == "video" for stream in details.get("streams", [])):
            raise ValueError("uploaded file has no video stream")
        destination = directory / f"{digest.hexdigest()}{suffix}"
        if not destination.exists():
            partial.replace(destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)
