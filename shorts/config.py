from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def binary(name: str, env_name: str) -> str:
    configured = os.getenv(env_name)
    if configured:
        path = project_path(configured)
        if path.is_file():
            return str(path)
        found = shutil.which(configured)
        if found:
            return found
        raise RuntimeError(f"{env_name} points to a missing executable: {configured}")
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        packages = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
        matches = sorted(packages.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"), reverse=True)
        if matches:
            return str(matches[0])
    raise RuntimeError(f"{name} was not found. Install FFmpeg or set {env_name} in .env")


@dataclass(frozen=True)
class Settings:
    database: Path
    work_dir: Path
    secrets_dir: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_fallback_model: str
    wikimedia_user_agent: str
    pexels_api_key: str
    pixabay_api_key: str
    stock_fallback: str
    stock_vision_model: str
    width: int
    height: int
    fps: int
    crf: int
    preset: str
    max_download_bytes: int
    http_timeout: int
    retry_limit: int
    youtube_client_secret: Path
    youtube_token: Path
    meta_graph_version: str
    meta_page_id: str
    meta_page_token: str
    instagram_user_id: str
    webhook_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        width = int(os.getenv("VIDEO_WIDTH", "720"))
        height = int(os.getenv("VIDEO_HEIGHT", "1280"))
        fps = int(os.getenv("VIDEO_FPS", "30"))
        if width < 360 or height < 640 or width * 16 != height * 9:
            raise ValueError("VIDEO_WIDTH and VIDEO_HEIGHT must form a vertical 9:16 frame of at least 360x640")
        if fps < 23 or fps > 60:
            raise ValueError("VIDEO_FPS must be between 23 and 60")
        crf = int(os.getenv("VIDEO_CRF", "21"))
        if not 0 <= crf <= 51:
            raise ValueError("VIDEO_CRF must be between 0 and 51")
        preset = os.getenv("FFMPEG_PRESET", "fast")
        if preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
            raise ValueError("FFMPEG_PRESET is not a supported x264 preset")
        fallback = os.getenv("STOCK_FALLBACK", "concept")
        if fallback not in {"concept", "gradient", "error"}:
            raise ValueError("STOCK_FALLBACK must be concept, gradient, or error")
        webhook = os.getenv("WEBHOOK_URL", "")
        if webhook and not webhook.startswith("https://"):
            raise ValueError("WEBHOOK_URL must use HTTPS")
        max_download = int(os.getenv("MAX_VIDEO_DOWNLOAD_BYTES", "50000000"))
        timeout = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))
        retries = int(os.getenv("RETRY_LIMIT", "3"))
        if max_download < 1_000_000:
            raise ValueError("MAX_VIDEO_DOWNLOAD_BYTES must be at least 1000000")
        if timeout < 5 or not 1 <= retries <= 10:
            raise ValueError("HTTP_TIMEOUT_SECONDS must be at least 5 and RETRY_LIMIT must be 1-10")
        return cls(
            database=project_path(os.getenv("DATABASE_PATH", "work/jobs.sqlite3")),
            work_dir=project_path(os.getenv("WORK_DIR", "work")),
            secrets_dir=project_path(os.getenv("SECRETS_DIR", ".secrets")),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", os.getenv("NVIDIA_API_KEY", "")),
            llm_model=os.getenv("LLM_MODEL", "meta/llama-3.1-8b-instruct"),
            llm_fallback_model=os.getenv("LLM_FALLBACK_MODEL", ""),
            wikimedia_user_agent=os.getenv("WIKIMEDIA_USER_AGENT", "ShortsPublisher/0.1 (local video research tool)"),
            pexels_api_key=os.getenv("PEXELS_API_KEY", ""),
            pixabay_api_key=os.getenv("PIXABAY_API_KEY", ""),
            stock_fallback=fallback,
            stock_vision_model=os.getenv("STOCK_VISION_MODEL") or os.getenv("LLM_MODEL", "meta/llama-3.1-8b-instruct"),
            width=width,
            height=height,
            fps=fps,
            crf=crf,
            preset=preset,
            max_download_bytes=max_download,
            http_timeout=timeout,
            retry_limit=retries,
            youtube_client_secret=project_path(os.getenv("YOUTUBE_CLIENT_SECRET_FILE", ".secrets/client_secret.json")),
            youtube_token=project_path(os.getenv("YOUTUBE_TOKEN_FILE", ".secrets/youtube-token.json")),
            meta_graph_version=os.getenv("META_GRAPH_VERSION", "v24.0"),
            meta_page_id=os.getenv("META_PAGE_ID", ""),
            meta_page_token=os.getenv("META_PAGE_ACCESS_TOKEN", ""),
            instagram_user_id=os.getenv("INSTAGRAM_USER_ID", ""),
            webhook_url=webhook,
        )
