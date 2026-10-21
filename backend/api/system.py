from __future__ import annotations

import json

from fastapi import APIRouter, Request

from shorts.publishers import YOUTUBE_SCOPE

from backend.api.presenters import job_payload
from shorts.local_vision import LocalVision


router = APIRouter(prefix="/api", tags=["workspace"])


def _accounts(settings) -> list[dict]:
    try:
        saved = json.loads(settings.youtube_token.read_text(encoding="utf-8"))
        scopes = saved.get("scopes", []) if isinstance(saved, dict) else []
        youtube = settings.youtube_client_secret.is_file() and set(YOUTUBE_SCOPE).issubset(scopes)
    except (OSError, ValueError, TypeError):
        youtube = False
    meta = bool(settings.meta_page_id and settings.meta_page_token)
    return [
        {"platform": "youtube", "configured": youtube, "label": "YouTube Shorts",
         "detail": "OAuth ready for uploads" if youtube else "OAuth setup needed"},
        {"platform": "facebook", "configured": meta, "label": "Facebook Reels",
         "detail": "Page publishing configured" if meta else "Page token needed"},
        {"platform": "instagram", "configured": meta and bool(settings.instagram_user_id),
         "label": "Instagram Reels", "detail": "Professional account configured"
         if meta and settings.instagram_user_id else "Linked account needed"},
    ]


@router.get("/overview")
def overview(request: Request):
    runtime = request.app.state.runtime
    db = runtime.db
    with db.connect() as connection:
        total = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        ready = connection.execute("SELECT COUNT(*) FROM jobs WHERE output_path IS NOT NULL").fetchone()[0]
        published = connection.execute("SELECT COUNT(*) FROM publications WHERE status='succeeded'").fetchone()[0]
        active = connection.execute("""SELECT COUNT(*) FROM jobs WHERE status IN
            ('queued','generating_script','generating_audio','generating_captions',
             'retrieving_footage','rendering','validating','publishing')""").fetchone()[0]
    recent = [job_payload(db.job(row["id"])) for row in db.list_jobs(6)]
    accounts = _accounts(runtime.settings)
    return {"stats": {"total": total, "rendered": ready, "published": published, "active": active},
            "accounts": accounts, "recent_jobs": recent}


@router.get("/health")
def health(request: Request):
    settings = request.app.state.runtime.settings
    return {"status": "ok", "accounts": _accounts(settings),
            "llm_configured": bool(settings.llm_api_key),
            "stock_footage_configured": bool(settings.pexels_api_key or settings.pixabay_api_key),
            "stock_vision_configured": bool(LocalVision.dependencies_available() or
                                             (settings.llm_api_key and settings.stock_vision_model)),
            "stock_footage_sources": [name for name, ready in (("Pexels", settings.pexels_api_key),
                                                                  ("Pixabay", settings.pixabay_api_key)) if ready]}
