from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .config import Settings
from .database import Database
from .net import ExternalError, request_json


log = logging.getLogger(__name__)
YOUTUBE_SCOPE = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class UnknownOutcome(RuntimeError):
    """An upload may have succeeded remotely, so automatic replay is unsafe."""


@dataclass(frozen=True)
class PublishResult:
    status: str
    external_id: str | None = None
    url: str | None = None
    remote_state: dict[str, Any] = field(default_factory=dict)


def authorize_youtube(settings: Settings) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not settings.youtube_client_secret.is_file():
        raise RuntimeError(f"YouTube OAuth client file is missing: {settings.youtube_client_secret}")
    flow = InstalledAppFlow.from_client_secrets_file(str(settings.youtube_client_secret), YOUTUBE_SCOPE)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    settings.youtube_token.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.youtube_token.with_suffix(".partial")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(settings.youtube_token)
    return settings.youtube_token


def _youtube_service(settings: Settings):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not settings.youtube_token.is_file():
        raise RuntimeError(f"YouTube token is missing. Run 'python -m shorts auth-youtube' after placing the OAuth client file at {settings.youtube_client_secret}.")
    credentials = Credentials.from_authorized_user_file(str(settings.youtube_token))
    if not set(YOUTUBE_SCOPE).issubset(set(credentials.scopes or ())):
        raise RuntimeError("YouTube OAuth token lacks upload and video status read access. Run 'python -m shorts auth-youtube' to grant both scopes.")
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        temporary = settings.youtube_token.with_suffix(".partial")
        temporary.write_text(credentials.to_json(), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(settings.youtube_token)
    if not credentials.valid:
        raise RuntimeError("YouTube OAuth token is invalid or expired without refresh access. Run 'python -m shorts auth-youtube'.")
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


class YouTubePublisher:
    platform = "youtube"

    def publish(self, job_id: str, video: Path, metadata: dict, existing: dict,
                db: Database, settings: Settings) -> PublishResult:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        service = _youtube_service(settings)
        video_id = existing.get("external_id")
        if not video_id:
            status = {"privacyStatus": metadata.get("visibility", {}).get("youtube", "private"),
                      "selfDeclaredMadeForKids": bool(metadata.get("made_for_kids", False)),
                      "containsSyntheticMedia": bool(metadata.get("contains_synthetic_media", False))}
            snippet = {"title": metadata["title"][:100], "description": metadata["description"],
                       "tags": [tag.lstrip("#") for tag in metadata.get("hashtags", [])],
                       "categoryId": "22"}
            media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
            try:
                request = service.videos().insert(
                    part="snippet,status", body={"snippet": snippet, "status": status}, media_body=media)
                response = None
                failures = 0
                while response is None:
                    try:
                        progress, response = request.next_chunk()
                        if progress is not None:
                            log.info("job=%s platform=youtube upload=%.0f%%", job_id, 100 * progress.progress())
                    except HttpError as exc:
                        if exc.resp.status not in {429, 500, 502, 503, 504} or failures >= settings.retry_limit:
                            raise UnknownOutcome(f"YouTube upload ended with HTTP {exc.resp.status}; check the channel before retrying") from exc
                        failures += 1
                        time.sleep(min(20, 2 ** failures) + random.random())
                    except (OSError, TimeoutError) as exc:
                        if failures >= settings.retry_limit:
                            raise UnknownOutcome("YouTube upload connection failed; check the channel before retrying") from exc
                        failures += 1
                        time.sleep(min(20, 2 ** failures) + random.random())
            finally:
                media.stream().close()
            video_id = response.get("id") if isinstance(response, dict) else None
            if not video_id:
                raise UnknownOutcome("YouTube upload did not return a video ID; check the channel before retrying")
            db.update_publication(job_id, self.platform, status="processing", external_id=video_id,
                                  url=f"https://www.youtube.com/watch?v={video_id}")
        try:
            result = service.videos().list(part="status,processingDetails", id=video_id).execute()
        except HttpError as exc:
            if exc.resp.status in {429, 500, 502, 503, 504}:
                return PublishResult("processing", video_id, f"https://www.youtube.com/watch?v={video_id}")
            raise ExternalError("YouTube", f"status check returned HTTP {exc.resp.status}") from exc
        items = result.get("items", [])
        if not items:
            raise ExternalError("YouTube", "uploaded video could not be found in the authenticated channel")
        item = items[0]
        upload_status = item.get("status", {}).get("uploadStatus")
        processing = item.get("processingDetails", {}).get("processingStatus")
        if upload_status in {"failed", "rejected", "deleted"} or processing in {"failed", "terminated"}:
            raise ExternalError("YouTube", f"video processing failed ({upload_status or processing})")
        state = {"upload_status": upload_status, "processing_status": processing,
                 "privacy_status": item.get("status", {}).get("privacyStatus")}
        if processing == "succeeded" or upload_status == "processed":
            return PublishResult("succeeded", video_id, f"https://www.youtube.com/watch?v={video_id}", state)
        return PublishResult("processing", video_id, f"https://www.youtube.com/watch?v={video_id}", state)


def _meta_url(settings: Settings, path: str) -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_version}/{path.lstrip('/')}"


