from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

from .config import Settings, binary


log = logging.getLogger(__name__)


def _run_ffmpeg(arguments: list[str], cwd: Path, timeout: int = 1800) -> None:
    command = [binary("ffmpeg", "FFMPEG_PATH"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *arguments]
    log.info("FFmpeg started in %s", cwd)
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg timed out after {timeout} seconds") from exc
    if result.returncode:
        raise RuntimeError(f"FFmpeg failed: {result.stderr.strip()[-1000:]}")


def render_video(sources: list[Path], voice_path: Path, ass_path: Path,
                 audio_seconds: float, job_dir: Path, settings: Settings,
                 footage_start_seconds: float = 0.0) -> Path:
    if not sources:
        raise ValueError("rendering needs at least one footage source")
    temp = job_dir / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    # A scene may be a still image or a video; each scene is normalized before concatenation.
    scene_count = min(4, len(sources))
    segment_seconds = audio_seconds / scene_count + 0.15
    scene_paths: list[Path] = []
    for index, source in enumerate(sources[:scene_count]):
        output = temp / f"scene_{index}.mp4"
        if source.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            input_args = ["-loop", "1", "-framerate", str(settings.fps), "-i", str(source)]
        else:
            input_args = ["-stream_loop", "-1", "-i", str(source)]
            if index == 0 and footage_start_seconds:
                input_args += ["-ss", f"{footage_start_seconds:.3f}"]
        filters = (f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase,"
                   f"crop={settings.width}:{settings.height},setsar=1,fps={settings.fps},format=yuv420p")
        _run_ffmpeg([*input_args, "-t", f"{segment_seconds:.3f}", "-an", "-vf", filters,
                     "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
                     "-video_track_timescale", "30000", str(output)], job_dir)
        scene_paths.append(output)
    concat = temp / "scenes.txt"
    concat.write_text("".join(f"file '{path.name}'\n" for path in scene_paths), encoding="utf-8")
    temporary = job_dir / "final.partial.mp4"
    final = job_dir / "final.mp4"
    temporary.unlink(missing_ok=True)
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(voice_path),
                 "-t", f"{audio_seconds:.3f}", "-map", "0:v:0", "-map", "1:a:0",
                 "-vf", f"ass={ass_path.name}", "-c:v", "libx264", "-preset", settings.preset,
                 "-crf", str(settings.crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                 "-b:a", "128k", "-movflags", "+faststart", str(temporary)], job_dir)
    if not temporary.is_file() or temporary.stat().st_size < 10_000:
        raise RuntimeError("FFmpeg finished without producing a usable video")
    temporary.replace(final)
    return final
