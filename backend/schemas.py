from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Platform = Literal["youtube", "facebook", "instagram"]


class CreateJobRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    duration_seconds: int = Field(default=30, ge=3, le=180)
    language: str = "English"
    tone: str = "educational"
    voice: str = "en-US-AriaNeural"
    platforms: list[Platform] = Field(default_factory=list)
    youtube_visibility: Literal["private", "unlisted", "public"] = "private"
    title: str | None = None
    description: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    script_text: str | None = None
    source_notes: str | None = None
    background_keywords: str = Field(default="", max_length=400)
    footage_upload_id: str | None = None
    footage_start_seconds: float = 0
    made_for_kids: bool = False
    contains_synthetic_media: bool = False
    new: bool = False


class PublishRequest(BaseModel):
    platforms: list[Platform] = Field(min_length=1)
    reviewed: bool = False
