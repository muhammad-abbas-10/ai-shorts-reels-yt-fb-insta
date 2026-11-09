from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

try:
    from fastapi.testclient import TestClient
    from backend.main import create_app
except ImportError:
    TestClient = None
    create_app = None

from shorts.config import Settings
from shorts.models import JobConfig


@unittest.skipUnless(TestClient is not None, "install the web dependencies to test the API")
class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = replace(Settings.from_env(), database=root / "jobs.sqlite3", work_dir=root,
                                secrets_dir=root / "secrets", llm_api_key="", meta_page_id="123",
                                meta_page_token="test-token", instagram_user_id="456")
        self.app = create_app(self.settings, start_worker=False)
        self.app.state.runtime.enqueue = Mock(return_value=True)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def ready_job(self) -> str:
        db = self.app.state.runtime.db
        config = JobConfig(topic="A quiet morning", script_text="Take a moment to breathe and begin the day.")
        job_id, _ = db.create_job(config, config.fingerprint())
        for stage in ("generating_script", "generating_audio", "generating_captions",
                      "retrieving_footage", "rendering", "validating", "ready_to_publish"):
            db.transition(job_id, stage)
        video = self.settings.work_dir / job_id / "final.mp4"
        video.parent.mkdir()
        video.write_bytes(b"fake-mp4-for-api-test")
        db.save_artifacts(job_id, video, {"title": "Quiet morning", "description": "A calm start",
                                          "validation": {"duration_seconds": 12}})
        db.transition(job_id, "completed")
        return job_id

    def test_overview_and_existing_job_are_secret_free(self):
        job_id = self.ready_job()
        overview = self.client.get("/api/overview")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["stats"]["total"], 1)
        self.assertEqual(overview.json()["stats"]["rendered"], 1)
        job = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(job.status_code, 200)
        self.assertTrue(job.json()["has_video"])
        self.assertNotIn("test-token", job.text)
        video = self.client.get(f"/api/jobs/{job_id}/video")
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.content, b"fake-mp4-for-api-test")

    def test_creation_queues_once_and_rejects_bad_upload_id(self):
        body = {"topic": "One useful morning habit", "script_text": "Open the curtains and enjoy a fresh start."}
        first = self.client.post("/api/jobs", json=body)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertTrue(first.json()["created"])
        self.assertEqual(first.json()["job"]["status"], "queued")
        second = self.client.post("/api/jobs", json=body)
        self.assertEqual(second.status_code, 202)
        self.assertFalse(second.json()["created"])
        self.assertEqual(self.app.state.runtime.enqueue.call_count, 1)
        invalid = self.client.post("/api/jobs", json={**body, "footage_upload_id": "../secrets"})
        self.assertEqual(invalid.status_code, 422)

    def test_background_keywords_are_saved_with_job(self):
        body = {"topic": "How search engines work",
                "script_text": "A search index helps people find useful pages quickly.",
                "background_keywords": "search results screen, server racks"}
        response = self.client.post("/api/jobs", json=body)
        self.assertEqual(response.status_code, 202, response.text)
        config = response.json()["job"]["config"]
        self.assertEqual(config["background_keywords"], ["search results screen", "server racks"])
        other = self.client.post("/api/jobs", json={**body, "background_keywords": "library shelves"})
        self.assertTrue(other.json()["created"])

    def test_publication_requires_review_and_does_not_retry_success(self):
        job_id = self.ready_job()
        endpoint = f"/api/jobs/{job_id}/publish"
        denied = self.client.post(endpoint, json={"platforms": ["facebook"], "reviewed": False})
        self.assertEqual(denied.status_code, 422)
        accepted = self.client.post(endpoint, json={"platforms": ["facebook"], "reviewed": True})
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertTrue(accepted.json()["queued"])
        self.assertEqual(self.app.state.runtime.enqueue.call_count, 1)
        db = self.app.state.runtime.db
        db.add_platforms(job_id, ("facebook",))
        db.update_publication(job_id, "facebook", status="succeeded", external_id="123")
        repeated = self.client.post(endpoint, json={"platforms": ["facebook"], "reviewed": True})
        self.assertEqual(repeated.status_code, 202)
        self.assertFalse(repeated.json()["queued"])
        self.assertEqual(self.app.state.runtime.enqueue.call_count, 1)
