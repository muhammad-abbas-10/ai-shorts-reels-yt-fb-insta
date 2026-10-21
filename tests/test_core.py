from __future__ import annotations

import json
import tempfile
import unittest
import requests
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from shorts.config import Settings, binary
from shorts.database import Database
from shorts.footage import _pexels_candidates, _pixabay_candidates, obtain_footage
from shorts.media import validate_video
from shorts.models import JobConfig, Script, parse_background_keywords
from shorts.visuals import query_fits_story, stock_relevance
from shorts.net import ExternalError, request_json
from shorts.pipeline import prepare_metadata, publish_job, run_job
from shorts.publishers import (FacebookPublisher, InstagramPublisher, PublishResult,
                               UnknownOutcome, YouTubePublisher, _youtube_service)
from shorts.render import render_video
from shorts.vision import FrameVerifier
from shorts.voice import Word, write_captions


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = replace(Settings.from_env(), database=self.root / "jobs.sqlite3",
                                work_dir=self.root, width=360, height=640)
        self.db = Database(self.settings.database)

    def config(self, **overrides):
        data = {"topic": "A calm night", "script_text": "Pause and look at the night sky for a quiet moment."}
        data.update(overrides)
        return JobConfig.from_dict(data)

    def ready_job(self, config):
        job_id, created = self.db.create_job(config, config.fingerprint())
        self.assertTrue(created)
        for status in ("generating_script", "generating_audio", "generating_captions",
                       "retrieving_footage", "rendering", "validating", "ready_to_publish"):
            self.db.transition(job_id, status)
        video = self.root / "video.mp4"
        video.write_bytes(b"x" * 12_000)
        self.db.save_artifacts(job_id, video, {"title": "Calm night", "description": "A quiet video", "hashtags": [],
                                               "visibility": {"youtube": "private"},
                                               "validation": {"duration_seconds": 10}})
        return job_id

    def test_input_validation(self):
        with self.assertRaisesRegex(ValueError, "topic"):
            self.config(topic="")
        with self.assertRaisesRegex(ValueError, "60 seconds"):
            self.config(platforms=("facebook",), duration_seconds=61)
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.config(platforms=("youtube",), publish_mode="schedule", scheduled_at="2030-01-01T12:00:00")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.config(platforms=("instagram",), visibility={"instagram": "private"})
        with self.assertRaisesRegex(ValueError, "choose --voice"):
            self.config(language="Urdu")
        with self.assertRaisesRegex(ValueError, "background keywords"):
            self.config(background_keywords=("one", "two", "three", "four", "five"))
        self.assertEqual(parse_background_keywords(" laptop screen, document search\nserver racks "),
                         ("laptop screen", "document search", "server racks"))

    def test_script_validation(self):
        with self.assertRaisesRegex(ValueError, "narration"):
            Script.from_llm({"title": "A", "description": "B"}, "topic")
        script = Script.from_llm({"title": "T", "narration": "One two three four five six.",
                                  "description": "D", "hashtags": ["space", "#space"],
                                  "footage_queries": ["stars"]}, "topic")
        self.assertEqual(script.hashtags, ("#space",))

    def test_job_transitions_and_persistence(self):
        config = self.config()
        job_id, _ = self.db.create_job(config, config.fingerprint())
        with self.assertRaisesRegex(ValueError, "invalid job transition"):
            self.db.transition(job_id, "completed")
        self.db.transition(job_id, "generating_script")
        reopened = Database(self.settings.database)
        self.assertEqual(reopened.job(job_id)["status"], "generating_script")

    def test_idempotent_submission(self):
        config = self.config()
        first, created = self.db.create_job(config, config.fingerprint())
        second, created_again = self.db.create_job(config, config.fingerprint())
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, second)
        keyed = config.fingerprint("caller-key")
        self.db.create_job(config, keyed)
        with self.assertRaisesRegex(ValueError, "idempotency key"):
            self.db.create_job(self.config(topic="A different night"), keyed)

    def test_metadata_preparation(self):
        script = Script("A title", "Narration words here", "Description", ("#sky",), ("stars",),
                        ("https://en.wikipedia.org/wiki/Space",))
        metadata = prepare_metadata(self.config(), script, [{"author": "A", "url": "https://www.pexels.com/video/1", "source": "Pexels"}])
        self.assertIn("#sky", metadata["description"])
        self.assertIn("Pexels", metadata["description"])
        self.assertEqual(metadata["visibility"]["youtube"], "private")
        pixabay = prepare_metadata(self.config(), script, [{"author": "B", "url": "https://pixabay.com/videos/id-2/", "source": "Pixabay"}])
        self.assertIn("B / Pixabay: https://pixabay.com/videos/id-2/", pixabay["description"])

    def test_pixabay_stock_search_is_cached_and_prefers_vertical_video(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key")
        response = {"hits": [
            {"id": 1, "user": "Wide", "pageURL": "https://pixabay.com/videos/id-1/",
             "tags": "night sky stars",
             "videos": {"medium": {"url": "https://cdn.pixabay.com/1.mp4", "width": 1920,
                                   "height": 1080, "size": 1200000}}},
            {"id": 2, "user": "Tall", "pageURL": "https://pixabay.com/videos/id-2/",
             "tags": "night sky stars",
             "videos": {"medium": {"url": "https://cdn.pixabay.com/2.mp4", "width": 720,
                                   "height": 1280, "size": 1800000}}},
        ]}
        with patch("shorts.footage.request_json", return_value=response) as request:
            first = _pixabay_candidates("night sky", settings)
            second = _pixabay_candidates("night sky", settings)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["id"], "2")
        self.assertEqual(request.call_args.kwargs["params"]["q"], "night sky vertical")

    def test_pixabay_search_falls_back_when_portrait_query_has_no_match(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key")
        response = {"hits": [{"id": 1, "tags": "distant galaxy", "videos": {"medium": {
            "url": "https://cdn.pixabay.com/1.mp4", "width": 1920, "height": 1080, "size": 1000000}}}]}
        with patch("shorts.footage.request_json", return_value=response) as request:
            clips = _pixabay_candidates("distant galaxy", settings)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(clips[0]["id"], "1")
        self.assertEqual(request.call_args.kwargs["params"]["q"], "distant galaxy")

    def test_pixabay_search_uses_visual_subject_from_a_sentence(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key")
        response = {"hits": [{"id": 3, "tags": "stars galaxy", "videos": {"medium": {
            "url": "https://cdn.pixabay.com/3.mp4", "width": 720, "height": 1280, "size": 1000000}}}]}
        with patch("shorts.footage.request_json", return_value=response) as request:
            clips = _pixabay_candidates("A quiet moment under the stars", settings)
        self.assertEqual(clips[0]["id"], "3")
        self.assertEqual(request.call_args.kwargs["params"]["q"], "stars vertical")

    def test_stock_relevance_rejects_unrelated_portrait_and_weak_ai_match(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key")
        response = {"hits": [
            {"id": 1, "tags": "flowers clouds nature ai generated", "videos": {"medium": {
                "url": "https://cdn.pixabay.com/1.mp4", "width": 720, "height": 1280, "size": 1000000}}},
            {"id": 2, "tags": "chatbot interface computer screen", "videos": {"medium": {
                "url": "https://cdn.pixabay.com/2.mp4", "width": 1920, "height": 1080, "size": 1000000}}},
        ]}
        with patch("shorts.footage.request_json", return_value=response):
            clips = _pixabay_candidates("AI chatbot interface", settings)
        self.assertEqual([clip["id"] for clip in clips], ["2"])
        self.assertEqual(stock_relevance("AI chatbot interface", "flowers ai generated"), 0)
        self.assertEqual(stock_relevance("document search", "bumblebee nectar search"), 0)
        self.assertEqual(stock_relevance("laptop screen", "man laptop green screen"), 0)

    def test_pexels_requires_descriptive_subject_in_page_title(self):
        settings = replace(self.settings, pexels_api_key="test-key")
        response = {"videos": [
            {"id": 1, "url": "https://www.pexels.com/video/birds-in-a-garden-1/",
             "video_files": [{"file_type": "video/mp4", "link": "https://example.com/1.mp4",
                              "width": 720, "height": 1280}]},
            {"id": 2, "url": "https://www.pexels.com/video/person-using-laptop-screen-2/",
             "video_files": [{"file_type": "video/mp4", "link": "https://example.com/2.mp4",
                              "width": 1920, "height": 1080}]},
        ]}
        with patch("shorts.footage.request_json", return_value=response):
            clips = _pexels_candidates("laptop screen", settings)
        self.assertEqual([clip["id"] for clip in clips], ["2"])

    def test_user_background_keywords_override_unrelated_script_queries(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key",
                           stock_fallback="concept", llm_api_key="test-key",
                           stock_vision_model="test-model")
        candidate = {"id": "2", "url": "https://cdn.pixabay.com/2.mp4", "width": 720,
                     "height": 1280, "bytes_hint": 1000000, "credit": "A",
                     "page": "https://pixabay.com/videos/id-2/"}
        with patch("shorts.footage._pixabay_candidates", side_effect=[[candidate], []]) as search, \
             patch("shorts.footage.FrameVerifier.verify", return_value=True), \
             patch("shorts.footage._download", side_effect=lambda url, path, cfg: path.write_bytes(b"video")):
            sources, credits = obtain_footage(
                ("birds and flowers",), "How AI tools answer questions", self.root, settings,
                narration="An AI tool searches documents. It then forms an answer.",
                background_keywords=("laptop screen", "document search"))
        self.assertEqual([call.args[0] for call in search.call_args_list],
                         ["laptop screen", "document search"])
        self.assertEqual([source.suffix for source in sources], [".mp4", ".png"])
        self.assertEqual(len(credits), 1)

    def test_unrelated_automatic_query_uses_narration_visual(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key",
                           stock_fallback="concept")
        self.assertFalse(query_fits_story("birds and flowers", "How databases work",
                                          "A database stores records for quick lookup."))
        with patch("shorts.footage._pixabay_candidates") as search:
            sources, credits = obtain_footage(
                ("birds and flowers",), "How databases work", self.root, settings,
                narration="A database stores records for quick lookup.")
        search.assert_not_called()
        self.assertEqual(credits, [])
        with Image.open(sources[0]) as image:
            self.assertEqual(image.size, (settings.width, settings.height))

    def test_automatic_queries_must_fit_their_narration_scene(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key",
                           stock_fallback="concept")
        with patch("shorts.footage._pixabay_candidates") as search:
            sources, credits = obtain_footage(
                ("watering a garden", "student reading a book"), "A daily routine",
                self.root, settings,
                narration="A student reads a book. Later someone waters a garden.")
        search.assert_not_called()
        self.assertEqual([path.suffix for path in sources], [".png", ".png"])
        self.assertEqual(credits, [])

    def test_auto_footage_uses_pixabay_when_no_local_clip(self):
        settings = replace(self.settings, pexels_api_key="", pixabay_api_key="test-key",
                           llm_api_key="test-key", stock_vision_model="test-model")
        candidate = {"id": "2", "url": "https://cdn.pixabay.com/2.mp4", "width": 720,
                     "height": 1280, "bytes_hint": 1800000, "credit": "Tall",
                     "page": "https://pixabay.com/videos/id-2/"}
        with patch("shorts.footage._pixabay_candidates", return_value=[candidate]), \
             patch("shorts.footage._pexels_candidates") as pexels, \
             patch("shorts.footage.FrameVerifier.verify", return_value=True), \
             patch("shorts.footage._download", side_effect=lambda url, path, cfg: path.write_bytes(b"video")):
            sources, credits = obtain_footage(("night sky",), "A calm night", self.root, settings)
        pexels.assert_not_called()
        self.assertEqual(sources[0].suffix, ".mp4")
        self.assertEqual(credits[0]["source"], "Pixabay")

    def test_frame_verifier_rejects_and_caches_mismatched_clip(self):
        settings = replace(self.settings, llm_api_key="", stock_vision_model="")
        clip = self.root / "stock.mp4"
        clip.write_bytes(b"test")
        verifier = FrameVerifier(settings)
        verifier.local = Mock()
        verifier.local.matches.return_value = False
        verifier.available = True
        frames = (Image.new("RGB", (360, 640), "green"),
                  Image.new("RGB", (360, 640), "green"))
        with patch("shorts.vision._sample_frames", return_value=frames) as sample, \
             patch("shorts.vision.request_json") as remote:
            self.assertFalse(verifier.verify(clip, "document search", "How retrieval works", 8))
            self.assertFalse(verifier.verify(clip, "document search", "How retrieval works", 8))
        sample.assert_called_once()
        remote.assert_not_called()

    def test_remote_frame_verifier_accepts_explicit_match(self):
        settings = replace(self.settings, llm_api_key="test-key", stock_vision_model="image-model")
        clip = self.root / "stock.mp4"
        clip.write_bytes(b"test")
        verifier = FrameVerifier(settings)
        verifier.local = None
        verifier.remote_available = True
        verifier.available = True
        frames = (Image.new("RGB", (360, 640), "blue"),
                  Image.new("RGB", (360, 640), "blue"))
        response = {"choices": [{"message": {"content": '{"match": true}'}}]}
        with patch("shorts.vision._sample_frames", return_value=frames), \
             patch("shorts.vision.request_json", return_value=response) as api:
            self.assertTrue(verifier.verify(clip, "typing keyboard", "How coding works", 8))
        self.assertEqual(api.call_args.kwargs["json"]["model"], "image-model")

    def test_file_validation(self):
        video = self.root / "test.mp4"
        video.write_bytes(b"x" * 15_000)
        good = {"format": {"duration": "10", "format_name": "mov,mp4"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280},
                            {"codec_type": "audio", "codec_name": "aac"}]}
        with patch("shorts.media.probe", return_value=good):
            self.assertEqual(validate_video(video, self.settings, expected_seconds=10)["width"], 720)
        bad = dict(good, streams=good["streams"][:1])
        with patch("shorts.media.probe", return_value=bad):
            with self.assertRaisesRegex(ValueError, "both video and audio"):
                validate_video(video, self.settings)

    def test_transient_http_retry(self):
        first = Mock(status_code=429, headers={}, ok=False)
        second = Mock(status_code=200, headers={}, ok=True)
        second.json.return_value = {"ok": True}
        session = Mock()
        session.request.side_effect = [first, second]
        with patch("shorts.net.time.sleep"):
            self.assertEqual(request_json("GET", "https://example.com", service="test", session=session), {"ok": True})
        self.assertEqual(session.request.call_count, 2)

    def test_llm_http_error_is_actionable_without_leaking_provider_body(self):
        response = Mock(status_code=400, headers={}, ok=False)
        response.json.return_value = {"error": {"code": 400, "message": "secret-provider-detail"}}
        session = Mock()
        session.request.return_value = response
        with self.assertRaises(ExternalError) as raised:
            request_json("POST", "https://example.com", service="LLM", session=session)
        message = str(raised.exception)
        self.assertIn("configured models and JSON output support", message)
        self.assertNotIn("secret-provider-detail", message)

    def test_partial_failure_and_retry(self):
        config = self.config(platforms=("youtube", "facebook"), publish_mode="immediate", duration_seconds=10)
        job_id = self.ready_job(config)
        youtube = Mock()
        youtube.publish.return_value = PublishResult("succeeded", "yt1", "https://www.youtube.com/watch?v=yt1")
        facebook = Mock()
        facebook.publish.side_effect = [Exception("unexpected provider failure"), PublishResult("succeeded", "fb1", "https://www.facebook.com/reel/fb1")]
        with patch("shorts.pipeline.validate_video", return_value={}), \
             patch.dict("shorts.pipeline.PUBLISHERS", {"youtube": youtube, "facebook": facebook}):
            first = publish_job(job_id, self.db, self.settings)
            self.assertEqual(first["status"], "partially_completed")
            second = publish_job(job_id, self.db, self.settings)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(youtube.publish.call_count, 1)
        self.assertEqual(facebook.publish.call_count, 2)
        self.assertEqual(self.db.publication(job_id, "youtube")["external_id"], "yt1")

    def test_unknown_upload_requires_manual_override(self):
        config = self.config(platforms=("youtube",), publish_mode="immediate", duration_seconds=10)
        job_id = self.ready_job(config)
        publisher = Mock()
        publisher.publish.side_effect = UnknownOutcome("check the channel")
        with patch("shorts.pipeline.validate_video", return_value={}), \
             patch.dict("shorts.pipeline.PUBLISHERS", {"youtube": publisher}):
            publish_job(job_id, self.db, self.settings)
            publish_job(job_id, self.db, self.settings)
        self.assertEqual(publisher.publish.call_count, 1)
        self.assertEqual(self.db.publication(job_id, "youtube")["status"], "needs_review")

    def test_scheduled_job_recovery(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = self.config(platforms=("youtube",), publish_mode="schedule", scheduled_at=future)
        job_id = self.ready_job(config)
        self.assertNotIn(job_id, self.db.due_jobs())
        with self.db.connect() as connection:
            connection.execute("UPDATE jobs SET scheduled_at=? WHERE id=?", ("2020-01-01T00:00:00+00:00", job_id))
        self.assertIn(job_id, Database(self.settings.database).due_jobs())

    def test_immediate_ready_job_recovery(self):
        config = self.config(platforms=("youtube",), publish_mode="immediate")
        job_id = self.ready_job(config)
        self.assertIn(job_id, Database(self.settings.database).due_jobs())

    def test_worker_polls_manual_publication_still_processing(self):
        config = self.config(platforms=("youtube",), publish_mode="generate_only")
        job_id = self.ready_job(config)
        self.db.update_publication(job_id, "youtube", status="processing", external_id="yt1")
        self.assertIn(job_id, self.db.due_jobs())
        publisher = Mock()
        publisher.publish.return_value = PublishResult("succeeded", "yt1", "https://www.youtube.com/watch?v=yt1")
        with patch("shorts.pipeline.validate_video", return_value={}), \
             patch.dict("shorts.pipeline.PUBLISHERS", {"youtube": publisher}):
            result = run_job(job_id, self.db, self.settings)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["publications"][0]["status"], "succeeded")
        self.assertEqual(publisher.publish.call_count, 1)

    def test_adapter_missing_prerequisites(self):
        video = self.root / "video.mp4"
        video.write_bytes(b"x")
        settings = replace(self.settings, meta_page_id="", meta_page_token="", instagram_user_id="")
        with self.assertRaisesRegex(RuntimeError, "META_PAGE_ID"):
            FacebookPublisher().publish("job", video, {}, {}, self.db, settings)
        with self.assertRaisesRegex(RuntimeError, "INSTAGRAM_USER_ID"):
            InstagramPublisher().publish("job", video, {}, {}, self.db, settings)
        with self.assertRaisesRegex(RuntimeError, "YouTube token"):
            YouTubePublisher().publish("job", video, {}, {}, self.db,
                                       replace(settings, youtube_token=self.root / "missing-youtube-token.json"))

    def test_youtube_requires_read_scope_for_status_check(self):
        token = self.root / "youtube-token.json"
        token.write_text("{}", encoding="utf-8")
        credentials = Mock(scopes=["https://www.googleapis.com/auth/youtube.upload"])
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=credentials):
            with self.assertRaisesRegex(RuntimeError, "lacks upload and video status read access"):
                _youtube_service(replace(self.settings, youtube_token=token))

    def test_facebook_official_flow_with_mock_responses(self):
        config = self.config(platforms=("facebook",), duration_seconds=10)
        job_id, _ = self.db.create_job(config, config.fingerprint())
        video = self.root / "video.mp4"
        video.write_bytes(b"x" * 12_000)
        settings = replace(self.settings, meta_page_id="123", meta_page_token="token")
        current = self.db.claim_publication(job_id, "facebook")
        upload_response = Mock(ok=True)
        upload_response.json.return_value = {"success": True}
        answers = [{"id": "123"}, {"video_id": "fb123", "upload_url": "https://rupload.facebook.com/video-upload/v24.0/fb123"},
                   {"success": True}, {"status": {"video_status": "published", "publishing_phase": {"status": "complete"}}}]
        with patch("shorts.publishers.request_json", side_effect=answers) as api, \
             patch("shorts.publishers.requests.post", return_value=upload_response) as upload:
            result = FacebookPublisher().publish(job_id, video, {"title": "T", "description": "D"}, current, self.db, settings)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.external_id, "fb123")
        self.assertEqual(api.call_args_list[1].args[1], "https://graph.facebook.com/v24.0/me/video_reels")
        self.assertEqual(upload.call_count, 1)

    def test_instagram_container_publish_with_mock_responses(self):
        config = self.config(platforms=("instagram",), duration_seconds=10)
        job_id, _ = self.db.create_job(config, config.fingerprint())
        video = self.root / "video.mp4"
        video.write_bytes(b"x" * 12_000)
        settings = replace(self.settings, meta_page_id="123", meta_page_token="token",
                           instagram_user_id="456")
        current = self.db.claim_publication(job_id, "instagram")
        answers = [{"instagram_business_account": {"id": "456"}},
                   {"id": "container1", "uri": "https://rupload.facebook.com/ig-api-upload/v24.0/container1"},
                   {"status_code": "FINISHED"}, {"id": "media1"},
                   {"permalink": "https://www.instagram.com/reel/example/"}]
        upload_response = Mock(ok=True)
        upload_response.json.return_value = {"success": True}
        with patch("shorts.publishers.request_json", side_effect=answers) as api, \
             patch("shorts.publishers.requests.post", return_value=upload_response) as upload:
            result = InstagramPublisher().publish(job_id, video, {"description": "D"}, current, self.db, settings)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.external_id, "media1")
        self.assertEqual(api.call_count, 5)
        self.assertEqual(api.call_args_list[1].kwargs["data"]["upload_type"], "resumable")
        self.assertNotIn("video_url", api.call_args_list[1].kwargs["data"])
        upload.assert_called_once()
        self.assertEqual(upload.call_args.args[0], "https://rupload.facebook.com/ig-api-upload/v24.0/container1")
        self.assertEqual(upload.call_args.kwargs["headers"]["file_size"], "12000")

    def test_instagram_failed_container_can_be_retried(self):
        config = self.config(platforms=("instagram",), duration_seconds=10)
        job_id, _ = self.db.create_job(config, config.fingerprint())
        video = self.root / "video.mp4"
        video.write_bytes(b"x" * 12_000)
        settings = replace(self.settings, meta_page_id="123", meta_page_token="token",
                           instagram_user_id="456")
        current = self.db.claim_publication(job_id, "instagram")
        answers = [{"instagram_business_account": {"id": "456"}},
                   {"id": "container1", "uri": "https://rupload.facebook.com/ig-api-upload/v24.0/container1"},
                   {"status_code": "EXPIRED"}]
        upload_response = Mock(ok=True)
        upload_response.json.return_value = {"success": True}
        with patch("shorts.publishers.request_json", side_effect=answers), \
             patch("shorts.publishers.requests.post", return_value=upload_response):
            with self.assertRaisesRegex(RuntimeError, "expired"):
                InstagramPublisher().publish(job_id, video, {"description": "D"}, current, self.db, settings)
        record = self.db.publication(job_id, "instagram")
        self.assertEqual(record["external_id"], "")
        self.assertNotIn("container_id", record["remote_state"])

    def test_instagram_upload_recovery_checks_existing_container(self):
        config = self.config(platforms=("instagram",), duration_seconds=10)
        job_id, _ = self.db.create_job(config, config.fingerprint())
        video = self.root / "video.mp4"
        video.write_bytes(b"x" * 12_000)
        settings = replace(self.settings, meta_page_id="123", meta_page_token="token",
                           instagram_user_id="456")
        current = self.db.claim_publication(job_id, "instagram")
        answers = [{"instagram_business_account": {"id": "456"}},
                   {"id": "container1", "uri": "https://rupload.facebook.com/ig-api-upload/v24.0/container1"},
                   {"instagram_business_account": {"id": "456"}}, {"status_code": "FINISHED"},
                   {"status_code": "FINISHED"}, {"id": "media1"},
                   {"permalink": "https://www.instagram.com/reel/example/"}]
        with patch("shorts.publishers.request_json", side_effect=answers), \
             patch("shorts.publishers.requests.post", side_effect=requests.ConnectionError) as upload:
            with self.assertRaisesRegex(RuntimeError, "upload connection failed"):
                InstagramPublisher().publish(job_id, video, {"description": "D"}, current, self.db, settings)
            saved = self.db.publication(job_id, "instagram")
            result = InstagramPublisher().publish(job_id, video, {"description": "D"}, saved, self.db, settings)
        self.assertEqual(result.status, "succeeded")
        upload.assert_called_once()

    def test_instagram_rejects_untrusted_upload_uri(self):
        video = self.root / "video.mp4"
        video.write_bytes(b"x" * 12_000)
        with patch("shorts.publishers.requests.post") as upload:
            with self.assertRaisesRegex(RuntimeError, "unexpected resumable upload URL"):
                InstagramPublisher._upload_file("https://example.com/steal", video, "token")
        upload.assert_not_called()

    def test_youtube_resumable_upload_with_mock_service(self):
        config = self.config(platforms=("youtube",), duration_seconds=10)
        job_id, _ = self.db.create_job(config, config.fingerprint())
        video = self.root / "video.mp4"
        video.write_bytes(b"x" * 12_000)
        current = self.db.claim_publication(job_id, "youtube")
        service = Mock()
        service.videos.return_value.insert.return_value.next_chunk.return_value = (None, {"id": "yt123"})
        service.videos.return_value.list.return_value.execute.return_value = {
            "items": [{"status": {"uploadStatus": "processed", "privacyStatus": "private"},
                       "processingDetails": {"processingStatus": "succeeded"}}]}
        with patch("shorts.publishers._youtube_service", return_value=service):
            result = YouTubePublisher().publish(job_id, video,
                                                {"title": "T", "description": "D", "hashtags": [],
                                                 "visibility": {"youtube": "private"}},
                                                current, self.db, self.settings)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.external_id, "yt123")
        self.assertEqual(self.db.publication(job_id, "youtube")["external_id"], "yt123")

    def test_publication_result_persists(self):
        config = self.config(platforms=("youtube",))
        job_id, _ = self.db.create_job(config, config.fingerprint())
        self.db.update_publication(job_id, "youtube", status="succeeded", external_id="abc",
                                   url="https://www.youtube.com/watch?v=abc")
        reopened = Database(self.settings.database)
        self.assertEqual(reopened.publication(job_id, "youtube")["external_id"], "abc")

    def test_add_destination_after_generation(self):
        config = self.config(duration_seconds=10)
        job_id = self.ready_job(config)
        self.db.transition(job_id, "completed")
        self.db.add_platforms(job_id, ("youtube",))
        repeated, created = self.db.create_job(config, config.fingerprint())
        self.assertEqual(repeated, job_id)
        self.assertFalse(created)
        publisher = Mock()
        publisher.publish.return_value = PublishResult("succeeded", "yt2", "https://www.youtube.com/watch?v=yt2")
        with patch("shorts.pipeline.validate_video", return_value={}), \
             patch.dict("shorts.pipeline.PUBLISHERS", {"youtube": publisher}):
            result = publish_job(job_id, self.db, self.settings)
        self.assertEqual(result["status"], "completed")

    def test_actual_ffmpeg_render(self):
        try:
            ffmpeg = binary("ffmpeg", "FFMPEG_PATH")
            binary("ffprobe", "FFPROBE_PATH")
        except RuntimeError:
            self.skipTest("FFmpeg is not installed")
        import subprocess
        job_dir = self.root / "render"
        job_dir.mkdir()
        image = job_dir / "background.png"
        Image.new("RGB", (360, 640), "#203050").save(image)
        audio = job_dir / "audio.wav"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                        "-y", str(audio)], check=True)
        srt, ass = job_dir / "captions.srt", job_dir / "captions.ass"
        write_captions((Word("Hello", 0.2, 0.6), Word("world", 0.7, 1.1)), 4, srt, ass, 360, 640, "Hello world.")
        video = render_video([image], audio, ass, 4, job_dir, self.settings)
        result = validate_video(video, self.settings, expected_seconds=4, caption_path=srt)
        self.assertEqual((result["width"], result["height"]), (360, 640))
        self.assertIn("world.", srt.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
