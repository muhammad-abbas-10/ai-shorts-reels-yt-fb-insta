from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .database import Database
from .footage import obtain_footage
from .media import validate_video
from .models import JobConfig
from .net import ExternalError
from .publishers import PUBLISHERS, UnknownOutcome
from .render import render_video
from .script import generate_script
from .voice import make_voiceover, write_captions


log = logging.getLogger(__name__)


def _notify(job: dict[str, Any], settings: Settings) -> None:
    if not settings.webhook_url or job["status"] not in {"completed", "partially_completed", "failed"}:
        return
    payload = {"job_id": job["id"], "status": job["status"],
               "publications": [{"platform": p["platform"], "status": p["status"],
                                 "url": p["url"], "error": p["error"]} for p in job["publications"]]}
    try:
        response = requests.post(settings.webhook_url, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        log.warning("job=%s notification webhook failed", job["id"])


def prepare_metadata(config: JobConfig, script: Any, credits: list[dict]) -> dict[str, Any]:
    title = (config.title or script.title).strip()[:100]
    if not title:
        raise ValueError("video title is empty")
    tags = list(dict.fromkeys(config.hashtags or script.hashtags))[:12]
    parts = [(config.description or script.description).strip()]
    if tags:
        parts.append(" ".join(tags))
    if script.source_urls:
        parts.append("Sources: " + ", ".join(script.source_urls))
    if credits:
        credit_lines = [f"{item['author']} / {item['source']}: {item['url']}" for item in credits]
        parts.append("Footage: " + "; ".join(credit_lines))
    description = "\n\n".join(part for part in parts if part)
    if not description or len(description) > 2200:
        raise ValueError("description is empty or exceeds Instagram's 2200-character limit")
    return {"title": title, "description": description, "hashtags": tags,
            "visibility": {"youtube": "private", "facebook": "public", "instagram": "public", **config.visibility},
            "made_for_kids": config.made_for_kids,
            "contains_synthetic_media": config.contains_synthetic_media,
            "source_urls": list(script.source_urls), "footage_credits": credits}


def generate_job(job_id: str, db: Database, settings: Settings) -> dict[str, Any]:
    job = db.job(job_id)
    config = JobConfig.from_dict(job["config"], allow_past_schedule=True)
    job_dir = settings.work_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        started = time.monotonic()
        db.transition(job_id, "generating_script")
        log.info("job=%s stage=generating_script start", job_id)
        script = generate_script(config, settings)
        (job_dir / "script.json").write_text(json.dumps(script.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("job=%s stage=generating_script done seconds=%.1f", job_id, time.monotonic() - started)

        started = time.monotonic()
        db.transition(job_id, "generating_audio")
        log.info("job=%s stage=generating_audio start", job_id)
        voice = make_voiceover(script.narration, config.voice, job_dir / "voice.mp3", config.duration_seconds)
        log.info("job=%s stage=generating_audio done seconds=%.1f audio=%.1f", job_id, time.monotonic() - started, voice.duration_seconds)

        db.transition(job_id, "generating_captions")
        captions = job_dir / "captions.srt"
        ass = job_dir / "captions.ass"
        write_captions(voice.words, voice.duration_seconds, captions, ass, settings.width, settings.height,
                       script.narration)

        started = time.monotonic()
        db.transition(job_id, "retrieving_footage")
        log.info("job=%s stage=retrieving_footage start", job_id)
        sources, credits = obtain_footage(script.footage_queries, config.topic, job_dir, settings,
                                          config.footage_file, config.footage_start_seconds,
                                          narration=script.narration,
                                          background_keywords=config.background_keywords,
                                          scene_seconds=voice.duration_seconds / max(1, min(
                                              4, len(config.background_keywords or script.footage_queries))))
        log.info("job=%s stage=retrieving_footage done seconds=%.1f sources=%d", job_id,
                 time.monotonic() - started, len(sources))
        metadata = prepare_metadata(config, script, credits)
        metadata["visual_source"] = ("uploaded" if config.footage_file else
                                     "mixed" if credits and any(path.suffix.lower() != ".mp4" for path in sources) else
                                     "stock" if credits else
                                     "explainer" if settings.stock_fallback == "concept" else "gradient")
        metadata["voice_provider"] = voice.provider

        started = time.monotonic()
        db.transition(job_id, "rendering")
        log.info("job=%s stage=rendering start sources=%d", job_id, len(sources))
        final = render_video(sources, voice.path, ass, voice.duration_seconds, job_dir, settings,
                             footage_start_seconds=config.footage_start_seconds)
        log.info("job=%s stage=rendering done seconds=%.1f", job_id, time.monotonic() - started)

        db.transition(job_id, "validating")
        validation = validate_video(final, settings, expected_seconds=voice.duration_seconds,
                                    platforms=config.platforms, caption_path=captions)
        metadata["validation"] = validation
        (job_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        db.save_artifacts(job_id, final, metadata)
        db.transition(job_id, "ready_to_publish")
        log.info("job=%s stage=ready_to_publish path=%s", job_id, final)
        return db.job(job_id)
    except Exception as exc:
        log.exception("job=%s stage=generation failed", job_id)
        try:
            db.transition(job_id, "failed", f"{type(exc).__name__}: {exc}"[:500])
            _notify(db.job(job_id), settings)
        except (ValueError, KeyError):
            pass
        raise


def publish_job(job_id: str, db: Database, settings: Settings, *,
                platforms: tuple[str, ...] | None = None, force_unknown: bool = False) -> dict[str, Any]:
    job = db.job(job_id)
    if not job["output_path"]:
        raise ValueError("job has no validated video to publish")
    selected = platforms or tuple(record["platform"] for record in job["publications"])
    if not selected:
        raise ValueError("choose at least one platform")
    video = Path(job["output_path"])
    if not job["metadata"].get("title") or not job["metadata"].get("description"):
        raise ValueError("validated title and description are required before publishing")
    validate_video(video, settings, platforms=selected,
                   caption_path=settings.work_dir / job_id / "captions.srt")
    db.transition(job_id, "publishing")
    for platform in selected:
        current = db.claim_publication(job_id, platform, force_unknown=force_unknown)
        if current is None:
            continue
        started = time.monotonic()
        log.info("job=%s platform=%s publish start attempt=%s", job_id, platform, current["attempts"])
        try:
            result = PUBLISHERS[platform].publish(job_id, video, job["metadata"], current, db, settings)
            db.update_publication(job_id, platform, status=result.status, external_id=result.external_id,
                                  url=result.url, remote_state=result.remote_state)
            log.info("job=%s platform=%s publish status=%s seconds=%.1f", job_id, platform,
                     result.status, time.monotonic() - started)
        except UnknownOutcome as exc:
            db.update_publication(job_id, platform, status="needs_review", error=str(exc)[:500])
            log.warning("job=%s platform=%s outcome unknown: %s", job_id, platform, exc)
        except (ExternalError, RuntimeError, ValueError) as exc:
            db.update_publication(job_id, platform, status="failed", error=str(exc)[:500])
            log.warning("job=%s platform=%s publish failed: %s", job_id, platform, exc)
        except Exception as exc:
            db.update_publication(job_id, platform, status="failed",
                                  error=f"{type(exc).__name__}: check credentials, permissions, and the local log")
            log.error("job=%s platform=%s publish failed unexpectedly: %s", job_id, platform, type(exc).__name__)
    results = db.job(job_id)["publications"]
    statuses = {record["status"] for record in results}
    if statuses == {"succeeded"}:
        db.transition(job_id, "completed")
    elif "succeeded" in statuses:
        db.transition(job_id, "partially_completed")
    elif "processing" in statuses:
        db.transition(job_id, "ready_to_publish")
    else:
        db.transition(job_id, "failed", "No platform has confirmed publication; inspect per-platform errors.")
    result = db.job(job_id)
    _notify(result, settings)
    return result


def run_job(job_id: str, db: Database, settings: Settings, *, force_unknown: bool = False) -> dict[str, Any]:
    owner = uuid.uuid4().hex
    if not db.lease(job_id, owner):
        raise RuntimeError("job is already running or cancelled")
    try:
        job = db.job(job_id)
        config = JobConfig.from_dict(job["config"], allow_past_schedule=True)
        if job["status"] in {"queued", "generating_script", "generating_audio", "generating_captions",
                             "retrieving_footage", "rendering", "validating"} or (job["status"] == "failed" and not job["output_path"]):
            job = generate_job(job_id, db, settings)
        processing = tuple(record["platform"] for record in job["publications"]
                           if record["status"] == "processing")
        if processing:
            publish_job(job_id, db, settings, platforms=processing,
                        force_unknown=force_unknown)
        elif config.publish_mode == "generate_only":
            if job["status"] == "ready_to_publish":
                db.transition(job_id, "completed")
                _notify(db.job(job_id), settings)
        elif config.publish_mode == "immediate" or (job["scheduled_at"] and datetime.fromisoformat(job["scheduled_at"]) <= datetime.now(timezone.utc)):
            if job["status"] in {"ready_to_publish", "partially_completed", "publishing", "completed"}:
                publish_job(job_id, db, settings, force_unknown=force_unknown)
        return db.job(job_id)
    finally:
        db.release(job_id, owner)