def _meta_headers(settings: Settings) -> dict[str, str]:
    if not settings.meta_page_token:
        raise RuntimeError("META_PAGE_ACCESS_TOKEN is missing. Configure a Page token with publishing permissions.")
    return {"Authorization": f"Bearer {settings.meta_page_token}"}


class FacebookPublisher:
    platform = "facebook"

    def publish(self, job_id: str, video: Path, metadata: dict, existing: dict,
                db: Database, settings: Settings) -> PublishResult:
        if not settings.meta_page_id:
            raise RuntimeError("META_PAGE_ID is missing. Facebook Reels publishing requires a managed Page.")
        headers = _meta_headers(settings)
        page = request_json("GET", _meta_url(settings, "me"), service="Facebook",
                            attempts=settings.retry_limit, timeout=settings.http_timeout,
                            headers=headers, params={"fields": "id"})
        if str(page.get("id")) != settings.meta_page_id:
            raise RuntimeError("META_PAGE_ACCESS_TOKEN does not belong to META_PAGE_ID")
        state = dict(existing.get("remote_state") or {})
        video_id = existing.get("external_id")
        if not video_id:
            data = request_json("POST", _meta_url(settings, "me/video_reels"),
                                service="Facebook", attempts=1, timeout=settings.http_timeout,
                                headers=headers, data={"upload_phase": "start"})
            video_id = str(data.get("video_id") or "")
            if not video_id:
                raise ExternalError("Facebook", "start response did not contain video_id")
            state = {"upload_url": data.get("upload_url"), "phase": "started"}
            db.update_publication(job_id, self.platform, status="attempting", external_id=video_id, remote_state=state)
        if state.get("phase") == "finished":
            return self._check(video_id, settings, state)
        if state.get("phase") == "uploaded":
            remote = self._check(video_id, settings, state)
            if remote.status == "succeeded" or remote.remote_state.get("publishing_status") not in {None, "not_started"}:
                return remote
        if state.get("phase") != "uploaded":
            upload_url = state.get("upload_url") or f"https://rupload.facebook.com/video-upload/{settings.meta_graph_version}/{video_id}"
            # The upload URL is supplied by Meta or reconstructed from its documented local upload path.
            with video.open("rb") as stream:
                try:
                    response = requests.post(upload_url, data=stream, timeout=(10, 300),
                                             headers={"Authorization": f"OAuth {settings.meta_page_token}",
                                                      "offset": "0", "file_size": str(video.stat().st_size),
                                                      "Content-Type": "application/octet-stream"})
                except requests.RequestException as exc:
                    raise ExternalError("Facebook", "local Reel upload failed; retry will resume the same video ID", transient=True) from exc
            if not response.ok or not response.json().get("success"):
                raise ExternalError("Facebook", f"local Reel upload returned HTTP {response.status_code}")
            state["phase"] = "uploaded"
            db.update_publication(job_id, self.platform, status="attempting", external_id=video_id, remote_state=state)
        data = request_json("POST", _meta_url(settings, "me/video_reels"),
                            service="Facebook", attempts=1, timeout=settings.http_timeout, headers=headers,
                            data={"upload_phase": "finish", "video_id": video_id,
                                  "video_state": "PUBLISHED", "title": metadata["title"],
                                  "description": metadata["description"]})
        if not data.get("success"):
            raise ExternalError("Facebook", "finish response did not confirm success")
        state["phase"] = "finished"
        db.update_publication(job_id, self.platform, status="processing", external_id=video_id, remote_state=state)
        return self._check(video_id, settings, state)

    def _check(self, video_id: str, settings: Settings, state: dict) -> PublishResult:
        data = request_json("GET", _meta_url(settings, video_id), service="Facebook",
                            attempts=settings.retry_limit, timeout=settings.http_timeout,
                            headers=_meta_headers(settings), params={"fields": "status"})
        status = data.get("status", {})
        if not isinstance(status, dict):
            status = {}
        phase = status.get("publishing_phase", {})
        phase_status = phase.get("status") if isinstance(phase, dict) else None
        video_status = status.get("video_status")
        if video_status in {"error", "failed"} or phase_status in {"error", "failed"}:
            raise ExternalError("Facebook", f"Reel processing failed ({video_status or phase_status})")
        state = {**state, "video_status": video_status, "publishing_status": phase_status}
        url = f"https://www.facebook.com/reel/{video_id}"
        if video_status == "published" or phase_status in {"complete", "published"}:
            return PublishResult("succeeded", video_id, url, state)
        return PublishResult("processing", video_id, url, state)


