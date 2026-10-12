from __future__ import annotations

import logging
import hashlib
import json
import math
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from PIL import Image, ImageDraw, ImageFont

from .config import Settings
from .media import probe
from .net import ExternalError, request_json
from .vision import FrameVerifier
from .visuals import make_concept_cards, query_fits_story, stock_relevance, story_beats


log = logging.getLogger(__name__)

_NON_VISUAL_WORDS = {"a", "an", "and", "about", "are", "at", "can", "do", "does", "for",
                     "from", "how", "in", "is", "it", "moment", "of", "on", "or", "our",
                     "over", "quiet", "story", "surprising", "the", "things", "this", "to",
                     "under", "what", "when", "where", "who", "why", "with", "your"}


def _visual_query(query: str) -> str:
    words = re.findall(r"[\w'-]+", query, flags=re.UNICODE)
    useful = [word for word in words if word.casefold() not in _NON_VISUAL_WORDS]
    return (" ".join(useful[:5]) or query).strip()[:100]


def _gradient(path: Path, topic: str, width: int, height: int) -> None:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            glow = 18 * math.sin((x / width + y / height) * math.pi)
            pixels[x, y] = (int(16 + glow + 16 * y / height),
                            int(28 + glow + 20 * x / width),
                            int(58 + glow + 24 * (1 - y / height)))
    draw = ImageDraw.Draw(image)
    try:
        font_path = Path("C:/Windows/Fonts/arialbd.ttf")
        font = ImageFont.truetype(str(font_path), max(30, width // 14)) if font_path.is_file() else ImageFont.load_default()
    except OSError:
        font = ImageFont.load_default()
    words = topic.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width * 0.8:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    lines = lines[:5]
    line_height = max(48, width // 11)
    start = height // 2 - line_height * len(lines) // 2
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text((x, start + index * line_height), line, font=font,
                  fill="white", stroke_width=3, stroke_fill="#081222")
    image.save(path)


def _download(url: str, path: Path, settings: Settings) -> None:
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.partial")
    total = 0
    try:
        with requests.get(url, stream=True, timeout=(10, settings.http_timeout)) as response:
            response.raise_for_status()
            declared = int(response.headers.get("Content-Length", "0"))
            if declared and declared > settings.max_download_bytes:
                raise ValueError("stock clip exceeds MAX_VIDEO_DOWNLOAD_BYTES")
            with temporary.open("wb") as output:
                for chunk in response.iter_content(1024 * 256):
                    total += len(chunk)
                    if total > settings.max_download_bytes:
                        raise ValueError("stock clip exceeded MAX_VIDEO_DOWNLOAD_BYTES")
                    output.write(chunk)
        details = probe(temporary)
        if not any(stream.get("codec_type") == "video" for stream in details.get("streams", [])):
            raise ValueError("downloaded stock clip has no video stream")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _pexels_candidates(query: str, settings: Settings) -> list[dict]:
    data = request_json("GET", "https://api.pexels.com/v1/videos/search", service="Pexels",
                        timeout=settings.http_timeout, attempts=settings.retry_limit,
                        headers={"Authorization": settings.pexels_api_key},
                        params={"query": query, "orientation": "portrait", "per_page": 12})
    candidates = []
    for video in data.get("videos", []):
        if not isinstance(video, dict):
            continue
        page = video.get("url") or ""
        description = unquote(urlparse(page).path.replace("-", " "))
        relevance = stock_relevance(query, description)
        if not relevance:
            continue
        for media in video.get("video_files", []):
            if not isinstance(media, dict):
                continue
            if media.get("file_type") == "video/mp4" and media.get("link") and media.get("width") and media.get("height"):
                candidates.append({"id": str(video.get("id")), "url": media["link"],
                                   "width": int(media["width"]), "height": int(media["height"]),
                                   "bytes_hint": media.get("size") or 0,
                                   "relevance": relevance,
                                   "credit": (video.get("user") or {}).get("name", "Pexels contributor"),
                                   "page": page})
    candidates.sort(key=lambda c: (
        c["relevance"],
        c["width"] >= settings.width and c["height"] >= settings.height,
        c["height"] / c["width"] > 1.3,
        -abs(c["width"] - settings.width),
    ), reverse=True)
    return candidates


def _pixabay_search(query: str, settings: Settings) -> dict:
    # Pixabay requires API search responses to be cached for at least 24 hours.
    cache = settings.work_dir / "cache" / "pixabay" / "search"
    cache.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(query.strip().casefold().encode("utf-8")).hexdigest()
    cache_file = cache / f"{cache_key}.json"
    data = None
    try:
        if time.time() - cache_file.stat().st_mtime < 24 * 60 * 60:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = None
    except (OSError, ValueError):
        pass
    if data is None:
        data = request_json("GET", "https://pixabay.com/api/videos/", service="Pixabay",
                            timeout=settings.http_timeout, attempts=settings.retry_limit,
                            params={"key": settings.pixabay_api_key, "q": query[:100],
                                    "per_page": 15, "safesearch": "true"})
        temporary = cache_file.with_name(cache_file.name + f".{uuid.uuid4().hex}.partial")
        try:
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(cache_file)
        finally:
            temporary.unlink(missing_ok=True)
    return data


def _pixabay_candidates(query: str, settings: Settings) -> list[dict]:
    # Pixabay has no video orientation filter. A vertical keyword favors 9:16 clips.
    visual_query = _visual_query(query)
    portrait_query = f"{visual_query[:91].strip()} vertical"
    searches = tuple(dict.fromkeys((portrait_query, visual_query, query)))
    fallback: list[dict] = []
    for search_query in searches:
        try:
            data = _pixabay_search(search_query, settings)
        except ExternalError:
            if search_query == searches[-1] and not fallback:
                raise
            continue
        candidates = _pixabay_candidates_from_data(data, settings, visual_query)
        if candidates and (not fallback or candidates[0]["relevance"] > fallback[0]["relevance"]):
            fallback = candidates
        if any(item["height"] > item["width"] for item in candidates):
            return candidates
    return fallback


def _pixabay_candidates_from_data(data: dict, settings: Settings, query: str) -> list[dict]:
    candidates = []
    for hit in data.get("hits", []):
        if not isinstance(hit, dict) or not isinstance(hit.get("videos"), dict):
            continue
        relevance = stock_relevance(query, str(hit.get("tags") or ""))
        if not relevance:
            continue
        for media in hit["videos"].values():
            if not isinstance(media, dict) or not media.get("url") or not media.get("width") or not media.get("height"):
                continue
            bytes_hint = int(media.get("size") or 0)
            if bytes_hint > settings.max_download_bytes:
                continue
            candidates.append({"id": str(hit.get("id")), "url": media["url"],
                               "width": int(media["width"]), "height": int(media["height"]),
                               "bytes_hint": bytes_hint,
                               "relevance": relevance,
                               "credit": hit.get("user") or "Pixabay contributor",
                               "page": hit.get("pageURL") or "https://pixabay.com/videos/"})
    candidates.sort(key=lambda c: (
        c["relevance"],
        c["height"] > c["width"],
        c["width"] >= settings.width and c["height"] >= settings.height,
        -abs(c["height"] / c["width"] - settings.height / settings.width),
        -abs(c["width"] - settings.width),
    ), reverse=True)
    return candidates


def obtain_footage(queries: tuple[str, ...], topic: str, job_dir: Path,
                   settings: Settings, footage_file: str | None = None,
                   footage_start_seconds: float = 0.0, *, narration: str = "",
                   background_keywords: tuple[str, ...] = (),
                   scene_seconds: float = 8.0) -> tuple[list[Path], list[dict]]:
    if footage_file:
        path = Path(footage_file).resolve()
        details = probe(path)
        if not any(stream.get("codec_type") == "video" for stream in details.get("streams", [])):
            raise ValueError("local footage has no video stream")
        seconds = float(details.get("format", {}).get("duration", 0))
        if footage_start_seconds >= seconds:
            raise ValueError(f"footage start must be before the clip ends at {seconds:.1f}s")
        return [path], []
    selected: list[Path] = []
    credits: list[dict] = []
    seen: set[str] = set()
    suggestions = queries[:4] or ("",)
    beats = story_beats(narration, len(suggestions), topic)
    planned = (background_keywords[:4] if background_keywords else
               tuple(query if query_fits_story(query, "", beats[index]) else ""
                     for index, query in enumerate(suggestions))) or ("",)
    cards: list[Path] | None = None
    verifier = FrameVerifier(settings)
    providers = []
    if settings.pixabay_api_key:
        providers.append(("Pixabay", _pixabay_candidates))
    if settings.pexels_api_key:
        providers.append(("Pexels", _pexels_candidates))
    for index, query in enumerate(planned):
        chosen = False
        for source, search in providers if query and verifier.available else ():
            checked: set[str] = set()
            cache = settings.work_dir / "cache" / source.lower()
            cache.mkdir(parents=True, exist_ok=True)
            try:
                candidates = search(query, settings)
            except ExternalError as exc:
                log.warning("%s search failed: %s", source, exc)
                continue
            for candidate in candidates:
                identity = f"{source}:{candidate['id']}"
                if identity in seen or identity in checked:
                    continue
                if len(checked) >= 3:
                    break
                checked.add(identity)
                if candidate["bytes_hint"] and int(candidate["bytes_hint"]) > settings.max_download_bytes:
                    continue
                path = cache / f"{candidate['id']}-{candidate['width']}x{candidate['height']}.mp4"
                try:
                    if not path.is_file():
                        _download(candidate["url"], path, settings)
                    else:
                        probe(path)
                except (ValueError, OSError, subprocess.CalledProcessError, requests.RequestException) as exc:
                    log.warning("%s clip %s could not be used: %s", source, candidate["id"], type(exc).__name__)
                    path.unlink(missing_ok=True)
                    continue
                if not verifier.verify(path, query, topic, scene_seconds):
                    if not verifier.available:
                        break
                    continue
                selected.append(path)
                credits.append({"author": candidate["credit"], "url": candidate["page"], "source": source})
                seen.add(identity)
                chosen = True
                break
            if chosen:
                break
            if not verifier.available:
                break
        if not chosen:
            if settings.stock_fallback == "error":
                raise RuntimeError(f"No relevant stock footage for {query or topic!r}. Add background keywords, provide --footage-file, or set STOCK_FALLBACK=concept.")
            if settings.stock_fallback == "concept":
                if cards is None:
                    cards = make_concept_cards(topic, narration, planned, job_dir, settings)
                selected.append(cards[index])
            else:
                backdrop = job_dir / f"backdrop_{index + 1:02}.png"
                _gradient(backdrop, topic, settings.width, settings.height)
                selected.append(backdrop)
    return selected, credits
