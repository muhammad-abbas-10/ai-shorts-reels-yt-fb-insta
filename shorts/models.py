from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLATFORMS = ("youtube", "facebook", "instagram")
STAGES = (
    "queued", "generating_script", "generating_audio", "generating_captions",
    "retrieving_footage", "rendering", "validating", "ready_to_publish",
    "publishing", "completed", "partially_completed", "failed", "cancelled",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_aware_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scheduled time must be ISO 8601 with a timezone offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("scheduled time needs a timezone offset, for example 2026-10-10T14:00:00+05:00")
    return parsed.astimezone(timezone.utc)


def parse_background_keywords(value: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in re.split(r"[,;\n]+", value or "")
                               if part.strip()))


@dataclass(frozen=True)
class JobConfig:
    topic: str
    language: str = "English"
    duration_seconds: int = 45
    tone: str = "educational"
    platforms: tuple[str, ...] = ()
    publish_mode: str = "generate_only"
    scheduled_at: str | None = None
    title: str | None = None
    description: str | None = None
    hashtags: tuple[str, ...] = ()
    visibility: dict[str, str] = field(default_factory=dict)
    voice: str = "en-US-AriaNeural"
    script_text: str | None = None
    source_notes: str | None = None
    background_keywords: tuple[str, ...] = ()
    footage_file: str | None = None
    footage_start_seconds: float = 0.0
    made_for_kids: bool = False
    contains_synthetic_media: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, allow_past_schedule: bool = False) -> "JobConfig":
        data = dict(raw)
        data["platforms"] = tuple(data.get("platforms") or ())
        data["hashtags"] = tuple(data.get("hashtags") or ())
        data["background_keywords"] = tuple(data.get("background_keywords") or ())
        config = cls(**data)
        config.validate(allow_past_schedule=allow_past_schedule)
        return config

    def validate(self, *, allow_past_schedule: bool = False) -> None:
        if not self.topic.strip() or len(self.topic) > 300:
            raise ValueError("topic must contain 1 to 300 characters")
        if not 3 <= self.duration_seconds <= 180:
            raise ValueError("duration must be between 3 and 180 seconds")
        if not self.language.strip() or len(self.language) > 80:
            raise ValueError("language must contain 1 to 80 characters")
        if not self.tone.strip() or len(self.tone) > 60:
            raise ValueError("tone must contain 1 to 60 characters")
        if self.publish_mode not in {"generate_only", "immediate", "schedule"}:
            raise ValueError("publish_mode must be generate_only, immediate, or schedule")
        if len(set(self.platforms)) != len(self.platforms) or any(p not in PLATFORMS for p in self.platforms):
            raise ValueError("platforms must be a unique selection of youtube, facebook, instagram")
        if self.publish_mode != "generate_only" and not self.platforms:
            raise ValueError("choose at least one platform when publishing")
        if "facebook" in self.platforms and self.duration_seconds > 60:
            raise ValueError("Facebook Reels publishing requires a video of 60 seconds or less")
        if "facebook" in self.platforms and self.duration_seconds < 4:
            raise ValueError("Facebook Reels publishing requires a video of at least 4 seconds")
        if self.publish_mode == "schedule":
            if not self.scheduled_at:
                raise ValueError("schedule mode needs --scheduled-at with an ISO 8601 timezone offset")
            if not allow_past_schedule and parse_aware_time(self.scheduled_at) <= datetime.now(timezone.utc):
                raise ValueError("scheduled time must be in the future")
        elif self.scheduled_at:
            raise ValueError("scheduled_at is only valid in schedule mode")
        if self.title is not None and (not self.title.strip() or len(self.title) > 100):
            raise ValueError("title must contain 1 to 100 characters")
        if self.description is not None and len(self.description) > 2200:
            raise ValueError("description must be at most 2200 characters")
        for tag in self.hashtags:
            if not re.fullmatch(r"#[\w]{1,50}", tag, re.UNICODE):
                raise ValueError(f"invalid hashtag {tag!r}; use # followed by letters or digits")
        for platform, value in self.visibility.items():
            if platform not in self.platforms:
                raise ValueError(f"visibility set for unselected platform: {platform}")
            allowed = {"youtube": {"private", "unlisted", "public"}, "facebook": {"public"}, "instagram": {"public"}}
            if value not in allowed[platform]:
                raise ValueError(f"unsupported {platform} visibility: {value}")
        if self.script_text is not None and len(self.script_text.strip()) < 10:
            raise ValueError("provided script must contain at least 10 characters")
        if self.script_text is not None and len(self.script_text) > 5000:
            raise ValueError("provided script must be at most 5000 characters")
        if self.source_notes is not None and len(self.source_notes) > 12000:
            raise ValueError("source notes must be at most 12000 characters")
        if len(self.background_keywords) > 4 or any(
                not isinstance(term, str) or not term.strip() or len(term) > 80
                for term in self.background_keywords):
            raise ValueError("background keywords must contain up to 4 phrases of at most 80 characters each")
        if self.footage_file and not Path(self.footage_file).is_file():
            raise ValueError(f"footage file does not exist: {self.footage_file}")
        if not 0 <= self.footage_start_seconds <= 7200:
            raise ValueError("footage start must be between 0 and 7200 seconds")
        if self.footage_start_seconds and not self.footage_file:
            raise ValueError("footage start requires a local footage file")
        if not self.voice.strip():
            raise ValueError("voice cannot be empty; run edge-tts --list-voices")
        if self.language.casefold() not in {"english", "en", "en-us"} and self.voice == "en-US-AriaNeural":
            raise ValueError("choose --voice for the requested language; run python -m edge_tts --list-voices")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self, key: str | None = None) -> str:
        payload = key if key is not None else json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Script:
    title: str
    narration: str
    description: str
    hashtags: tuple[str, ...]
    footage_queries: tuple[str, ...]
    source_urls: tuple[str, ...] = ()

    @classmethod
    def from_llm(cls, raw: dict[str, Any], topic: str) -> "Script":
        if not isinstance(raw, dict):
            raise ValueError("LLM response must be a JSON object")
        title = raw.get("title")
        narration = raw.get("narration") or raw.get("script")
        description = raw.get("description")
        hashtags = raw.get("hashtags") or raw.get("tags") or []
        queries = raw.get("footage_queries") or raw.get("scenes") or [topic]
        if not all(isinstance(x, str) and x.strip() for x in (title, narration, description)):
            raise ValueError("LLM response needs nonempty title, narration, and description")
        if len(narration.split()) < 5 or len(narration) > 5000:
            raise ValueError("LLM narration is empty or too long")
        if not isinstance(hashtags, list) or not isinstance(queries, list):
            raise ValueError("LLM hashtags and footage_queries must be arrays")
        clean_tags = tuple(dict.fromkeys("#" + re.sub(r"[^\w]", "", tag.lstrip("#")) for tag in hashtags if isinstance(tag, str) and tag.strip()))
        clean_queries = tuple(q.strip()[:100] for q in queries if isinstance(q, str) and q.strip())[:6]
        if not clean_queries:
            clean_queries = (topic,)
        return cls(title=title.strip()[:100], narration=narration.strip(),
                   description=description.strip()[:2000], hashtags=clean_tags[:12],
                   footage_queries=clean_queries)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