class InstagramPublisher:
    platform = "instagram"

    @staticmethod
    def _validate_upload_uri(uri: str) -> None:
        try:
            parsed = urlsplit(uri)
            port = parsed.port
        except ValueError as exc:
            raise ExternalError("Instagram", "Meta returned an unexpected resumable upload URL") from exc
        if (parsed.scheme != "https" or parsed.hostname != "rupload.facebook.com"
                or port not in {None, 443} or parsed.username or parsed.password
                or not parsed.path.startswith("/ig-api-upload/") or parsed.query or parsed.fragment):
            raise ExternalError("Instagram", "Meta returned an unexpected resumable upload URL")

    @classmethod
    def _upload_file(cls, uri: str, video: Path, token: str) -> None:
        cls._validate_upload_uri(uri)
        size = video.stat().st_size
        if not 0 < size <= 1_000_000_000:
            raise ValueError("Instagram Reel file must be between 1 byte and 1 GB")
        with video.open("rb") as stream:
            try:
                response = requests.post(uri, data=stream, timeout=(10, 300),
                                         headers={"Authorization": f"OAuth {token}",
                                                  "offset": "0", "file_size": str(size),
                                                  "Content-Type": "application/octet-stream"})
            except requests.RequestException as exc:
                raise ExternalError("Instagram", "resumable Reel upload connection failed", transient=True) from exc
        if not response.ok:
            raise ExternalError("Instagram", f"resumable Reel upload returned HTTP {response.status_code}")
        try:
            result = response.json()
        except ValueError:
            result = {}
        if isinstance(result, dict) and result.get("success") is False:
            raise ExternalError("Instagram", "resumable Reel upload was not accepted")

    @staticmethod
    def _container_status(container_id: str, settings: Settings, headers: dict[str, str]) -> str:
        data = request_json("GET", _meta_url(settings, container_id), service="Instagram",
                            attempts=settings.retry_limit, timeout=settings.http_timeout, headers=headers,
                            params={"fields": "status_code,status"})
        return str(data.get("status_code") or "")

    def publish(self, job_id: str, video: Path, metadata: dict, existing: dict,
                db: Database, settings: Settings) -> PublishResult:
        if not settings.instagram_user_id:
            raise RuntimeError("INSTAGRAM_USER_ID is missing. Configure a supported professional account linked to the Page.")
        if not settings.meta_page_id:
            raise RuntimeError("META_PAGE_ID is missing. Instagram Facebook Login publishing needs a linked Page.")
        headers = _meta_headers(settings)
        page = request_json("GET", _meta_url(settings, settings.meta_page_id), service="Instagram",
                            attempts=settings.retry_limit, timeout=settings.http_timeout,
                            headers=headers, params={"fields": "instagram_business_account"})
        linked_id = str((page.get("instagram_business_account") or {}).get("id") or "")
        if linked_id != settings.instagram_user_id:
            raise RuntimeError("INSTAGRAM_USER_ID is not the professional account linked to META_PAGE_ID")
        state = dict(existing.get("remote_state") or {})
        if state.get("media_id"):
            return self._media_result(str(state["media_id"]), state, settings)
        container_id = state.get("container_id")
        if container_id and state.get("phase") == "created":
            # A previous upload may have succeeded before the process stopped.
            if self._container_status(str(container_id), settings, headers) == "FINISHED":
                state["phase"] = "uploaded"
                db.update_publication(job_id, self.platform, status="processing",
                                      external_id=str(container_id), remote_state=state)
            else:
                # An unused container cannot make a public post. Start a fresh upload.
                state["last_abandoned_container_id"] = str(container_id)
                for key in ("container_id", "upload_uri", "phase"):
                    state.pop(key, None)
                db.update_publication(job_id, self.platform, status="attempting",
                                      external_id="", remote_state=state)
                container_id = None
        if not container_id:
            data = request_json("POST", _meta_url(settings, f"{settings.instagram_user_id}/media"),
                                service="Instagram", attempts=1, timeout=settings.http_timeout,
                                headers=headers, data={"media_type": "REELS", "upload_type": "resumable",
                                                       "caption": metadata["description"]})
            container_id = str(data.get("id") or "")
            upload_uri = str(data.get("uri") or "")
            if not container_id or not upload_uri:
                raise ExternalError("Instagram", "resumable container response lacked an ID or upload URI")
            self._validate_upload_uri(upload_uri)
            state.update({"container_id": container_id, "upload_uri": upload_uri, "phase": "created"})
            db.update_publication(job_id, self.platform, status="attempting",
                                  external_id=container_id, remote_state=state)
            self._upload_file(upload_uri, video, settings.meta_page_token)
            state["phase"] = "uploaded"
            db.update_publication(job_id, self.platform, status="processing",
                                  external_id=container_id, remote_state=state)
        container_status = self._container_status(str(container_id), settings, headers)
        state["container_status"] = container_status
        if container_status in {"ERROR", "EXPIRED"}:
            state["last_failed_container_id"] = container_id
            state.pop("container_id", None)
            state.pop("upload_uri", None)
            state.pop("phase", None)
            db.update_publication(job_id, self.platform, status="failed", external_id="", remote_state=state)
            raise ExternalError("Instagram", f"media container {container_status.lower()}")
        if container_status == "PUBLISHED":
            raise UnknownOutcome("Instagram container is published but its media ID was not recorded. Check the account before retrying.")
        if container_status != "FINISHED":
            return PublishResult("processing", container_id, None, state)
        try:
            published = request_json("POST", _meta_url(settings, f"{settings.instagram_user_id}/media_publish"),
                                     service="Instagram", attempts=1, timeout=settings.http_timeout,
                                     headers=headers, data={"creation_id": container_id})
        except ExternalError as exc:
            raise UnknownOutcome("Instagram media_publish outcome is unknown. Check the account before retrying.") from exc
        media_id = str(published.get("id") or "")
        if not media_id:
            raise UnknownOutcome("Instagram media_publish returned no ID. Check the account before retrying.")
        state["media_id"] = media_id
        state.pop("upload_uri", None)
        db.update_publication(job_id, self.platform, status="processing", external_id=media_id, remote_state=state)
        return self._media_result(media_id, state, settings)

    def _media_result(self, media_id: str, state: dict, settings: Settings) -> PublishResult:
        data = request_json("GET", _meta_url(settings, media_id), service="Instagram",
                            attempts=settings.retry_limit, timeout=settings.http_timeout,
                            headers=_meta_headers(settings), params={"fields": "permalink,media_type"})
        url = data.get("permalink")
        if url:
            return PublishResult("succeeded", media_id, url, state)
        return PublishResult("processing", media_id, None, state)


PUBLISHERS = {"youtube": YouTubePublisher(), "facebook": FacebookPublisher(), "instagram": InstagramPublisher()}
