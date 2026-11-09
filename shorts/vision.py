from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

from .config import Settings, binary
from .local_vision import LocalVision
from .media import probe
from .net import ExternalError, request_json


log = logging.getLogger(__name__)
_CHECK_VERSION = "stock-frames-v2"


def _portrait_frame(path: Path, seconds: float) -> Image.Image:
    command = [binary("ffmpeg", "FFMPEG_PATH"), "-hide_banner", "-loglevel", "error",
               "-nostdin", "-ss", f"{seconds:.3f}", "-i", str(path),
               "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode or not result.stdout:
        raise ValueError("could not inspect stock video frame")
    with Image.open(io.BytesIO(result.stdout)) as source:
        return ImageOps.fit(source.convert("RGB"), (360, 640), method=Image.Resampling.LANCZOS)


def _sample_frames(path: Path, scene_seconds: float) -> tuple[Image.Image, Image.Image]:
    details = probe(path)
    duration = float(details.get("format", {}).get("duration") or 0)
    window = max(0.5, min(duration or scene_seconds, scene_seconds))
    times = (max(0.1, window * 0.2), max(0.2, window * 0.7))
    return _portrait_frame(path, times[0]), _portrait_frame(path, times[1])


def _contact_sheet(frames: tuple[Image.Image, Image.Image]) -> bytes:
    sheet = Image.new("RGB", (720, 640), "#101820")
    for index, frame in enumerate(frames):
        sheet.paste(frame, (index * 360, 0))
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=73, optimize=True)
    return buffer.getvalue()


class FrameVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.local = (LocalVision(settings.work_dir / "models" / "clip-vit-base-patch32")
                      if LocalVision.dependencies_available() else None)
        self.remote_available = bool(settings.llm_api_key and settings.stock_vision_model)
        self.available = bool(self.local or self.remote_available)
        self.cache_dir = settings.work_dir / "cache" / "vision"

    def _cache_file(self, path: Path, query: str, topic: str,
                    scene_seconds: float, model: str) -> Path:
        stats = path.stat()
        key = hashlib.sha256(json.dumps(
            [_CHECK_VERSION, str(path.resolve()), stats.st_size, stats.st_mtime_ns,
             query, topic, round(scene_seconds, 2), model],
            ensure_ascii=False).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    @staticmethod
    def _cached(cache_file: Path) -> bool | None:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached.get("match"), bool):
                return cached["match"]
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def _save(self, cache_file: Path, matched: bool) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"match": matched}), encoding="utf-8")

    def _remote_verify(self, frames: tuple[Image.Image, Image.Image],
                       query: str, topic: str) -> bool:
        sheet = base64.b64encode(_contact_sheet(frames)).decode("ascii")
        prompt = (
            "These are two portrait-cropped frames from one stock video. "
            f"The requested visible subject is: {query}. The video topic is: {topic}. "
            "Does the main subject in BOTH frames clearly depict the requested subject and support the topic? "
            "Judge only what is visibly present. A laptop with a green screen does not show a real interface; "
            "unrelated animals, flowers, scenery, or generic office shots do not count unless specifically requested. "
            'Reply only with JSON: {"match": true} or {"match": false}.')
        data = request_json(
            "POST", f"{self.settings.llm_base_url}/chat/completions", service="stock vision",
            timeout=max(self.settings.http_timeout, 60), attempts=1,
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}",
                     "Content-Type": "application/json"},
            json={"model": self.settings.stock_vision_model, "temperature": 0,
                  "max_completion_tokens": 100, "reasoning": {"enabled": False},
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": [
                      {"type": "text", "text": prompt},
                      {"type": "image_url", "image_url": {
                          "url": f"data:image/jpeg;base64,{sheet}"}},
                  ]}]})
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content
                               if isinstance(part, dict))
        answer = re.search(r'"match"\s*:\s*(true|false)', str(content), re.IGNORECASE)
        if not answer:
            raise ValueError("vision model did not return a match decision")
        return answer.group(1).casefold() == "true"

    def verify(self, path: Path, query: str, topic: str, scene_seconds: float) -> bool:
        if not self.available:
            return False
        frames = None
        if self.local is not None:
            cache_file = self._cache_file(path, query, topic, scene_seconds, "local-clip-vit-b32")
            cached = self._cached(cache_file)
            if cached is not None:
                return cached
            try:
                frames = _sample_frames(path, scene_seconds)
                matched = self.local.matches(frames, query)
                self._save(cache_file, matched)
                return matched
            except Exception as exc:
                log.warning("Local stock frame check unavailable (%s)", type(exc).__name__)
                self.local = None
        if not self.remote_available:
            self.available = False
            return False
        cache_file = self._cache_file(path, query, topic, scene_seconds, self.settings.stock_vision_model)
        cached = self._cached(cache_file)
        if cached is not None:
            return cached
        try:
            matched = self._remote_verify(frames or _sample_frames(path, scene_seconds), query, topic)
            self._save(cache_file, matched)
            return matched
        except (ExternalError, KeyError, IndexError, TypeError, ValueError, OSError,
                subprocess.SubprocessError) as exc:
            log.warning("Stock frame verification unavailable (%s); using topic visuals", type(exc).__name__)
            if isinstance(exc, (ExternalError, KeyError, IndexError, TypeError)) or (
                    isinstance(exc, ValueError) and "vision model" in str(exc)):
                self.remote_available = False
                self.available = False
            return False
