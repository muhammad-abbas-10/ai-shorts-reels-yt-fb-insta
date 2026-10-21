from __future__ import annotations

from pathlib import Path
from typing import Any


def job_payload(job: dict[str, Any]) -> dict[str, Any]:
    job_id = job["id"]
    has_video = bool(job["output_path"] and Path(job["output_path"]).is_file())
    return {
        "id": job_id,
        "topic": job["topic"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "scheduled_at": job["scheduled_at"],
        "error": job["error"],
        "metadata": job["metadata"],
        "config": job["config"],
        "has_video": has_video,
        "video_url": f"/api/jobs/{job_id}/video" if has_video else None,
        "thumbnail_url": f"/api/jobs/{job_id}/thumbnail" if has_video else None,
        "publications": [
            {key: row[key] for key in ("platform", "status", "external_id", "url", "attempts", "error")}
            for row in job["publications"]
        ],
    }
