"""Explicit opt-in external integration checks. Ordinary test runs skip every case here."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from shorts.config import Settings
from shorts.database import Database
from shorts.footage import _pexels_candidates, _pixabay_candidates
from shorts.media import validate_video
from shorts.models import JobConfig
from shorts.publishers import FacebookPublisher, InstagramPublisher, YouTubePublisher
from shorts.script import generate_script


class LiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        if os.getenv("SHORTS_LIVE_TESTS") != "1":
            self.skipTest("set SHORTS_LIVE_TESTS=1 to enable external integration checks")
        self.settings = Settings.from_env()

    def _video(self, platforms: tuple[str, ...]) -> Path:
        value = os.getenv("SHORTS_LIVE_VIDEO", "")
        if not value:
            self.skipTest("set SHORTS_LIVE_VIDEO to a validated local MP4")
        path = Path(value).resolve()
        validate_video(path, self.settings, platforms=platforms)
        return path

    def _job(self, platform: str) -> tuple[Database, str, dict]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "jobs.sqlite3")
        config = JobConfig(topic="Explicit live adapter test", platforms=(platform,), duration_seconds=10)
        job_id, _ = db.create_job(config, config.fingerprint())
        current = db.claim_publication(job_id, platform)
        return db, job_id, current

    def test_llm_script(self):
        if not self.settings.llm_api_key:
            self.skipTest("LLM_API_KEY is required")
        config = JobConfig(topic="What is outer space?", duration_seconds=15,
                           source_notes="Outer space is the expanse beyond Earth's atmosphere.")
        result = generate_script(config, self.settings)
        self.assertGreater(len(result.narration), 20)

    def test_pexels_search(self):
        if not self.settings.pexels_api_key:
            self.skipTest("PEXELS_API_KEY is required")
        self.assertTrue(_pexels_candidates("stars", self.settings))

    def test_pixabay_search(self):
        if not self.settings.pixabay_api_key:
            self.skipTest("PIXABAY_API_KEY is required")
        self.assertTrue(_pixabay_candidates("stars", self.settings))

    def test_youtube_private_upload(self):
        if os.getenv("SHORTS_LIVE_YOUTUBE_UPLOAD") != "1" or not self.settings.youtube_token.is_file():
            self.skipTest("set SHORTS_LIVE_YOUTUBE_UPLOAD=1 and authorize YouTube")
        video = self._video(("youtube",))
        db, job_id, current = self._job("youtube")
        metadata = {"title": "Private Shorts API check " + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                    "description": "Private upload integration check", "hashtags": [],
                    "visibility": {"youtube": "private"}}
        result = YouTubePublisher().publish(job_id, video, metadata, current, db, self.settings)
        self.assertIn(result.status, {"processing", "succeeded"})
        self.assertTrue(result.external_id)

    def test_facebook_public_upload(self):
        if os.getenv("SHORTS_LIVE_FACEBOOK_UPLOAD") != "1" or not (self.settings.meta_page_id and self.settings.meta_page_token):
            self.skipTest("set SHORTS_LIVE_FACEBOOK_UPLOAD=1 and configure a Page token")
        video = self._video(("facebook",))
        db, job_id, current = self._job("facebook")
        result = FacebookPublisher().publish(job_id, video,
                                             {"title": "Shorts API check", "description": "Live Page Reel integration check"},
                                             current, db, self.settings)
        self.assertIn(result.status, {"processing", "succeeded"})
        self.assertTrue(result.external_id)

    def test_instagram_public_upload(self):
        if os.getenv("SHORTS_LIVE_INSTAGRAM_UPLOAD") != "1" or not (
            self.settings.meta_page_id and self.settings.meta_page_token and self.settings.instagram_user_id
        ):
            self.skipTest("set SHORTS_LIVE_INSTAGRAM_UPLOAD=1 and configure Meta")
        video = self._video(("instagram",))
        db, job_id, current = self._job("instagram")
        result = InstagramPublisher().publish(job_id, video, {"description": "Live Instagram Reel integration check"},
                                              current, db, self.settings)
        self.assertIn(result.status, {"processing", "succeeded"})
        self.assertTrue(result.external_id)
