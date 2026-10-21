from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from shorts.config import binary
from shorts.models import JobConfig, parse_background_keywords
from shorts.pipeline import run_job
from shorts.service import publish_existing, require_publish_setup

from backend.api.presenters import job_payload
from backend.schemas import CreateJobRequest, PublishRequest


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _runtime(request: Request):
    return request.app.state.runtime


def _job(request: Request, job_id: str) -> dict:
    try:
        return _runtime(request).db.job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


def _uploaded_footage(request: Request, upload_id: str | None) -> str | None:
    if upload_id is None:
        return None
    import re

    if not re.fullmatch(r"[a-f0-9]{64}\.(?:mp4|mov|mkv|webm)", upload_id):
        raise HTTPException(status_code=422, detail="Invalid footage upload ID")
    file = _runtime(request).settings.work_dir / "uploads" / upload_id
    if not file.is_file():
        raise HTTPException(status_code=404, detail="Uploaded footage was not found")
    return str(file)


@router.get("")
def list_jobs(request: Request, limit: int = 30):
    runtime = _runtime(request)
    limit = min(max(limit, 1), 100)
    return {"jobs": [job_payload(runtime.db.job(row["id"])) for row in runtime.db.list_jobs(limit)]}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_job(request: Request, payload: CreateJobRequest):
    runtime = _runtime(request)
    settings = runtime.settings
    selected = tuple(payload.platforms)
    config = JobConfig(
        topic=payload.topic.strip(), duration_seconds=payload.duration_seconds,
        language=payload.language.strip(), tone=payload.tone.strip(), voice=payload.voice.strip(),
        platforms=selected, publish_mode="generate_only",
        visibility={"youtube": payload.youtube_visibility} if "youtube" in selected else {},
        title=payload.title.strip() if payload.title else None,
        description=payload.description.strip() if payload.description else None,
        hashtags=tuple(payload.hashtags),
        script_text=payload.script_text.strip() if payload.script_text else None,
        source_notes=payload.source_notes.strip() if payload.source_notes else None,
        background_keywords=parse_background_keywords(payload.background_keywords),
        footage_file=_uploaded_footage(request, payload.footage_upload_id),
        footage_start_seconds=payload.footage_start_seconds,
        made_for_kids=payload.made_for_kids,
        contains_synthetic_media=payload.contains_synthetic_media,
    )
    try:
        config.validate()
        binary("ffmpeg", "FFMPEG_PATH")
        binary("ffprobe", "FFPROBE_PATH")
        if selected and (settings.width < 540 or settings.height < 960):
            raise ValueError("publishing needs VIDEO_WIDTH and VIDEO_HEIGHT of at least 540x960")
        if not config.script_text and not settings.llm_api_key:
            raise ValueError("Add an OpenRouter key in .env or provide a narration script")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    fingerprint = config.fingerprint(uuid.uuid4().hex if payload.new else None)
    try:
        job_id, created = runtime.db.create_job(config, fingerprint)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if created:
        runtime.enqueue(job_id, lambda: run_job(job_id, runtime.db, settings))
    return {"created": created, "job": job_payload(runtime.db.job(job_id))}


@router.get("/{job_id}")
def get_job(request: Request, job_id: str):
    return job_payload(_job(request, job_id))


@router.post("/{job_id}/publish", status_code=status.HTTP_202_ACCEPTED)
def publish_job(request: Request, job_id: str, payload: PublishRequest):
    if not payload.reviewed:
        raise HTTPException(status_code=422, detail="Review the video and metadata before publishing")
    runtime = _runtime(request)
    job = _job(request, job_id)
    selected = tuple(payload.platforms)
    if len(set(selected)) != len(selected):
        raise HTTPException(status_code=422, detail="Choose each destination only once")
    if not job["output_path"] or not Path(job["output_path"]).is_file():
        raise HTTPException(status_code=409, detail="Generate and validate the video before publishing")
    existing = {row["platform"]: row for row in job["publications"]}
    if all(existing.get(name, {}).get("status") == "succeeded" for name in selected):
        return {"queued": False, "job": job_payload(job)}
    if any(existing.get(name, {}).get("status") == "needs_review" for name in selected):
        raise HTTPException(status_code=409, detail="An upload outcome needs manual review before retrying")
    try:
        seconds = job["metadata"].get("validation", {}).get("duration_seconds", 0)
        if "facebook" in selected and not 4 <= seconds <= 60.2:
            raise ValueError("Facebook requires a video of 4 to 60 seconds")
        require_publish_setup(selected, runtime.settings)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not runtime.enqueue(job_id, lambda: publish_existing(
            job_id, runtime.db, runtime.settings, platforms=selected)):
        raise HTTPException(status_code=409, detail="This job is already running")
    return {"queued": True, "job": job_payload(job)}


@router.post("/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(request: Request, job_id: str):
    runtime = _runtime(request)
    job = _job(request, job_id)
    if job["status"] != "failed" or job["output_path"]:
        if job["output_path"]:
            raise HTTPException(status_code=409, detail="Review the video and use Publish to retry a destination")
        raise HTTPException(status_code=409, detail="This job does not need a retry")
    operation = lambda: run_job(job_id, runtime.db, runtime.settings)
    if not runtime.enqueue(job_id, operation):
        raise HTTPException(status_code=409, detail="This job is already running")
    return {"queued": True, "job": job_payload(job)}
