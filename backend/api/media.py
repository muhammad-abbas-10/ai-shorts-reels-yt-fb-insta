from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from shorts.config import binary
from shorts.studio import store_video_upload


router = APIRouter(prefix="/api", tags=["media"])


def _video_path(request: Request, job_id: str) -> Path:
    runtime = request.app.state.runtime
    try:
        job = runtime.db.job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    if not job["output_path"]:
        raise HTTPException(status_code=404, detail="Video is not ready")
    path = Path(job["output_path"]).resolve()
    if not path.is_relative_to(runtime.settings.work_dir.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="Video is not available")
    return path


@router.post("/uploads/footage")
def upload_footage(request: Request, file: UploadFile = File(...)):
    runtime = request.app.state.runtime
    try:
        path = store_video_upload(file.file, file.filename or "", runtime.settings)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        file.file.close()
    return {"id": path.name, "name": file.filename, "size_bytes": path.stat().st_size}


@router.get("/jobs/{job_id}/video")
def job_video(request: Request, job_id: str):
    return FileResponse(_video_path(request, job_id), media_type="video/mp4")


@router.get("/jobs/{job_id}/thumbnail")
def job_thumbnail(request: Request, job_id: str):
    video = _video_path(request, job_id)
    thumbnail = video.parent / "thumbnail.jpg"
    if not thumbnail.is_file():
        temporary = video.parent / f"thumbnail.{uuid.uuid4().hex}.partial.jpg"
        try:
            subprocess.run([binary("ffmpeg", "FFMPEG_PATH"), "-hide_banner", "-loglevel", "error",
                            "-ss", "1", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=360:-2", "-q:v", "3", "-y", str(temporary)],
                           check=True, capture_output=True, timeout=30)
            temporary.replace(thumbnail)
        except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=404, detail="Thumbnail is not available") from exc
        finally:
            temporary.unlink(missing_ok=True)
    return FileResponse(thumbnail, media_type="image/jpeg")
