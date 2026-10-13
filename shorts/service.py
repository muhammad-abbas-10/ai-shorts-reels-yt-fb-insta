from __future__ import annotations

import uuid

from .config import Settings, binary
from .database import Database
from .models import JobConfig
from .pipeline import publish_job, run_job


def require_publish_setup(platforms: tuple[str, ...], settings: Settings) -> None:
    if "youtube" in platforms and not settings.youtube_token.is_file():
        raise RuntimeError("YouTube token is missing. Run 'python -m shorts auth-youtube'.")
    if any(p in platforms for p in ("facebook", "instagram")) and not (settings.meta_page_id and settings.meta_page_token):
        raise RuntimeError("Meta publishing needs META_PAGE_ID and META_PAGE_ACCESS_TOKEN.")
    if "instagram" in platforms and not settings.instagram_user_id:
        raise RuntimeError("Instagram publishing needs INSTAGRAM_USER_ID.")


def submit_job(config: JobConfig, db: Database, settings: Settings, *,
               idempotency_key: str | None = None, new: bool = False) -> tuple[dict, bool]:
    """Validate and run a job for either the CLI or the local studio."""
    config.validate()
    binary("ffmpeg", "FFMPEG_PATH")
    binary("ffprobe", "FFPROBE_PATH")
    if config.platforms and (settings.width < 540 or settings.height < 960):
        raise ValueError("publishing needs VIDEO_WIDTH and VIDEO_HEIGHT of at least 540x960")
    if not config.script_text and not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is missing. Set it in .env or supply a narration script.")
    if config.publish_mode != "generate_only":
        require_publish_setup(config.platforms, settings)
    fingerprint = config.fingerprint(uuid.uuid4().hex if new else idempotency_key)
    job_id, created = db.create_job(config, fingerprint)
    return (run_job(job_id, db, settings) if created else db.job(job_id)), created


def publish_existing(job_id: str, db: Database, settings: Settings, *,
                     platforms: tuple[str, ...] | None = None,
                     force_unknown: bool = False) -> dict:
    """Publish a validated job while preventing two sessions from racing."""
    owner = uuid.uuid4().hex
    if not db.lease(job_id, owner):
        raise RuntimeError("job is already running or cancelled")
    try:
        job = db.job(job_id)
        selected = platforms or tuple(record["platform"] for record in job["publications"])
        if platforms:
            seconds = job["metadata"].get("validation", {}).get("duration_seconds", 0)
            if "facebook" in platforms and not 4 <= seconds <= 60.2:
                raise ValueError("Facebook requires a video of 4-60 seconds")
        require_publish_setup(selected, settings)
        if platforms:
            db.add_platforms(job_id, platforms)
        return publish_job(job_id, db, settings, platforms=platforms,
                           force_unknown=force_unknown)
    finally:
        db.release(job_id, owner)
