from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import Settings, binary
from .database import Database
from .models import JobConfig, PLATFORMS, parse_background_keywords
from .pipeline import run_job
from .publishers import YOUTUBE_SCOPE, authorize_youtube
from .service import publish_existing, submit_job


def _platforms(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    result = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    if len(set(result)) != len(result) or any(part not in PLATFORMS for part in result):
        raise ValueError("--platforms must contain unique names from youtube,facebook,instagram")
    return result


def _visibility(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--visibility must look like youtube=private")
        key, choice = value.split("=", 1)
        result[key.strip().lower()] = choice.strip().lower()
    return result


def _summary(job: dict) -> dict:
    return {"id": job["id"], "topic": job["topic"], "status": job["status"],
            "scheduled_at": job["scheduled_at"], "output_path": job["output_path"],
            "metadata": job["metadata"], "error": job["error"],
            "publications": [{key: value for key, value in publication.items()
                              if key in {"platform", "status", "external_id", "url", "attempts", "error"}}
                             for publication in job["publications"]]}


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _result_code(job: dict) -> int:
    return 2 if job["status"] in {"failed", "partially_completed"} or any(
        p["status"] in {"failed", "needs_review"} for p in job["publications"]
    ) else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m shorts", description="Generate and publish vertical narrated videos")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="create a video and optionally publish or schedule it")
    generate.add_argument("topic")
    generate.add_argument("--language", default="English")
    generate.add_argument("--duration", type=int, default=45)
    generate.add_argument("--tone", default="educational")
    generate.add_argument("--voice", default="en-US-AriaNeural")
    generate.add_argument("--platforms", help="comma-separated youtube,facebook,instagram")
    generate.add_argument("--publish-mode", choices=["generate_only", "immediate", "schedule"], default="generate_only")
    generate.add_argument("--scheduled-at", help="ISO 8601 with timezone offset")
    generate.add_argument("--title")
    generate.add_argument("--description")
    generate.add_argument("--hashtags", help="comma-separated tags like #space,#science")
    generate.add_argument("--visibility", action="append", default=[], help="example: youtube=private")
    generate.add_argument("--made-for-kids", action="store_true", help="declare the YouTube audience as made for kids")
    generate.add_argument("--contains-synthetic-media", action="store_true", help="disclose realistic altered or synthetic media to YouTube")
    generate.add_argument("--script-file", type=Path, help="use supplied narration instead of the LLM")
    generate.add_argument("--source-file", type=Path, help="factual notes supplied to the LLM")
    generate.add_argument("--background-keywords", help="up to four comma-separated visual subjects for stock search")
    generate.add_argument("--footage-file", type=Path, help="licensed local video instead of stock search")
    generate.add_argument("--footage-start", type=float, default=0.0,
                          help="start time in seconds when using --footage-file")
    generate.add_argument("--idempotency-key", help="stable key for safe duplicate submission")
    generate.add_argument("--new", action="store_true", help="create a fresh job even if inputs match an earlier job")

    sub.add_parser("list", help="show recent jobs").add_argument("--limit", type=int, default=20)
    sub.add_parser("status", help="inspect a job").add_argument("job_id")
    publish = sub.add_parser("publish", help="publish an already generated video")
    publish.add_argument("job_id")
    publish.add_argument("--platforms", help="comma-separated platforms; defaults to those on the job")
    publish.add_argument("--force-unknown", action="store_true", help="retry after manually checking an uncertain prior upload")
    retry = sub.add_parser("retry", help="retry failed generation or unsuccessful destinations")
    retry.add_argument("job_id")
    retry.add_argument("--force-unknown", action="store_true")
    sub.add_parser("cancel", help="cancel a queued or scheduled job").add_argument("job_id")
    worker = sub.add_parser("worker", help="recover queued jobs and publish due schedules")
    worker.add_argument("--loop", action="store_true", help="keep polling instead of one pass")
    worker.add_argument("--interval", type=int, default=30)
    sub.add_parser("auth-youtube", help="run the YouTube OAuth browser flow")
    sub.add_parser("check", help="show local prerequisite status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[logging.StreamHandler(sys.stderr),
                                      logging.FileHandler(settings.work_dir / "shorts.log", encoding="utf-8")])
        db = Database(settings.database)
        if args.command == "generate":
            config = JobConfig(
                topic=args.topic.strip(), language=args.language.strip(), duration_seconds=args.duration,
                tone=args.tone.strip(), platforms=_platforms(args.platforms), publish_mode=args.publish_mode,
                scheduled_at=args.scheduled_at, title=args.title, description=args.description,
                hashtags=tuple(tag.strip() for tag in (args.hashtags or "").split(",") if tag.strip()),
                visibility=_visibility(args.visibility), voice=args.voice,
                script_text=args.script_file.read_text(encoding="utf-8") if args.script_file else None,
                source_notes=args.source_file.read_text(encoding="utf-8") if args.source_file else None,
                background_keywords=parse_background_keywords(args.background_keywords),
                footage_file=str(args.footage_file.resolve()) if args.footage_file else None,
                footage_start_seconds=args.footage_start,
                made_for_kids=args.made_for_kids,
                contains_synthetic_media=args.contains_synthetic_media,
            )
            result, created = submit_job(config, db, settings,
                                         idempotency_key=args.idempotency_key,
                                         new=args.new)
            if not created:
                _print({"duplicate_of": result["id"], **_summary(result)})
                return 0
            _print(_summary(result))
            return _result_code(result)
        elif args.command == "list":
            _print(db.list_jobs(args.limit))
        elif args.command == "status":
            _print(_summary(db.job(args.job_id)))
        elif args.command == "publish":
            result = publish_existing(args.job_id, db, settings,
                                      platforms=_platforms(args.platforms) or None,
                                      force_unknown=args.force_unknown)
            _print(_summary(result))
            return _result_code(result)
        elif args.command == "retry":
            job = db.job(args.job_id)
            if job["output_path"]:
                result = publish_existing(args.job_id, db, settings,
                                          force_unknown=args.force_unknown)
            else:
                result = run_job(args.job_id, db, settings, force_unknown=args.force_unknown)
            _print(_summary(result))
            return _result_code(result)
        elif args.command == "cancel":
            db.cancel(args.job_id)
            _print(_summary(db.job(args.job_id)))
        elif args.command == "worker":
            while True:
                ids = db.due_jobs()
                for job_id in ids:
                    try:
                        result = run_job(job_id, db, settings)
                        _print(_summary(result))
                    except Exception as exc:
                        logging.getLogger(__name__).error("job=%s worker error=%s", job_id, exc)
                if not args.loop:
                    break
                time.sleep(max(5, args.interval))
        elif args.command == "auth-youtube":
            _print({"token_file": str(authorize_youtube(settings))})
        elif args.command == "check":
            checks = {}
            try:
                saved_token = json.loads(settings.youtube_token.read_text(encoding="utf-8"))
                saved_scopes = saved_token.get("scopes", []) if isinstance(saved_token, dict) else []
                youtube_scopes_ok = isinstance(saved_scopes, list) and set(YOUTUBE_SCOPE).issubset(saved_scopes)
            except (OSError, ValueError):
                youtube_scopes_ok = False
            for name, setting in (("ffmpeg", "FFMPEG_PATH"), ("ffprobe", "FFPROBE_PATH")):
                try:
                    checks[name] = binary(name, setting)
                except RuntimeError as exc:
                    checks[name] = str(exc)
            checks.update({"llm_api_key": bool(settings.llm_api_key), "pexels_api_key": bool(settings.pexels_api_key),
                           "pixabay_api_key": bool(settings.pixabay_api_key),
                           "wikimedia_user_agent": bool(settings.wikimedia_user_agent),
                           "youtube_oauth_client": settings.youtube_client_secret.is_file(),
                           "youtube_oauth_token": settings.youtube_token.is_file(),
                           "youtube_oauth_scopes": youtube_scopes_ok,
                           "facebook_page_id": bool(settings.meta_page_id),
                           "facebook_page_token": bool(settings.meta_page_token),
                           "instagram_user_id": bool(settings.instagram_user_id),
                           "database": str(settings.database), "work_dir": str(settings.work_dir)})
            _print(checks)
        return 0
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
